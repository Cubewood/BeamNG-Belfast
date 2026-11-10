#!/usr/bin/env python3
"""Start, drive and stop BeamNG.drive for QA, safely. Importable module and CLI.

    uv run python tools/gamectl.py start | status | load-level NAME | screenshot OUT.jpg
                                    stop | clear-lock | log-errors | selftest --cycles 3

Rules (CLAUDE.md guard rails 1-4, task.md section 8):
  * never touches a BeamNG process it did not start; `start` exits at once if any BeamNG
    process exists (the user may be playing);
  * game runs only with the throwaway profile H:\\BeamNG-QA\\profile and `-enablemcp`;
    the flag that would open MCP to the network is never passed;
  * no launch between 09:00 and 18:00 local time unless --allow-daytime;
  * the shared GPU lock H:\\BelfastData\\gpu.lock keeps the game and the local model apart
    (same file and line format as tools/vlm.py: "<kind> pid=<pid> t=<epoch>");
  * `stop` ends only the recorded PID, after checking it is still the same BeamNG process.

See docs/gamectl.md for exit codes, settings and timings.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime as dt
import json
import os
import re
import socket
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import beamng_mcp  # noqa: E402
from beamng_mcp import McpClient, McpError  # noqa: E402

# ---------------------------------------------------------------- constants

INSTALL_DIR = Path(r"H:\SteamLibrary\steamapps\common\BeamNG.drive")
EXE = INSTALL_DIR / "Bin64" / "BeamNG.drive.x64.exe"
QA_ROOT = Path(r"H:\BeamNG-QA")
PROFILE = QA_ROOT / "profile"
DATA_DIR = Path(r"H:\BelfastData")
LOCK_PATH = DATA_DIR / "gpu.lock"
STATE_PATH = DATA_DIR / "game.state.json"
SELFTEST_DIR = DATA_DIR / "selftest"
REPO_ROOT = Path(__file__).resolve().parents[1]

MCP_HOST = "127.0.0.1"
MCP_PORT = 29292
GAME_IMAGE = "beamng.drive.x64.exe"
DAY_START, DAY_END = dt.time(9, 0), dt.time(18, 0)  # no launches in [09:00, 18:00)
LOCK_POLL_S = 15
LOCK_TIMEOUT_S = 20 * 60
READY_TIMEOUT_S = 120
LEVEL_TIMEOUT_S = 300
STOP_TIMEOUT_S = 30
START_TIME_TOLERANCE_S = 2.0

# Exit codes
EXIT_OK = 0
EXIT_ERROR = 1            # unexpected / generic failure
EXIT_RUNNING = 2          # a BeamNG process already exists (not touched)
EXIT_DAYTIME = 3          # 09:00-18:00 and no --allow-daytime
EXIT_LOCK_TIMEOUT = 4     # GPU lock still held after --lock-timeout
EXIT_LAUNCH_FAILED = 5    # game did not become ready (or died); it was stopped
EXIT_BIND = 6             # MCP port bound to something other than 127.0.0.1; game stopped
EXIT_SCREENSHOT = 7       # screenshot failed or image is black/flat
EXIT_LOG_ERRORS = 8      # log-errors --strict found errors
EXIT_NOT_RUNNING = 9      # our game is not running (status, load-level, screenshot)
EXIT_LOCK_REFUSED = 10    # clear-lock: lock is not stale
EXIT_LEVEL = 11           # level did not load in time / unknown level
EXIT_STOP_FAILED = 12     # process still alive after stop
EXIT_SELFTEST = 13        # selftest cycle failed

# QA settings, merged into the QA profile before launch. Paths are relative to <profile>\current\settings.
# value: what we write; the same keys are verified through MCP after start.
QA_SETTING_FILES: dict[str, dict] = {
    "settings.json": {
        "AudioMasterVol": 0.0,
        "fpsLimitBackgroundEnabled": False,
        "GraphicDisplayModes": "Window",
        "GraphicDisplayResolutions": "1280 720",
        "enableMcp": True,
    },
    "cloud/settings.json": {
        "onboardingPopupShown": True,
        "showedInputLayoutPopupV37": True,  # fresh profile otherwise opens the "Input Changes" screen
        "onlineFeatures": "disable",
        "telemetry": "disable",
    },
}
# How each key is read back from the running game (Lua expression -> comparable value).
QA_VERIFY_LUA: dict[str, str] = {
    "AudioMasterVol": "settings.getValue('AudioMasterVol')",
    "AudioChannelMaster": "VariableRegistry.get('$pref::SFX::AudioChannelMaster')",
    "fpsLimitBackgroundEnabled": "settings.getValue('fpsLimitBackgroundEnabled')",
    "GraphicDisplayModes": "settings.getValue('GraphicDisplayModes')",
    "GraphicDisplayResolutions": "settings.getValue('GraphicDisplayResolutions')",
    "enableMcp": "settings.getValue('enableMcp')",
    "onboardingPopupShown": "settings.getValue('onboardingPopupShown')",
    "showedInputLayoutPopupV37": "settings.getValue('showedInputLayoutPopupV37')",
    "onlineFeatures": "settings.getValue('onlineFeatures')",
    "telemetry": "settings.getValue('telemetry')",
}
# Expected live values keyed like QA_VERIFY_LUA (AudioChannelMaster mirrors AudioMasterVol).
QA_EXPECTED: dict[str, object] = {
    **{k: v for f in QA_SETTING_FILES.values() for k, v in f.items()},
    "AudioChannelMaster": 0.0,
}


class GamectlError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- Windows process helpers

class ProcInfo:
    __slots__ = ("pid", "start", "image")

    def __init__(self, pid: int, start: float | None, image: str | None):
        self.pid, self.start, self.image = pid, start, image

    def __repr__(self):
        return f"ProcInfo(pid={self.pid}, start={self.start}, image={self.image!r})"


_PROCESS_QUERY_LIMITED = 0x1000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x102
_EPOCH_DIFF_100NS = 116444736000000000  # 1601-01-01 -> 1970-01-01 in 100 ns units


def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                             ctypes.POINTER(wintypes.DWORD)]
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    return k


def _handle_info(k, h, pid: int) -> ProcInfo:
    c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
    start = None
    if k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
        ticks = (c.dwHighDateTime << 32) | c.dwLowDateTime
        start = (ticks - _EPOCH_DIFF_100NS) / 1e7
    buf = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(len(buf))
    image = buf.value if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else None
    return ProcInfo(pid, start, image)


def get_proc_info(pid: int) -> ProcInfo | None:
    """ProcInfo of a live process (creation time as epoch seconds, full image path), else None."""
    if not pid or pid <= 0:
        return None
    k = _k32()
    h = k.OpenProcess(_PROCESS_QUERY_LIMITED | _SYNCHRONIZE, False, pid)
    if not h:
        # 5 = access denied (exists, unreadable): report as alive with unknown identity.
        return ProcInfo(pid, None, None) if ctypes.get_last_error() == 5 else None
    try:
        if k.WaitForSingleObject(h, 0) != _WAIT_TIMEOUT:
            return None  # signalled: the process has exited
        return _handle_info(k, h, pid)
    finally:
        k.CloseHandle(h)


def is_beamng_image(image: str | None) -> bool:
    return bool(image) and Path(image).name.lower() == GAME_IMAGE


def same_process(recorded_start: float | None, info: ProcInfo | None,
                 tol: float = START_TIME_TOLERANCE_S) -> bool:
    """PID-reuse guard: alive, a BeamNG game image, and the recorded creation time."""
    if info is None or recorded_start is None or info.start is None:
        return False
    return is_beamng_image(info.image) and abs(info.start - recorded_start) <= tol


def terminate_if_same(pid: int, recorded_start: float | None, tol: float = START_TIME_TOLERANCE_S) -> bool:
    """TerminateProcess on pid, but only if the handle we hold is still the recorded BeamNG process."""
    k = _k32()
    h = k.OpenProcess(_PROCESS_QUERY_LIMITED | _PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        if k.WaitForSingleObject(h, 0) != _WAIT_TIMEOUT:
            return False
        if not same_process(recorded_start, _handle_info(k, h, pid), tol):
            return False
        return bool(k.TerminateProcess(h, 1))
    finally:
        k.CloseHandle(h)


def parse_tasklist_csv(text: str) -> list[tuple[str, int]]:
    """Rows of `tasklist /FO CSV /NH` -> (image name, pid)."""
    import csv
    out = []
    for row in csv.reader(text.splitlines()):
        if len(row) >= 2 and row[1].strip().isdigit():
            out.append((row[0], int(row[1])))
    return out


def list_beamng_processes() -> list[tuple[str, int]]:
    """Every process whose image name contains 'beamng' (launcher, game, tech, helpers), any owner."""
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GamectlError(EXIT_ERROR, f"cannot list processes (refusing to start blind): {e}")
    return [(n, p) for n, p in parse_tasklist_csv(out) if "beamng" in n.lower()]


# ---------------------------------------------------------------- time window

def is_daytime(now: dt.datetime | None = None) -> bool:
    t = (now or dt.datetime.now()).time()
    return DAY_START <= t < DAY_END


# ---------------------------------------------------------------- lock + state files

_OWNER_RE = re.compile(r"^\s*(\w+)\s+pid=(\d+)\s+t=(\d+)")


def parse_owner(text: str) -> dict | None:
    m = _OWNER_RE.match(text or "")
    return {"kind": m.group(1), "pid": int(m.group(2)), "t": int(m.group(3))} if m else None


def read_lock(path: Path | None = None) -> str | None:
    path = Path(path or LOCK_PATH)
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None
    except OSError:
        return "?"


def lock_is_stale(text: str | None, pid_alive=lambda p: get_proc_info(p) is not None) -> bool:
    """Stale = a parsable owner line whose pid is dead. Unparsable content is never called stale."""
    owner = parse_owner(text or "")
    return owner is not None and not pid_alive(owner["pid"])


class GameLock:
    """Exclusive-create lock on the shared GPU lock file. Releases only a lock this object wrote."""

    def __init__(self, path: Path | None = None, owner: str | None = None):
        self.path = Path(path or LOCK_PATH)
        self.owner = owner or f"game pid={os.getpid()} t={int(time.time())}"
        self.held = False

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):  # PermissionError: Windows delete-pending
            return False
        with os.fdopen(fd, "w") as f:
            f.write(self.owner)
        self.held = True
        return True

    def acquire(self, timeout: float = LOCK_TIMEOUT_S, poll: float = LOCK_POLL_S,
                sleep=time.sleep, clock=time.monotonic, on_wait=None) -> None:
        deadline = clock() + timeout
        while True:
            if self.try_acquire():
                return
            held = read_lock(self.path)
            owner = parse_owner(held or "")
            # A dead *game* lock is ours to clear (crashed gamectl). A dead vlm lock may still
            # mean a loaded model (vlm.py hands the lock over on `load`): never auto-cleared.
            if owner and owner["kind"] == "game" and lock_is_stale(held):
                log(f"removing stale game lock: {held}")
                with contextlib.suppress(OSError):
                    self.path.unlink()
                continue
            if clock() >= deadline:
                raise GamectlError(EXIT_LOCK_TIMEOUT,
                                   f"GPU lock {self.path} still held after {timeout:.0f}s: {held}")
            if on_wait:
                on_wait(held)
            sleep(min(poll, max(0.0, deadline - clock())))

    def set_owner(self, line: str) -> None:
        """Rewrite the content of a lock we hold (e.g. swap our pid for the game's)."""
        if not self.held or read_lock(self.path) != self.owner:
            raise GamectlError(EXIT_ERROR, "gpu lock is no longer ours")
        self.path.write_text(line)
        self.owner = line

    def release(self) -> bool:
        """Remove the file only if its content is exactly what we wrote."""
        if not self.held:
            return False
        self.held = False
        if read_lock(self.path) == self.owner:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
            return True
        return False


def release_lock_if_game(pid: int, path: Path | None = None) -> bool:
    """Remove the lock iff it is a game lock naming this game pid (used by stop / clear paths)."""
    path = Path(path or LOCK_PATH)
    owner = parse_owner(read_lock(path) or "")
    if owner and owner["kind"] == "game" and owner["pid"] == pid:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return True
    return False


def read_state(path: Path | None = None) -> dict | None:
    try:
        return json.loads(Path(path or STATE_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"corrupt": True}


def write_state(state: dict, path: Path | None = None) -> None:
    path = Path(path or STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def remove_state(path: Path | None = None) -> None:
    with contextlib.suppress(FileNotFoundError):
        Path(path or STATE_PATH).unlink()


# ---------------------------------------------------------------- settings seeding

def merge_json_file(path: Path, updates: dict) -> list[str]:
    """Set `updates` keys in a JSON object file, keep every other key, write only if changed.
    Returns the keys whose value changed. A corrupt file is moved aside (*.corrupt)."""
    path = Path(path)
    data: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("not an object")
            data = loaded
        except (OSError, ValueError):
            os.replace(path, path.with_name(path.name + ".corrupt"))
    changed = [k for k, v in updates.items() if k not in data or data[k] != v]
    if changed or not path.exists():
        data.update(updates)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return changed


def require_qa_profile(profile: Path) -> Path:
    """Guard rail 2: only ever write inside H:\\BeamNG-QA."""
    p = Path(os.path.abspath(profile))
    root = Path(os.path.abspath(QA_ROOT))
    if root != p and root not in p.parents:
        raise GamectlError(EXIT_ERROR, f"profile {p} is not under {root}; refusing")
    return p


def seed_settings(profile: Path) -> dict[str, list[str]]:
    """Merge QA_SETTING_FILES into the QA profile. Returns {file: [changed keys]}."""
    profile = require_qa_profile(profile)
    base = profile / "current" / "settings"
    return {rel: merge_json_file(base / rel, upd) for rel, upd in QA_SETTING_FILES.items()}


def archive_crash_reports(profile: Path) -> str | None:
    """A leftover crash report makes the next launch show a 'Send Error Reports' dialog.
    Move it out of the way (QA profile only)."""
    profile = require_qa_profile(profile)
    src = profile / "current" / "temp" / "crashReports"
    if not src.is_dir() or not any(src.iterdir()):
        return None
    dest = QA_ROOT / "crash-archive" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest)
    src.mkdir(parents=True, exist_ok=True)
    return str(dest)


def lua_literal(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def verify_lua() -> str:
    parts = ",".join(f"['{k}']={expr}" for k, expr in QA_VERIFY_LUA.items())
    return f"return jsonEncode({{{parts}}})"


def values_match(expected, actual) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(expected - actual) < 1e-6
    return expected == actual


def verify_settings(client: McpClient, fix: bool = True) -> dict:
    """Read each QA setting back from the running game. For a mismatch (when `fix`) set it live
    through settings.setValue and read again. Returns {key: {expected, actual, ok, method}} where
    method is 'preseed' (file was enough), 'live' (needed setValue) or 'FAILED'."""
    actual = client.run_lua_json(verify_lua())
    report = {}
    for key, exp in QA_EXPECTED.items():
        ok = values_match(exp, actual.get(key))
        report[key] = {"expected": exp, "actual": actual.get(key), "ok": ok,
                       "method": "preseed" if ok else "FAILED"}
    bad = [k for k, r in report.items() if not r["ok"] and k in QA_VERIFY_LUA and
           QA_VERIFY_LUA[k].startswith("settings.getValue")]
    if bad and fix:
        for k in bad:
            client.run_lua(f"settings.setValue('{k}', {lua_literal(QA_EXPECTED[k])})")
        time.sleep(0.5)
        again = client.run_lua_json(verify_lua())
        for k in report:
            if k in bad or not report[k]["ok"]:
                report[k]["actual"] = again.get(k)
                report[k]["ok"] = values_match(report[k]["expected"], again.get(k))
                report[k]["method"] = "live" if report[k]["ok"] else "FAILED"
    return report


# ---------------------------------------------------------------- network checks

_NETSTAT_RE = re.compile(r"^\s*TCP\s+(\S+):(\d+)\s+(\S+)\s+(\w+)\s+(\d+)\s*$")
_TCP_STATES = {1: "CLOSED", 2: "LISTENING", 3: "SYN_SENT", 4: "SYN_RECEIVED", 5: "ESTABLISHED",
               6: "FIN_WAIT_1", 7: "FIN_WAIT_2", 8: "CLOSE_WAIT", 9: "CLOSING", 10: "LAST_ACK",
               11: "TIME_WAIT", 12: "DELETE_TCB"}


def parse_netstat(text: str) -> list[dict]:
    """Rows of `netstat -ano -p TCP` text (evidence capture and tests)."""
    rows = []
    for line in text.splitlines():
        m = _NETSTAT_RE.match(line)
        if m:
            rows.append({"addr": m.group(1), "port": int(m.group(2)), "foreign": m.group(3),
                         "state": m.group(4), "pid": int(m.group(5)), "line": line.strip()})
    return rows


def netstat_lines(port: int) -> list[str]:
    """Raw `netstat -ano` lines that mention the port (stored as evidence)."""
    out = []
    for proto in ("TCP", "TCPv6"):
        text = subprocess.run(["netstat", "-ano", "-p", proto], capture_output=True, text=True, timeout=30).stdout
        out += [ln.strip() for ln in text.splitlines() if f":{port} " in ln]
    return out


def _fmt_row(addr: str, port: int, faddr: str, fport: int, state: str, pid: int) -> str:
    return f"TCP    {f'{addr}:{port}':<22} {f'{faddr}:{fport}':<22} {state:<15} {pid}"


def tcp_rows() -> list[dict]:
    """The kernel's TCP table (IPv4 and IPv6) with owning pids, via GetExtendedTcpTable (about 1 ms).
    Same row shape as parse_netstat. The TCP state column is unreliable on this Windows build (even a plain
    Python listener shows as state 0 here, and netstat shows system listeners as CLOSED or FIN_WAIT_1), so
    callers judge by local address, owner pid and foreign address, never by state."""
    import socket as _s
    import struct
    iph = ctypes.WinDLL("iphlpapi")
    rows = []
    for family, rowlen in ((2, 24), (23, 56)):
        size = wintypes.DWORD(0)
        iph.GetExtendedTcpTable(None, ctypes.byref(size), False, family, 5, 0)
        for _ in range(5):
            buf = ctypes.create_string_buffer(size.value)
            r = iph.GetExtendedTcpTable(buf, ctypes.byref(size), False, family, 5, 0)
            if r == 0:
                break
            if r != 122:  # ERROR_INSUFFICIENT_BUFFER: table grew, retry
                raise OSError(f"GetExtendedTcpTable failed: {r}")
        else:
            continue
        n = struct.unpack_from("<I", buf, 0)[0]
        for i in range(n):
            off = 4 + i * rowlen
            if family == 2:
                st, la, lp, ra, rp, pid = struct.unpack_from("<6I", buf, off)
                laddr, raddr = _s.inet_ntoa(struct.pack("<I", la)), _s.inet_ntoa(struct.pack("<I", ra))
            else:
                laddr = "[" + _s.inet_ntop(_s.AF_INET6, bytes(buf[off:off + 16])) + "]"
                lp = struct.unpack_from("<I", buf, off + 20)[0]
                raddr = "[" + _s.inet_ntop(_s.AF_INET6, bytes(buf[off + 24:off + 40])) + "]"
                rp, st, pid = struct.unpack_from("<3I", buf, off + 44)
            lport, rport = _s.ntohs(lp & 0xFFFF), _s.ntohs(rp & 0xFFFF)
            state = _TCP_STATES.get(st, f"STATE_{st}")
            rows.append({"addr": laddr, "port": lport, "foreign": f"{raddr}:{rport}", "state": state, "pid": pid,
                         "line": _fmt_row(laddr, lport, raddr, rport, state, pid)})
    return rows


def rows_on_port(port: int, rows: list[dict] | None = None) -> list[dict]:
    """Sockets with this LOCAL port that belong to a live process (TIME_WAIT leftovers have pid 0)."""
    rows = tcp_rows() if rows is None else rows
    return [r for r in rows if r["port"] == port and r["pid"] != 0]


def evaluate_bind(rows: list[dict], port: int, game_pid: int) -> tuple[str, list[str]]:
    """Judge one snapshot of the TCP table. Returns
      ('bad', evidence)  if any live socket on the port is not 127.0.0.1 or not owned by the game;
      ('good', evidence) if there are such sockets and all are 127.0.0.1 owned by the game;
      ('unknown', ...)   if the port has no live socket in this snapshot.
    State is ignored on purpose (see tcp_rows): on this Windows build BeamNG's listener is reported as state 0,
    FIN_WAIT_1 or CLOSED with local address 127.0.0.1 and foreign 0.0.0.0:0, rarely as LISTENING."""
    mine = rows_on_port(port, rows)
    bad = [r for r in mine if r["addr"] != "127.0.0.1" or r["pid"] != game_pid]
    if bad:
        return "bad", [r["line"] for r in bad]
    if mine:
        return "good", [r["line"] for r in mine]
    return "unknown", [f"no live socket on port {port}"]


def check_bind(port: int, game_pid: int, sampler=tcp_rows, window: float = 10.0, observe: float = 1.5,
               interval: float = 0.01, sleep=time.sleep, clock=time.monotonic) -> tuple[bool, list[str]]:
    """Sample the TCP table: wait up to `window` s for the game's MCP socket, then keep watching for
    `observe` s. Fails at once on any socket on the port bound to another address or owned by another process."""
    deadline = clock() + window
    seen_since = None
    evidence: dict[str, None] = {}
    while True:
        verdict, ev = evaluate_bind(sampler(), port, game_pid)
        now = clock()
        if verdict == "bad":
            return False, ev
        if verdict == "good":
            seen_since = seen_since if seen_since is not None else now
            for line in ev:
                evidence.setdefault(re.sub(r"\s+", " ", line), None)
            if now - seen_since >= observe:
                return True, list(evidence)
        elif now >= deadline:
            return False, [f"no socket of the game on port {port} seen within {window:.0f} s"]
        sleep(interval)


def wait_port_closed(port: int, timeout: float = 10.0, poll: float = 0.1) -> tuple[bool, list[str]]:
    """Closed = connect refused and no live socket left on the port. Returns (closed, leftover rows)."""
    deadline = time.monotonic() + timeout
    while True:
        rows = [r["line"] for r in rows_on_port(port)]
        if not rows and not port_open(port):
            return True, []
        if time.monotonic() >= deadline:
            return False, rows or [f"connect to port {port} still succeeds"]
        time.sleep(poll)


def port_open(port: int, host: str = MCP_HOST, timeout: float = 0.5) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


# ---------------------------------------------------------------- log helpers

_LOG_RE = re.compile(r"^\s*([\d.]+)\|([DIWEA])\|([^|]*)\|(.*)$")


def log_path(profile: Path | None = None) -> Path:
    return Path(profile or PROFILE) / "current" / "beamng.log"


def parse_log_lines(lines, levels=("E",), ignore: list[str] | None = None) -> list[str]:
    pats = [re.compile(p) for p in (ignore or [])]
    out = []
    for line in lines:
        m = _LOG_RE.match(line)
        if m and m.group(2) in levels and not any(p.search(line) for p in pats):
            out.append(line.rstrip())
    return out


def log_errors(profile: Path | None = None, ignore: list[str] | None = None) -> dict:
    """Error and warning lines of the profile's beamng.log (the current/last run)."""
    p = log_path(profile)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return {"log": str(p), "found": False, "errors": [], "warnings": []}
    return {"log": str(p), "found": True,
            "errors": parse_log_lines(lines, ("E",), ignore),
            "warnings": parse_log_lines(lines, ("W",), ignore)}


def mcp_listen_line(profile: Path | None = None) -> str | None:
    p = log_path(profile)
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if "MCP listening on" in line:
                return line.strip()
    except OSError:
        pass
    return None


# ---------------------------------------------------------------- screenshot helpers

def is_ignored_by_git(path: Path) -> bool:
    r = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "-q", str(path)],
                       capture_output=True)
    return r.returncode == 0


def check_output_path(out: Path, ignored=is_ignored_by_git) -> Path:
    """Images are never committed: allow paths outside the repo, or inside it only if git-ignored."""
    out = Path(os.path.abspath(out))
    repo = Path(os.path.abspath(REPO_ROOT))
    if repo in out.parents and not ignored(out):
        raise GamectlError(EXIT_ERROR, f"{out} is inside the repo and not git-ignored; refusing")
    return out


def image_stats(data: bytes) -> dict:
    import io
    from PIL import Image, ImageStat
    img = Image.open(io.BytesIO(data))
    g = img.convert("L")
    st = ImageStat.Stat(g)
    return {"width": img.width, "height": img.height, "format": img.format,
            "mean": round(st.mean[0], 2), "stddev": round(st.stddev[0], 2), "bytes": len(data)}


def screenshot_ok(stats: dict, min_mean: float = 5.0, min_std: float = 5.0) -> bool:
    return stats["mean"] >= min_mean and stats["stddev"] >= min_std


# ---------------------------------------------------------------- game control

def make_client(state: dict | None = None) -> McpClient:
    port = (state or {}).get("port", MCP_PORT)
    return McpClient(f"http://{MCP_HOST}:{port}/mcp")


def our_game(state: dict | None = None) -> tuple[dict, ProcInfo] | tuple[None, None]:
    """(state, ProcInfo) when the recorded game is alive and verified, else (None, None)."""
    state = state if state is not None else read_state()
    if not state or state.get("corrupt") or "pid" not in state:
        return None, None
    info = get_proc_info(state["pid"])
    if same_process(state.get("start_time"), info):
        return state, info
    return None, None


def require_our_game() -> tuple[dict, McpClient]:
    state, _ = our_game()
    if not state:
        raise GamectlError(EXIT_NOT_RUNNING, "our game is not running (no verified game.state.json)")
    return state, make_client(state)


def wait_exit(pid: int, start: float | None, timeout: float, poll: float = 0.25) -> bool:
    """True when the recorded process has exited (or the pid now belongs to something else)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not same_process(start, get_proc_info(pid)):
            return True
        time.sleep(poll)
    return not same_process(start, get_proc_info(pid))


def wait_no_beamng(timeout: float = 20.0, poll: float = 0.5) -> list[tuple[str, int]]:
    """After the main process is gone its CEF helpers (same image name) need a few seconds to exit,
    longer after a forced kill. Wait for them; never kill them. Returns what is still there."""
    deadline = time.monotonic() + timeout
    while True:
        left = list_beamng_processes()
        if not left or time.monotonic() >= deadline:
            return left
        time.sleep(poll)


def _kill_ours(state: dict, wait: float = 10.0) -> bool:
    """Terminate exactly the recorded process (identity re-checked on the handle)."""
    terminate_if_same(state["pid"], state.get("start_time"))
    gone = wait_exit(state["pid"], state.get("start_time"), wait)
    wait_no_beamng()
    return gone


def cmd_start(allow_daytime: bool = False, lock_timeout: float = LOCK_TIMEOUT_S,
              ready_timeout: float = READY_TIMEOUT_S, profile: Path | None = None,
              lock_poll: float = LOCK_POLL_S) -> dict:
    profile = require_qa_profile(profile or PROFILE)
    # 1. Anything BeamNG running? Not ours to touch, whoever started it.
    procs = list_beamng_processes()
    if procs:
        raise GamectlError(EXIT_RUNNING, "BeamNG is already running, not touching it: " +
                           ", ".join(f"{n} pid={p}" for n, p in procs))
    # 2. Time window.
    if is_daytime() and not allow_daytime:
        raise GamectlError(EXIT_DAYTIME, "refusing to launch between 09:00 and 18:00 local time "
                                         "(day job runs on this PC); use --allow-daytime to override")
    if not EXE.is_file():
        raise GamectlError(EXIT_ERROR, f"game executable not found: {EXE}")
    # 3. A state file whose process is gone is leftover from a crash: drop it.
    old = read_state()
    if old is not None:
        if our_game(old)[0]:
            raise GamectlError(EXIT_RUNNING, f"our game is already running (pid {old['pid']})")
        log("removing stale game.state.json")
        remove_state()
    # 4. GPU lock (shared with vlm.py): wait for the model, then take it.
    lock = GameLock()
    lock.acquire(lock_timeout, lock_poll,
                 on_wait=lambda held: log(f"waiting for GPU lock held by: {held}"))
    state: dict | None = None
    try:
        procs = list_beamng_processes()  # may have changed while we waited
        if procs:
            raise GamectlError(EXIT_RUNNING, "BeamNG started while waiting for the lock: " +
                               ", ".join(f"{n} pid={p}" for n, p in procs))
        if port_open(MCP_PORT):
            raise GamectlError(EXIT_LAUNCH_FAILED, f"{MCP_HOST}:{MCP_PORT} is already in use by something else")
        archived = archive_crash_reports(profile)
        seeded = seed_settings(profile)
        # 5. Launch. No -windowed / -fullscreen (both crash this build), never -headless.
        args = [str(EXE), "-userpath", str(profile), "-enablemcp"]
        assert not any("mcpremote" in a for a in args)
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        t_launch = time.monotonic()
        launched_at = time.time()
        proc = subprocess.Popen(args, cwd=str(INSTALL_DIR), creationflags=flags, close_fds=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        info = get_proc_info(proc.pid)
        state = {"pid": proc.pid, "start_time": info.start if info else None, "launch_time": launched_at,
                 "exe": str(EXE), "profile": str(profile), "port": MCP_PORT,
                 "gamectl_pid": os.getpid(), "lock": None}
        lock.set_owner(f"game pid={proc.pid} t={int(launched_at)}")
        state["lock"] = lock.owner
        write_state(state)
        # 6. Wait for MCP.
        client = make_client(state)
        status = None
        deadline = t_launch + ready_timeout
        while time.monotonic() < deadline:
            if not same_process(state["start_time"], get_proc_info(proc.pid)):
                raise GamectlError(EXIT_LAUNCH_FAILED, "game exited during startup")
            try:
                client.initialize(timeout=2)
                status = client.get_status(timeout=5)
                break
            except McpError:
                time.sleep(1)
        if status is None:
            raise GamectlError(EXIT_LAUNCH_FAILED, f"MCP did not answer within {ready_timeout}s")
        ready_s = time.monotonic() - t_launch
        # 7. Loopback-only bind, owned by the game.
        ok, evidence = check_bind(MCP_PORT, proc.pid)
        if not ok:
            raise GamectlError(EXIT_BIND, "MCP port is not bound to 127.0.0.1 by the game: " + "; ".join(evidence))
        # 8. QA settings: verify, fix live where the file was not enough.
        report = verify_settings(client)
        listen_log = None
        for _ in range(10):  # the game flushes beamng.log in bursts
            listen_log = mcp_listen_line(profile)
            if listen_log:
                break
            time.sleep(0.5)
        netstat = netstat_lines(MCP_PORT)
        state.update({"ready_s": round(ready_s, 2), "bind_evidence": evidence, "netstat": netstat,
                      "mcp_listen_log": listen_log, "settings": report})
        write_state(state)
        failed = [k for k, r in report.items() if not r["ok"]]
        if failed:
            log("WARNING: QA settings not in effect: " + ", ".join(failed))
        return {"pid": proc.pid, "launch_to_ready_s": round(ready_s, 2), "bind": evidence, "netstat": netstat,
                "mcp_listen_log": state["mcp_listen_log"], "crash_reports_archived": archived,
                "seeded": seeded, "settings": report, "status": status}
    except BaseException:
        # Anything went wrong after we may have started a game: end ONLY our recorded process.
        if state:
            _kill_ours(state)
            remove_state()
        lock.release()
        raise


def cmd_status() -> dict:
    state = read_state()
    ours, info = our_game(state)
    out: dict = {"ours_running": bool(ours), "pid": ours["pid"] if ours else None,
                 "beamng_processes": [{"name": n, "pid": p} for n, p in list_beamng_processes()],
                 "lock": read_lock(), "state_file": state is not None, "mcp": False, "level": None}
    if ours:
        out["profile"] = ours.get("profile")
        out["lock_owner_is_ours"] = release_check_owner(ours)
        try:
            client = make_client(ours)
            client.initialize(timeout=3)
            st = client.get_status(timeout=5)
            out["mcp"] = True
            out["level"] = st.get("level") or None
            out["vehicle_ready"] = "vehicle" in st
            out["vehicle_count"] = st.get("vehicleCount")
        except McpError as e:
            out["mcp_error"] = str(e)
    return out


def release_check_owner(state: dict) -> bool:
    owner = parse_owner(read_lock() or "")
    return bool(owner and owner["kind"] == "game" and owner["pid"] == state["pid"])


def level_matches(status_level: str | None, name: str) -> bool:
    return bool(status_level) and name.lower() in status_level.lower().replace("\\", "/")


def is_loading(client: McpClient) -> bool:
    """True while the game's loading screen is requested (core_gamestate.loading())."""
    return client.run_lua("return tostring(core_gamestate.loading())", timeout=10).strip() == "true"


def cmd_load_level(name: str, timeout: float = LEVEL_TIMEOUT_S, settle: float = 3.0,
                   hide_ui: bool = True) -> dict:
    """Load a level and wait until it is really ready: get_status reports the level and a vehicle,
    and the loading screen has gone. Then hide the UI (QA view) so screenshots show only the 3D scene."""
    state, client = require_our_game()
    levels = client.call_json("list_levels", timeout=15)
    names = [l.get("name") for l in levels]
    if name not in names:
        raise GamectlError(EXIT_LEVEL, f"unknown level '{name}'. Installed: {', '.join(sorted(map(str, names)))}")
    t0 = time.monotonic()
    reply = client.load_level(name)
    deadline = t0 + timeout
    status = None
    while time.monotonic() < deadline:
        if not same_process(state.get("start_time"), get_proc_info(state["pid"])):
            raise GamectlError(EXIT_LEVEL, "game exited while loading the level")
        try:
            st = client.get_status(timeout=10)  # may time out while the game is busy loading
            if level_matches(st.get("level"), name) and "vehicle" in st and not is_loading(client):
                status = st
                break
        except McpError:
            pass
        time.sleep(1)
    if status is None:
        raise GamectlError(EXIT_LEVEL, f"level '{name}' not ready (level + vehicle + loading screen gone) "
                                       f"within {timeout:.0f}s")
    load_s = time.monotonic() - t0
    time.sleep(settle)  # loading screen fade-out and a few rendered frames
    if hide_ui:
        client.call("toggle_ui", {"show": False})
        time.sleep(1.5)  # the UI takes a moment to disappear: a screenshot right after still shows it
    return {"level": status["level"], "vehicle": status["vehicle"].get("jbeam"),
            "config": status["vehicle"].get("configKey"), "load_s": round(load_s, 2),
            "camera": status.get("camera", {}).get("mode"), "ui_hidden": hide_ui, "load_reply": reply}


def cmd_screenshot(out: Path, scale: float = 1.0, timeout: float = 30.0,
                   min_mean: float = 5.0, min_std: float = 5.0) -> dict:
    out = check_output_path(out)
    state, client = require_our_game()
    t0 = time.monotonic()
    try:
        data = client.screenshot_image(scale=scale, timeout=timeout)
    except McpError as e:
        raise GamectlError(EXIT_SCREENSHOT, str(e))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    stats = image_stats(data)
    stats.update({"path": str(out), "seconds": round(time.monotonic() - t0, 2),
                  "non_black": screenshot_ok(stats, min_mean, min_std)})
    return stats


def cmd_stop(timeout: float = STOP_TIMEOUT_S) -> dict:
    state = read_state()
    if state is None or state.get("corrupt") or "pid" not in state:
        procs = list_beamng_processes()
        if procs:
            raise GamectlError(EXIT_RUNNING, "no game.state.json: a BeamNG process exists but is not ours, "
                                             "not touching it: " + ", ".join(f"{n} pid={p}" for n, p in procs))
        remove_state()
        return {"stopped": False, "note": "nothing to stop (no state file, no BeamNG process)"}
    pid, start = state["pid"], state.get("start_time")
    t0 = time.monotonic()
    result = {"pid": pid}
    if not same_process(start, get_proc_info(pid)):
        # Recorded process is gone (pid may even be reused): only clean up our own files.
        result.update(stopped=False, note="recorded process is not running (or pid was reused); "
                                          "cleaned up state and lock")
    else:
        method = "mcp-shutdown"
        try:
            make_client(state).run_lua("shutdown(0)", timeout=5)
        except McpError:
            pass  # the connection may close right after the call
        if not wait_exit(pid, start, timeout):
            method = "terminate-recorded-pid"
            terminate_if_same(pid, start)
            if not wait_exit(pid, start, 10):
                raise GamectlError(EXIT_STOP_FAILED, f"pid {pid} still running after stop")
        result.update(stopped=True, method=method)
        # the port closes with the process; give the socket a moment
        result["port_closed"], result["port_leftover"] = wait_port_closed(state.get("port", MCP_PORT))
        result["helpers_left"] = [f"{n}:{p}" for n, p in wait_no_beamng()]
    result["released_lock"] = release_lock_if_game(pid)
    remove_state()
    result["stop_s"] = round(time.monotonic() - t0, 2)
    return result


def cmd_clear_lock(also_vlm: bool = False) -> dict:
    held = read_lock()
    state = read_state()
    removed = []
    if state is not None and not our_game(state)[0] and not list_beamng_processes():
        remove_state()
        removed.append("state file (recorded process not running)")
    if held is None:
        return {"removed": removed, "note": "no lock file"}
    owner = parse_owner(held)
    if owner is None:
        raise GamectlError(EXIT_LOCK_REFUSED, f"lock content not understood, not removing: {held!r}")
    if get_proc_info(owner["pid"]) is not None:
        raise GamectlError(EXIT_LOCK_REFUSED, f"lock owner is alive (pid {owner['pid']}): {held}")
    if owner["kind"] != "game" and not also_vlm:
        raise GamectlError(EXIT_LOCK_REFUSED, f"lock belongs to '{owner['kind']}' (owner pid dead, but its "
                                              f"model may still be loaded): run `vlm.py unload`, or pass "
                                              f"--also-vlm if you know the model is unloaded: {held}")
    if owner["kind"] == "game" and list_beamng_processes():
        raise GamectlError(EXIT_LOCK_REFUSED, "a BeamNG process is running; not clearing its lock")
    LOCK_PATH.unlink()
    removed.append(f"lock ({held})")
    return {"removed": removed}


# ---------------------------------------------------------------- selftest

def end_state() -> dict:
    """What must be true after stop: no BeamNG process, port closed, lock + state files gone."""
    procs = list_beamng_processes()
    lock = read_lock()
    owner = parse_owner(lock or "")
    closed, leftover = wait_port_closed(MCP_PORT, timeout=5)
    return {"beamng_processes": [f"{n}:{p}" for n, p in procs],
            "port_closed": closed, "port_leftover": leftover,
            "state_file_gone": not STATE_PATH.exists(),
            "game_lock_gone": not (owner and owner["kind"] == "game"),
            "lock": lock}


def end_state_clean(s: dict) -> bool:
    return not s["beamng_processes"] and s["port_closed"] and s["state_file_gone"] and s["game_lock_gone"]


def refusal_check(allow_daytime: bool) -> dict:
    """With OUR game running, a second `start` (separate CLI process) must refuse with EXIT_RUNNING
    and leave the first game, its state file, lock and MCP untouched."""
    before_state, before_lock = read_state(), read_lock()
    cmd = [sys.executable, str(Path(__file__).resolve()), "start"] + (["--allow-daytime"] if allow_daytime else [])
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    after_state, after_lock = read_state(), read_lock()
    ours, _ = our_game(after_state)
    mcp = False
    if ours:
        with contextlib.suppress(McpError):
            make_client(ours).get_status(timeout=5)
            mcp = True
    return {"exit_code": p.returncode, "message": p.stderr.strip()[:200],
            "state_unchanged": before_state == after_state, "lock_unchanged": before_lock == after_lock,
            "game_still_ours": bool(ours), "mcp_still_answers": mcp,
            "ok": p.returncode == EXIT_RUNNING and before_state == after_state and before_lock == after_lock
            and bool(ours) and mcp}


def cmd_selftest(cycles: int = 3, level: str = "gridmap_v2", allow_daytime: bool = False,
                 lock_timeout: float = LOCK_TIMEOUT_S, out_dir: Path | None = None,
                 keep_going: bool = False, refusal: bool = True) -> dict:
    out_dir = Path(out_dir or SELFTEST_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    for n in range(1, cycles + 1):
        r: dict = {"cycle": n}
        log(f"--- cycle {n}/{cycles}")
        try:
            s = cmd_start(allow_daytime=allow_daytime, lock_timeout=lock_timeout)
            r.update(launch_to_ready_s=s["launch_to_ready_s"], bind=s["bind"], netstat=s["netstat"],
                     mcp_listen_log=s["mcp_listen_log"],
                     settings_ok=all(v["ok"] for v in s["settings"].values()),
                     settings_methods={k: v["method"] for k, v in s["settings"].items()})
            if refusal and n == 1:
                r["refusal"] = refusal_check(allow_daytime)
                if not r["refusal"]["ok"]:
                    raise GamectlError(EXIT_SELFTEST, f"second start did not refuse cleanly: {r['refusal']}")
            lv = cmd_load_level(level)
            r["level_load_s"] = lv["load_s"]
            r["level"] = lv["level"]
            shot = cmd_screenshot(out_dir / f"{stamp}-cycle{n}.jpg")
            r["screenshot"] = {k: shot[k] for k in ("path", "width", "height", "mean", "stddev", "non_black")}
            if not shot["non_black"]:
                raise GamectlError(EXIT_SCREENSHOT, f"screenshot looks black/flat: mean={shot['mean']} std={shot['stddev']}")
            r["log"] = {k: len(v) for k, v in log_errors().items() if isinstance(v, list)}
            r["log_errors"] = log_errors()["errors"]
            r["ok"] = True
        except GamectlError as e:
            r.update(ok=False, error=f"exit {e.code}: {e.message}")
        except Exception as e:  # noqa: BLE001 - report, then clean up
            r.update(ok=False, error=f"{type(e).__name__}: {e}")
        # Always stop what we started, then verify the end state.
        try:
            st = cmd_stop()
            r["stop_s"] = st.get("stop_s")
            r["stop_method"] = st.get("method")
        except GamectlError as e:
            r.update(ok=False, error=(r.get("error", "") + f" | stop failed: {e.message}").strip(" |"))
        r["end_state"] = end_state()
        r["end_clean"] = end_state_clean(r["end_state"])
        r["ok"] = bool(r.get("ok")) and r["end_clean"]
        results.append(r)
        log(json.dumps({k: r.get(k) for k in ("cycle", "ok", "launch_to_ready_s", "level_load_s", "stop_s", "error")}))
        if not r["ok"] and not keep_going:
            break
    report = {"time": stamp, "level": level, "cycles_requested": cycles, "results": results,
              "ok": len(results) == cycles and all(r["ok"] for r in results),
              "allow_daytime": allow_daytime}
    (out_dir / f"selftest-{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="launch the game with the QA profile and wait for MCP")
    s.add_argument("--allow-daytime", action="store_true", help="allow a launch between 09:00 and 18:00")
    s.add_argument("--lock-timeout", type=float, default=LOCK_TIMEOUT_S, help="seconds to wait for the GPU lock")
    s.add_argument("--ready-timeout", type=float, default=READY_TIMEOUT_S)
    sub.add_parser("status", help="is our game up, MCP, level, lock")
    s = sub.add_parser("load-level", help="load a level through MCP and wait until it is ready")
    s.add_argument("name")
    s.add_argument("--timeout", type=float, default=LEVEL_TIMEOUT_S)
    s.add_argument("--settle", type=float, default=3.0, help="seconds to let frames render after load")
    s.add_argument("--keep-ui", action="store_true", help="do not hide the game UI after loading")
    s = sub.add_parser("stop", help="clean quit through MCP; fallback ends only the recorded PID")
    s.add_argument("--timeout", type=float, default=STOP_TIMEOUT_S)
    s = sub.add_parser("screenshot", help="save a JPEG of the 3D view (outside the repo or git-ignored)")
    s.add_argument("out")
    s.add_argument("--scale", type=float, default=1.0, help="1.0 = full window size")
    s.add_argument("--min-mean", type=float, default=5.0, help="mean brightness below this = black")
    s = sub.add_parser("clear-lock", help="remove a stale lock (owner pid dead); refuses otherwise")
    s.add_argument("--also-vlm", action="store_true", help="also remove a dead vlm-owned lock")
    s = sub.add_parser("log-errors", help="error/warning lines of the QA profile's beamng.log")
    s.add_argument("--strict", action="store_true", help="exit 8 if any error line is left after --ignore")
    s.add_argument("--ignore", action="append", default=[], help="regex of lines to drop (repeatable)")
    s.add_argument("--no-warnings", action="store_true")
    s = sub.add_parser("selftest", help="start -> load-level -> screenshot -> stop, N times")
    s.add_argument("--cycles", type=int, default=3)
    s.add_argument("--level", default="gridmap_v2")
    s.add_argument("--allow-daytime", action="store_true")
    s.add_argument("--lock-timeout", type=float, default=LOCK_TIMEOUT_S)
    s.add_argument("--keep-going", action="store_true")
    s.add_argument("--no-refusal-check", action="store_true", help="skip the second-start refusal proof in cycle 1")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        if a.cmd == "start":
            res = cmd_start(a.allow_daytime, a.lock_timeout, a.ready_timeout)
        elif a.cmd == "status":
            res = cmd_status()
            print(json.dumps(res, indent=2))
            return EXIT_OK if res["ours_running"] else EXIT_NOT_RUNNING
        elif a.cmd == "load-level":
            res = cmd_load_level(a.name, a.timeout, a.settle, hide_ui=not a.keep_ui)
        elif a.cmd == "stop":
            res = cmd_stop(a.timeout)
        elif a.cmd == "screenshot":
            res = cmd_screenshot(Path(a.out), a.scale, min_mean=a.min_mean)
            print(json.dumps(res, indent=2))
            return EXIT_OK if res["non_black"] else EXIT_SCREENSHOT
        elif a.cmd == "clear-lock":
            res = cmd_clear_lock(a.also_vlm)
        elif a.cmd == "log-errors":
            res = log_errors(ignore=a.ignore)
            if a.no_warnings:
                res.pop("warnings")
            print(json.dumps(res, indent=2))
            return EXIT_LOG_ERRORS if (a.strict and res["errors"]) else EXIT_OK
        elif a.cmd == "selftest":
            res = cmd_selftest(a.cycles, a.level, a.allow_daytime, a.lock_timeout, keep_going=a.keep_going,
                               refusal=not a.no_refusal_check)
            print(json.dumps(res, indent=2))
            return EXIT_OK if res["ok"] else EXIT_SELFTEST
        else:  # pragma: no cover
            raise AssertionError(a.cmd)
        print(json.dumps(res, indent=2))
        return EXIT_OK
    except GamectlError as e:
        print(f"error: {e.message}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
