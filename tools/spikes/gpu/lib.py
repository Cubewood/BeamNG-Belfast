"""Shared helpers for the T-008 GPU co-residency spike (scratch code, not a product tool).

Overlap workaround: gamectl.cmd_start() takes the shared gpu.lock itself, which would wait on
the lock this spike holds. We hold the lock for the whole run ourselves and, inside THIS
process only, replace gamectl.GameLock by a no-op stand-in. gamectl.py/vlm.py are not edited.
"""
from __future__ import annotations

import contextlib
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools"))

import gamectl  # noqa: E402
import vlm  # noqa: E402
from beamng_mcp import McpClient, result_image, result_text  # noqa: E402

OUT = Path(r"H:\BelfastData\spikes\gpu")
OUT.mkdir(parents=True, exist_ok=True)
LOCK = Path(r"H:\BelfastData\gpu.lock")
NVSMI = "nvidia-smi"


class NoopGameLock:
    def __init__(self, *a, **k):
        self.held = True
        self.owner = ""

    def acquire(self, *a, **k):
        pass

    def try_acquire(self):
        return True

    def set_owner(self, line):
        pass

    def release(self):
        return False


gamectl.GameLock = NoopGameLock


class SpikeLock:
    def __init__(self):
        self.owner = f"spike-T008 pid={os.getpid()} t={int(time.time())}"
        self.held = False

    def __enter__(self):
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # fails if anyone holds it
        with os.fdopen(fd, "w") as f:
            f.write(self.owner)
        self.held = True
        return self

    def __exit__(self, *a):
        if self.held and LOCK.exists() and LOCK.read_text().strip() == self.owner:
            LOCK.unlink()
        self.held = False


