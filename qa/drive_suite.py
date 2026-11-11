#!/usr/bin/env python3
"""Route drive suite: named routes around the test centre, per-frame telemetry, G3 metrics (T-302).

    uv run --no-project --python 3.12 --with requests python qa/drive_suite.py run [--gate] [--only ID,ID]
                                                         [--mode graph|script] [--timeout S] [--out DIR]
    uv run --no-project --python 3.12 --with requests python qa/drive_suite.py dump-graph   # game must run

`run` is unattended: it starts the QA game through tools/gamectl.py (which refuses if any BeamNG
process exists or the local time is 09:00-18:00), loads the level, dumps the live navgraph, checks
every route against it, spawns a FRESH Ibishu Covet 15le_M for each route (docs/spikes/ai-graph.md,
I-001: a car that ran a scripted path keeps a dummy road graph), drives the route while a game-side
logger records every frame, detects events with MapNG's default thresholds (qa/drive_lib.py),
writes telemetry_<route>.csv and metrics.json under H:\\BelfastData\\qa\\drive\\<stamp>\\, and
always stops the game. Route failures are reported in metrics.json and do NOT change the exit
code; with --gate a failed G3 exits 20. Tool failures exit non-zero (gamectl codes, 1 generic,
21 hard timeout). Only 127.0.0.1 is contacted (the game's MCP); the remote-MCP flag is never used.

Drive modes (docs/drive_suite.md): `graph` (default) = ai.driveUsingPath{wpTargetList, driveInLane
= 'on'}: the AI follows the level's road graph in its lane, so it also shows which side of the road
it keeps; `script` = ai.driveUsingPath{script} along the route polyline like MapNG's driveTest.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(HERE))
import beamng_mcp  # noqa: E402
import drive_lib as dl  # noqa: E402
import gamectl  # noqa: E402
import route_lib as rl  # noqa: E402
from beamng_mcp import McpError  # noqa: E402

DEFAULT_ROUTES = HERE / "routes" / "belfast_slice.json"
OUT_ROOT = Path(r"H:\BelfastData\qa\drive")
GRAPH_DIR = Path(r"H:\BelfastData\qa\graph")
LEVEL = "belfast_slice"
VEHICLE = ("covet", "15le_M")
SPEED_MS = 13.4          # 30 mph cap for the AI (routeSpeed, 'limit'); MapNG uses 10-12 m/s on turns
AGGRESSION = 0.6         # ingame/driveTest.mjs:444
PATH_EDGE_M = 1.3        # ingame/driveTest.mjs:40: script corridor this far inside the road edges
END_EASE_M, END_SPEED_MS = 60.0, 4.0   # driveTest.mjs:42-43
TURN_SPEED_MS, TURN_SLOW_M = 7.0, 15.0  # lib/driveAnalysis.mjs:26-27
POLL_S = 0.5             # driveTest.mjs:454
MAX_ROUTE_S = 400.0      # the game-side logger stops itself after this (MapNG: 180 s, driveTest.mjs:52)
OVERALL_TIMEOUT_S = 3600.0
EXIT_GATE = 20
EXIT_TIMEOUT = 21
KNOWN_CONSOLE = [r"Could not initialize online service provider", r"No online platform for achievements",
                 r"swap chain", r"licensePlate-default"]   # game baseline (docs/spikes/baseline.md)

LUA_CARROT = "_G.carRot = function(dir) return quat(0, 0, 1, 0) * quatFromDir(dir, vec3(0, 0, 1)) end return 'ok'"
LUA_DRAIN = ("local b = _G.belfastDrive if not b then return '' end "
             "local s = table.concat(b.samples, ';') b.samples = {} return s")


class SuiteTimeout(RuntimeError):
    pass


class GameLost(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


class Link:
    """MCP access with a deadline. Only ever talks to 127.0.0.1."""

    def __init__(self, client: beamng_mcp.McpClient, deadline: float):
        assert client.url.startswith("http://127.0.0.1:"), client.url
        self.c = client
        self.deadline = deadline

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            raise SuiteTimeout("overall timeout reached")

    def lua(self, code: str, timeout: float = 30.0) -> str:
        self.check()
        last = None
        for _ in range(3):
            try:
                return self.c.run_lua(code, timeout=timeout)
            except beamng_mcp.ToolError:
                raise
            except McpError as e:
                last = e
                time.sleep(1.0)
        raise GameLost(f"MCP not answering: {last}")

    def vlua(self, code: str, tries: int = 12) -> str:
        """Vehicle Lua is async: the first call queues, a later call returns the result (JSON map id -> value)."""
        self.check()
        self.c.call_text("run_lua_vehicle", {"code": code}, timeout=30)
        for _ in range(tries):
            time.sleep(0.3)
            text = self.c.call_text("run_lua_vehicle", {"code": code}, timeout=30)
            if text and not text.startswith("queued"):
                try:
                    vals = list(json.loads(text).values())
                    return str(vals[0]) if vals else ""
                except ValueError:
                    return text
        return ""

    def damage(self) -> float | None:
        try:
            text = self.c.call_text("get_vehicle_damage", {}, timeout=15)
        except McpError:
            return None
        m = re.search(r'"damageSum"\s*:\s*([\d.eE+-]+)', text)
        return float(m.group(1)) if m else None


# ------------------------------------------------------------------------------- graph

def dump_graph(link: Link) -> dict:
    code = """local mp = map.getMap()
