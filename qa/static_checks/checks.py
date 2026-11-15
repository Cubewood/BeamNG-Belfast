"""The static checks (G0). Each takes a loaded Level and returns a list of findings (see geometry.finding).

No game and no model: only the level zip, the OSM extract it was built from, and the game file index.
Findings use the format of docs/digest.md. Positions are world metres, x east, y north.
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
import shapely.ops
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from .buildings import (attribute, exported_building_geometry, osm_buildings,
                        overlay_in_lanes)
from .game_index import GameIndex
from .kerbs import level_kerb_faces
from .geometry import (LineIndex, dedupe_points, finding, lane_corridor, lane_roads, nav_roads, resample)
from .level import DRIVABLE_HIGHWAYS, PUBLIC_HIGHWAYS, Level

DEFAULTS = {
    # roads present
    "road_sample_m": 8.0,          # OSM public roads are sampled this often
    "road_match_m": 6.0,           # a drawn drivable road must lie within this of each sample
    "road_min_gap_m": 16.0,        # a missing stretch must be at least this long
    "edge_margin_m": 33.0,         # MapNG ends roads about 31 m short of the map edge (graph extent 993 of 1024): skip samples there
    # connection
    "join_m": 3.0,                 # an exported road end this close to another road joins it (game graph islands match at 0.5 to 12 m)
    "island_min_m": 20.0,          # fragments shorter than this are ignored
    "osm_match_m": 12.0,           # exported road to OSM way
    # one-way
    "oneway_match_m": 6.0,
    "oneway_min_agree": 0.5,
    # signals and roundabouts (T-407)
    "signal_match_m": 30.0,        # an OSM signal node with no signal head this close is reported
    "roundabout_match_m": 4.0,     # a drawn road within this of a ring sample draws the ring there
    # height
    "height_tol_m": 0.5,           # G0: road within 0.5 m of the ground reference
    "bridge_clear_m": 15.0,        # no height check this close to an OSM bridge or tunnel
    "bridge_object_m": 50.0,       # ... or this close to a bridge shape
    # buildings
    "building_min_area_m2": 1.0,
    "building_min_depth_m": 0.15,  # a sliver thinner than this along the lanes' edge is not a wall in the lanes
    "building_high_area_m2": 5.0,
    "lane_margin_m": 0.3,          # MapNG leaves walls flush with the lanes' edge, out this far
    # terrain steps
    "step_limit_m": 0.15,          # T-413: no step over 0.15 m across drivable lanes
    "step_high_m": 0.25,
    "step_cell_m": 10.0,
    "step_bridge_dz_m": 1.0,       # road more than this above the ground is a deck, not a step
    "step_offsets": (-3.0, -1.5, 0.0, 1.5, 3.0),
    # kerbs (I-006)
    "kerb_limit_m": 0.20,          # a kerb face taller than this at a lane edge is reported (UK kerbs 0.10 to 0.125 m)
    "kerb_high_m": 0.30,
    "kerb_reach_m": 0.5,           # ... if it lies within this of a drivable lane's edge (or inside the lane)
    "kerb_cell_m": 10.0,
    "kerb_side_m": 0.3,            # the ground beside a face is the terrain this far off it, on the lane's side (I-005)
    "kerb_deck_m": 0.5,            # ... unless it is further than this from the road's decal (a bridge deck): the decal
    # road widths against DfI (T-406)
    "width_tol_m": 0.75,           # G0: within 0.75 m of the DfI carriageway width
    "width_sample_m": 6.0,
    "width_end_skip_m": 8.0,       # junction mouths, flares and MapNG's width tapers are not the running width
    "width_match_m": 3.0,          # a body road within this of the OSM way, running along it
    "width_min_samples": 3,
    "dfi_widths": None,            # a pipeline/dfi.py widths file (default H:\BelfastData\dfi\widths_full.json)
    # reporting
    "max_per_kind": 150,
}


def cfg(over: dict | None) -> dict:
    c = dict(DEFAULTS)
    c.update(over or {})
    return c


def way_id(osm_id: str) -> str:
    return str(osm_id).split("_seg")[0]


def is_oneway(tags: dict) -> int:
    """0 two-way, 1 along the way's node order, -1 against it (MapNG's isOneWayRoad rules)."""
    v = str(tags.get("oneway", "")).strip().lower()
    if v in ("-1", "reverse"):
        return -1
    if v in ("yes", "1", "true"):
        return 1
    if tags.get("junction") == "roundabout" or tags.get("highway") in ("motorway", "motorway_link"):
        return 1
    return 0


def public_roads(level: Level):
    """OSM ways that are part of the public road network (what a learner can be sent along)."""
    for f in level.osm:
        if f.type != "road":
            continue
        t = f.tags
        hw = t.get("highway")
        if hw in PUBLIC_HIGHWAYS:
            pass
        elif hw == "service" and t.get("service") not in ("driveway", "parking_aisle", "drive-through") \
                and t.get("access") not in ("private", "no"):
            pass
        else:
            continue
        if len(f.xy) >= 2:
            yield f


def drivable_osm_roads(level: Level):
    for f in level.osm:
        if f.type == "road" and f.tags.get("highway") in DRIVABLE_HIGHWAYS and len(f.xy) >= 2:
            yield f


def _name(f) -> str:
    t = f.tags
    return t.get("name") or f"way {way_id(f.id)}"


# ------------------------------------------------------------------ speed limits (T-408)

MPH_PER_MS = 2.2369362920544
SPEED_STEP_MPH = 5.0       # every limit the game or the Highway Code uses is a multiple of 5 mph
SPEED_TOL_MPH = 0.3        # MapNG rounds the m/s value to 3 decimals; 30 mph is 13.411
SPEED_MAX_MPH = 70.0


def check_speed_limits(level: Level, c: dict) -> list[dict]:
    """Every road edge carries a limit, and it is a plausible mph value.

    A road edge is a DecalRoad in the navigation graph (`drivability` above 0): the `_ai` road of each carriageway
    and the body roads with no `_ai` twin. MapNG writes `speedLimit` (m/s) there only when OSM `maxspeed` parsed;
    pipeline/speed_limits.py fills `maxspeed` in mph on every road way before the export, so a missing value here
    means the export read an osm.json that step never saw."""
    out = []
    for r in nav_roads(level):
        v = r.props.get("speedLimit")
        x, y = (float(r.nodes[0, 0]), float(r.nodes[0, 1]))
        if not isinstance(v, (int, float)) or v <= 0:
            out.append(finding("road-no-speed-limit", x, y, where=r.name, severity="high",
                               detail="navigation road with no speedLimit: BeamNG will work out a speed itself"))
            continue
        mph = v * MPH_PER_MS
        near5 = round(mph / SPEED_STEP_MPH) * SPEED_STEP_MPH
        if abs(mph - near5) > SPEED_TOL_MPH or not (0 < near5 <= SPEED_MAX_MPH):
            out.append(finding("road-speed-not-mph-step", x, y, where=r.name, severity="low", value=mph, metric="mph",
                               detail=f"speedLimit {v} m/s is {mph:.1f} mph, not a multiple of 5 up to {SPEED_MAX_MPH:.0f}"))
    return out


# ------------------------------------------------------------------ 1. public roads present

def check_roads_present(level: Level, c: dict) -> list[dict]:
    bodies = [r for r in level.roads if r.is_vehicle_body or (r.is_ai and r.material == "road_invisible")]
    idx = LineIndex([r.xy for r in bodies])
    half = level.size_m / 2
    out = []
    for f in public_roads(level):
        pts, _ = resample(f.xy, c["road_sample_m"])
        miss = []
        for p in pts:
            if abs(p[0]) > half - c["edge_margin_m"] or abs(p[1]) > half - c["edge_margin_m"]:
                miss.append(None)
                continue
            hit = idx.nearest(p[0], p[1], c["road_match_m"])
            miss.append(hit is None)
        run = []
        runs = []
        for p, m in zip(pts, miss):
            if m:
                run.append(p)
            else:
                if run:
                    runs.append(run)
                run = []
        if run:
            runs.append(run)
        for r in runs:
            length = (len(r) - 1) * c["road_sample_m"] if len(r) > 1 else 0.0
            whole = len(r) == len(pts)
            if length >= c["road_min_gap_m"] or (whole and len(pts) >= 1 and f.xy.shape[0] >= 2):
                mid = r[len(r) // 2]
                length = max(length, c["road_sample_m"])
                out.append(finding("road-missing", mid[0], mid[1], f"{_name(f)} (OSM way {way_id(f.id)})",
                                   "high" if f.tags.get("highway") in PUBLIC_HIGHWAYS else "medium", length,
                                   f"{length:.0f} m with no drivable road within {c['road_match_m']:.0f} m",
                                   f"highway={f.tags.get('highway')}, no DecalRoad body or AI road follows it"))
    return out


# ------------------------------------------------------------------ 2. connection

class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[a] = b


def check_connection(level: Level, c: dict) -> list[dict]:
    """Drivable roads that should be one network but are drawn as separate islands.

    The OSM network says what is connected (ways sharing a node). Each exported AI road is given the OSM
    component of the way it follows. Where one OSM component is drawn as several islands (exported AI roads
    whose ends are not within `join_m` of each other's lines), the smaller islands are reported. The game merges
    DecalRoads into a graph only where they meet; a gap breaks the route for the AI and the learner."""
    ai = nav_roads(level)
    if not ai:
        return []
    # OSM components over drivable ways (shared vertex coordinates)
    ways = list(public_roads(level))
    node_id: dict[tuple, int] = {}
    uf_osm = _UF(len(ways))
    for wi, f in enumerate(ways):
        for lo, la in f.lonlat:
            key = (round(lo, 7), round(la, 7))
            if key in node_id:
                uf_osm.union(wi, node_id[key])
            else:
                node_id[key] = wi
    osm_idx = LineIndex([f.xy for f in ways])
    # exported islands
    lines = [LineString(dedupe_points(r.xy)) for r in ai]
    tree = STRtree(lines)
    uf = _UF(len(ai))
    for i, ln in enumerate(lines):
        for end in (Point(ln.coords[0]), Point(ln.coords[-1])):
            for k in tree.query(end.buffer(c["join_m"])):
                if k != i and lines[k].distance(end) <= c["join_m"]:
                    uf.union(i, k)
    # label exported roads with their OSM component
    groups: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    for i, r in enumerate(ai):
        mid = lines[i].interpolate(0.5, normalized=True)
        hit = osm_idx.nearest(mid.x, mid.y, c["osm_match_m"])
        if hit is None:
            continue
        groups[uf_osm.find(hit[0])][uf.find(i)].append(i)
    out = []
    for comp, islands in groups.items():
        if len(islands) < 2:
            continue
        sized = sorted(islands.values(), key=lambda m: -sum(lines[i].length for i in m))
        main = sized[0]
        for isl in sized[1:]:
            length = sum(lines[i].length for i in isl)
            if length < c["island_min_m"]:
                continue
            # where the island sits closest to the main network
            isl_geom = shapely.unary_union([lines[i] for i in isl])
            main_geom = shapely.unary_union([lines[i] for i in main])
            a, b = shapely.ops.nearest_points(isl_geom, main_geom)
            gap = a.distance(b)
            name = ai[isl[0]].name
            out.append(finding("road-disconnected", a.x, a.y, f"{name} and {len(isl) - 1} more",
                               "high", length,
                               f"{length:.0f} m of road in an island, {gap:.0f} m from the rest of its network",
                               "OSM joins these ways to the main network, the drawn AI roads do not meet"))
    return out


# ------------------------------------------------------------------ 3. one-way

def _direction(r, pt) -> np.ndarray:
    """Unit direction of the road's node order at its point nearest to pt (flipDirection reverses it)."""
    xy = dedupe_points(r.xy)
    ln = LineString(xy)
    s = ln.project(Point(*pt))
    s0, s1 = max(0.0, s - 1.0), min(ln.length, s + 1.0)
    a, b = ln.interpolate(s0), ln.interpolate(s1)
    v = np.array([b.x - a.x, b.y - a.y])
    n = np.hypot(*v)
    v = v / n if n > 0 else v
    return -v if r.props.get("flipDirection") else v


def check_oneway(level: Level, c: dict) -> list[dict]:
    ai = nav_roads(level)
    ai_idx = LineIndex([r.xy for r in ai])
    out = []
    # OSM one-way ways: the drawn AI road must be one-way, running the same way
    for f in drivable_osm_roads(level):
        ow = is_oneway(f.tags)
        if not ow:
            continue
        pts, tans = resample(f.xy, 10.0)
        want = 0
        wrong = 0
        notow = 0
        first_bad = None
        for k, (p, t) in enumerate(zip(pts, tans)):
            if len(pts) > 3 and k in (0, len(pts) - 1):
                continue                      # junction mouths: crossing roads are closer than the road itself
            hits = ai_idx.parallel_all(p[0], p[1], t, c["oneway_match_m"])
            if not hits:
                continue
            # roads meeting end to end at this sample (a one-way stub and the two-way road beside it) both fit:
            # the sample is judged against the one-way ones, and is right if any of them runs the OSM way's way
            cand = [ai[k] for k, _ in hits if ai[k].props.get("oneWay")] or [ai[hits[0][0]]]
            want += 1
            if not cand[0].props.get("oneWay"):
                notow += 1
                first_bad = p if first_bad is None else first_bad
                continue
            if all(float(np.dot(_direction(r, p), t * ow)) < 0 for r in cand):
                wrong += 1
                first_bad = p if first_bad is None else first_bad
        if not want:
            continue
        if notow / want >= c["oneway_min_agree"]:
            out.append(finding("oneway-missing", first_bad[0], first_bad[1], f"{_name(f)} (OSM way {way_id(f.id)})",
                               "high", notow / want, f"{notow} of {want} samples on a two-way road",
                               f"OSM oneway={f.tags.get('oneway', 'implied')}; the AI road has no oneWay"))
        elif wrong / want >= c["oneway_min_agree"]:
            out.append(finding("oneway-wrong-direction", first_bad[0], first_bad[1], f"{_name(f)} (OSM way {way_id(f.id)})",
                               "high", wrong / want, f"{wrong} of {want} samples run against OSM",
                               f"OSM oneway={f.tags.get('oneway', 'implied')}; the AI road runs the other way"))
    # drawn one-way roads on two-way OSM ways
    ways = list(drivable_osm_roads(level))
    way_idx = LineIndex([f.xy for f in ways])
    seen = set()
    for r in ai:
        if not r.props.get("oneWay"):
            continue
        ln = LineString(dedupe_points(r.xy))
        mid = ln.interpolate(0.5, normalized=True)
        s = ln.project(mid)
        a, b = ln.interpolate(max(0.0, s - 1.0)), ln.interpolate(min(ln.length, s + 1.0))
        v = np.array([b.x - a.x, b.y - a.y])
        if not np.hypot(*v):
            continue
        hit = way_idx.nearest_parallel(mid.x, mid.y, v / np.hypot(*v), c["oneway_match_m"])
        if hit is None:
            continue
        f = ways[hit[0]]
        if not is_oneway(f.tags) and way_id(f.id) not in seen:
            seen.add(way_id(f.id))
            out.append(finding("oneway-extra", mid.x, mid.y, f"{_name(f)} (OSM way {way_id(f.id)})", "medium", 1,
                               "drawn one-way, OSM says two-way", f"AI road {r.name} has oneWay on a two-way OSM way"))
    return out


# ------------------------------------------------------------------ left-hand traffic: signals, roundabouts (T-407)

def _signals_module():
    import sys
    pipe = str(Path(__file__).resolve().parents[2] / "pipeline")
    if pipe not in sys.path:
        sys.path.insert(0, pipe)
    import signals_fix
    return signals_fix


def check_signals(level: Level, c: dict) -> list[dict]:
    """The level's traffic signal data (`signals.json`) as the game will run it (docs/left_hand_traffic.md).

    * `signal-never-changes` (high): a signal head with no sequence keeps its controller's default state, red, for
      ever (`core/trafficSignals.lua` SignalInstance:setActive). MapNG gives a sequence only to clusters with two
      axes or a protected turn, so a one-axis cluster is a light stuck on red. `pipeline/signals_fix.py` repairs it.
    * `signal-not-uk-order` (high): a controller whose states are not red, red and amber, green, amber (cyclic).
    * `signal-short-phase` (medium): a sequence phase shorter than its controller's cycle, so the next phase would
      cut a cycle off and two directions could be open together.
    * `signal-missing` (medium): an OSM `highway=traffic_signals` node with no signal head within 30 m
      (MapNG drops a head when it finds no clear spot off the road)."""
    name = f"{level.base}signals.json"
    out: list[dict] = []
    if name not in level.zf.namelist():
        if any(f.tags.get("highway") == "traffic_signals" for f in level.osm):
            out.append(finding("signals-file-missing", where=name, severity="high",
                               detail="OSM has traffic signals but the level has no signals.json: the game runs no lights"))
        return out
    sf = _signals_module()
    data = json.loads(level.zf.read(name).decode("utf-8"))
    res = sf.analyse(data)
    by_id = {i["id"]: i for i in data.get("instances", [])}
    for e in res["never_change"]:
        i = by_id[e["instance"]]
        out.append(finding("signal-never-changes", i["pos"][0], i["pos"][1], e["name"] or f"signal {e['instance']}", "high", 1,
                           "no sequence: red for ever", f"controller {e['controller']} has no sequence, so the light stays red"))
    first = {}
    for i in data.get("instances", []):
        first.setdefault(i["controllerId"], i)
    for e in res["not_uk_order"]:
        i = first.get(e["controller"])
        out.append(finding("signal-not-uk-order", i["pos"][0] if i else None, i["pos"][1] if i else None, e["name"] or "",
                           "high", 1, " > ".join(s.replace("TrafficLight", "") for s in e["states"]),
                           "UK order is redYellow, green, yellow, red"))
    for e in res["short_phase"]:
        out.append(finding("signal-short-phase", where=f"sequence {e['sequence']} phase {e['phase']}", severity="medium",
                           value=e["totalDuration"], metric="s",
                           detail=f"phase lasts {e['totalDuration']} s, its controller needs {e['needs']} s"))
    heads = [np.array(i["pos"][:2], dtype=float) for i in data.get("instances", [])]
    for f in level.osm:
        if f.type != "street_furniture" or f.tags.get("highway") != "traffic_signals" or not len(f.xy):
            continue
        d = min((float(np.hypot(*(h - f.xy[0]))) for h in heads), default=1e9)
        if d > c["signal_match_m"]:
            out.append(finding("signal-missing", f.xy[0][0], f.xy[0][1], f"OSM node {f.id}", "medium", d,
                               f"nearest signal head {d:.0f} m away", "no traffic light was placed for this OSM signal"))
    return out


def check_roundabouts(level: Level, c: dict) -> list[dict]:
    """OSM roundabouts (`junction=roundabout`) as the level draws them, for left-hand traffic.

    * `roundabout-osm-anticlockwise` (high): a closed ring whose node order runs anticlockwise; UK traffic goes
      clockwise and MapNG follows the node order (the one-way check then agrees with it).
    * `roundabout-not-drawn` (medium): fewer than 60 % of the ring has a drivable road within 4 m, so no car can
      circulate (a tiny ring is drawn as a plain junction).
    * `roundabout-two-way` / `roundabout-wrong-way` (high): the drawn ring is two-way, or runs against the way."""
    out: list[dict] = []
    ai = nav_roads(level)
    idx = LineIndex([r.xy for r in ai])
    for f in level.osm:
        if f.type != "road" or f.tags.get("junction") != "roundabout" or len(f.xy) < 4:
            continue
        ring = dedupe_points(f.xy)
        closed = float(np.hypot(*(ring[0] - ring[-1]))) < 1.0
        centre = ring.mean(axis=0)
        where = f"{_name(f)} (OSM way {way_id(f.id)})"
        if closed:
            a = 0.0
            for p, q in zip(ring[:-1], ring[1:]):
                a += p[0] * q[1] - q[0] * p[1]
            if a > 0:
                out.append(finding("roundabout-osm-anticlockwise", centre[0], centre[1], where, "high", 1,
                                   "node order runs anticlockwise", "a UK roundabout is driven clockwise"))
        pts, tans = resample(ring, 3.0)
        seen = missing = two = wrong = 0
        for p, t in zip(pts, tans):
            seen += 1
            hit = idx.nearest_parallel(p[0], p[1], t, c["roundabout_match_m"])
            if hit is None:
                missing += 1
                continue
            r = ai[hit[0]]
            if not r.props.get("oneWay"):
                two += 1
            elif float(np.dot(_direction(r, p), t)) < 0:
                wrong += 1
        if seen and missing / seen > 0.4:
            radius = float(np.mean(np.hypot(*(ring - centre).T)))
            out.append(finding("roundabout-not-drawn", centre[0], centre[1], where, "medium", missing / seen,
                               f"{missing} of {seen} samples have no road within {c['roundabout_match_m']:.0f} m",
                               f"ring radius about {radius:.1f} m"))
        if seen - missing and two / (seen - missing) >= 0.5:
            out.append(finding("roundabout-two-way", centre[0], centre[1], where, "high", two / (seen - missing),
                               f"{two} of {seen - missing} samples on a two-way road", "traffic could circulate both ways"))
        if seen - missing and wrong / (seen - missing) >= 0.5:
            out.append(finding("roundabout-wrong-way", centre[0], centre[1], where, "high", wrong / (seen - missing),
                               f"{wrong} of {seen - missing} samples run against the ring", "the drawn ring runs the other way"))
    return out


# ------------------------------------------------------------------ 4. road height

def _bridge_zones(level: Level, c: dict):
    lines = [f.xy for f in level.osm if f.type == "road" and len(f.xy) >= 2 and
             (str(f.tags.get("bridge", "no")) not in ("no", "") or str(f.tags.get("tunnel", "no")) not in ("no", ""))]
    objs = [np.array(o["position"][:2]) for o in level.objects
            if o.get("class") == "TSStatic" and "bridge" in str(o.get("shapeName", "")).lower() and "position" in o]
    return LineIndex(lines), objs


def _on_bridge(x: float, y: float, bridge_idx, bridge_objs, c: dict) -> bool:
    if bridge_idx.nearest(x, y, c["bridge_clear_m"]) is not None:
        return True
    return any(np.hypot(x - o[0], y - o[1]) < c["bridge_object_m"] for o in bridge_objs)


def check_road_height(level: Level, c: dict, reference=None) -> list[dict]:
    """Drivable road nodes within `height_tol_m` of the ground, off bridges and tunnels.

    `reference` is a callable (x, y arrays) -> metres; by default the level's own terrain. When LiDAR (T-203)
    is the reference, pass a sampler of the same coordinates here."""
    ref = reference or (level.terrain.height if level.terrain else None)
    if ref is None:
        return [finding("check-not-run", where="road-height", severity="medium",
                        detail="no terrain in the level and no reference heights given")]
    bridge_idx, bridge_objs = _bridge_zones(level, c)
    out = []
    for r, _half, nav in lane_roads(level):
        x, y, z = r.nodes[:, 0], r.nodes[:, 1], r.nodes[:, 2]
        g = ref(x, y)
        dz = z - g
        bad = np.where(np.abs(dz) > c["height_tol_m"])[0]
        for i in bad:
            if np.isnan(g[i]):
                continue
            if bridge_idx.nearest(x[i], y[i], c["bridge_clear_m"]) is not None:
                continue
            if any(np.hypot(x[i] - o[0], y[i] - o[1]) < c["bridge_object_m"] for o in bridge_objs):
                continue
            sev = "high" if abs(dz[i]) > 1.5 else "medium"
            if not nav:
                sev = "low" if sev == "medium" else "medium"       # service roads and driveways carry no test route
            out.append(finding("road-height", x[i], y[i], r.name, sev,
                               abs(dz[i]), f"road {z[i]:.2f} m, ground {g[i]:.2f} m ({dz[i]:+.2f} m)",
                               f"limit {c['height_tol_m']} m off bridges" + ("" if nav else "; not in the navigation graph")))
            break  # one per road: the worst is not needed to find the road
    return out


# ------------------------------------------------------------------ 5. object budget

def load_budget(path: Path | None) -> dict:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {}


def level_counts(level: Level, zip_bytes: int | None = None) -> dict:
    statics = sum(1 for o in level.objects if o.get("class") == "TSStatic")
    tri = re.search(r"([\d,]+) triangles", level.report_text)
    counts = {"objects": level.object_count,
              "decal_roads": len(level.roads), "statics": statics,
              "terrain_px": int(level.terrain.heights.shape[0]) if level.terrain else 0,
              "building_triangles": int(tri.group(1).replace(",", "")) if tri else 0}
    if zip_bytes is not None:
        counts["zip_mb"] = round(zip_bytes / 1e6, 1)
    return counts


def check_budget(level: Level, budget: dict, zip_bytes: int | None = None) -> tuple[list[dict], dict]:
    counts = level_counts(level, zip_bytes)
    limits = {k: v for k, v in budget.items() if not k.startswith("_")}
    out = []
    for key, limit in limits.items():
        if key not in counts:
            out.append(finding("check-not-run", where=f"budget {key}", severity="medium",
                               detail=f"no count for budget item {key}"))
            continue
        if counts[key] > limit:
            out.append(finding("over-budget", where=key, severity="high", value=counts[key] / limit,
                               metric=f"{key} {counts[key]} over the limit {limit}",
                               detail="limits are in config/static_budget.json (provisional until T-701)"))
    if not limits:
        out.append(finding("check-not-run", where="budget", severity="medium", detail="no budget file"))
    return out, counts


# ------------------------------------------------------------------ 6. buildings in the lanes

def _no(v) -> bool:
    return v is None or str(v).strip().lower() in ("", "no")


def road_counts_against_buildings(tags: dict) -> bool:
    """MapNG's rule (`buildingRoadConflicts.js:roadCountsAgainstBuildings`): tunnels, covered ways, bridges,
    other layers, driveways and parking aisles may legitimately pass under or end inside a building."""
    hw = tags.get("highway")
    if not hw or tags.get("area") == "yes":
        return False
    if hw == "service" and tags.get("service") in ("driveway", "parking_aisle", "drive-through"):
        return False
    if not (_no(tags.get("tunnel")) and _no(tags.get("covered")) and _no(tags.get("bridge"))):
        return False
    if tags.get("location") == "underground" or tags.get("indoor") == "yes":
        return False
    try:
        layer = float(tags.get("layer"))
        if layer != 0:
            return False
    except (TypeError, ValueError):
        pass
    return True


def _roads_that_count(level: Level):
    """Lane roads minus those that follow an OSM way that may pass a building (no OSM: all of them)."""
    roads = lane_roads(level)
    ways = list(drivable_osm_roads(level))
    if not ways:
        return roads
    idx = LineIndex([f.xy for f in ways])
    keep = []
    for item in roads:
        r = item[0]
        ln = LineString(dedupe_points(r.xy))
        mid = ln.interpolate(0.5, normalized=True)
        s = ln.project(mid)
        a, b = ln.interpolate(max(0.0, s - 1.0)), ln.interpolate(min(ln.length, s + 1.0))
        v = np.array([b.x - a.x, b.y - a.y])
        hit = idx.nearest_parallel(mid.x, mid.y, v / np.hypot(*v), 10.0) if np.hypot(*v) else None
        if hit is not None and not road_counts_against_buildings(ways[hit[0]].tags):
            continue
        keep.append(item)
    return keep


def check_buildings(level: Level, c: dict, mode: str = "mesh") -> tuple[list[dict], dict]:
    """Buildings reaching into the driving lanes, by area.

    mode `mesh`: the footprints the export built (roof faces of the tile collision meshes). mode `osm`: the OSM
    outlines (what MapNG would start from; a bigger list, since MapNG cuts some back). `auto` is mesh with
    OSM as the fallback when the level has no building meshes."""
    counted = _roads_that_count(level)
    nav_cor = lane_corridor([i for i in counted if i[2]], shrink=c["lane_margin_m"])
    # service roads and driveways are drawn but carry no test route: reported, one notch lower
    other_cor = lane_corridor([i for i in counted if not i[2]], shrink=c["lane_margin_m"])
    other_cor = other_cor.difference(nav_cor) if not other_cor.is_empty else other_cor
    obs = osm_buildings(level)
    tree = STRtree([p for _, p in obs]) if obs else None
    info = {"mode": mode}
    polys = None
    wall_segs = None
    if mode in ("mesh", "auto"):
        tris, wall_segs, skipped = exported_building_geometry(level)
        info["tiles_without_colmesh"] = len(skipped)
        if len(tris):
            n = len(tris)
            ring = np.concatenate([tris, tris[:, :1]], axis=1)       # close the ring
            polys = shapely.polygons(ring)
            polys = polys[~shapely.is_empty(polys)]
            info["mode"] = "mesh"
            info["triangles"] = int(n)
        elif mode == "mesh":
            return [finding("check-not-run", where="buildings", severity="medium",
                            detail="no building tile meshes with a Colmesh in the level")], info
    if polys is None:
        polys = np.array([p for _, p in obs], dtype=object)
        wall_segs = None
        info["mode"] = "osm"
    out = []
    for nav, corridor in ((True, nav_cor), (False, other_cor)):
        pieces = overlay_in_lanes(polys, corridor, c["building_min_area_m2"], c["building_min_depth_m"], walls=wall_segs)
        for pc in pieces:
            g = pc["geom"]
            f, _a = attribute(g, obs, tree)
            c0 = g.representative_point()
            name = f"OSM building {way_id(f.id)}" if f is not None else "building mesh (no OSM outline near)"
            sev = "high" if pc["area"] >= c["building_high_area_m2"] or pc["depth"] >= 1.0 else "medium"
            if not nav:
                sev = "medium" if sev == "high" else "low"
            out.append(finding("building-in-lane", c0.x, c0.y, name, sev, pc["area"],
                               f"{pc['area']:.1f} m2 inside the driving lanes, up to {pc['depth']:.2f} m deep",
                               f"source {info['mode']}; lanes shrunk by {c['lane_margin_m']} m"
                               + (f"; {pc['wall_m']:.1f} m of wall in the lanes" if pc.get("wall_m") is not None else "")
                               + ("" if nav else "; lane of a service road or driveway (not in the navigation graph)")))
    info["found"] = len(out)
    return out, info


# ------------------------------------------------------------------ 7. terrain steps

STEP_DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))      # (d row, d col): x, y and both diagonals


