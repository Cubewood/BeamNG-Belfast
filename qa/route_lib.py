"""Road graph, route resolution and validation for the drive suite (T-302). Pure logic, no game.

The graph is the game's own navgraph (`map.getMap()`), dumped once per level by
`drive_suite.py dump-graph`. A static graph rebuilt from the exported DecalRoads is NOT used: the
roads are separate polylines that only join where the game merges them (the DecalRoad layer alone
splits into several islands, the game's graph is one network). Street names come from the node
names (`dr_<Street>_<n>_s1_ai<k>`, taken from OSM); signals, roundabouts and give-way points come
from the OSM features MapNG fetched for the slice.
"""
from __future__ import annotations

import heapq
import json
import math
import re
from collections import defaultdict
from pathlib import Path

CELL_M = 25.0        # spatial index cell
FEATURE_M = 30.0     # an OSM signal / roundabout / give-way this near a junction belongs to it
PATH_MATCH_M = 3.0   # a route path may stray this far from the graph route through its via points
TURN_MIN_DEG = 35.0  # heading change at a junction counted as a turn
EARTH_M_PER_DEG = 111320.0
M_PER_DEG_LAT = 110574.0   # fits MapNG's world to about 1 m (median 0.8 m, T-302 check)

_NAME_RE = re.compile(r"^dr_(.*?)(?:_\d+)?_s\d+_(?:ai|b)\d*$")


def latlon_to_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    return ((lon - lon0) * EARTH_M_PER_DEG * math.cos(math.radians(lat0)), (lat - lat0) * M_PER_DEG_LAT)


def xy_to_latlon(x: float, y: float, lat0: float, lon0: float) -> tuple[float, float]:
    return (lat0 + y / M_PER_DEG_LAT, lon0 + x / (EARTH_M_PER_DEG * math.cos(math.radians(lat0))))


def street_name(node_name: str) -> str:
    """Street of a game graph node or DecalRoad name: `dr_Hospital_Road_5_s1_ai12` -> `Hospital Road`."""
    m = _NAME_RE.match(node_name)
    name = (m.group(1) if m else node_name).replace("_s_", "'s_").replace("_", " ")
    um = re.match(r"^road (\d+) seg(?: \d+)*$", name)
    if um:
        return f"unnamed way {um.group(1)}"
    if name.startswith("autojunction"):
        return "junction " + name.split(" ", 1)[1] if " " in name else "junction"
    return name


def load_osm_features(osm_json: Path, lat0: float, lon0: float) -> list[dict]:
    """Signals, give-way points and roundabouts as [{kind, x, y, lat, lon}] in world metres."""
    out = []
    for f in json.loads(Path(osm_json).read_text(encoding="utf-8")):
        tags = f.get("tags", {})
        kind = None
        if f.get("type") == "street_furniture" and tags.get("highway") in ("traffic_signals", "give_way", "stop"):
            kind = {"traffic_signals": "signals", "give_way": "give_way", "stop": "stop"}[tags["highway"]]
        elif f.get("type") == "road" and tags.get("junction") == "roundabout":
            kind = "roundabout"
        if not kind or not f.get("geometry"):
            continue
        g = f["geometry"]
        lat = sum(p["lat"] for p in g) / len(g)
        lon = sum(p["lng"] for p in g) / len(g)
        x, y = latlon_to_xy(lat, lon, lat0, lon0)
        out.append({"kind": kind, "x": x, "y": y, "lat": lat, "lon": lon})
    return out