local nodes, links = {}, {}
for name, n in pairs(mp.nodes) do
  nodes[#nodes+1] = {name, math.floor(n.pos.x*100+0.5)/100, math.floor(n.pos.y*100+0.5)/100, math.floor(n.pos.z*100+0.5)/100, math.floor(n.radius*100+0.5)/100}
  for o, e in pairs(n.links) do
    links[#links+1] = {name, o, e.oneWay and 1 or 0, e.inNode == name and 1 or 0, math.floor((e.drivability or 0)*100+0.5)/100, math.floor((e.speedLimit or 0)*10+0.5)/10, e.lanes or ''}
  end
end
return jsonEncode({nodes = nodes, links = links})"""
    d = json.loads(link.lua(code, timeout=90))
    if not d.get("nodes"):
        raise RuntimeError("the game has no navgraph (map.getMap().nodes is empty)")
    return d


# ------------------------------------------------------------------------------- driving

def wait_vehicle(link: Link, timeout: float = 30.0) -> int:
    """After `spawn`: the new Covet is the player vehicle and its vehicle Lua answers."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        st = link.c.get_status(timeout=10)
        v = st.get("vehicle") or {}
        if v.get("jbeam") == VEHICLE[0] and v.get("configKey") == VEHICLE[1]:
            if link.vlua("return 'up'", tries=6) == "up":
                return int(v["id"])
        time.sleep(0.5)
    raise RuntimeError("the spawned Covet did not become ready")


def spawn_fresh(link: Link) -> int:
    link.c.call_text("spawn", {"model": VEHICLE[0], "config": VEHICLE[1]}, timeout=60)
    time.sleep(1.0)
    return wait_vehicle(link)


def place(link: Link, p0, p1) -> dict:
    """Put the car on the road at p0 facing p1, physics paused first (MapNG driveTest.mjs:350-376)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    placed = link.lua(f"""
      be:setPhysicsRunning(false)
      local veh = be:getPlayerVehicle(0)
      local bb = veh:getSpawnWorldOOBB()
      local bottom = bb:getCenter() - veh:getDirectionVectorUp() * bb:getHalfExtents().z
      local refH = veh:getPosition().z - bottom.z
      if refH < 0 or refH > 3 then refH = 0.5 end
      local ground, best, top = {p0[2]}, math.huge, {p0[2] + 3}
      for _ = 1, 6 do
        local hit = castRay(vec3({p0[0]}, {p0[1]}, top), vec3({p0[0]}, {p0[1]}, {p0[2] - 10}), true, true)
        if not hit then break end
        if math.abs(hit.pt.z - {p0[2]}) < best then best = math.abs(hit.pt.z - {p0[2]}) ground = hit.pt.z end
        top = hit.pt.z - 0.05
      end
      local q = carRot(vec3({dx / n:.5f}, {dy / n:.5f}, 0))
      veh:setPosRot({p0[0]}, {p0[1]}, ground + refH + 0.5, q.x, q.y, q.z, q.w)
      veh:resetBrokenFlexMesh()
      return string.format('%.3f,%.3f', ground, refH)""")
    ground, ref_h = (float(v) for v in placed.split(","))
    link.lua("""be:setPhysicsRunning(true)
      be:getPlayerVehicle(0):queueLuaCommand('ai.setMode("disabled") input.event("throttle", 0, 1) input.event("brake", 0, 1) input.event("parkingbrake", 0, 1) obj:requestReset(RESET_PHYSICS)')
      return 'ok'""")
    time.sleep(0.5 + 1.5)    # MapNG SPAWN_GRACE_S 0.5 + settle 1.5 (driveTest.mjs:49, 379)
    st = link.lua("""local v = be:getPlayerVehicle(0) local d = v:getDirectionVector() local u = v:getDirectionVectorUp()
      local r = d:cross(u):normalized() local pos = v:getPosition() local vel = v:getVelocity()
      return string.format('%.2f,%.2f,%.2f,%.2f', math.deg(math.asin(math.max(-1, math.min(1, r.z)))), math.deg(math.asin(math.max(-1, math.min(1, d:normalized().z)))), pos.z, vel:length())""")
    roll, pitch, z, speed = (float(v) for v in st.split(","))
    problems = []
    if abs(roll) > 8:         # driveTest.mjs:50 SPAWN_ROLL_DEG
        problems.append(f"tilted {roll:.0f} deg sideways")
    if z - ref_h < ground - 0.5:
        problems.append(f"sank {ground - (z - ref_h):.1f} m into the surface")
    if z - ref_h > ground + 1:
        problems.append(f"{z - ref_h - ground:.1f} m over the surface")
    if speed > 1.5 and speed < dl.MAX_SANE_MS:
        problems.append(f"moving {speed:.1f} m/s after settling")
    return {"x": round(p0[0], 1), "y": round(p0[1], 1), "roll": round(roll, 1), "pitch": round(pitch, 1), "problems": problems}


def lua_start_logger(max_s: float) -> str:
    return f"""_G.belfastDrive = {{ samples = {{}}, on = true }}
    extensions.load('core_vehicleBridge')
    local veh = be:getPlayerVehicle(0)
    for _, k in ipairs({{'steering_input', 'throttle_input', 'brake_input'}}) do core_vehicleBridge.registerValueChangeNotification(veh, k) end
    local hp = hptimer()
    core_jobsystem.create(function(job)
      while _G.belfastDrive.on and hp:stop() < {int(max_s * 1000)} do
        local v = be:getPlayerVehicle(0)
        if v then
          local pos = v:getPosition(); local vel = v:getVelocity(); local d = v:getDirectionVector()
          local id = v:getId()
          local st = core_vehicleBridge.getCachedVehicleData(id, 'steering_input') or 0
          local th = core_vehicleBridge.getCachedVehicleData(id, 'throttle_input') or 0
          local br = core_vehicleBridge.getCachedVehicleData(id, 'brake_input') or 0
          local s = _G.belfastDrive.samples
          s[#s + 1] = string.format('%.3f,%.2f,%.2f,%.2f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f', hp:stop() / 1000, pos.x, pos.y, pos.z, vel.x, vel.y, vel.z, d.x, d.y, st, th, br)
        end
        job.sleep(0.02)
      end
    end, 0.01)
    return 'ok'"""


def parse_samples(raw: str) -> list[dict]:
    out = []
    for row in raw.split(";"):
        f = row.split(",")
        if len(f) != 12:
            continue
        try:
            t, x, y, z, vx, vy, vz, dx, dy, st, th, br = (float(v) for v in f)
        except ValueError:
            continue
        n = (dx * dx + dy * dy) ** 0.5 or 1.0
        out.append({"t": t, "x": x, "y": y, "z": z, "vx": vx, "vy": vy, "vz": vz, "dx": dx / n, "dy": dy / n,
                    "steer": st, "throttle": th, "brake": br})
    return out


def waypoints(g: rl.Graph, via: list[dict], limit: int = 80) -> list[str]:
    """Graph node names for wpTargetList: the via points plus every node of 3+ neighbours the route
    passes, so the AI cannot take another way between them."""
    path = rl.resolve_via(g, via)
    und = g.undirected()
    keep = [path[0]] + [n for n in path[1:-1] if len(und[n]) >= 3] + [path[-1]]
    out = [keep[0]]
    for n in keep[1:]:
        if n != out[-1]:
            out.append(n)
    if len(out) > limit:
        step = len(out) / limit
        out = [out[int(i * step)] for i in range(limit)] + [out[-1]]
    return out


def script_points(route: dict, speed: float) -> str:
    pts = route["path"]
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + rl.dist2(a, b))
    total = cum[-1]
    turn_at = [j["at_m"] for j in route["junctions"] if j["turn"] != "straight"]
    out = []
    for i, q in enumerate(pts):
        v = TURN_SPEED_MS if any(abs(cum[i] - t) < TURN_SLOW_M for t in turn_at) else speed
        to_end = total - cum[i]
        if to_end < END_EASE_M:
            v = min(v, END_SPEED_MS + (v - END_SPEED_MS) * (to_end / END_EASE_M))
        if i == 0:
            v = min(v, speed * 0.6)
        r = max(1.0, route.get("half_width_m", 3.0) - PATH_EDGE_M)
        out.append(f"{{x={q[0]:.2f},y={q[1]:.2f},z={q[2]:.2f},r={r:.1f},v={v:.1f}}}")
    return ",".join(out)


def start_ai(link: Link, g: rl.Graph, route: dict, mode: str, speed: float, aggression: float) -> dict:
    if mode == "graph":
        # I-001: a graph-based AI mode needs the level graph in the vehicle's mapmgr
        link.lua("be:getPlayerVehicle(0):queueLuaCommand('mapmgr.requestMap()') return 'ok'")
        ok = ""
        for _ in range(20):
            time.sleep(0.5)
            ok = link.vlua("return tostring(mapmgr.mapData ~= nil and mapmgr.mapData.edgeList ~= nil)")
            if ok == "true":
                break
        if ok != "true":
            raise RuntimeError("vehicle mapmgr has no level graph (edgeList nil): refusing a graph-based AI mode")
        wps = waypoints(g, route["via"])
        names = ",".join("'" + n + "'" for n in wps)
        call = (f"ai.driveUsingPath{{wpTargetList = {{{names}}}, driveInLane = 'on', avoidCars = 'off', "
                f"routeSpeed = {speed}, routeSpeedMode = 'limit', aggression = {aggression}}}")
        info = {"waypoints": len(wps)}
    else:
        call = (f"ai.driveUsingPath{{script = {{{script_points(route, speed)}}}, avoidCars = 'off', "
                f"driveInLane = 'off', aggression = {aggression}}}")
        info = {"script_points": len(route["path"])}
    link.lua(f"be:getPlayerVehicle(0):queueLuaCommand([==[{call}]==]) return 'ok'")
    return info


def write_rows(w, samples: list[dict]) -> None:
    for s in samples:
        w.writerow(dl.telemetry_row(s))


def drive_route(link: Link, g: rl.Graph, route: dict, mode: str, outdir: Path, speed: float,
                aggression: float) -> dict:
    rid = route["id"]
    started = dt.datetime.now().isoformat(timespec="seconds")
    t_start = time.monotonic()
    meta = {"id": rid, "name": route["name"]}
    r = dl.Route(rid, route["path"], half=float(route.get("half_width_m", 3.0)))
    mon = dl.Monitor(r)
    tele = outdir / f"telemetry_{rid}.csv"
    fh = tele.open("w", newline="", encoding="utf-8")
    w = csv.writer(fh)
    w.writerow(dl.TELEMETRY_COLUMNS)
    extra: dict = {}
    end_reason = "error"
    try:
        link.lua(LUA_CARROT)
        spawn = {}
        for attempt in range(2):
            spawn_fresh(link)
            k = 1
            while k < len(r.points) - 1 and rl.dist2(r.points[k], r.points[0]) < 5:
                k += 1
            spawn = place(link, r.points[0], r.points[k])
            if not spawn["problems"]:
                break
            log(f"  {rid}: bad spawn ({'; '.join(spawn['problems'])}), try {attempt + 2}")
        extra["spawn"] = spawn
        if spawn["problems"]:
            mon.events.append({"type": "no spawn", "t": 0.0, "x": spawn["x"], "y": spawn["y"],
                               "value": "; ".join(spawn["problems"])})
            end_reason = "no spawn"
        else:
            link.lua(lua_start_logger(MAX_ROUTE_S))
            dmg_hold = link.damage()
            extra.update(start_ai(link, g, route, mode, speed, aggression))
            timeout_s = dl.route_timeout_s(r.length, speed)
            t_run = time.monotonic()
            end_reason = "timeout"
            while time.monotonic() - t_run < timeout_s:
                time.sleep(POLL_S)
                batch = parse_samples(link.lua(LUA_DRAIN))
                d = link.damage()
                dmg_hold = d if d is not None else dmg_hold
                for s in batch:
                    s["dmg"] = dmg_hold
                    mon.feed(s)
                write_rows(w, batch)
                fh.flush()
                if mon.stop_reason:
                    end_reason = mon.stop_reason
                    break
                if mon.samples and mon.samples[-1]["t"] > 2.0 and mon.arrived():
                    end_reason = "arrived"
                    break
    except SuiteTimeout:
        end_reason = "suite timeout"
        mon.events.append({"type": "error", "t": round(time.monotonic() - t_start, 1), "value": "suite timeout"})
        raise
    except (RuntimeError, McpError, beamng_mcp.ToolError) as e:
        end_reason = "error"
        mon.events.append({"type": "error", "t": round(time.monotonic() - t_start, 1), "value": str(e)[:200]})
        log(f"  {rid}: error: {e}")
    finally:
        with contextlib.suppress(Exception):   # leave the car and logger stopped whatever happened
            link.lua("if _G.belfastDrive then _G.belfastDrive.on = false end "
                     "local v = be:getPlayerVehicle(0) if v then v:queueLuaCommand('ai.setMode(\"disabled\") input.event(\"brake\", 1, 1)') end return 'ok'", timeout=10)
            tail = parse_samples(link.lua(LUA_DRAIN, timeout=10))
            for s in tail:
                s["dmg"] = mon.samples[-1].get("dmg") if mon.samples else None
                mon.feed(s)
            write_rows(w, tail)
        fh.close()
    return dl.route_result(meta, mon, mode=mode, end_reason=end_reason, started_at=started,
                           duration_s=time.monotonic() - t_start if not mon.samples else
                           mon.samples[-1]["t"] - mon.samples[0]["t"],
                           telemetry_file=tele.name, extra=extra)


# ------------------------------------------------------------------------------- suite

def load_routes(path: Path, only: list[str] | None) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    errs = rl.validate_routes(doc, None)
    if errs:
        raise ValueError("routes file invalid: " + "; ".join(errs))
    if only:
        known = {r["id"] for r in doc["routes"]}
        missing = [i for i in only if i not in known]
        if missing:
            raise ValueError(f"unknown route id(s): {', '.join(missing)}")
        doc["routes"] = [r for r in doc["routes"] if r["id"] in only]
    return doc


def write_metrics(outdir: Path, m: dict) -> None:
    tmp = outdir / "metrics.json.tmp"
    tmp.write_text(json.dumps(m, indent=1), encoding="utf-8")
    os.replace(tmp, outdir / "metrics.json")


def run_suite(a) -> int:
    routes_path = Path(a.routes)
    only = [s for s in a.only.split(",") if s] if a.only else None
    doc = load_routes(routes_path, only)
    expected = [r["id"] for r in doc["routes"]]
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    outdir = Path(a.out) if a.out else OUT_ROOT / stamp
    gamectl.check_output_path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "routes.json").write_text(json.dumps(doc), encoding="utf-8")
    t0 = time.monotonic()
    deadline = t0 + a.timeout
    m: dict = {
        "tool": "qa/drive_suite.py", "task": "T-302", "level": a.level, "mode": a.mode,
        "vehicle": f"{VEHICLE[0]} {VEHICLE[1]}", "started": dt.datetime.now().isoformat(timespec="seconds"),
        "routes_file": str(routes_path), "routes_sha256": hashlib.sha256(routes_path.read_bytes()).hexdigest()[:16],
        "provisional_start": doc.get("provisional"), "speed_cap_ms": a.speed, "aggression": a.aggression,
        "thresholds": {k: getattr(dl, k) for k in ("BUMP_ACC", "AIR_TIME_S", "AIR_ACC", "OFF_ROUTE_M", "STUCK_S",
                                                   "SETTLE_S", "FELL_M", "COMPLETED_M", "DAMAGE_CRASH", "REVERSE_MS")},
        "status": "running", "results": [],
    }
    write_metrics(outdir, m)
    hard = threading.Timer(a.timeout + 120, lambda: hard_stop(outdir, m))
    hard.daemon = True
    hard.start()
    rc = 0
    started = False
    try:
        gamectl.log(f"drive suite: {len(expected)} routes, mode {a.mode}, out {outdir}")
        info = gamectl.cmd_start()
        started = True
        m["game"] = {"launch_to_ready_s": info.get("launch_to_ready_s"), "pid": info.get("pid")}
        loaded = gamectl.cmd_load_level(a.level, timeout=900)
        m["game"]["load_s"] = loaded.get("load_s")
        state, client = gamectl.require_our_game()
        link = Link(client, deadline)
        dump = dump_graph(link)
        GRAPH_DIR.mkdir(parents=True, exist_ok=True)
        (GRAPH_DIR / f"{a.level}.graph.json").write_text(json.dumps(dump), encoding="utf-8")
        g = rl.Graph(dump)
        m["graph"] = {"nodes": len(g.nodes), "one_way_edges": g.one_way_edges, "components": len(g.components())}
        bad = {}
        for r in doc["routes"]:
            e = rl.validate_routes({"routes": [r]}, g)
            if e:
                bad[r["id"]] = e
        for route in doc["routes"]:
            rid = route["id"]
            link.check()
            if rid in bad:
                log(f"{rid}: not drivable on the live graph: {bad[rid][0]}")
                res = {"id": rid, "name": route["name"], "mode": a.mode, "completed": False, "end_reason": "invalid",
                       "duration_s": 0.0, "length_m": route["length_m"], "progress_m": 0,
                       "events": [{"type": "error", "t": 0.0, "value": "; ".join(bad[rid])[:300]}],
                       "event_counts": {"error": 1}, "max_bump_ms2": 0.0, "max_deviation_m": 0.0,
                       "max_speed_ms": 0.0, "frames": 0, "telemetry": None}
            else:
                log(f"route {rid} ({route['length_m']} m)")
                res = drive_route(link, g, route, a.mode, outdir, a.speed, a.aggression)
            m["results"].append(res)
            evs = ", ".join(f"{e['type']}" + (f" {e['value']}" if "value" in e else "") + (f" at ({e['x']}, {e['y']})" if "x" in e else "")
                            for e in res["events"])
            log(f"  {'DONE' if res['completed'] else 'FAIL'} {rid}: {res['progress_m']}/{res['length_m']} m, "
                f"{res['duration_s']} s, bump {res['max_bump_ms2']}, dev {res['max_deviation_m']}"
                f"{', ' + evs if evs else ''}")
            m["summary"] = dl.summarise(m["results"], expected)
            write_metrics(outdir, m)
        m["status"] = "ok"
    except SuiteTimeout:
        m["status"] = "timeout"
        rc = EXIT_TIMEOUT
        log("overall timeout: stopping")
    except gamectl.GamectlError as e:
        m["status"] = "tool failure"
        m["error"] = e.message
        rc = e.code
        log(f"gamectl: {e.message}")
    except Exception as e:  # noqa: BLE001 - tool failure: record, stop the game, exit non-zero
        m["status"] = "tool failure"
        m["error"] = f"{type(e).__name__}: {e}"
        rc = 1
        log(f"error: {m['error']}")
    finally:
        hard.cancel()
        stopped = {}
        if started or gamectl.read_state() is not None:
            try:
                stopped = gamectl.cmd_stop()
            except Exception as e:  # noqa: BLE001
                stopped = {"error": str(e)}
                rc = rc or gamectl.EXIT_STOP_FAILED
        end = gamectl.end_state()
        m["game_stopped"] = {"stop": stopped, "end_state": end, "clean": gamectl.end_state_clean(end)}
        if not m["game_stopped"]["clean"]:
            rc = rc or gamectl.EXIT_STOP_FAILED
        if started:
            con = gamectl.log_errors(ignore=KNOWN_CONSOLE)
            m["console"] = {"log": con["log"], "errors": con["errors"][:50], "warnings": len(con["warnings"]),
                            "note": "baseline lines ignored: " + ", ".join(KNOWN_CONSOLE)}
        m["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        m["wall_s"] = round(time.monotonic() - t0, 1)
        if "summary" not in m:
            m["summary"] = dl.summarise(m["results"], expected)
        write_metrics(outdir, m)
    s = m["summary"]
    print(json.dumps({"out": str(outdir), "status": m["status"], "routes": s["routes"], "completed": s["completed"],
                      "events": s["events"], "g3_pass": s["g3"]["pass"], "wall_s": m.get("wall_s"),
                      "game_stopped": m["game_stopped"]["clean"]}, indent=1))
    if rc == 0 and a.gate and not s["g3"]["pass"]:
        return EXIT_GATE
    return rc


def hard_stop(outdir: Path, m: dict) -> None:   # pragma: no cover - last resort
    with contextlib.suppress(Exception):
        gamectl.cmd_stop()
    with contextlib.suppress(Exception):
        m["status"] = "hard timeout"
        write_metrics(outdir, m)
    os._exit(EXIT_TIMEOUT)


def cmd_dump_graph(a) -> int:
    state, client = gamectl.require_our_game()
    link = Link(client, time.monotonic() + 300)
    d = dump_graph(link)
    out = Path(a.out) if a.out else GRAPH_DIR / f"{a.level}.graph.json"
    gamectl.check_output_path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d), encoding="utf-8")
    print(f"{len(d['nodes'])} nodes, {len(d['links'])} links -> {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start the game, drive every route, write metrics, stop the game")
    r.add_argument("--routes", default=str(DEFAULT_ROUTES))
    r.add_argument("--level", default=LEVEL)
    r.add_argument("--only", help="comma separated route ids")
    r.add_argument("--mode", choices=["graph", "script"], default="graph")
    r.add_argument("--speed", type=float, default=SPEED_MS, help="AI speed cap in m/s (default 13.4 = 30 mph)")
    r.add_argument("--aggression", type=float, default=AGGRESSION)
    r.add_argument("--timeout", type=float, default=OVERALL_TIMEOUT_S, help="overall hard timeout in seconds")
    r.add_argument("--out", help="output folder (default H:\\BelfastData\\qa\\drive\\<date_time>)")
    r.add_argument("--gate", action="store_true", help="exit 20 when gate G3 fails")
    d = sub.add_parser("dump-graph", help="save map.getMap() of the running game (game must be running)")
    d.add_argument("--level", default=LEVEL)
    d.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        return run_suite(a) if a.cmd == "run" else cmd_dump_graph(a)
    except gamectl.GamectlError as e:
        print(f"error: {e.message}", file=sys.stderr)
        return e.code
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