# ---------------------------------------------------------------- system probes
class _MS(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def ram_mib() -> tuple[int, int]:
    """(used MiB, available MiB)."""
    m = _MS()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return (m.ullTotalPhys - m.ullAvailPhys) // 2**20, m.ullAvailPhys // 2**20


def vram_mib() -> int | None:
    try:
        o = subprocess.run([NVSMI, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=15).stdout.strip()
        return int(o.splitlines()[0])
    except Exception:
        return None


PS_SHARED = (r"$s=(Get-Counter '\GPU Adapter Memory(*)\Shared Usage','\GPU Adapter Memory(*)\Dedicated Usage')"
             ".CounterSamples; ($s|%{ $_.Path + '=' + [int64]$_.CookedValue }) -join ';'")


def gpu_counters_mib() -> dict:
    """Shared and dedicated usage per adapter (MiB) from the Windows perf counters; adapter with
    the biggest dedicated usage is the 4090."""
    try:
        o = subprocess.run(["powershell", "-NoProfile", "-Command", PS_SHARED], capture_output=True,
                           text=True, timeout=30).stdout.strip()
    except Exception:
        return {}
    ded, sh = {}, {}
    for part in o.split(";"):
        if "=" not in part:
            continue
        p, v = part.rsplit("=", 1)
        luid = p.split("(")[1].split(")")[0]
        (sh if "shared usage" in p else ded)[luid] = int(v) // 2**20
    if not ded:
        return {}
    gpu = max(ded, key=ded.get)
    return {"dedicated": ded[gpu], "shared": sh.get(gpu)}


class Sampler(threading.Thread):
    """1 Hz nvidia-smi VRAM + RAM; perf counters (shared GPU memory) every ~4 s.
    .tripped is set when available RAM stays under limit for 3 consecutive samples."""

    def __init__(self, ram_floor_mib=2048):
        super().__init__(daemon=True)
        self.stop_ev = threading.Event()
        self.rows = []
        self.tripped = False
        self.floor = ram_floor_mib
        self.mark = 0

    def run(self):
        low, n = 0, 0
        while not self.stop_ev.is_set():
            used, avail = ram_mib()
            row = {"t": time.time(), "vram": vram_mib(), "ram_used": used, "ram_avail": avail}
            if n % 4 == 0:
                row.update({"c_" + k: v for k, v in gpu_counters_mib().items()})
            self.rows.append(row)
            n += 1
            low = low + 1 if avail < self.floor else 0
            if low >= 3:
                self.tripped = True
            self.stop_ev.wait(1.0)

    def stop(self):
        self.stop_ev.set()
        self.join(timeout=30)

    def window(self, t0, t1=None):
        t1 = t1 or time.time()
        rs = [r for r in self.rows if t0 <= r["t"] <= t1]
        def agg(key, fn):
            v = [r[key] for r in rs if r.get(key) is not None]
            return fn(v) if v else None
        return {"n": len(rs), "vram_peak": agg("vram", max), "vram_min": agg("vram", min),
                "ram_used_peak": agg("ram_used", max), "ram_avail_min": agg("ram_avail", min),
                "shared_peak": agg("c_shared", max), "dedicated_peak": agg("c_dedicated", max)}


# ---------------------------------------------------------------- game
def start_game() -> dict:
    return gamectl.cmd_start(allow_daytime=False, lock_timeout=1, ready_timeout=120)


def stop_game() -> dict:
    return gamectl.cmd_stop()


def client() -> McpClient:
    st = gamectl.read_state()
    c = gamectl.make_client(st)
    c.initialize(timeout=5)
    return c


def fps_probe(c: McpClient, seconds: float = 10.0) -> dict:
    """Sample get_performance_metrics ~ every 0.25 s for `seconds`; returns the raw keys of the
    first sample plus summary of frame-time-like values (filled in by the caller after inspection)."""
    samples = []
    t_end = time.time() + seconds
    while time.time() < t_end:
        try:
            samples.append(json.loads(c.call_text("get_performance_metrics", timeout=10)))
        except Exception as e:  # noqa: BLE001
            samples.append({"error": str(e)})
        time.sleep(0.25)
    return {"samples": samples}


# ---------------------------------------------------------------- model
def lms(*args, timeout=900):
    return vlm.run_lms(*args, timeout=timeout)


def unload_all():
    with contextlib.suppress(Exception):
        lms("unload", "--all", timeout=120)


# ---------------------------------------------------------------- fps counter (in-game Lua)
FPS_INSTALL = r"""
local M = {}
M.n = 0 M.sum = 0 M.max = 0 M.slow = 0
M.onPreRender = function(dtReal, dtSim, dtRaw)
  M.n = M.n + 1 M.sum = M.sum + dtReal
  if dtReal > M.max then M.max = dtReal end
  if dtReal > 0.05 then M.slow = M.slow + 1 end
end
M.take = function()
  local r = {n = M.n, sum = M.sum, max = M.max, slow = M.slow}
  M.n = 0 M.sum = 0 M.max = 0 M.slow = 0
  return jsonEncode(r)
end
package.loaded['spikeFps'] = M
extensions.load('spikeFps')
_G.spikeFpsTake = M.take
return tostring(extensions.spikeFps ~= nil)
"""


def install_fps(c: McpClient) -> str:
    return c.run_lua(FPS_INSTALL)


def fps_take(c: McpClient) -> dict | None:
    t = c.run_lua("return spikeFpsTake()", timeout=10)
    try:
        return json.loads(t)
    except ValueError:
        return None


def measure_fps(c: McpClient, seconds: float = 10.0, tag: str = "") -> dict:
    """Frames rendered per wall second over `seconds` (counter reset at start)."""
    fps_take(c)
    t0 = time.time()
    time.sleep(seconds)
    d = fps_take(c)
    dt = time.time() - t0
    if not d or not d["n"]:
        return {"tag": tag, "fps": None, "raw": d}
    return {"tag": tag, "fps": round(d["n"] / dt, 1), "frames": d["n"], "wall_s": round(dt, 2),
            "mean_ms": round(d["sum"] / d["n"] * 1000, 1), "max_ms": round(d["max"] * 1000, 1),
            "frames_over_50ms": d["slow"]}


def defocus_game():
    """Move the foreground to the taskbar so the game window is unfocused."""
    u = ctypes.windll.user32
    h = u.FindWindowW("Shell_TrayWnd", None)
    if h:
        u.SetForegroundWindow(h)


def game_focused(c: McpClient) -> bool:
    return bool(json.loads(c.call_text("get_fps_limit_reason")).get("windowFocused"))
