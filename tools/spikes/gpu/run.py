"""T-008 spike driver. Phases: baseline | configs | loop. Always cleans up in finally.

  run.py baseline
  run.py configs --cfg 16384:max --cfg 8192:max ... [--level west_coast_usa]
  run.py loop --cfg 8192:max [--steps 10]

Holds H:\\BelfastData\\gpu.lock itself (owner tag spike-T008) for the whole run. gamectl's own lock
is replaced by a no-op inside this process only (see lib.py); gamectl.py and vlm.py are unchanged.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import *  # noqa: F401,F403
import lib

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["sees_road", "action", "reason"],
          "properties": {"sees_road": {"type": "boolean"},
                         "action": {"type": "string", "enum": ["forward", "left", "right", "stop"]},
                         "reason": {"type": "string"}}}
PROMPT = ("You are looking at one frame from a driving game, seen from behind a vehicle. "
          "Say whether a road is visible, choose the next driving action, and give a short reason (max 15 words).")
PRESETS_JSON = Path(r"H:\SteamLibrary\steamapps\common\BeamNG.drive\lua\ge\extensions\core\settings\settingsPresets.json")
STAMP = time.strftime("%Y%m%d-%H%M%S")
RES = {}
RES_PATH = None
sampler = None


def p(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def save():
    RES_PATH.write_text(json.dumps(RES, indent=2, default=str), encoding="utf-8")


def apply_lowest(c) -> dict:
    """GraphicOverallQuality=Lowest, then each preset key explicitly (the preset alone left the
    sub-keys unchanged when set live; checked by read-back)."""
    preset = json.loads(PRESETS_JSON.read_text())["Lowest"]
    c.run_lua("settings.setValue('GraphicOverallQuality','Lowest') return 'ok'")
    time.sleep(1)
    for k, v in preset.items():
        c.run_lua(f"settings.setValue({gamectl.lua_literal(k)}, {gamectl.lua_literal(v)}) return 'ok'")
    time.sleep(3)
    back = {}
    for k in preset:
        back[k] = c.run_lua(f"return tostring(settings.getValue({gamectl.lua_literal(k)}))")
    back["GraphicOverallQuality"] = c.run_lua("return tostring(settings.getValue('GraphicOverallQuality'))")
    ok = {k: (str(v).lower() == back[k].lower()) for k, v in preset.items()}
    return {"readback": back, "all_match": all(ok.values()), "mismatch": [k for k, v in ok.items() if not v]}


def game_vram(c):
    try:
        return round(int(c.call_text("get_vram_usage")) / 2**20)
    except Exception:  # noqa: BLE001
        return c.call_text("get_vram_usage")


def snapshot(c, tag):
    used, avail = ram_mib()
    return {"tag": tag, "vram_smi_mib": vram_mib(), "game_vram_mib": game_vram(c), "ram_used_mib": used,
            "ram_avail_mib": avail, **{"gpu_" + k: v for k, v in gpu_counters_mib().items()}}


def take_frames(c, n=5, tag="f"):
    d = OUT / "frames"
    d.mkdir(exist_ok=True)
    out = []
    for i in range(n):
        yaw = 15 + i * 70
        c.call("orbit_camera", {"yaw": yaw, "pitch": 12, "distance": 9})
        time.sleep(1.2)
        paths = {}
        for label, scale in (("360", 0.5), ("720", 1.0)):
            img = c.screenshot_image(scale=scale)
            f = d / f"{tag}_{i}_{label}.jpg"
            f.write_bytes(img)
            paths[label] = f
            st = gamectl.image_stats(img)
            paths[label + "_stats"] = {k: st[k] for k in st if k in ("width", "height", "mean", "stddev")}
        out.append(paths)
    return out


def judge_set(c, frames, label):
    """Judge each frame; fps counter over the whole window. Returns stats."""
    rows, errs = [], []
    fps_take(c)
    t0 = time.time()
    for i, fr in enumerate(frames):
        if sampler.tripped:
            errs.append("ram floor tripped")
            break
        try:
            r = vlm.judge_image(str(fr[label]), SCHEMA, PROMPT, reasoning="low")
            rows.append({"i": i, "seconds": r["seconds"], "tokens": r["tokens"], "prompt_tokens": r["prompt_tokens"],
                         "attempts": r["attempts"], "tok_per_s": round(r["tokens"] / r["seconds"], 1),
                         "result": r["result"]})
        except Exception as e:  # noqa: BLE001
            errs.append(f"{i}: {e}"[:300])
    t1 = time.time()
    d = fps_take(c)
    out = {"input": label, "n_ok": len(rows), "errors": errs, "rows": rows}
    if rows:
        s = [r["seconds"] for r in rows]
        out.update(sec_mean=round(sum(s) / len(s), 1), sec_min=min(s), sec_max=max(s),
                   tok_per_s_mean=round(sum(r["tok_per_s"] for r in rows) / len(rows), 1),
                   prompt_tokens=rows[0]["prompt_tokens"])
    if d and d["n"]:
        out["game_fps_during_inference"] = round(d["n"] / max(t1 - t0, 1e-3), 1)
        out["game_max_frame_ms"] = round(d["max"] * 1000, 1)
        out["game_frames_over_50ms"] = d["slow"]
    out["window"] = sampler.window(t0, t1)
    return out


def load_model(ctx, gpu, timeout=420):
    args = ["load", vlm.MODEL_KEY, "--identifier", vlm.IDENTIFIER, "--context-length", str(ctx),
            "--gpu", str(gpu), "--ttl", "1800", "-y"]
    t0 = time.time()
    logf = OUT / "lms_load.log"
    proc = subprocess.Popen([vlm.LMS, *args], stdout=open(logf, "w"), stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    reason = None
    while proc.poll() is None:
        if sampler.tripped:
            reason = "ram floor tripped (<2 GB available)"
        elif time.time() - t0 > timeout:
            reason = f"load exceeded {timeout}s"
        if reason:
            proc.kill()
            break
        time.sleep(1)
    proc.wait()
    out = logf.read_text(errors="replace")
    ok = reason is None and proc.returncode == 0
    return {"ok": ok, "reason": reason or ("" if ok else f"lms exit {proc.returncode}"),
            "seconds": round(time.time() - t0, 1), "output_tail": (out or "").strip()[-400:]}


def lms_ps():
    r = lms("ps", "--json", timeout=60)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return r.stdout[-300:]


def settle_unload(base_vram):
    unload_all()
    for _ in range(40):
        v = vram_mib()
        if v is not None and v < base_vram + 600:
            break
        time.sleep(1)
    return vram_mib()


def start_level(level):
    r = start_game()
    p("game ready", r["launch_to_ready_s"], "s")
    c = client()
    lv = gamectl.cmd_load_level(level, timeout=300)
    p("level", level, lv["load_s"], "s")
    p("fps counter:", install_fps(c))
    low = apply_lowest(c)
    p("lowest preset applied; all_match:", low["all_match"], low["mismatch"])
    time.sleep(2)
    return c, lv, low


def phase_baseline(a):
    for level in a.levels:
        base = {"level": level}
        RES.setdefault("baseline", []).append(base)
        try:
            c, lv, low = start_level(level)
            base.update(load_s=lv["load_s"], preset=low)
            base["snap_start"] = snapshot(c, "after load")
            t = time.time()
            base["focused_as_launched"] = game_focused(c)
            base["fps_a"] = measure_fps(c, 12, "lowest, as launched (focused=%s)" % base["focused_as_launched"])
            base["snap_run"] = snapshot(c, "during run")
            defocus_game()
            time.sleep(1.5)
            base["focus_after_defocus"] = game_focused(c)
            base["fps_b"] = measure_fps(c, 12, "lowest, after defocus (focused=%s)" % base["focus_after_defocus"])
            base["fps_limit"] = json.loads(c.call_text("get_fps_limit_reason"))
            base["window"] = sampler.window(t)
            base["frames"] = [{k: str(v) for k, v in fr.items()} for fr in take_frames(c, 5, "base_" + level)]
            base["log_errors"] = gamectl.log_errors()
            p(json.dumps({k: base[k] for k in ("load_s", "fps_a", "fps_b")}, default=str))
        except Exception as e:  # noqa: BLE001
            base["error"] = traceback.format_exc()[-1500:]
            p("ERROR", e)
        finally:
            base["stop"] = stop_game()
            save()


def phase_configs(a):
    base_vram = vram_mib()
    RES["vram_baseline_before_game_mib"] = base_vram
    c, lv, low = start_level(a.level)
    RES.update(level=a.level, load_s=lv["load_s"], preset=low)
    RES["game_alone"] = snapshot(c, "game alone")
    RES["game_alone_fps"] = measure_fps(c, 10, "game alone")
    p("game alone", RES["game_alone_fps"])
    frames = take_frames(c, 5, "cfg")
    RES["configs"] = []
    for spec in a.cfg:
        ctx, gpu = spec.split(":")
        row = {"ctx": int(ctx), "gpu": gpu}
        RES["configs"].append(row)
        try:
            sampler.tripped = False
            t_load = time.time()
            ld = load_model(ctx, gpu)
            row["load"] = ld
            row["load_window"] = sampler.window(t_load)
            p("load", spec, ld["ok"], ld["seconds"], row["load_window"])
            if not ld["ok"]:
                row["verdict"] = "load failed: " + ld["reason"]
                continue
            time.sleep(3)
            row["ps"] = lms_ps()
            row["snap_loaded"] = snapshot(c, "model loaded, no request running")
            row["fps_model_idle"] = measure_fps(c, 10, "model idle")
            p("fps idle", row["fps_model_idle"])
            try:  # one throwaway call so the first timed frame is not a cold start
                t = time.time()
                vlm.judge_image(str(frames[0]["360"]), SCHEMA, PROMPT)
                row["warmup_s"] = round(time.time() - t, 1)
            except Exception as e:  # noqa: BLE001
                row["warmup_s"] = "error: " + str(e)[:200]
            row["inf_360"] = judge_set(c, frames, "360")
            p("inf 360", {k: v for k, v in row["inf_360"].items() if k not in ("rows", "window")})
            row["inf_720"] = judge_set(c, frames, "720")
            p("inf 720", {k: v for k, v in row["inf_720"].items() if k not in ("rows", "window")})
            row["snap_after_inf"] = snapshot(c, "after inference")
        except Exception as e:  # noqa: BLE001
            row["error"] = traceback.format_exc()[-1500:]
            p("ERROR in config", spec, e)
        finally:
            row["unloaded_vram_mib"] = settle_unload(base_vram)
            save()
    RES["log_errors"] = gamectl.log_errors()


def phase_rev(a):
    """Model first, then the game (RAM check: the model load needs ~18 GB of page cache)."""
    base_vram = vram_mib()
    RES["vram_baseline_before_all_mib"] = base_vram
    RES["configs"] = []
    for spec in a.cfg:
        ctx, gpu = spec.split(":")
        row = {"ctx": int(ctx), "gpu": gpu, "order": "model first"}
        RES["configs"].append(row)
        started = False
        try:
            sampler.tripped = False
            t_load = time.time()
            ld = load_model(ctx, gpu)
            row["load"] = ld
            row["load_window"] = sampler.window(t_load)
            p("load", spec, ld["ok"], ld["seconds"], row["load_window"])
            if not ld["ok"]:
                row["verdict"] = "load failed: " + ld["reason"]
                continue
            time.sleep(3)
            used, avail = ram_mib()
            row["ram_model_only"] = {"used": used, "avail": avail, "vram": vram_mib()}
            row["ps"] = lms_ps()
            sampler.tripped = False
            t_game = time.time()
            started = True
            c, lv, low = start_level(a.level)
            row["game_start_window"] = sampler.window(t_game)
            row["level_load_s"] = lv["load_s"]
            p("game up with model loaded", row["game_start_window"])
            if sampler.tripped:
                row["verdict"] = "ram floor tripped while starting the game"
                continue
            time.sleep(3)
            row["snap_loaded"] = snapshot(c, "model loaded, game running, no request")
            row["fps_model_idle"] = measure_fps(c, 10, "model idle")
            p("fps idle", row["fps_model_idle"])
            frames = take_frames(c, 5, "rev")
            try:
                t = time.time()
                vlm.judge_image(str(frames[0]["360"]), SCHEMA, PROMPT)
                row["warmup_s"] = round(time.time() - t, 1)
            except Exception as e:  # noqa: BLE001
                row["warmup_s"] = "error: " + str(e)[:200]
            row["inf_360"] = judge_set(c, frames, "360")
            p("inf 360", {k: v for k, v in row["inf_360"].items() if k not in ("rows", "window")})
            row["inf_720"] = judge_set(c, frames, "720")
            p("inf 720", {k: v for k, v in row["inf_720"].items() if k not in ("rows", "window")})
            row["snap_after_inf"] = snapshot(c, "after inference")
            row["log_errors_count"] = len(gamectl.log_errors().get("errors", []))
        except Exception as e:  # noqa: BLE001
            row["error"] = traceback.format_exc()[-1500:]
            p("ERROR in config", spec, e)
        finally:
            if started:
                try:
                    row["game_stop"] = stop_game()
                except Exception as e:  # noqa: BLE001
                    row["game_stop"] = f"error: {e}"
            row["unloaded_vram_mib"] = settle_unload(base_vram)
            save()


def top_ram():
    ps = "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 8 | ForEach-Object { $_.Name + '=' + [int]($_.WorkingSet64/1MB) }"
    return subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60).stdout.split()


def phase_loop(a):
    base_vram = vram_mib()
    ctx, gpu = a.cfg[0].split(":")
    ld = load_model(ctx, gpu)  # model first: the load needs ~18 GB of RAM headroom
    RES["load"] = ld
    p("load", {k: v for k, v in ld.items() if k != "output_tail"})
    if not ld["ok"]:
        return
    time.sleep(3)
    sampler.tripped = False
    c, lv, low = start_level(a.level)
    RES.update(level=a.level, preset_ok=low["all_match"], order="model first", ctx=ctx, gpu=gpu)
    tools = {t["name"] for t in c.list_tools()}
    RES["tools_present"] = {n: n in tools for n in ("orbit_camera", "screenshot_image", "get_status")}
    if sampler.tripped:
        RES["verdict"] = "ram floor tripped while starting the game"
        return
    time.sleep(3)
    vlm.judge_image(str(take_frames(c, 1, "loopwarm")[0]["360"]), SCHEMA, PROMPT)  # warm-up
    RES["steps"] = []
    yaw, dist = 0.0, 9.0
    fps_take(c)
    t_loop0 = time.time()
    for i in range(a.steps):
        s = {"i": i}
        RES["steps"].append(s)
        try:
            if sampler.tripped:
                s["error"] = "ram floor tripped"
                break
            t0 = time.time()
            img = c.screenshot_image(scale=0.5)
            (OUT / "frames" / f"loop_{i}.jpg").write_bytes(img)
            t1 = time.time()
            r = vlm.judge_image(str(OUT / "frames" / f"loop_{i}.jpg"), SCHEMA, PROMPT, reasoning="low")
            t2 = time.time()
            res = r["result"]
            if res["action"] == "left":
                yaw -= 25
            elif res["action"] == "right":
                yaw += 25
            elif res["action"] == "forward":
                dist = max(4.0, dist - 1.0)
            c.call("orbit_camera", {"yaw": yaw, "pitch": 12, "distance": dist})
            t3 = time.time()
            s.update(screenshot_s=round(t1 - t0, 2), model_s=round(t2 - t1, 2), action_s=round(t3 - t2, 2),
                     step_s=round(t3 - t0, 2), tokens=r["tokens"], attempts=r["attempts"], json_valid=True,
                     answer=res)
        except Exception as e:  # noqa: BLE001
            s.update(json_valid=False, error=str(e)[:300])
        p("step", s)
        save()
    t_loop1 = time.time()
    d = fps_take(c)
    RES["loop_wall_s"] = round(t_loop1 - t_loop0, 1)
    if d and d["n"]:
        RES["game_fps_during_loop"] = round(d["n"] / (t_loop1 - t_loop0), 1)
        RES["game_max_frame_ms"] = round(d["max"] * 1000, 1)
        RES["game_frames_over_50ms"] = d["slow"]
    RES["window"] = sampler.window(t_loop0, t_loop1)
    RES["top_ram_procs_mib"] = top_ram()
    RES["log_errors"] = gamectl.log_errors()
    settle_unload(base_vram)


def tdr_events(since):
    ps = ("Get-WinEvent -FilterHashtable @{LogName='System';StartTime=[datetime]'%s'} -ErrorAction SilentlyContinue | "
          "Where-Object { $_.ProviderName -in 'nvlddmkm','Display' -or $_.Id -eq 4101 } | "
          "Select-Object -First 10 TimeCreated,Id,ProviderName,Message | ConvertTo-Json -Compress") % since
    return subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                          timeout=60).stdout.strip()[:1500]


def main():
    global sampler, RES_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["baseline", "configs", "rev", "loop"])
    ap.add_argument("--cfg", action="append", default=[])
    ap.add_argument("--level", default="west_coast_usa")
    ap.add_argument("--levels", nargs="*", default=["gridmap_v2", "west_coast_usa"])
    ap.add_argument("--steps", type=int, default=10)
    a = ap.parse_args()
    RES_PATH = OUT / f"results_{a.phase}_{STAMP}.json"
    RES["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
    since = time.strftime("%Y-%m-%dT%H:%M:%S")
    server_started = False
    if vlm.beamng_running() or gamectl.list_beamng_processes():
        print("BeamNG already running; abort")
        sys.exit(2)
    with SpikeLock():
        sampler = Sampler()
        sampler.start()
        try:
            if a.phase != "baseline":
                server_started = vlm.start_server()
                RES["server_started_by_spike"] = server_started
                unload_all()
            {"baseline": phase_baseline, "configs": phase_configs, "rev": phase_rev, "loop": phase_loop}[a.phase](a)
        except BaseException:
            RES["fatal"] = traceback.format_exc()[-2000:]
            p("FATAL", RES["fatal"])
        finally:
            unload_all()
            try:
                RES["final_stop"] = stop_game()
            except Exception as e:  # noqa: BLE001
                RES["final_stop"] = f"error: {e}"
            if server_started:
                lms("server", "stop", timeout=60)
            sampler.stop()
            RES["sampler_overall"] = sampler.window(0, time.time() + 1)
            RES["driver_events"] = tdr_events(since)
            RES["end"] = {"beamng": gamectl.list_beamng_processes(), "state": gamectl.read_state(),
                          "vram": vram_mib(), "server_up": vlm.server_up()}
            save()
            p("done; results", RES_PATH)


if __name__ == "__main__":
    main()