def dist2(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Graph:
    """Directed road graph of the game (`map.getMap()` nodes and links, dumped by
    `drive_suite.py dump-graph`). nodes: {name: (x, y, z)}; adj[name] = [(other, length_m)].

    The game, not the DecalRoads, decides what is connected: roads are merged and junctions are
    made when the navgraph is built, so the AI can only drive what is in this graph."""

    def __init__(self, dump: dict):
        self.nodes: dict[str, tuple[float, float, float]] = {}
        self.radius: dict[str, float] = {}
        self.adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
        self._cell: dict[tuple[int, int], list[str]] = defaultdict(list)
        for name, x, y, z, r in dump["nodes"]:
            self.nodes[name] = (x, y, z)
            self.radius[name] = r
            self._cell[(int(x // CELL_M), int(y // CELL_M))].append(name)
        self.one_way_edges = 0
        for name, other, one_way, name_is_in, *_rest in dump["links"]:
            if name not in self.nodes or other not in self.nodes:
                continue
            length = dist2(self.nodes[name], self.nodes[other])
            if not one_way:
                self.adj[name].append((other, length))
                self.adj[other].append((name, length))
            else:
                self.one_way_edges += 1
                a, b = (name, other) if name_is_in else (other, name)
                self.adj[a].append((b, length))

    def _near(self, p, r) -> list[str]:
        n = int(r // CELL_M) + 1
        cx, cy = int(p[0] // CELL_M), int(p[1] // CELL_M)
        return [i for dx in range(-n, n + 1) for dy in range(-n, n + 1)
                for i in self._cell.get((cx + dx, cy + dy), ()) if dist2(self.nodes[i], p) <= r]

    def nearest(self, x: float, y: float, limit: float = 60.0) -> tuple[str, float] | None:
        best = None
        r = CELL_M
        while r <= limit * 2 and best is None:
            for i in self._near((x, y), min(r, limit)):
                d = dist2(self.nodes[i], (x, y))
                if best is None or d < best[1]:
                    best = (i, d)
            if r >= limit:
                break
            r *= 2
        return best if best and best[1] <= limit else None

    def undirected(self) -> dict[str, set[str]]:
        und: dict[str, set[str]] = defaultdict(set)
        for a, lst in self.adj.items():
            for b, _ in lst:
                und[a].add(b)
                und[b].add(a)
        return und

    def components(self) -> list[set[str]]:
        und = self.undirected()
        seen: set[str] = set()
        comps = []
        for s in list(und):
            if s in seen:
                continue
            comp, st = {s}, [s]
            seen.add(s)
            while st:
                u = st.pop()
                for v in und[u]:
                    if v not in seen:
                        seen.add(v)
                        comp.add(v)
                        st.append(v)
            comps.append(comp)
        return sorted(comps, key=len, reverse=True)

    def shortest(self, a: str, b: str) -> list[str] | None:
        if a == b:
            return [a]
        dist = {a: 0.0}
        prev: dict[int, int] = {}
        pq = [(0.0, a)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == b:
                path = [b]
                while path[-1] != a:
                    path.append(prev[path[-1]])
                return path[::-1]
            if d > dist.get(u, 1e18):
                continue
            for v, length in self.adj.get(u, ()):
                nd = d + length
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        return None

def path_length(pts) -> float:
    return sum(dist2(a, b) for a, b in zip(pts, pts[1:]))


def dist_to_polyline(p, poly) -> float:
    best = 1e18
    for a, b in zip(poly, poly[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
        best = min(best, math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy))
    return best if len(poly) > 1 else dist2(p, poly[0])


def densify(pts, spacing: float) -> list[tuple[float, float, float]]:
    """Insert points so no two neighbours are more than `spacing` metres apart (graph nodes are up
    to ~70 m apart on straight roads). Height is interpolated."""
    out = [tuple(pts[0])]
    for a, b in zip(pts, pts[1:]):
        n = max(1, math.ceil(dist2(a, b) / spacing))
        for k in range(1, n + 1):
            t = k / n
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])))
    return out


def resolve_via(g: Graph, vias: list[dict]) -> list[str]:
    """Graph node path through the via points in order (shortest legs). Raises ValueError if a leg
    has no path or a via point is more than 60 m from any road."""
    ids = []
    for v in vias:
        near = g.nearest(v["x"], v["y"])
        if not near:
            raise ValueError(f"via {v.get('name')!r} at ({v['x']}, {v['y']}) is not near any drivable road")
        ids.append(near[0])
    full: list[str] = []
    for a, b in zip(ids, ids[1:]):
        leg = g.shortest(a, b)
        if leg is None:
            raise ValueError(f"no drivable path between via points {a} and {b}")
        full += leg if not full else leg[1:]
    return full


def turn_angle(p0, p1, p2) -> float:
    """Heading change at p1 in degrees, positive = left (x east, y north)."""
    ax, ay = p1[0] - p0[0], p1[1] - p0[1]
    bx, by = p2[0] - p1[0], p2[1] - p1[1]
    return math.degrees(math.atan2(ax * by - ay * bx, ax * bx + ay * by))


def junctions_on_path(g: Graph, path: list[str], features: list[dict]) -> list[dict]:
    """Junctions the path passes: nodes with 3+ neighbours. Each gets the streets in and out, the
    turn (left/right/straight with angle) and nearby OSM features (signals, give_way, roundabout)."""
    und = g.undirected()
    cum = [0.0]
    for a, b in zip(path, path[1:]):
        cum.append(cum[-1] + dist2(g.nodes[a], g.nodes[b]))
    out = []
    last_at = -1e9
    for k in range(1, len(path) - 1):
        i = path[k]
        nb = len(und[i])
        s_in = street_name(path[k - 1])
        s_out = street_name(path[k + 1])
        if nb < 3:
            continue
        if cum[k] - last_at < 12.0:
            continue
        kb = k
        while kb > 0 and cum[k] - cum[kb] < 15:
            kb -= 1
        kf = k
        while kf < len(path) - 1 and cum[kf] - cum[k] < 15:
            kf += 1
        ang = turn_angle(g.nodes[path[kb]], g.nodes[i], g.nodes[path[kf]])
        turn = "left" if ang > TURN_MIN_DEG else "right" if ang < -TURN_MIN_DEG else "straight"
        feats = sorted({f["kind"] for f in features if dist2((f["x"], f["y"]), g.nodes[i]) <= FEATURE_M})
        out.append({"x": round(g.nodes[i][0], 1), "y": round(g.nodes[i][1], 1), "at_m": round(cum[k]),
                    "from": s_in, "to": s_out, "turn": turn, "angle": round(ang), "features": feats,
                    "branches": nb})
        last_at = cum[k]
    return out


def build_route(g: Graph, spec: dict, features: list[dict], lat0: float, lon0: float,
                spacing: float = 10.0) -> dict:
    """Derived fields (path, length, junctions) for a route spec {id, name, description, via[]}."""
    node_path = resolve_via(g, spec["via"])
    pts = [g.nodes[i] for i in node_path]
    path = [[round(q[0], 1), round(q[1], 1), round(q[2], 2)] for q in densify(pts, spacing)]
    jn = junctions_on_path(g, node_path, features)
    turns = [j for j in jn if j["turn"] != "straight"]
    via = []
    for v in spec["via"]:
        lat, lon = xy_to_latlon(v["x"], v["y"], lat0, lon0)
        via.append({**v, "lat": round(lat, 6), "lon": round(lon, 6)})
    return {
        "id": spec["id"], "name": spec["name"], "description": spec.get("description", ""),
        "via": via,
        "length_m": round(path_length(pts)),
        "streets": list(dict.fromkeys(street_name(n) for n in node_path)),
        "junctions": jn,
        "counts": {"left": sum(1 for j in turns if j["turn"] == "left"),
                   "right": sum(1 for j in turns if j["turn"] == "right"),
                   "signals": sum(1 for j in jn if "signals" in j["features"]),
                   "roundabouts": sum(1 for j in jn if "roundabout" in j["features"])},
        "path": path,
    }


def validate_routes(doc: dict, g: Graph | None = None) -> list[str]:
    """Problems found in a routes document (empty list = valid). With a graph, every via point must
    be on the drivable network and every consecutive pair of via points connected by a path."""
    errs: list[str] = []
    if not isinstance(doc.get("routes"), list) or not doc["routes"]:
        return ["no routes"]
    seen = set()
    for r in doc["routes"]:
        rid = r.get("id", "?")
        if rid in seen:
            errs.append(f"{rid}: duplicate id")
        seen.add(rid)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(rid)):
            errs.append(f"{rid}: id must use only A-Z a-z 0-9 _ -")
        missing = [k for k in ("name", "via", "path", "length_m", "junctions") if k not in r]
        if missing:
            errs.append(f"{rid}: missing {', '.join(missing)}")
            continue
        if len(r["via"]) < 2:
            errs.append(f"{rid}: needs at least 2 via points")
        for v in r["via"]:
            if not all(isinstance(v.get(k), (int, float)) for k in ("x", "y")):
                errs.append(f"{rid}: via point without x, y")
        p = r["path"]
        if len(p) < 2 or any(len(q) < 3 for q in p):
            errs.append(f"{rid}: path needs at least 2 [x, y, z] points")
            continue
        gap = max(dist2(a, b) for a, b in zip(p, p[1:]))
        if gap > 60:
            errs.append(f"{rid}: path has a {gap:.0f} m gap between points")
        plen = path_length(p)
        if abs(plen - r["length_m"]) > max(5, 0.03 * plen):
            errs.append(f"{rid}: length_m {r['length_m']} does not match path ({plen:.0f} m)")
        if g is not None:
            try:
                expect = [g.nodes[n] for n in resolve_via(g, r["via"])]
            except ValueError as e:
                errs.append(f"{rid}: {e}")
                continue
            worst = max(dist_to_polyline(q, expect) for q in p)
            if worst > PATH_MATCH_M:
                errs.append(f"{rid}: path is {worst:.0f} m from the graph route through its via points "
                            f"(graph changed or path edited: run build_routes.py rebuild)")
    return errs