def step_rasters(heights: np.ndarray) -> list[np.ndarray]:
    """Second difference of the ground over 1 m, per vertex, one raster per direction in STEP_DIRECTIONS."""
    h = heights.astype(np.float64)
    n, m = h.shape
    p = np.pad(h, 1, mode="edge")
    out = []
    for dy, dx in STEP_DIRECTIONS:
        a = p[1 + dy: 1 + dy + n, 1 + dx: 1 + dx + m]
        b = p[1 - dy: 1 - dy + n, 1 - dx: 1 - dx + m]
        out.append(np.abs(a - 2 * h + b))
    return out


def step_raster(heights: np.ndarray) -> np.ndarray:
    """Largest second difference of the ground over 1 m in four directions (x, y and both diagonals), per vertex.

    A smooth slope, even a steep one, gives 0; a step of height h across one cell gives h in the direction it is
    crossed. Looking in four directions means a step is found whatever way it runs relative to the road."""
    return np.maximum.reduce(step_rasters(heights))


def check_terrain_steps(level: Level, c: dict) -> list[dict]:
    """Height steps in the ground under the driving lanes, off bridges.

    The ground is the exported terrain (the game's heightfield; DecalRoads are draped on it). The lanes are the
    drawn `_ai` carriageways at their drawn width (`geometry.lane_roads`). A step is flagged where the second
    difference over 1 m exceeds `step_limit_m` (T-413: 0.15 m) at a terrain vertex whose 1 m stencil lies wholly inside the lanes (kerbs and banks at the lane edge do not count); flagged
    vertices are merged into one finding per `step_cell_m` (the worst one). Roads next to a bridge or tunnel
    mapped in OSM, or a bridge shape, are skipped: a deck is meant to differ from the ground."""
    if level.terrain is None:
        return [finding("check-not-run", where="terrain-step", severity="medium", detail="no terrain in the level")]
    ter = level.terrain
    roads = lane_roads(level)
    if not roads:
        return []
    corridor = lane_corridor(roads)
    shapely.prepare(corridor)
    # A step counts only where the whole stencil (the vertex and both neighbours it is measured against) lies
    # inside the lanes. A kerb or bank beside the lane has one end outside and is not a step a car drives over.
    best: dict[tuple[int, int], float] = {}
    for (dy, dx), d in zip(STEP_DIRECTIONS, step_rasters(ter.heights)):
        cand = np.argwhere(d > c["step_limit_m"])                   # (row, col) of vertices over the limit
        if not len(cand):
            continue
        inside = np.ones(len(cand), dtype=bool)
        for k in (0, 1, -1):
            px = ter.origin[0] + (cand[:, 1] + k * dx) * ter.square
            py = ter.origin[1] + (cand[:, 0] + k * dy) * ter.square
            inside &= shapely.contains_xy(corridor, px, py)
        for (r, q) in cand[inside]:
            v = float(d[r, q])
            if v > best.get((int(r), int(q)), 0.0):
                best[(int(r), int(q))] = v
    if not best:
        return []
    cand = np.array(list(best.keys()))
    vals = np.array(list(best.values()))
    xs = ter.origin[0] + cand[:, 1] * ter.square
    ys = ter.origin[1] + cand[:, 0] * ter.square
    order = np.argsort(-vals)
    bridge_idx, bridge_objs = _bridge_zones(level, c)
    # which road each flagged vertex belongs to (nearest lane road), for the name and the navigation flag
    idx = LineIndex([r.xy for r, _h, _n in roads])
    kept: list[tuple] = []
    cell = c["step_cell_m"]
    grid: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    for i in order:
        x, y, v = float(xs[i]), float(ys[i]), float(vals[i])
        cx, cy = int(x // cell), int(y // cell)
        if any(math.hypot(x - kx, y - ky) < cell for dx in (-1, 0, 1) for dy in (-1, 0, 1)
               for _, kx, ky, _, _ in grid[(cx + dx, cy + dy)]):
            continue
        if _on_bridge(x, y, bridge_idx, bridge_objs, c):
            continue
        hit = idx.nearest(x, y, 15.0)
        name, nav = (roads[hit[0]][0].name, roads[hit[0]][2]) if hit else ("", True)
        grid[(cx, cy)].append((v, x, y, name, nav))
        kept.append((v, x, y, name, nav))

    def sev(v, nav):
        s = "high" if v >= c["step_high_m"] else "medium"
        if not nav:
            s = "medium" if s == "high" else "low"        # service roads and driveways carry no test route
        return s

    return [finding("terrain-step", x, y, name, sev(v, nav), v,
                    f"{v * 100:.0f} cm step in the ground under the lanes",
                    f"second difference over 1 m; limit {c['step_limit_m'] * 100:.0f} cm"
                    + ("" if nav else "; road not in the navigation graph"))
            for v, x, y, name, nav in kept]


# ------------------------------------------------------------------ 7b. kerb heights (I-006)

def _road_z_at(nodes: np.ndarray, x: float, y: float) -> float:
    """A DecalRoad's height (nodes x, y, z, width) at its point nearest (x, y)."""
    a, b = nodes[:-1, :3], nodes[1:, :3]
    if not len(a):
        return float(nodes[0, 2])
    d = b[:, :2] - a[:, :2]
    ll = np.einsum("ij,ij->i", d, d)
    t = np.clip(np.einsum("ij,ij->i", np.array([x, y]) - a[:, :2], d) / np.where(ll > 0, ll, 1.0), 0.0, 1.0)
    q = a + (b - a) * t[:, None]
    k = int(np.argmin(np.hypot(q[:, 0] - x, q[:, 1] - y)))
    return float(q[k, 2])


def _ground_beside(level: Level, line, nodes: np.ndarray, q: np.ndarray, u: np.ndarray, c: dict) -> float:
    """Height of the ground a wheel on the lane stands on at a point `q` of a kerb face running along `u`.

    DecalRoads have no collision: a wheel rolls on the terrain, and a drawn road's z is its centreline height,
    which on a cross-sloped road or a side road's mouth is not the ground at its edge (I-005: a 6 m service road's
    centreline 0.12 m under the main road's gutter at (635, 172)). So the ground is the terrain `kerb_side_m` off
    the face on the lane's side (the side nearer the lane's centreline; the lower of the two when the face runs
    across the lane, so a wheel meets it end on). The decal's z is used where there is no terrain, or where the
    terrain is more than `kerb_deck_m` off the decal (a bridge deck)."""
    road_z = _road_z_at(nodes, float(q[0]), float(q[1]))
    t = level.terrain
    if t is None:
        return road_z
    n = np.array([-u[1], u[0]]) * c["kerb_side_m"]
    pts = np.array([q + n, q - n])
    g = t.height(pts[:, 0], pts[:, 1])
    if np.isnan(g).any():
        return road_z
    dist = shapely.distance(line, shapely.points(pts))
    if abs(dist[0] - dist[1]) > c["kerb_side_m"] * 2 / 3:     # the face runs along the lane: its lane side
        ground = float(g[int(np.argmin(dist))])
    else:                                                    # it runs across the lane: the lower side
        ground = float(g.min())
    return road_z if abs(ground - road_z) > c["kerb_deck_m"] else ground


def _in_reach(seg: np.ndarray, roads, idx: LineIndex, reach: float, cache: dict) -> list[float]:
    """Positions (0 to 1 along the face) of the ends of the parts of a face within `reach` of a lane.

    Only those parts stand beside a lane: a face that starts at a dead end's corner and climbs on past it is
    measured where it is within reach, not 1 m further on (I-005: a footway carrying on uphill from the end of a
    service road at (-277, 821))."""
    face = shapely.LineString(seg)
    near = []
    for k in idx.tree.query(face, predicate="dwithin", distance=reach + 15.0):
        ri = idx.valid[int(k)]
        if ri not in cache:
            r, half, _nav = roads[ri]
            cache[ri] = idx.lines[ri].buffer(half, cap_style="flat", join_style="round").buffer(reach) if half > 0 else None
        if cache[ri] is not None:
            near.append(cache[ri])
    if not near:
        return []
    part = face.intersection(shapely.unary_union(near))
    length = max(face.length, 1e-9)
    out = []
    for g in getattr(part, "geoms", [part]):
        if isinstance(g, shapely.LineString) and not g.is_empty:
            out += [face.project(shapely.Point(xy)) / length for xy in (g.coords[0], g.coords[-1])]
    return out


def check_kerb_heights(level: Level, c: dict) -> list[dict]:
    """Kerb faces of the sidewalk collision meshes taller than `kerb_limit_m` at the edge of a drivable lane.

    A face is the vertical part of a sidewalk tile's Colmesh (`kerbs.kerb_faces`). It counts when its ground
    segment lies within `kerb_reach_m` of the drivable lanes (`geometry.lane_roads`, at drawn width; a face inside
    the lanes counts too). Its height is what a wheel on the road meets: its top at each end over the ground on the
    nearest lane's side there (`_ground_beside`), but never more than the face itself (MapNG sinks faces below the
    road: a walkway's edge reaches 0.30 m down, so its 0.40 m face shows 0.10 to 0.15 m; I-005). A face still over
    the limit is measured again on its parts within `kerb_reach_m` of a lane only (`_in_reach`). Faces are merged into
    one finding per `kerb_cell_m` (the tallest). I-004 found the front wheel of a car on a one-lane slip striking
    a face 0.40 m over the road."""
    segs, heights, tops, skipped = level_kerb_faces(level, with_tops=True)
    if not len(segs):
        return [finding("check-not-run", where="kerb-height", severity="medium",
                        detail=f"no readable sidewalk collision mesh in the level ({len(skipped)} tile(s) unreadable)")]
    roads = lane_roads(level)
    if not roads:
        return []
    tall = heights > c["kerb_limit_m"]          # a face no taller than the limit cannot stand over it
    segs, heights, tops = segs[tall], heights[tall], tops[tall]
    if not len(segs):
        return []
    lines = shapely.linestrings(segs)
    reach = c["kerb_reach_m"]
    tree = STRtree(lines)
    nav_cor = lane_corridor([i for i in roads if i[2]])
    other_cor = lane_corridor([i for i in roads if not i[2]])
    near_nav = np.zeros(len(lines), dtype=bool)
    near_other = np.zeros(len(lines), dtype=bool)
    for cor, flag in ((nav_cor, near_nav), (other_cor, near_other)):
        if not cor.is_empty:
            # one buffer and a tree query: shapely.dwithin / intersects of every face against the big union takes minutes
            flag[tree.query(cor.buffer(reach), predicate="intersects")] = True
    keep = near_nav | near_other
    if not keep.any():
        return []
    ks = segs[keep]
    mids = ks.mean(axis=1)
    hs = heights[keep]
    nav = near_nav[keep]
    idx = LineIndex([r.xy for r, _h, _n in roads])
    tk = tops[keep]
    strips: dict = {}
    for i, (x, y) in enumerate(mids):
        hit = idx.nearest(float(x), float(y), 15.0)
        if not hit:
            continue
        line, nodes = idx.lines[hit[0]], roads[hit[0]][0].nodes
        a, b = ks[i]
        u = (b - a) / max(float(np.hypot(*(b - a))), 1e-9)
        top = max(tk[i, 0] - _ground_beside(level, line, nodes, a, u, c), tk[i, 1] - _ground_beside(level, line, nodes, b, u, c))
        if min(hs[i], top) > c["kerb_limit_m"]:           # over the limit: measure only the part beside a lane
            top = max((tk[i, 0] + (tk[i, 1] - tk[i, 0]) * f - _ground_beside(level, line, nodes, a + (b - a) * f, u, c)
                       for f in _in_reach(ks[i], roads, idx, reach, strips)), default=0.0)
        hs[i] = min(hs[i], top)
    over = hs > c["kerb_limit_m"]
    mids, hs, nav = mids[over], hs[over], nav[over]
    cell = c["kerb_cell_m"]
    grid: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    kept: list[tuple] = []
    for i in np.argsort(-hs, kind="stable"):
        x, y, h = float(mids[i, 0]), float(mids[i, 1]), float(hs[i])
        cx, cy = int(x // cell), int(y // cell)
        if any(math.hypot(x - kx, y - ky) < cell for dx in (-1, 0, 1) for dy in (-1, 0, 1)
               for kx, ky, _ in grid[(cx + dx, cy + dy)]):
            continue
        grid[(cx, cy)].append((x, y, h))
        hit = idx.nearest(x, y, 15.0)
        kept.append((h, x, y, roads[hit[0]][0].name if hit else "", bool(nav[i])))

    def sev(h, is_nav):
        s = "high" if h >= c["kerb_high_m"] else "medium"
        if not is_nav:
            s = "medium" if s == "high" else "low"        # service roads and driveways carry no test route
        return s

    return [finding("kerb-height", x, y, name, sev(h, is_nav), h, "m",
                    f"{h * 100:.0f} cm kerb face over the road within {reach} m of a drivable lane edge "
                    f"(sidewalk collision mesh; limit {c['kerb_limit_m'] * 100:.0f} cm, UK kerbs 10 to 12.5 cm)"
                    + ("" if is_nav else "; road not in the navigation graph"))
            for h, x, y, name, is_nav in kept]


# ------------------------------------------------------------------ 8. stock assets

ASSET_EXT = (".dae", ".cdae", ".dds", ".png", ".jpg", ".jpeg", ".json", ".cs", ".prefab", ".ter", ".forest4.json")
ASSET_ROOTS = ("assets/", "art/", "levels/", "vehicles/", "core/", "cookedassets/")
_PATHISH = re.compile(r"^/?[A-Za-z0-9_\-./ ()+']+$")


def _walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def referenced_paths(level: Level):
    """(file in the zip, referenced path, position or None) for every asset path in the level's JSON."""
    names = level.zf.namelist()
    for n in names:
        if not n.endswith(".json") or n.endswith("info.json"):
            continue
        if n.endswith("terrain.terrain.json") or n.endswith("mapng.json") or n.endswith("mapng_probes.json"):
            continue
        text = level.zf.read(n).decode("utf-8", "replace")
        docs = []
        if n.endswith("items.level.json") or n.endswith(".forest4.json"):
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        docs.append(json.loads(line))
                    except ValueError:
                        pass
        else:
            try:
                docs.append(json.loads(text))
            except ValueError:
                continue
        for d in docs:
            pos = d.get("position") if isinstance(d, dict) else None
            for s in _walk_strings(d):
                if len(s) > 400 or not _PATHISH.match(s):
                    continue
                rel = s.lstrip("/")
                if rel.lower().endswith(ASSET_EXT) and rel.lower().startswith(ASSET_ROOTS):
                    yield n, rel, pos


def check_stock_assets(level: Level, index: GameIndex) -> tuple[list[dict], dict]:
    """Assets the level points at that exist neither in the zip nor in the game (they load as nothing, silently)."""
    inzip = {n.lower() for n in level.zf.namelist()}

    def in_zip(rel: str) -> bool:
        r = rel.lower()
        alts = [r, r + ".link"]
        for a, bs in ((".png", (".dds", ".jpg")), (".dds", (".png", ".jpg")), (".jpg", (".dds", ".png")),
                      (".dae", (".cdae",)), (".cdae", (".dae",))):
            if r.endswith(a):
                alts += [r[: -len(a)] + b for b in bs]
        return any(a in inzip for a in alts)

    missing: dict[str, dict] = {}
    checked = set()
    stock = 0
    for file, rel, pos in referenced_paths(level):
        key = rel.lower()
        if key in checked:
            if key in missing:
                missing[key]["count"] += 1
            continue
        checked.add(key)
        if in_zip(rel):
            continue
        if not rel.lower().startswith(level.base.lower()) and index.has(rel):
            stock += 1
            continue
        missing[key] = {"path": rel, "file": file, "pos": pos, "count": 1}
    # materials of the roads
    defined = set()
    for n in level.zf.namelist():
        if n.endswith("materials.json"):
            try:
                doc = json.loads(level.zf.read(n).decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(doc, dict):
                for k, v in doc.items():
                    defined.add(k)
                    if isinstance(v, dict):
                        for fld in ("name", "mapTo", "internalName"):
                            if isinstance(v.get(fld), str):
                                defined.add(v[fld])
    out = []
    for m in missing.values():
        p = m["pos"]
        dangling = index.dangling(m["path"])
        out.append(finding("missing-stock-asset", p[0] if p else None, p[1] if p else None, m["path"],
                           "low" if dangling else "high", m["count"], f"{m['path']} (used {m['count']} time(s))",
                           ("also missing in the game: its own materials reference it; " if dangling else
                            "not in the level zip and not in the game files; ") + f"referenced from {m['file']}"))
    mats: dict[str, DecalRoadRef] = {}
    for r in level.roads:
        if r.material and r.material not in defined and not index.has_material(r.material) and r.material not in mats:
            mats[r.material] = DecalRoadRef(r.name, r.nodes[0])
    for name, ref in mats.items():
        out.append(finding("missing-stock-material", ref.node[0], ref.node[1], name, "high", 1,
                           f"DecalRoad material {name} is defined neither in the level nor by the game",
                           f"first used by {ref.road}"))
    return out, {"checked_paths": len(checked), "stock_found": stock, "missing": len(missing) + len(mats)}


class DecalRoadRef:
    def __init__(self, road, node):
        self.road, self.node = road, node
