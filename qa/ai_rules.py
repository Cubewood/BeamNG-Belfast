#!/usr/bin/env python3
"""Left-hand traffic and AI rules in the game: roundabouts, one-ways, signals (T-407).

    uv run --no-project --python 3.12 --with requests python qa/ai_rules.py run [--allow-daytime] [--out DIR]
                                                          [--only-controllers ID,ID] [--max-approaches N]
    uv run --no-project --python 3.12 --with requests python qa/ai_rules.py analyse DIR [--drive DIR2 ...]

`run` starts the QA game through tools/gamectl.py (one game process, refuses when BeamNG already runs, refuses
09:00-18:00 unless --allow-daytime, never the remote-MCP flag), loads belfast_slice and then:

  0. reads the level's traffic signal system (core_trafficSignals) and the vehicle's road rules, and starts a
     game-side logger that records every change of every signal head for the whole run;
  1. drives the roundabout circuits (qa/routes/belfast_rules.json, graph AI as in the drive suite);
  2. for one head of each signal controller, puts a fresh Covet 60 m up the road, waits until that head shows red
     for the next 9 s, and sets AI traffic mode (the only AI mode that obeys signals,
     `lua/vehicle/ai.lua:4474-4650`; it picks its own turns) for up to 45 s;
  3. stops the game.

Everything the game said is written to the output folder (`inventory.json`, `transitions.txt`,
`approach_<id>.csv`, `approaches.json`, the circuit telemetry) and `analyse` judges it with qa/ai_rules_lib.py:
no game needed, so a run can be re-judged. Output: H:\\BelfastData\\qa\\ai_rules\\<date_time>\\metrics.json.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import json
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(HERE))
import ai_rules_lib as lib  # noqa: E402
import drive_suite as ds  # noqa: E402
import gamectl  # noqa: E402
import route_lib as rl  # noqa: E402

OUT_ROOT = Path(r"H:\BelfastData\qa\ai_rules")
RULES_ROUTES = HERE / "routes" / "belfast_rules.json"
ROUNDABOUT_CENTRES = {"newtownbreda": (462.12, 871.33)}      # OSM way 148565334 (junction=roundabout, C302)
ROUNDABOUT_R_M = 17.0
APPROACH_BACK_M = 60.0
APPROACH_MAX_S = 45.0
RED_AHEAD_S = 9.0
RED_WAIT_MAX_S = 70.0
PAST_SIGNAL_M = 12.0

# core_trafficSignals is one of main.lua's presetExtensions: a freeroam start loads it (freeroam.lua:38), the MCP's
# load_level may not. Loading it by hand, then `loadSignals(nil, true)` (the level's own signals.json, run at
# once, the navgraph being up), gives the state a player in freeroam has. `preloaded` records which it was.
LUA_INVENTORY = """
local preloaded = core_trafficSignals ~= nil
extensions.load('core_trafficSignals')
if not core_trafficSignals.getData().loaded then core_trafficSignals.loadSignals(nil, true) end
local d = core_trafficSignals.getData()
local out = {preloaded = preloaded, loaded = d.loaded, active = d.active, timer = d.timer, instances = {}, controllers = {}, sequences = {}}
for _, i in ipairs(d.instances) do
  local st = i:getState()
  out.instances[#out.instances + 1] = {id = i.id, name = i.name, c = i.controllerId, s = i.sequenceId,
    x = i.pos.x, y = i.pos.y, dx = i.dir.x, dy = i.dir.y, n1 = i.road and i.road.n1 or '', n2 = i.road and i.road.n2 or '',
    invalid = i._invalid and true or false, active = i.active and true or false, state = st}
