"""Junction layout (T-404, docs/junctions.md): Give Way, Stop and roundabout Give Way lines at their mouths, signal Stop
lines and their heads, kerb radii at junction corners. No game, no MapNG code: the level zip and the OSM it was built
from.

Which arm of a junction gives way is decided again here, from OSM, by the same rule the MapNG patch uses
(`services/ukJunctions.js`): road class, then the road carrying on (name or ref), then the straightest pair of arms;
every entry onto a roundabout gives way to the ring. The lines themselves are read from the level: DecalRoads named
`jm_*` in the UK line materials; the carriageways they meet are the drawn road bodies (kerb to kerb).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from .geometry import dedupe_points, finding
from .level import Level

# Tolerances. Sources: rules/uk_markings.yaml (stop_1002_1, giveway_1003a, giveway_roundabout_1003_1,
# stop_signals_1001) and Creating Places (DOE NI, 2000) Table 4 and para 16.27 for kerb radii. "ours" = a tolerance
# we chose, not a figure from a manual.
JUNCTION_TOL = {
    # TSM 3 2.3.1 to 2.3.4, Figure 2-5: the line's edge nearest the major road continues that road's edge
    "mouth_gap_min_m": -0.05,     # less: the line lies on the road it meets
    "mouth_gap_max_m": 1.0,       # ours: more is off the mouth
    # TSM 6 4.2.1: a signal Stop line at right angles to the centre line; ours for the others
    "square_deg": 10.0,
    # TSM 3 2.3.2 / TSM 6 4.2.1: kerb to centre line on a two-way road, full width on a one-way road, and full width
    # where kerb to centre is under 2.75 m (Stop and Give Way lines). Ends: MapNG paints 0.3 m inside the kerb and
    # leaves 0.25 m at the centre line; ours: 0.3 m more either way
    "kerb_end_m": 0.6,
    "centre_end_m": 0.6,
    "full_width_under_m": 2.75,
    # signal Stop lines: clear of the crossing carriageway (ours), and TSM 6 4.2.1: at least 1.5 m in front of the
    # near-side primary signal
    "signal_gap_min_m": 0.5,
    "signal_gap_max_m": 15.0,
    "signal_head_min_m": 1.5,
    "head_match_m": 4.0,          # ours: a signal instance this near a signal Stop line is its head
    # Creating Places Table 4 and 16.27: 6 m between access roads (4 m where the minor road serves about 50
    # dwellings or for shared surfaces), 10 m at junctions with local distributor roads
    "radius_min_m": 4.0,
    "radius_max_m": 10.5,
    "corner_turn_deg": (60.0, 120.0),   # Table 4 / 16.26: the minor road enters at 90 degrees; ours: within 30 of it
    "corner_spacing_m": 25.0,           # Table 4: junctions at least 25 m apart (adjacent, centre line to centre line)
    "arm_match_m": 20.0,          # ours: a line on an arm lies within this of the junction node
}

LINE_KINDS = {   # material -> (kind, painted half width m)
    "uk_giveway_1003a_cubewood": ("giveway", 0.35),
    "uk_dash_1000_1000_cubewood": ("roundabout", 0.1),
}
STOP_MATERIAL = "uk_solid_white_cubewood"
STRIP_NAME = re.compile(r"_b\d+$")
ROAD_SKIP = {"footway", "path", "pedestrian", "steps", "cycleway", "bridleway", "corridor", "proposed", "construction"}
RANK = {"motorway": 9, "trunk": 8, "primary": 7, "secondary": 6, "tertiary": 5, "unclassified": 4, "residential": 3,
        "road": 3, "living_street": 2, "service": 1, "track": 0}
MIN_PUBLIC_RANK = 2
MERGE_DEG = 35.0
ARM_DIRECTION_M = 5.0
CORNER_BAND_OFFSET_M = 0.5      # MapNG's first corner band runs 0.5 m outside the kerb arc (cornerPatches)
UK_CORNER_RADII = {"residential": 6, "unclassified": 6, "road": 6, "living_street": 4, "tertiary": 10, "secondary": 10,
                   "primary": 10, "trunk": 10}


def rank(highway) -> int:
    return RANK.get(str(highway or "").removesuffix("_link"), 3)


def oneway_dir(tags: dict) -> int:
    v = str(tags.get("oneway", "")).strip().lower()
    if v in ("-1", "reverse"):
        return -1
    if v in ("yes", "1", "true"):
        return 1
    if v == "no":
        return 0
    if str(tags.get("junction", "")).lower() == "roundabout" or tags.get("highway") in ("motorway", "motorway_link"):
        return 1
    return 0


def is_ring(tags: dict) -> bool:
    return str(tags.get("junction", "")).strip().lower() in ("roundabout", "circular")


def identity(f) -> str:
    t = f.tags
    return (t.get("name") or t.get("ref") or f"way {str(f.id).split('_seg')[0]}").strip().lower()


# ------------------------------------------------------------------ the OSM junction graph and the priority rule

class Graph:
    """OSM road ways joined at shared vertices (MapNG's buildRoadGraph: the same roads, keyed by lat/lng to 7 places)."""

    def __init__(self, level: Level):
        self.xy: dict[str, np.ndarray] = {}
        self.edges: dict[str, list[dict]] = defaultdict(list)
        for f in level.osm:
            t = f.tags
            hw = str(t.get("highway", "")).strip().lower()
            if f.type != "road" or not hw or hw in ROAD_SKIP or str(t.get("area", "")).lower() == "yes" or len(f.xy) < 2:
                continue
            way = oneway_dir(t)
            for i in range(len(f.xy) - 1):
                a = f"{f.lonlat[i][1]:.7f}:{f.lonlat[i][0]:.7f}"
                b = f"{f.lonlat[i + 1][1]:.7f}:{f.lonlat[i + 1][0]:.7f}"
                if a == b:
                    continue
                self.xy.setdefault(a, f.xy[i])
                self.xy.setdefault(b, f.xy[i + 1])
                e = {"a": a, "b": b, "f": f, "fwd": way >= 0, "back": way <= 0}
                self.edges[a].append(e)
                self.edges[b].append(e)

    @staticmethod
    def other(e, k):
        return e["b"] if e["a"] == k else e["a"]

    def walk(self, k, e, max_m):
        """Points out from node k along edge e through plain vertices, up to max_m (or the next junction)."""
        pts = [self.xy[k]]
        n, cur, run = k, e, 0.0
        while cur is not None:
            nxt = self.other(cur, n)
            pts.append(self.xy[nxt])
            run += float(np.hypot(*(pts[-1] - pts[-2])))
            if run >= max_m or len(self.edges[nxt]) != 2:
                break
            n, cur = nxt, next((x for x in self.edges[nxt] if x is not cur), None)
        return np.array(pts)

    def arms(self, k):
        out = []
        for e in self.edges[k]:
            f = e["f"]
            r = rank(f.tags.get("highway"))
            if r < MIN_PUBLIC_RANK:
                continue
            pts = self.walk(k, e, 40.0)
            line = LineString(dedupe_points(pts)) if len(dedupe_points(pts)) > 1 else None
            if line is None:
                continue
            p = line.interpolate(min(ARM_DIRECTION_M, line.length))
            u = np.array([p.x, p.y]) - self.xy[k]
            u = u / (np.hypot(*u) or 1.0)
            inbound = e["fwd"] if e["b"] == k else e["back"]
            outbound = e["fwd"] if e["a"] == k else e["back"]
            out.append({"edge": e, "f": f, "rank": r, "id": identity(f), "u": u, "line": line, "inbound": inbound,
                        "outbound": outbound, "two_way": e["fwd"] and e["back"], "ring": is_ring(f.tags)})
        return out


def priority_at_junction(arms):
    """{priority, give_way, roundabout} for one junction's arms, or None: the rule of services/ukJunctions.js."""
    if len(arms) < 3:
        return None
    if any(a["ring"] for a in arms):
        return {"priority": [a for a in arms if a["ring"]], "give_way": [a for a in arms if not a["ring"] and a["inbound"]],
                "roundabout": True}
    best = None
    for i in range(len(arms)):
        for j in range(i + 1, len(arms)):
            a, b = arms[i], arms[j]
            if not ((a["inbound"] and b["outbound"]) or (b["inbound"] and a["outbound"])):
                continue
            key = (min(a["rank"], b["rank"]), 1 if a["id"] == b["id"] else 0, -float(np.dot(a["u"], b["u"])))
            if best is None or key > best[0]:
                best = (key, a, b)
    if best is None:
        return None
    key, a, b = best
    into = [x for x in (a, b) if x["inbound"]]
    merge_cos = math.cos(math.radians(MERGE_DEG))
    give = []
    for x in arms:
        if x is a or x is b or not x["inbound"]:
            continue
        if not x["two_way"] and x["id"] == a["id"] == b["id"] and x["rank"] == key[0] and (a["two_way"] or b["two_way"]):
            continue
        if not x["two_way"] and any(float(np.dot(p["u"], x["u"])) > merge_cos for p in into):
            continue
        give.append(x)
    return {"priority": [a, b], "give_way": give, "roundabout": False}


# ------------------------------------------------------------------ level data

def carriageways(level: Level):
    """Drawn road bodies kerb to kerb: (line, half width, road). A road paved in strips (`_b1`, `_b2`, ...: paving
    stones, setts) is its `_ai` line, as wide as its strips reach."""
    out = []
    strips = defaultdict(list)
    for r in level.roads:
        if r.is_vehicle_body:
            pts = dedupe_points(r.xy)
            if len(pts) > 1:
                out.append((LineString(pts), float(np.median(r.nodes[:, 3])) / 2.0, r))
        elif STRIP_NAME.search(r.name) and len(r.nodes) > 1:
            strips[STRIP_NAME.sub("_ai", r.name)].append(r)
    for r in level.roads:
        if r.name in strips and r.is_ai:
            pts = dedupe_points(r.xy)
            if len(pts) < 2:
                continue
            line = LineString(pts)
            reach = max(line.distance(Point(*s.nodes[len(s.nodes) // 2, :2])) + float(s.nodes[0, 3]) / 2 for s in strips[r.name])
            out.append((line, reach, r))
    return out


def junction_lines(level: Level):
    """The transverse junction lines: [{kind, a, b, half (painted), name}]."""
    out = []
    for r in level.roads:
        if not r.name.startswith("jm_") or len(r.nodes) < 2:
            continue
        a, b = r.nodes[0, :2], r.nodes[-1, :2]
        if r.material in LINE_KINDS and not r.name.startswith("jm_giveway_triangle"):
            kind, half = LINE_KINDS[r.material]
        elif r.material == STOP_MATERIAL and r.name.startswith("jm_stopline"):
            w = float(r.nodes[0, 3]) * 0.75                          # painted = decal width x 0.75
            kind, half = ("stop" if w > 0.35 else "signal"), w / 2.0
        else:
            continue
        out.append({"kind": kind, "a": a, "b": b, "half": half, "name": r.name})
    return out


def _tangent(line: LineString, p: Point) -> np.ndarray:
    s = line.project(p)
    q0, q1 = line.interpolate(max(0.0, s - 1.0)), line.interpolate(min(line.length, s + 1.0))
    v = np.array([q1.x - q0.x, q1.y - q0.y])
    return v / (np.hypot(*v) or 1.0)


CROSSING_COS = 0.9      # a road within about 26 degrees of the line's road runs along it (MapNG's own cut: 0.85, per segment)


def _edge_gap(cws, tree, pts: np.ndarray, own: int, along: np.ndarray, paint_half: float, reach: float = 20.0) -> tuple[float, str]:
    """Smallest distance from the painted edges of a line (its samples pts, painted paint_half either side) to the edge of
    a carriageway crossing the line's road (negative: on it), and that carriageway's name."""
    best, who = math.inf, ""
    for p in pts:
        pt = Point(*p)
        edges = [Point(*(p + along * paint_half)), Point(*(p - along * paint_half))]
        for k in tree.query(pt.buffer(reach)):
            if k == own:
                continue
            line, half, road = cws[k]
            if abs(float(np.dot(_tangent(line, pt), along))) > CROSSING_COS:   # parallel: the road itself or its continuation
                continue
            g = min(line.distance(e) for e in edges) - half
            if g < best:
                best, who = g, road.name
    return best, who


def corner_radii(level: Level):
    """Kerb return radii from MapNG's corner paving bands (cornerPatches: concentric bands laid along the arc, the
    first 0.5 m outside it): [(centre x, y, radius, turn in degrees)]. The turn is the angle the kerb turns through
    round the corner (90 at a square corner). Bands too short to fit a circle to (under 2 m or 20 degrees) are skipped."""
    fits = []
    for r in level.roads:
        if not r.name.startswith("corner_paving_") or len(r.nodes) < 3:
            continue
        x, y = r.nodes[:, 0], r.nodes[:, 1]
        A = np.c_[2 * x, 2 * y, np.ones(len(x))]
        (cx, cy, c), *_ = np.linalg.lstsq(A, x * x + y * y, rcond=None)
        R = float(np.sqrt(max(0.0, c + cx * cx + cy * cy)))
        length = float(np.sum(np.hypot(np.diff(x), np.diff(y))))
        if not (0 < R < 50) or length < 2.0 or length / R < math.radians(20):
            continue
        fits.append((float(cx), float(cy), R, math.degrees(length / R)))
    corners: list[tuple[float, float, float, float]] = []
    for cx, cy, R, turn in sorted(fits, key=lambda t: t[2]):
        if not any(math.hypot(cx - x, cy - y) < 0.5 for x, y, *_ in corners):
            corners.append((cx, cy, R - CORNER_BAND_OFFSET_M, turn))
    return corners


# ------------------------------------------------------------------ the check

def check_junctions(level: Level, c: dict, tol: dict | None = None) -> tuple[list[dict], dict]:
    """Placement of the junction lines and the kerb radii (docs/junctions.md). Returns (findings, summary).

    * `junction-line-on-road` (high): a Stop, Give Way or roundabout line reaches onto the carriageway it meets.
    * `junction-line-off-mouth` (medium): its near edge lies more than `mouth_gap_max_m` from that carriageway.
    * `junction-line-not-square` (medium), `junction-line-span` (medium): not square to its road; not kerb to centre
      line (two-way) or kerb to kerb (one-way, or kerb to centre under 2.75 m).
    * `junction-line-on-priority-road` (high): a Give Way or Stop line on an arm the rule gives priority.
    * `junction-mouth-unmarked` (medium) / `roundabout-entry-unmarked` (high): a minor arm or roundabout entry with no line.
    * `signal-line-in-junction` (high) / `signal-line-far` (medium): a signal Stop line on the crossing carriageway, or
      more than `signal_gap_max_m` back; `signal-line-no-head` / `signal-head-no-line` (medium); `signal-head-close`
      (low): the near-side head less than 1.5 m past the line.
    * `kerb-radius-small` (medium) / `kerb-radius-large` (low): a corner outside Creating Places' 4 to 10 m."""
    t = dict(JUNCTION_TOL)
    t.update(tol or {})
    out: list[dict] = []
    cws = carriageways(level)
    tree = STRtree([cw[0] for cw in cws]) if cws else None
    lines = junction_lines(level)
    summary: dict = {"lines": {}, "measured": {}}
    for ln in lines:
        summary["lines"][ln["kind"]] = summary["lines"].get(ln["kind"], 0) + 1
    if tree is None:
        return [finding("check-not-run", where="junctions", severity="medium", detail="no drawn road bodies")], summary

    gaps = defaultdict(list)
    for ln in lines:
        a, b = ln["a"], ln["b"]
        mid = (a + b) / 2
        u = (b - a) / (np.hypot(*(b - a)) or 1.0)
        pm = Point(*mid)
        # its own road: the carriageway under the line that runs across it, centreline nearest
        own, best = None, math.inf
        for k in tree.query(pm.buffer(8.0)):
            line, half, _ = cws[k]
            d = line.distance(pm)
            if d <= half + 0.2 and abs(float(np.dot(_tangent(line, pm), u))) < 0.5 and d < best:
                own, best = k, d
        where = ln["name"]
        if own is None:
            out.append(finding("junction-line-no-road", mid[0], mid[1], where, "medium", detail="no drawn road runs across this line"))
            continue
        oline, ohalf, oroad = cws[own]
        tng = _tangent(oline, Point(*oline.interpolate(oline.project(pm)).coords[0]))
        ln.update(own=own, mid=mid, u=u, tng=tng)
        ang = math.degrees(math.acos(min(1.0, abs(float(np.dot(tng, u))))))
        if abs(90.0 - ang) > t["square_deg"]:
            out.append(finding("junction-line-not-square", mid[0], mid[1], where, "medium", 90.0 - ang, "deg off square"))
        # lateral ends against the own road's centreline: the line's kerb end and its inner end
        base = np.array(oline.interpolate(oline.project(pm)).coords[0])
        nrm = np.array([-tng[1], tng[0]])
        la, lb = float(np.dot(a - base, nrm)), float(np.dot(b - base, nrm))
        kerb_end, inner = (la, lb) if abs(la) >= abs(lb) else (lb, la)
        ln["kerb_end"] = base + nrm * kerb_end
        full = abs(inner) > 1.0 and np.sign(inner) != np.sign(kerb_end)
        span_bad = abs(kerb_end) < ohalf - t["kerb_end_m"] or abs(kerb_end) > ohalf + 0.05 or abs(inner) > ohalf + 0.05
        if not full and not (-0.1 <= abs(inner) <= t["centre_end_m"] + 2.5):
            span_bad = True                                   # (an uneven lane split moves the centre line up to 2.5 m)
        if full and abs(inner) < ohalf - t["kerb_end_m"]:
            span_bad = True
        if span_bad:
            out.append(finding("junction-line-span", mid[0], mid[1], where, "medium", abs(lb - la), "m long",
                               f"ends at {la:+.2f} and {lb:+.2f} m from the centreline of a road {2 * ohalf:.1f} m kerb to kerb"))
        # at the mouth: the near edge against the carriageways crossing its road
        samples = np.array([a + (b - a) * s for s in np.linspace(0, 1, 7)])
        gap, met = _edge_gap(cws, tree, samples, own, tng, ln["half"])
        where = f"{ln['name']} on {oroad.name}, meets {met}"
        if not math.isfinite(gap):
            out.append(finding("signal-line-no-mouth" if ln["kind"] == "signal" else "junction-line-no-mouth", mid[0], mid[1],
                               where, "medium", detail="no carriageway crosses its road within 20 m"))
            continue
        gaps[ln["kind"]].append(gap)
        if ln["kind"] == "signal":
            if gap < t["signal_gap_min_m"]:
                out.append(finding("signal-line-in-junction", mid[0], mid[1], where, "high", gap, "m from the crossing carriageway"))
            elif gap > t["signal_gap_max_m"]:
                out.append(finding("signal-line-far", mid[0], mid[1], where, "medium", gap, "m from the crossing carriageway"))
        elif gap < t["mouth_gap_min_m"]:
            out.append(finding("junction-line-on-road", mid[0], mid[1], where, "high", gap, "m onto the carriageway it meets"))
        elif gap > t["mouth_gap_max_m"]:
            out.append(finding("junction-line-off-mouth", mid[0], mid[1], where, "medium", gap, "m back from the mouth"))
    for kind, g in gaps.items():
        summary["measured"][kind] = {"n": len(g), "gap_min_m": round(min(g), 2), "gap_median_m": round(float(np.median(g)), 2),
                                     "gap_max_m": round(max(g), 2)}

    out += _check_coverage(level, lines, cws, t, summary)
    out += _check_signal_heads(level, lines, t, summary)
    out += _check_radii(level, t, summary)
    # T-404's static geometry gate (docs/junctions.md): no placement finding and no kerb radius out of range; OSM's
    # own controls on a priority arm (low) and the signal measurements do not count
    failed: dict[str, int] = {}
    for f in out:
        if f["kind"] in GATE_KINDS and not (f["kind"] == "junction-line-on-priority-road" and f["severity"] == "low"):
            failed[f["kind"]] = failed.get(f["kind"], 0) + 1
    summary["gate"] = {"pass": not failed, "failed": failed}
    return out, summary


GATE_KINDS = {"junction-line-no-road", "junction-line-no-mouth", "junction-line-not-square", "junction-line-span", "junction-line-on-road",
              "junction-line-off-mouth", "junction-line-on-priority-road", "junction-mouth-unmarked",
              "roundabout-entry-unmarked", "kerb-radius-small", "kerb-radius-large"}


def _lines_on_arm(lines, arm, node_key, t, kinds):
    """Lines of `kinds` lying across this arm whose nearest junction node is this one, within arm_match_m of it."""
    hit = []
    for ln in lines:
        if ln["kind"] not in kinds or "mid" not in ln or ln.get("node") != node_key:
            continue
        if ln["node_d"] > t["arm_match_m"]:
            continue
        pm = Point(*ln["mid"])
        if arm["line"].distance(pm) > 7.0:
            continue
        if abs(float(np.dot(_tangent(arm["line"], pm), ln["u"]))) < 0.5:
            hit.append(ln)
    return hit


GAP_MIN_M = 5.0         # MapNG's: a central reserve gap with less room than a car behind the line takes no line


def _has_room(arm, cws, tree, clear: float, gap_between_priority: bool = False) -> bool:
    """Is there a spot on the arm, within 30 m of its node, where a line across the arm's whole drawn width is clear of
    every carriageway crossing it by `clear` (a line's near edge plus its clearance)? A link inside a junction has
    none; a link whose far end gives way too (a dual carriageway's gap) needs a clear stretch of the line's width
    plus GAP_MIN_M. (Stricter than MapNG, which needs only the approach half clear, so a miss here is real.)"""
    line = arm["line"]
    need = (2 * clear + GAP_MIN_M) if gap_between_priority else 0.0
    run = 0.0
    for s in np.arange(0.25, min(line.length - 0.5, 30.0) + 1e-9, 0.25):
        p = line.interpolate(s)
        tng = _tangent(line, p)
        nrm = np.array([-tng[1], tng[0]])
        own = [k for k in tree.query(p.buffer(8.0)) if abs(float(np.dot(_tangent(cws[k][0], p), tng))) > 0.85
               and cws[k][0].distance(p) < 1.0]
        half = max((cws[k][1] for k in own), default=3.25) - 0.3
        ok = True
        for lat in np.linspace(-half, half, 7):
            q = Point(p.x + nrm[0] * lat, p.y + nrm[1] * lat)
            for k in tree.query(q.buffer(20.0)):
                if k in own:
                    continue
                cl, h, _ = cws[k]
                if abs(float(np.dot(_tangent(cl, q), tng))) > ROOM_COS:
                    continue
                if cl.distance(q) - h < clear:
                    ok = False
                    break
            if not ok:
                break
        run = run + 0.25 if ok else 0.0
        if ok and run >= need:
            return True
    return False


ROOM_COS = 0.8          # stricter than MapNG's 0.85: more roads count as crossing, so "room" here means room


def _far_end_gives_way(g: Graph, k: str, arm) -> bool:
    """Does the arm, followed to the next junction, give way there too (a link between two priority roads)?"""
    n, cur = k, arm["edge"]
    for _ in range(200):
        nxt = g.other(cur, n)
        if len(g.edges[nxt]) >= 3:
            rule = priority_at_junction(g.arms(nxt))
            return bool(rule) and any(a["edge"] is cur for a in rule["give_way"])
        if len(g.edges[nxt]) != 2:
            return False
        n, cur = nxt, next((x for x in g.edges[nxt] if x is not cur), None)
        if cur is None:
            return False
    return False


def _check_coverage(level, lines, cws, t, summary) -> list[dict]:
    out: list[dict] = []
    g = Graph(level)
    tree = STRtree([cw[0] for cw in cws])
    signals = np.array([f.xy[0] for f in level.osm if f.tags.get("highway") == "traffic_signals" and len(f.xy)]).reshape(-1, 2)
    minis = {f"{f.lonlat[0][1]:.7f}:{f.lonlat[0][0]:.7f}" for f in level.osm if f.tags.get("highway") == "mini_roundabout" and len(f.lonlat)}
    controls = np.array([f.xy[0] for f in level.osm if f.tags.get("highway") in ("give_way", "stop") and len(f.xy)]).reshape(-1, 2)
    # MapNG paints junction lines only 0.02 of the map inside its edge (exportBeamNGLevel insideMargin)
    half_size = level.size_m / 2 - 0.02 * level.size_m - 2.0
    # each line belongs to the junction node nearest it (three public arms or more: a driveway's node is no junction)
    jkeys = [k for k, e in g.edges.items() if len(e) >= 3 and len(g.arms(k)) >= 3]
    jxy = np.array([g.xy[k] for k in jkeys]).reshape(-1, 2)
    for ln in lines:
        if "mid" in ln and len(jxy):
            d = np.hypot(*(jxy - ln["mid"]).T)
            i = int(np.argmin(d))
            ln["node"], ln["node_d"] = jkeys[i], float(d[i])
    n = defaultdict(int)
    for k in jkeys:
        if k in minis:
            continue
        xy = g.xy[k]
        if np.any(np.abs(xy) > half_size):
            continue
        rule = priority_at_junction(g.arms(k))
        if rule is None:
            continue
        if len(signals) and float(np.min(np.hypot(*(signals - xy).T))) < 40.0:
            n["junctions_by_signals_skipped"] += 1
            continue
        n["junctions"] += 1
        on_priority = []
        for arm in rule["priority"]:
            for ln in _lines_on_arm(lines, arm, k, t, ("giveway", "stop", "roundabout")):
                if arm["line"].distance(Point(*ln["mid"])) <= 3.0:
                    on_priority.append((arm, ln))
        for arm, ln in on_priority:
            # (MapNG ties an OSM give-way or stop node up to 40 m out to its junction: SIGNAL_TO_JUNCTION_MAX_M)
            mapped = len(controls) and float(np.min(np.hypot(*(controls - ln["mid"]).T))) < 45.0
            out.append(finding("junction-line-on-priority-road", ln["mid"][0], ln["mid"][1], ln["name"],
                               "low" if mapped else "high",
                               detail=f"on {arm['f'].tags.get('name') or arm['id']}, which has priority here"
                                      + (" (OSM maps a give-way or stop here: MapNG keeps OSM's)" if mapped else "")))
        if on_priority:
            n["junctions_osm_decides"] += 1                   # OSM gives way on another arm: its lines stand, ours are not added
            continue
        kinds = ("roundabout",) if rule["roundabout"] else ("giveway", "stop")
        for arm in rule["give_way"]:
            hit = _lines_on_arm(lines, arm, k, t, kinds)
            name = arm["f"].tags.get("name") or arm["id"]
            tag = "roundabout_entries" if rule["roundabout"] else "minor_arms"
            n[tag] += 1
            if hit:
                n[tag + "_marked"] += 1
                continue
            if not _has_room(arm, cws, tree, 0.45, _far_end_gives_way(g, k, arm)):
                n[tag + "_no_room"] += 1
                continue
            if rule["roundabout"]:
                out.append(finding("roundabout-entry-unmarked", xy[0], xy[1], f"{name} onto the roundabout", "high",
                                   detail="no 1003.1 Give Way line on this entry"))
            else:
                out.append(finding("junction-mouth-unmarked", xy[0], xy[1], f"{name}", "medium",
                                   detail="a minor arm with no Give Way or Stop line"))
    summary["coverage"] = dict(sorted(n.items()))
    return out


def _check_signal_heads(level, lines, t, summary) -> list[dict]:
    import json
    out: list[dict] = []
    name = f"{level.base}signals.json"
    sig = [ln for ln in lines if ln["kind"] == "signal" and "mid" in ln]
    if name not in level.zf.namelist():
        return out
    inst = json.loads(level.zf.read(name).decode("utf-8")).get("instances", [])
    poles = np.array([o.get("position", [0, 0, 0])[:2] for o in level.objects
                      if o.get("class") == "TSStatic" and "pole_traffic" in str(o.get("shapeName", ""))], dtype=float).reshape(-1, 2)
    heads = [(np.array(i["pos"][:2], float), np.array(i["dir"][:2], float)) for i in inst]
    used = set()
    clear = []
    for ln in sig:
        seg = LineString([ln["a"], ln["b"]])
        best = None
        for j, (p, d) in enumerate(heads):
            dd = seg.distance(Point(*p))
            if dd <= t["head_match_m"] and abs(float(np.dot(d / (np.hypot(*d) or 1), ln["tng"]))) > 0.7 and (best is None or dd < best[1]):
                best = (j, dd)
        if best is None:
            out.append(finding("signal-line-no-head", ln["mid"][0], ln["mid"][1], ln["name"], "medium",
                               detail="no signal instance on this Stop line"))
            continue
        used.add(best[0])
        travel = heads[best[0]][1] / (np.hypot(*heads[best[0]][1]) or 1)
        if len(poles):
            # the near-side primary: the pole nearest the line's kerb end; how far it stands past the line's near edge
            q = poles[np.argmin(np.hypot(*(poles - ln["kerb_end"]).T))]
            if float(np.hypot(*(q - ln["kerb_end"]))) < 8.0:
                ahead = float(np.dot(q - ln["mid"], travel)) - ln["half"]
                clear.append(ahead)
                if ahead < t["signal_head_min_m"]:
                    out.append(finding("signal-head-close", ln["mid"][0], ln["mid"][1], ln["name"], "low", ahead,
                                       "m from the line to the primary signal", "TSM 6 4.2.1: at least 1.5 m (2.5 m preferred)"))
    for j, (p, d) in enumerate(heads):
        if j not in used:
            out.append(finding("signal-head-no-line", p[0], p[1], inst[j].get("name", f"signal {inst[j].get('id')}"), "medium",
                               detail="no signal Stop line at this signal's stop point"))
    if clear:
        summary["signal_head_ahead_m"] = {"n": len(clear), "min": round(min(clear), 2), "median": round(float(np.median(clear)), 2),
                                         "max": round(max(clear), 2)}
    return out


def _arm_to_junction_m(g: Graph, k: str, arm) -> float:
    """How far the arm runs from junction node k to the next junction node (three public arms or more), up to 100 m."""
    n, cur, run = k, arm["edge"], 0.0
    while cur is not None and run < 100.0:
        nxt = g.other(cur, n)
        run += float(np.hypot(*(g.xy[nxt] - g.xy[n])))
        if len(g.edges[nxt]) >= 3 and len(g.arms(nxt)) >= 3:
            return run
        if len(g.edges[nxt]) == 1:
            return 100.0                                      # a dead end: nothing beyond it
        n, cur = nxt, next((x for x in g.edges[nxt] if x is not cur), None)
    return run


def _check_radii(level, t, summary) -> list[dict]:
    out: list[dict] = []
    corners = corner_radii(level)
    if not corners:
        summary["kerb_radii"] = {"corners": 0}
        return out
    roads = [(f, LineString(dedupe_points(f.xy))) for f in level.osm
             if f.type == "road" and rank(f.tags.get("highway")) >= MIN_PUBLIC_RANK and len(dedupe_points(f.xy)) > 1]
    tree = STRtree([r[1] for r in roads])
    g = Graph(level)
    jkeys = [k for k, e in g.edges.items() if len(e) >= 3 and len(g.arms(k)) >= 3]
    jxy = np.array([g.xy[k] for k in jkeys]).reshape(-1, 2)
    radii, skewed, close, expected_short = [], [], [], 0
    for cx, cy, r, turn in corners:
        p = Point(cx, cy)
        near = [roads[k][0] for k in tree.query(p.buffer(r + 9.0)) if roads[k][1].distance(p) <= r + 9.0]
        if not near:
            continue                                          # a corner of service roads only
        top = max(near, key=lambda f: rank(f.tags.get("highway")))
        want = UK_CORNER_RADII.get(str(top.tags.get("highway", "")).removesuffix("_link"))
        where = f"corner by {top.tags.get('name') or identity(top)}"
        # Creating Places' radii are for a minor road entering at 90 degrees (Table 4, 16.26); a skewed corner (the
        # kerb turning through under 60 or over 120 degrees) is measured, not judged
        if not t["corner_turn_deg"][0] <= turn <= t["corner_turn_deg"][1]:
            skewed.append(r)
            continue
        # ... and for junctions at least 25 m apart (Table 4, minimum adjacent spacing) with a straight minor arm of
        # twice the radius (16.26): a corner whose two roads reach another junction sooner is measured, not judged
        if len(jxy):
            d = np.hypot(*(jxy - (cx, cy)).T)
            k = jkeys[int(np.argmin(d))]
            arms = sorted(g.arms(k), key=lambda a: a["line"].distance(p))[:2]
            if float(d.min()) < r + 15.0 and any(_arm_to_junction_m(g, k, a) < t["corner_spacing_m"] for a in arms):
                close.append(r)
                continue
        radii.append(r)
        if want and r < want - 0.25:
            expected_short += 1
        if r < t["radius_min_m"] - 0.05:
            out.append(finding("kerb-radius-small", cx, cy, where, "medium", r, "m kerb radius",
                               f"Creating Places Table 4: {want or 6} m here, 4 m at least (kerb turns {turn:.0f} degrees)"))
        elif r > t["radius_max_m"]:
            out.append(finding("kerb-radius-large", cx, cy, where, "low", r, "m kerb radius", f"kerb turns {turn:.0f} degrees"))
    summary["kerb_radii"] = {"corners": len(radii), "skewed_not_judged": len(skewed), "close_junctions_not_judged": len(close),
                             "under_class_radius": expected_short}
    if close:
        summary["kerb_radii"]["close_min_median_max"] = [round(min(close), 2), round(float(np.median(close)), 2), round(max(close), 2)]
        summary["kerb_radii"]["close_under_4m"] = int(sum(1 for r in close if r < t["radius_min_m"] - 0.05))
    if radii:
        summary["kerb_radii"].update(min=round(min(radii), 2), median=round(float(np.median(radii)), 2), max=round(max(radii), 2),
                                     under_4m=int(sum(1 for r in radii if r < t["radius_min_m"] - 0.05)))
    if skewed:
        summary["kerb_radii"]["skewed_min_median_max"] = [round(min(skewed), 2), round(float(np.median(skewed)), 2), round(max(skewed), 2)]
    return out
