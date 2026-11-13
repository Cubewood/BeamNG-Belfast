#!/usr/bin/env python3
"""Junction camera sweep: driver's-eye view 30 m and 10 m before every route junction (T-303).

    uv run --no-project --python 3.12 --with requests --with pillow python qa/junction_sweep.py plan
    uv run --no-project --python 3.12 --with requests --with pillow python qa/junction_sweep.py run
                                                    [--only ID,ID] [--distances 30,10] [--out DIR]

`plan` needs no game: it derives every junction approach from qa/routes/belfast_slice.json and prints
the counts. `run` is unattended: it starts the QA game through tools/gamectl.py (which refuses if any
BeamNG process exists or the local time is 09:00-18:00; the loop also stops itself at 09:00), loads
belfast_slice, fixes the time of day, spawns ONE Ibishu Covet 15le_M, selects the game's own `driver`
camera (the eye point of the right-hand seat), and for every approach parks the car in its lane at
`distance` metres before the junction, waits for it to settle, saves the screenshot as JPG plus a pose
file, and always stops the game. Output (outside git): H:\\BelfastData\\qa\\sweep\\<date_time>\\
<route>\\<shot>.jpg + <shot>.json and manifest.json (index of all shots). See docs/junction_sweep.md.
Only 127.0.0.1 (the game's MCP) is contacted; the remote-MCP flag is never used.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(HERE))
import beamng_mcp  # noqa: E402
import drive_suite as ds  # noqa: E402
import gamectl  # noqa: E402
import route_lib as rl  # noqa: E402
from beamng_mcp import McpError  # noqa: E402

DEFAULT_ROUTES = HERE / "routes" / "belfast_slice.json"
GRAPH_FILE = Path(r"H:\BelfastData\qa\graph\belfast_slice.graph.json")
OUT_ROOT = Path(r"H:\BelfastData\qa\sweep")
LEVEL = ds.LEVEL
DISTANCES = (30.0, 10.0)        # metres before the junction
TIME_OF_DAY = 0.5               # 0..1, 0.5 = noon; clock stopped (play = false)
LANE_FRACTION = 0.5             # lane centre = this fraction of the road half width, left of the centre line
ONE_WAY_LATERAL_M = 5.0         # a one-way graph link along the travel direction this close = one-way road
HEADING_WINDOW_M = 4.0          # heading = route direction from s-4 m to s+4 m
SETTLE_S = 2.0                  # after teleport (suspension), as drive_suite.place
SETTLE_MAX_S = 6.0              # wait this long for the car to stop moving
CAMERA_WAIT_S = 0.8             # frames for the camera to catch up before the screenshot
STOP_HOUR, START_HOUR_BLOCK_END = 9, 18   # no game 09:00-18:00
OVERALL_TIMEOUT_S = 1800.0
EXIT_TIMEOUT = 21
EXIT_INCOMPLETE = 22
# game baseline (docs/spikes/baseline.md) plus one shutdown line: the UI is torn down while the game quits
# ("Hook discarded: FilteredInputChanged", logged after the fmod 'Closing project' lines of shutdown)
KNOWN_CONSOLE = ds.KNOWN_CONSOLE + [r"Hook discarded: FilteredInputChanged"]


# ------------------------------------------------------------------------------- plan (no game)

def cumulative(path) -> list[float]:
    cum = [0.0]
    for a, b in zip(path, path[1:]):
        cum.append(cum[-1] + rl.dist2(a, b))
    return cum


def point_at(path, cum, s: float):
    """(x, y, z) on the polyline s metres from its start (clamped)."""
    s = max(0.0, min(cum[-1], s))
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    seg = cum[hi] - cum[lo]
    f = 0.0 if seg <= 0 else (s - cum[lo]) / seg
    a, b = path[lo], path[hi]
    return tuple(a[i] + (b[i] - a[i]) * f for i in range(3))


def heading_at(path, cum, s: float) -> tuple[float, float]:
    """Unit 2D direction of travel at s (from s-4 m to s+4 m, clamped to the route)."""
    p0 = point_at(path, cum, s - HEADING_WINDOW_M)
    p1 = point_at(path, cum, s + HEADING_WINDOW_M)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    n = math.hypot(dx, dy)
    if n < 1e-6:
        raise ValueError("zero-length route direction")
    return dx / n, dy / n


def compass_deg(dx: float, dy: float) -> float:
    return round(math.degrees(math.atan2(dx, dy)) % 360.0, 1)


def one_way_segments(graph_dump: dict | None) -> list[tuple]:
    """(x1, y1, x2, y2, lanes) of every one-way link of the game graph (each link appears from both ends)."""
    if not graph_dump:
        return []
    pos = {n[0]: (n[1], n[2]) for n in graph_dump["nodes"]}
    seen, segs = set(), []
    for name, other, one_way, _inn, _drv, _spd, lanes in graph_dump["links"]:
        if one_way and name in pos and other in pos and (other, name) not in seen:
            seen.add((name, other))
            segs.append((*pos[name], *pos[other], max(1, len(str(lanes)))))
    return segs


def is_one_way(p, heading, segs: list[tuple]) -> int:
    """Lane count (>= 1) when a one-way link runs along the direction of travel within ONE_WAY_LATERAL_M of
    the point, else 0 (a two-way road)."""
    for x1, y1, x2, y2, lanes in segs:
        vx, vy = x2 - x1, y2 - y1
        n = math.hypot(vx, vy)
        if n < 0.5 or abs((vx * heading[0] + vy * heading[1]) / n) < 0.85:
            continue
        t = max(0.0, min(1.0, ((p[0] - x1) * vx + (p[1] - y1) * vy) / (n * n)))
        if math.hypot(p[0] - (x1 + t * vx), p[1] - (y1 + t * vy)) <= ONE_WAY_LATERAL_M:
            return lanes
    return 0


def derive_shots(doc: dict, distances=DISTANCES, graph_dump: dict | None = None, only: list[str] | None = None) -> list[dict]:
    """One shot per route junction per distance. The car stands in its lane (left of the centre line, or
    on the centre line of a one-way road). Where the junction is closer to the route start than the
    distance, the shot is taken at the route start and `clamped` is true (actual_distance_m shorter)."""
    segs = one_way_segments(graph_dump)
    shots = []
    for route in doc["routes"]:
        if only and route["id"] not in only:
            continue
        path = route["path"]
        cum = cumulative(path)
        half = float(route.get("half_width_m", 3.0))
        for ji, j in enumerate(route["junctions"]):
            jid = f"{route['id']}-J{ji + 1:02d}"
            at = float(j["at_m"])
            for d in distances:
                s = at - d
                clamped = s < 0
                s = max(0.0, s)
                x, y, z = point_at(path, cum, s)
                dx, dy = heading_at(path, cum, s)
                lanes = is_one_way((x, y), (dx, dy), segs) if segs else 0
                one_way = lanes > 0
                # two-way: lane centre half way to the road edge; one-way carriageway: the left-most lane
                offset = half * (lanes - 1) / lanes if one_way else LANE_FRACTION * half
                lx, ly = -dy, dx                       # left of travel
                px, py = x + lx * offset, y + ly * offset
                hd = compass_deg(dx, dy)
                key = f"{j['x']:.0f},{j['y']:.0f}@{int(round(hd / 30.0)) % 12 * 30}"
                shots.append({
                    "id": f"{jid}-{int(d)}m", "route": route["id"], "route_name": route.get("name"),
                    "junction_id": jid, "junction_index": ji + 1, "distance_m": d,
                    "actual_distance_m": round(at - s, 1), "clamped": clamped, "at_m": at,
                    "approach_key": key,
                    "junction": {"x": j["x"], "y": j["y"], "from": j.get("from"), "to": j.get("to"),
                                 "turn": j.get("turn"), "angle": j.get("angle"),
                                 "features": j.get("features", []), "branches": j.get("branches")},
                    "intended": {"x": round(px, 2), "y": round(py, 2), "z": round(z, 2), "dir_x": round(dx, 4),
                                 "dir_y": round(dy, 4), "heading_deg": hd, "lane_offset_m": round(offset, 2),
                                 "one_way": one_way, "one_way_lanes": lanes, "route_x": round(x, 2), "route_y": round(y, 2)},
                })
    return shots


def load_graph_dump() -> dict | None:
    try:
        return json.loads(GRAPH_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def plan_summary(shots: list[dict]) -> dict:
    junctions = {s["junction_id"] for s in shots}
    return {"shots": len(shots), "junction_approaches": len(junctions),
            "unique_approaches": len({s["approach_key"] for s in shots}),
            "clamped_shots": sum(1 for s in shots if s["clamped"]),
            "one_way_shots": sum(1 for s in shots if s["intended"]["one_way"]),
            "per_route": {r: len({s["junction_id"] for s in shots if s["route"] == r})
                          for r in dict.fromkeys(s["route"] for s in shots)}}


# ------------------------------------------------------------------------------- game side

def in_blocked_hours(now: dt.datetime | None = None) -> bool:
    h = (now or dt.datetime.now()).hour
    return STOP_HOUR <= h < START_HOUR_BLOCK_END


def log(msg: str) -> None:
    ds.log(msg)


def lua_place(shot: dict) -> str:
    i = shot["intended"]
    # Same placement as drive_suite.place (MapNG driveTest.mjs:350-376), but facing the route
    # direction exactly, on the parking brake, in the lane
    return f"""
      local function carRot(dir) return quat(0, 0, 1, 0) * quatFromDir(dir, vec3(0, 0, 1)) end
      be:setPhysicsRunning(false)
      local veh = be:getPlayerVehicle(0)
      local bb = veh:getSpawnWorldOOBB()
      local bottom = bb:getCenter() - veh:getDirectionVectorUp() * bb:getHalfExtents().z
      local refH = veh:getPosition().z - bottom.z
      if refH < 0 or refH > 3 then refH = 0.5 end
      local ground, best, top = {i['z']}, math.huge, {i['z'] + 3}
      for _ = 1, 6 do
        local hit = castRay(vec3({i['x']}, {i['y']}, top), vec3({i['x']}, {i['y']}, {i['z'] - 10}), true, true)
        if not hit then break end
        if math.abs(hit.pt.z - {i['z']}) < best then best = math.abs(hit.pt.z - {i['z']}) ground = hit.pt.z end
        top = hit.pt.z - 0.05
      end
      local q = carRot(vec3({i['dir_x']}, {i['dir_y']}, 0))
      veh:setPosRot({i['x']}, {i['y']}, ground + refH + 0.5, q.x, q.y, q.z, q.w)
      veh:resetBrokenFlexMesh()
      return string.format('%.3f,%.3f', ground, refH)"""


LUA_RESUME = """be:setPhysicsRunning(true)
      be:getPlayerVehicle(0):queueLuaCommand('ai.setMode("disabled") input.event("throttle", 0, 1) input.event("brake", 0, 1) input.event("parkingbrake", 1, 1) obj:requestReset(RESET_PHYSICS)')
      return 'ok'"""

LUA_POSE = """local v = be:getPlayerVehicle(0)
  local p = v:getPosition() local d = v:getDirectionVector() local u = v:getDirectionVectorUp() local vel = v:getVelocity()
  local cp = core_camera.getPosition() local cf = core_camera.getForward()
  local r = d:cross(u):normalized()
  return jsonEncode({car = {x = p.x, y = p.y, z = p.z, dx = d.x, dy = d.y, dz = d.z, speed = vel:length(),
    roll_deg = math.deg(math.asin(math.max(-1, math.min(1, r.z))))},
    cam = {x = cp.x, y = cp.y, z = cp.z, fx = cf.x, fy = cf.y, fz = cf.z, fov = core_camera.getFovDeg(),
    mode = tostring(core_camera.getActiveCamName(0)), rhd = select(2, core_camera.getDriverData(v)) and true or false}})"""


def read_pose(link: ds.Link) -> dict:
    return json.loads(link.lua(LUA_POSE))


def pose_record(shot: dict, pose: dict, ground: float, ref_h: float) -> dict:
    car, cam = pose["car"], pose["cam"]
    cdx, cdy = car["dx"], car["dy"]
    n = math.hypot(cdx, cdy) or 1.0
    cdx, cdy = cdx / n, cdy / n
    rx, ry = cdy, -cdx                             # right of the car
    ox, oy = cam["x"] - car["x"], cam["y"] - car["y"]
    fn = math.hypot(cam["fx"], cam["fy"]) or 1.0
    i = shot["intended"]
    return {
        "car": {"x": round(car["x"], 2), "y": round(car["y"], 2), "z": round(car["z"], 2),
                "heading_deg": compass_deg(cdx, cdy), "speed_ms": round(car["speed"], 3),
                "roll_deg": round(car["roll_deg"], 2), "ground_z": round(ground, 2)},
        "camera": {"x": round(cam["x"], 2), "y": round(cam["y"], 2), "z": round(cam["z"], 2),
                   "heading_deg": compass_deg(cam["fx"] / fn, cam["fy"] / fn),
                   "pitch_deg": round(math.degrees(math.asin(max(-1.0, min(1.0, cam["fz"])))), 1),
                   "fov_deg": round(cam["fov"], 1), "mode": cam["mode"], "right_hand_drive": cam["rhd"],
                   "offset_right_m": round(ox * rx + oy * ry, 2), "offset_forward_m": round(ox * cdx + oy * cdy, 2),
                   "height_above_ground_m": round(cam["z"] - ground, 2)},
        "position_error_m": round(math.hypot(car["x"] - i["x"], car["y"] - i["y"]), 2),
        "heading_error_deg": round(abs((compass_deg(cdx, cdy) - i["heading_deg"] + 180.0) % 360.0 - 180.0), 1),
    }


def settle(link: ds.Link) -> float:
    time.sleep(SETTLE_S)
    t0 = time.monotonic()
    while True:
        sp = float(link.lua("return string.format('%.3f', be:getPlayerVehicle(0):getVelocity():length())"))
        if sp < 0.15 or time.monotonic() - t0 > SETTLE_MAX_S:
            return sp
        time.sleep(0.4)


LUA_DRIVER_CAM = """local v = be:getPlayerVehicle(0)
  local cams = core_camera.getCameraDataById(v:getId()) or {}
  local n = cams['onboard.driver'] and 'onboard.driver' or nil
  if not n then
    for k, c in pairs(cams) do
      if k:find('^occupant%.', 1) and c.defaultPermissionMode == 'driver' then n = k break end
    end
  end
  if not n then return 'none' end
  if core_camera.getActiveCamName(0) ~= n then core_camera.setByName(0, n, false) end
  return tostring(core_camera.getActiveCamName(0))"""


def ensure_driver_camera(link: ds.Link) -> str:
    """Select the vehicle's driver camera ('onboard.driver', or the occupant camera whose permission mode is
    driver) and return the active camera name."""
    return link.lua(LUA_DRIVER_CAM)


def is_driver_cam(name: str) -> bool:
    return name == "onboard.driver" or name.startswith("occupant.")


def take_shot(link: ds.Link, shot: dict, outdir: Path, tod: dict, last_hash: str | None) -> dict:
    """Place, settle, photograph. Returns the pose record (also written next to the JPG)."""
    problems = []
    ground = ref_h = 0.0
    pose = None
    for attempt in range(2):
        placed = link.lua(lua_place(shot))
        ground, ref_h = (float(v) for v in placed.split(","))
        link.lua(LUA_RESUME)
        speed = settle(link)
        cam_name = ensure_driver_camera(link)
        time.sleep(CAMERA_WAIT_S)
        pose = read_pose(link)
        rec = pose_record(shot, pose, ground, ref_h)
        problems = []
        if not is_driver_cam(cam_name):
            problems.append(f"camera mode {cam_name}, not driver")
        if abs(rec["car"]["roll_deg"]) > 8:
            problems.append(f"tilted {rec['car']['roll_deg']} deg sideways")
        if speed > 0.5:
            problems.append(f"still moving at {speed:.2f} m/s")
        if rec["position_error_m"] > 1.5:
            problems.append(f"car {rec['position_error_m']} m from the intended spot")
        if rec["heading_error_deg"] > 10:
            problems.append(f"heading {rec['heading_error_deg']} deg off the route direction")
        if rec["car"]["z"] - ref_h < ground - 0.5:
            problems.append("car sank into the surface")
        if not problems:
            break
        log(f"  {shot['id']}: {'; '.join(problems)}, try {attempt + 2}")
    # screenshot: poll until an image arrives; reject black/flat or a repeat of the previous image
    img = None
    stats = {}
    for attempt in range(3):
        data = link.c.screenshot_image(scale=1.0, timeout=30)
        stats = gamectl.image_stats(data)
        h = hashlib.sha256(data).hexdigest()
        if gamectl.screenshot_ok(stats) and h != last_hash:
            img = data
            break
        log(f"  {shot['id']}: screenshot rejected (mean {stats['mean']}, std {stats['stddev']}"
            f"{', same as the previous image' if h == last_hash else ''}), retry")
        time.sleep(1.0)
    jpg = outdir / shot["route"] / f"{shot['id']}.jpg"
    jpg.parent.mkdir(parents=True, exist_ok=True)
    if img is None:
        problems.append("no usable screenshot")
    else:
        jpg.write_bytes(img)
    rec = pose_record(shot, pose, ground, ref_h)
    full = dict(shot)
    full.update(rec)
    full.update({"level": LEVEL, "vehicle": f"{ds.VEHICLE[0]} {ds.VEHICLE[1]}", "time_of_day": tod,
                 "image": f"{shot['route']}/{jpg.name}" if img else None,
                 "image_stats": {k: stats.get(k) for k in ("width", "height", "mean", "stddev", "bytes")},
                 "image_sha256": hashlib.sha256(img).hexdigest()[:16] if img else None,
                 "problems": problems, "ok": img is not None and not problems,
                 "captured": dt.datetime.now().isoformat(timespec="seconds")})
    (outdir / shot["route"] / f"{shot['id']}.json").write_text(json.dumps(full, indent=1), encoding="utf-8")
    return full


def fix_time_of_day(link: ds.Link) -> dict:
    link.c.call_text("set_time_of_day", {"time": TIME_OF_DAY, "play": False}, timeout=15)
    time.sleep(1.0)
    tod = link.c.call_json("get_time_of_day", timeout=15)
    if abs(float(tod.get("time", -1)) - TIME_OF_DAY) > 0.01 or tod.get("play"):
        raise RuntimeError(f"time of day not fixed: {tod}")
    return tod


def write_manifest(outdir: Path, m: dict) -> None:
    tmp = outdir / "manifest.json.tmp"
    tmp.write_text(json.dumps(m, indent=1), encoding="utf-8")
    os.replace(tmp, outdir / "manifest.json")


def manifest_entry(rec: dict) -> dict:
    keys = ("id", "route", "junction_id", "junction_index", "distance_m", "actual_distance_m", "clamped",
            "approach_key", "image", "ok", "problems")
    e = {k: rec[k] for k in keys}
    e["pose_file"] = f"{rec['route']}/{rec['id']}.json"
    e["junction"] = {"x": rec["junction"]["x"], "y": rec["junction"]["y"], "turn": rec["junction"]["turn"],
                     "features": rec["junction"]["features"]}
    e["camera"] = {k: rec["camera"][k] for k in ("x", "y", "z", "heading_deg", "offset_right_m", "right_hand_drive")}
    return e


def run_sweep(a) -> int:
    routes_path = Path(a.routes)
    only = [s for s in a.only.split(",") if s] if a.only else None
    doc = ds.load_routes(routes_path, only)
    distances = tuple(float(v) for v in a.distances.split(","))
    shots = derive_shots(doc, distances, load_graph_dump(), only)
    if not shots:
        raise ValueError("no shots to take")
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    outdir = Path(a.out) if a.out else OUT_ROOT / stamp
    gamectl.check_output_path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    deadline = t0 + a.timeout
    m: dict = {
        "tool": "qa/junction_sweep.py", "task": "T-303", "level": a.level, "vehicle": f"{ds.VEHICLE[0]} {ds.VEHICLE[1]}",
        "started": dt.datetime.now().isoformat(timespec="seconds"), "routes_file": str(routes_path),
        "routes_sha256": hashlib.sha256(routes_path.read_bytes()).hexdigest()[:16], "provisional_start": doc.get("provisional"),
        "distances_m": list(distances), "camera": "onboard.driver (the game camera at the driver's seat; right-hand on the Covet 15le_M)",
        "lane": f"two-way roads: left of the centre line by {LANE_FRACTION} x road half width; one-way carriageway of n lanes: left-most lane, half width x (n-1)/n",
        "plan": plan_summary(shots), "status": "running", "shots": [],
    }
    write_manifest(outdir, m)
    hard = threading.Timer(a.timeout + 120, lambda: ds.hard_stop(outdir, m))
    hard.daemon = True
    hard.start()
    rc = 0
    started = False
    try:
        gamectl.log(f"junction sweep: {len(shots)} shots, out {outdir}")
        info = gamectl.cmd_start()
        started = True
        m["game"] = {"launch_to_ready_s": info.get("launch_to_ready_s"), "pid": info.get("pid")}
        loaded = gamectl.cmd_load_level(a.level, timeout=900)
        m["game"]["load_s"] = loaded.get("load_s")
        state, client = gamectl.require_our_game()
        link = ds.Link(client, deadline)
        m["time_of_day"] = tod = fix_time_of_day(link)
        ds.spawn_fresh(link)
        log(f"driver camera: {ensure_driver_camera(link)}")
        last_hash = None
        for n, shot in enumerate(shots, 1):
            link.check()
            if in_blocked_hours():
                m["status"] = "stopped: reached 09:00"
                rc = EXIT_INCOMPLETE
                log("it is 09:00 or later: stopping")
                break
            try:
                rec = take_shot(link, shot, outdir, tod, last_hash)
            except (RuntimeError, McpError, beamng_mcp.ToolError, ValueError, KeyError) as e:
                if isinstance(e, ds.GameLost):
                    raise
                log(f"  {shot['id']}: error: {e}")
                m["shots"].append({"id": shot["id"], "route": shot["route"], "junction_id": shot["junction_id"],
                                   "distance_m": shot["distance_m"], "ok": False, "image": None,
                                   "problems": [f"error: {str(e)[:200]}"]})
                write_manifest(outdir, m)
                continue
            if rec["image_sha256"]:
                last_hash = hashlib.sha256((outdir / rec["image"]).read_bytes()).hexdigest()
            m["shots"].append(manifest_entry(rec))
            log(f"[{n}/{len(shots)}] {rec['id']}: {'ok' if rec['ok'] else 'FAIL'}"
                f" cam {rec['camera']['offset_right_m']:+.2f} m right, err {rec['position_error_m']} m"
                f"{', ' + '; '.join(rec['problems']) if rec['problems'] else ''}")
            if n % 10 == 0:
                write_manifest(outdir, m)
        if m["status"] == "running":
            m["status"] = "ok"
    except ds.SuiteTimeout:
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
        ok = sum(1 for s in m["shots"] if s.get("ok"))
        m["summary"] = {"planned": len(shots), "taken": len(m["shots"]), "ok": ok,
                        "failed": [s["id"] for s in m["shots"] if not s.get("ok")],
                        "missing": [s["id"] for s in shots if s["id"] not in {x["id"] for x in m["shots"]}],
                        "jpg_files": len(list(outdir.glob("*/*.jpg"))), "pose_files": len(list(outdir.glob("*/*.json")))}
        m["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        m["wall_s"] = round(time.monotonic() - t0, 1)
        write_manifest(outdir, m)
    s = m["summary"]
    print(json.dumps({"out": str(outdir), "status": m["status"], **s, "console_errors": len(m.get("console", {}).get("errors", [])),
                      "game_stopped": m["game_stopped"]["clean"], "wall_s": m["wall_s"]}, indent=1))
    if rc == 0 and (s["failed"] or s["missing"]):
        rc = EXIT_INCOMPLETE
    return rc


def cmd_plan(a) -> int:
    doc = ds.load_routes(Path(a.routes), [s for s in a.only.split(",") if s] if a.only else None)
    distances = tuple(float(v) for v in a.distances.split(","))
    shots = derive_shots(doc, distances, load_graph_dump())
    print(json.dumps(plan_summary(shots), indent=1))
    if a.verbose:
        for s in shots:
            i = s["intended"]
            print(f"{s['id']:44} at ({i['x']:8.1f},{i['y']:8.1f}) hdg {i['heading_deg']:5.1f} off {i['lane_offset_m']:4.2f}"
                  f"{' one-way' if i['one_way'] else ''}{' CLAMPED to ' + str(s['actual_distance_m']) + ' m' if s['clamped'] else ''}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--routes", default=str(DEFAULT_ROUTES))
        p.add_argument("--only", help="comma separated route ids")
        p.add_argument("--distances", default=",".join(str(int(d)) for d in DISTANCES), help="metres before the junction")
        if name == "plan":
            p.add_argument("-v", "--verbose", action="store_true")
        else:
            p.add_argument("--level", default=LEVEL)
            p.add_argument("--timeout", type=float, default=OVERALL_TIMEOUT_S)
            p.add_argument("--out", help="output folder (default H:\\BelfastData\\qa\\sweep\\<date_time>)")
    a = ap.parse_args(argv)
    try:
        return run_sweep(a) if a.cmd == "run" else cmd_plan(a)
    except gamectl.GamectlError as e:
        print(f"error: {e.message}", file=sys.stderr)
        return e.code
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