end
for _, c in ipairs(d.controllers) do
  local names = {}
  for k, s in ipairs(c.states) do names[k] = s.state end
  out.controllers[#out.controllers + 1] = {id = c.id, name = c.name, type = c.type, dur = c.totalDuration, default = c.defaultIndex, states = names}
end
for _, s in ipairs(d.sequences) do
  local ph = {}
  for k, p in ipairs(s.phases) do ph[k] = {ids = p.controllerIds, dur = p.totalDuration} end
  out.sequences[#out.sequences + 1] = {id = s.id, name = s.name, dur = s.totalDuration, phases = ph}
end
local n = 0
for _ in pairs(core_trafficSignals.getMapNodeSignals()) do n = n + 1 end
out.map_nodes_with_signals = n
return jsonEncode(out)
"""

# One game-side job for the whole run: every change of every head (t, id, code, signal timer) and, while
# `rec` is set, the player car's position and velocity.
LUA_LOGGER = """
_G.belfastRules = {samples = {}, trans = {}, on = true, rec = false}
local br = _G.belfastRules
local codes = {redTrafficLight = 0, redYellowTrafficLight = 1, greenTrafficLight = 2, yellowTrafficLight = 3}
local hp = hptimer()
core_jobsystem.create(function(job)
  local last = {}
  while br.on do
    local t = hp:stop() / 1000
    local d = core_trafficSignals.getData()
    for _, i in ipairs(d.instances) do
      if not i._invalid then
        local c = codes[i:getState()] or 9
        if last[i.id] ~= c then
          last[i.id] = c
          br.trans[#br.trans + 1] = string.format('%.3f,%d,%d,%.2f', t, i.id, c, d.timer)
        end
      end
    end
    if br.rec then
      local v = be:getPlayerVehicle(0)
      if v then
        local p = v:getPosition()
        local vel = v:getVelocity()
        br.samples[#br.samples + 1] = string.format('%.3f,%.2f,%.2f,%.2f,%.2f', t, p.x, p.y, vel.x, vel.y)
      end
    end
    job.sleep(0.03)
  end
end, 0.01)
return 'ok'
"""
LUA_DRAIN_SAMPLES = "local b = _G.belfastRules if not b then return '' end local s = table.concat(b.samples, ';') b.samples = {} return s"
LUA_DRAIN_TRANS = "local b = _G.belfastRules if not b then return '' end local s = table.concat(b.trans, ';') b.trans = {} return s"


def log(msg: str) -> None:
    ds.log(msg)


def parse_samples(raw: str) -> list[dict]:
    out = []
    for row in raw.split(";"):
        f = row.split(",")
        if len(f) != 5:
            continue
        try:
            t, x, y, vx, vy = (float(v) for v in f)
        except ValueError:
            continue
        out.append({"t": t, "x": x, "y": y, "vx": vx, "vy": vy})
    return out


def head_state(link: ds.Link, name: str, ahead_s: float) -> tuple[str, str]:
    raw = link.lua(f"local i = core_trafficSignals.getSignalByName('{name}') "
                   f"if not i then return 'none,none' end return i:getState() .. ',' .. i:getStateAfterTime({ahead_s})")
    a, _, b = raw.partition(",")
    return a, b


def wait_for_red(link: ds.Link, name: str, max_s: float) -> dict:
    """Until the head shows red now and still red in RED_AHEAD_S (the car will meet it red)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_s:
        now, later = head_state(link, name, RED_AHEAD_S)
        if now in ("redTrafficLight", "redYellowTrafficLight") and later == "redTrafficLight":
            return {"waited_s": round(time.monotonic() - t0, 1), "ok": True}
        time.sleep(0.4)
    return {"waited_s": round(time.monotonic() - t0, 1), "ok": False}


def request_map(link: ds.Link) -> None:
    link.lua("be:getPlayerVehicle(0):queueLuaCommand('mapmgr.requestMap()') return 'ok'")
    for _ in range(20):
        time.sleep(0.5)
        if link.vlua("return tostring(mapmgr.mapData ~= nil and mapmgr.mapData.edgeList ~= nil)") == "true":
            return
    raise RuntimeError("vehicle mapmgr has no level graph (edgeList nil)")


def run_approach(link: ds.Link, g: rl.Graph, inst: dict, outdir: Path) -> dict:
    """One car, one signal. Returns the record (the samples go to approach_<id>.csv)."""
    rec = {"instance": inst["id"], "name": inst["name"], "controller": inst["c"], "sequence": inst["s"],
           "pos": [round(inst["x"], 1), round(inst["y"], 1)], "dir": [round(inst["dx"], 3), round(inst["dy"], 3)],
           "edge": [inst["n1"], inst["n2"]]}
    start = lib.approach_start(g.adj, g.nodes, inst["n1"], inst["n2"], APPROACH_BACK_M)
    if start is None:
        rec["error"] = "less than 12 m of road the AI cannot leave before the signal's edge"
        return rec
    chain, p0, p1 = start
    rec["start"] = [round(p0[0], 1), round(p0[1], 1)]
    link.lua(ds.LUA_CARROT)
    ds.spawn_fresh(link)
    z0 = g.nodes[chain[0]][2]
    spawn = ds.place(link, (p0[0], p0[1], z0), (p1[0], p1[1], g.nodes[chain[1]][2]))
    rec["spawn"] = spawn
    if spawn["problems"]:
        rec["error"] = "bad spawn: " + "; ".join(spawn["problems"])
        return rec
    request_map(link)
    rec["vehicle_action_at_edge"] = link.vlua(
        f"local sd = mapmgr.signalsData local a = sd and sd['{inst['n1']}'] and sd['{inst['n1']}']['{inst['n2']}'] "
        f"return tostring(a and a[1] and a[1].action)")
    rec["red_wait"] = wait_for_red(link, inst["name"], RED_WAIT_MAX_S)
    link.lua("local b = _G.belfastRules b.samples = {} b.rec = true return 'ok'")
    link.lua("be:getPlayerVehicle(0):queueLuaCommand('ai.setMode(\"traffic\")') return 'ok'")
    rows: list[dict] = []
    ux, uy = inst["dx"], inst["dy"]
    n = math.hypot(ux, uy) or 1.0
    ux, uy = ux / n, uy / n
    t_run = time.monotonic()
    ended = "timeout"
    try:
        while time.monotonic() - t_run < APPROACH_MAX_S:
            time.sleep(0.5)
            rows += parse_samples(link.lua(LUA_DRAIN_SAMPLES))
            if rows:
                s = (rows[-1]["x"] - inst["x"]) * ux + (rows[-1]["y"] - inst["y"]) * uy
                lat = abs(-(rows[-1]["x"] - inst["x"]) * uy + (rows[-1]["y"] - inst["y"]) * ux)
                if s > PAST_SIGNAL_M and lat < 25:
                    ended = "passed"
                    break
    finally:
        with contextlib.suppress(Exception):
            link.lua("local b = _G.belfastRules b.rec = false "
                     "local v = be:getPlayerVehicle(0) if v then v:queueLuaCommand('ai.setMode(\"disabled\") input.event(\"brake\", 1, 1)') end return 'ok'", timeout=10)
            rows += parse_samples(link.lua(LUA_DRAIN_SAMPLES, timeout=10))
    rec["ended"] = ended
    rec["duration_s"] = round(time.monotonic() - t_run, 1)
    rec["frames"] = len(rows)
    with (outdir / f"approach_{inst['id']}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "x", "y", "vx", "vy"])
        for r in rows:
            w.writerow([r["t"], r["x"], r["y"], r["vx"], r["vy"]])
    return rec


def pick_instances(inv: dict, only: set[int] | None, limit: int | None) -> list[dict]:
    """One head per controller (the first with a road), so every controller's cycle is met by a car."""
    by_c: dict[int, dict] = {}
    for i in inv["instances"]:
        if i["invalid"] or not i["n1"] or not i["n2"]:
            continue
        if only and i["c"] not in only:
            continue
        by_c.setdefault(i["c"], i)
    out = [by_c[c] for c in sorted(by_c)]
    return out[:limit] if limit else out


def run(a) -> int:
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    outdir = Path(a.out) if a.out else OUT_ROOT / stamp
    gamectl.check_output_path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    doc = ds.load_routes(Path(a.routes), None)
    m: dict = {"tool": "qa/ai_rules.py", "task": "T-407", "started": dt.datetime.now().isoformat(timespec="seconds"),
               "level": a.level, "zip": a.zip_note, "status": "running"}
    t0 = time.monotonic()
    deadline = t0 + a.timeout
    started = False
    rc = 0
    approaches: list[dict] = []
    try:
        info = gamectl.cmd_start(allow_daytime=a.allow_daytime)
        started = True
        gamectl.cmd_load_level(a.level, timeout=900)
        state, client = gamectl.require_our_game()
        link = ds.Link(client, deadline)
        dump = ds.dump_graph(link)
        (outdir / "graph.json").write_text(json.dumps(dump), encoding="utf-8")
        g = rl.Graph(dump)
        # 0. inventory, vehicle rules, logger
        inv = json.loads(link.lua(LUA_INVENTORY, timeout=60))
        (outdir / "inventory.json").write_text(json.dumps(inv, indent=1), encoding="utf-8")
        log(f"signals (extension preloaded: {inv['preloaded']}): {len(inv['instances'])} heads, {len(inv['controllers'])} controllers, {len(inv['sequences'])} sequences, "
            f"active {inv['active']}, graph nodes with signals {inv['map_nodes_with_signals']}")
        link.lua(LUA_LOGGER)
        ds.spawn_fresh(link)
        request_map(link)
        m["vehicle"] = {"rightHandDrive": link.vlua("return tostring(mapmgr.rules and mapmgr.rules.rightHandDrive)"),
                        "rules": link.vlua("return jsonEncode(mapmgr.rules or {})"),
                        "signal_nodes": link.vlua("return tostring(tableSize(mapmgr.signalsData or {}))")}
        m["level_rules"] = link.lua("return jsonEncode(core_levels and core_levels.getLevelData and "
                                    "(core_levels.getLevelData(getCurrentLevelIdentifier()) or {}).roadRules or {})")
        log(f"vehicle rules {m['vehicle']}")
        # 1. roundabout circuits
        osm = ds.OsmContext(Path(a.osm), doc["origin"]["lat"], doc["origin"]["lon"])
        m["circuits"] = []
        for route in doc["routes"]:
            log(f"circuit {route['id']} ({route['length_m']} m)")
            res = ds.drive_route(link, g, route, "graph", outdir, ds.SPEED_MS, ds.AGGRESSION, osm)
            m["circuits"].append({k: res.get(k) for k in ("id", "completed", "end_reason", "length_m", "progress_m", "events", "lane")})
            log(f"  {'DONE' if res['completed'] else 'FAIL'} {route['id']}: {res['end_reason']}")
        # 2. signals under AI traffic mode
        only = {int(v) for v in a.only_controllers.split(",") if v} if a.only_controllers else None
        picks = pick_instances(inv, only, a.max_approaches)
        log(f"{len(picks)} signal approaches")
        for inst in picks:
            link.check()
            log(f"approach: head {inst['id']} (controller {inst['c']}, sequence {inst['s']}) at ({inst['x']:.0f}, {inst['y']:.0f})")
            try:
                rec = run_approach(link, g, inst, outdir)
            except (RuntimeError, ds.McpError, ds.beamng_mcp.ToolError) as e:
                rec = {"instance": inst["id"], "controller": inst["c"], "error": str(e)[:200]}
                log(f"  error: {e}")
            approaches.append(rec)
            (outdir / "approaches.json").write_text(json.dumps(approaches, indent=1), encoding="utf-8")
        trans = link.lua(LUA_DRAIN_TRANS, timeout=60)
        (outdir / "transitions.txt").write_text(trans, encoding="utf-8")
        link.lua("if _G.belfastRules then _G.belfastRules.on = false end return 'ok'")
        m["status"] = "ok"
    except ds.SuiteTimeout:
        m["status"] = "timeout"
        rc = ds.EXIT_TIMEOUT
    except gamectl.GamectlError as e:
        m["status"] = "tool failure"
        m["error"] = e.message
        rc = e.code
        log(f"gamectl: {e.message}")
    except Exception as e:  # noqa: BLE001
        m["status"] = "tool failure"
        m["error"] = f"{type(e).__name__}: {e}"
        rc = 1
        log(f"error: {m['error']}")
    finally:
        stopped = {}
        if started or gamectl.read_state() is not None:
            try:
                stopped = gamectl.cmd_stop()
            except Exception as e:  # noqa: BLE001
                stopped = {"error": str(e)}
                rc = rc or gamectl.EXIT_STOP_FAILED
        end = gamectl.end_state()
        m["game_stopped"] = {"stop": stopped, "end_state": end, "clean": gamectl.end_state_clean(end)}
        if started:
            con = gamectl.log_errors(ignore=ds.KNOWN_CONSOLE)
            m["console"] = {"log": con["log"], "errors": con["errors"][:50], "warnings": len(con["warnings"])}
        m["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        m["wall_s"] = round(time.monotonic() - t0, 1)
        (outdir / "run.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    if (outdir / "inventory.json").is_file():
        res = analyse_dir(outdir, [])
        print(json.dumps(res["summary"], indent=1))
    print(f"out: {outdir}")
    return rc


# ------------------------------------------------------------------ judging (no game)

def read_telemetry(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [{k: float(v) for k, v in r.items() if v not in ("", None)} for r in csv.DictReader(fh)]


def analyse_dir(d: Path, drives: list[Path]) -> dict:
    inv = json.loads((d / "inventory.json").read_text(encoding="utf-8"))
    trans_path = d / "transitions.txt"
    trans = lib.parse_transitions(trans_path.read_text(encoding="utf-8")) if trans_path.is_file() else {}
    out: dict = {"folder": str(d)}
    heads = {i["id"]: i for i in inv["instances"] if not i["invalid"]}
    # light sequence
    order = {iid: lib.check_order(tl) for iid, tl in trans.items() if iid in heads}
    controllers: dict[int, list] = {}
    for iid, h in heads.items():
        controllers.setdefault(h["c"], iid)
    seq_conf = {}
    for s in inv["sequences"]:
        cids = [cid for ph in s["phases"] for cid in ph["ids"]]
        by_c = {cid: trans[controllers[cid]] for cid in cids if cid in controllers and controllers[cid] in trans}
        seq_conf[s["id"]] = lib.sequence_conflicts(by_c)
    durations: dict[str, dict] = {}
    for iid, o in order.items():
        for name, v in o["durations_s"].items():
            cur = durations.setdefault(name, {"min": v["min"], "max": v["max"]})
            cur["min"], cur["max"] = min(cur["min"], v["min"]), max(cur["max"], v["max"])
    out["signals"] = {
        "heads": len(heads), "heads_recorded": len(order),
        "heads_never_change": sorted(i for i, o in order.items() if o["never_changes"]),
        "heads_bad_order": {i: o["bad_transitions"][:3] for i, o in order.items() if o["bad_transitions"]},
        "heads_with_cycles": sum(1 for o in order.values() if o["cycles"] >= 1),
        "durations_s": durations,
        "sequence_conflicts": {k: v[:3] for k, v in seq_conf.items() if v},
        "recording_s": round(max((tl[-1][0] for tl in trans.values()), default=0) - min((tl[0][0] for tl in trans.values()), default=0), 1),
    }
    # roundabout circuits
    circuits = []
    for f in sorted(d.glob("telemetry_roundabout-*.csv")):
        sw = lib.swept_angle_deg(read_telemetry(f), ROUNDABOUT_CENTRES["newtownbreda"], ROUNDABOUT_R_M)
        circuits.append({"route": f.stem.replace("telemetry_", ""), **sw})
    out["roundabout"] = circuits
    # one-way
    gp = d / "graph.json"
    ow: dict = {}
    if gp.is_file():
        idx = lib.OneWayIndex(json.loads(gp.read_text(encoding="utf-8")))
        out["one_way_edges"] = idx.one_way_count
        for folder in [d, *drives]:
            for f in sorted(folder.glob("telemetry_*.csv")):
                ow[f"{folder.name}/{f.stem.replace('telemetry_', '')}"] = lib.wrong_way(read_telemetry(f), idx)
    out["one_way"] = ow
    # lane side of the AI cars in traffic mode (their own choice of lane, no lane-keeping flag set by us)
    side = {"left": 0, "right": 0, "centre": 0}
    if gp.is_file():
        for f in sorted(d.glob("approach_*.csv")):
            r = lib.lane_side(read_telemetry(f), idx)
            for k in side:
                side[k] += r[k]
    out["traffic_lane_side"] = side
    # AI at signals
    appr_path = d / "approaches.json"
    results = []
    if appr_path.is_file():
        for rec in json.loads(appr_path.read_text(encoding="utf-8")):
            r = {k: rec.get(k) for k in ("instance", "controller", "sequence", "pos", "ended", "error", "vehicle_action_at_edge", "red_wait")}
            f = d / f"approach_{rec.get('instance')}.csv"
            iid = rec.get("instance")
            if f.is_file() and iid in trans:
                r.update(lib.judge_approach(read_telemetry(f), {"pos": rec["pos"], "dir": rec["dir"]}, trans[iid]))
            results.append(r)
    out["approaches"] = results
    out["summary"] = {
        "heads_never_change": out["signals"]["heads_never_change"],
        "heads_bad_order": len(out["signals"]["heads_bad_order"]),
        "sequence_conflicts": len(out["signals"]["sequence_conflicts"]),
        "roundabout": [(c["route"], c["direction"], c["sweep_deg"]) for c in circuits],
        "traffic_lane_side": side,
        "wrong_way_samples": sum(v["wrong_way"] for v in ow.values()),
        "one_way_samples": sum(v["on_one_way"] for v in ow.values()),
        "approaches": len(results),
        "crossed": sum(1 for r in results if r.get("crossed")),
        "stopped_at_red": sum(1 for r in results if r.get("stopped_at_red")),
        "ran_red": [r["instance"] for r in results if r.get("ran_red")],
        "errors": [(r["instance"], r["error"]) for r in results if r.get("error")],
    }
    (d / "metrics.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def cmd_analyse(a) -> int:
    res = analyse_dir(Path(a.dir), [Path(p) for p in a.drive or []])
    print(json.dumps(res["summary"], indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start the game, run the checks, stop the game")
    r.add_argument("--routes", default=str(RULES_ROUTES))
    r.add_argument("--level", default=ds.LEVEL)
    r.add_argument("--osm", default=str(ds.OSM_JSON))
    r.add_argument("--out")
    r.add_argument("--only-controllers", help="comma separated controller ids (default: every controller)")
    r.add_argument("--max-approaches", type=int)
    r.add_argument("--timeout", type=float, default=2400.0)
    r.add_argument("--zip-note", dest="zip_note", default="", help="free text: which build is installed (goes into run.json)")
    r.add_argument("--allow-daytime", action="store_true", help="allow a launch between 09:00 and 18:00 (only when the user says so)")
    an = sub.add_parser("analyse", help="judge a recorded run folder (no game)")
    an.add_argument("dir")
    an.add_argument("--drive", action="append", help="a drive-suite folder whose telemetry is also checked for wrong-way driving")
    a = ap.parse_args(argv)
    try:
        return {"run": run, "analyse": cmd_analyse}[a.cmd](a)
    except gamectl.GamectlError as e:
        print(f"error: {e.message}", file=sys.stderr)
        return e.code
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
