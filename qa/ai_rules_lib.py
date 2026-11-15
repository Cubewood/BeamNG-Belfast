"""AI traffic rules of the level, judged from recorded data (T-407). Pure logic, no game.

What is judged (docs/left_hand_traffic.md):
  * roundabout circulation: the swept angle of a car around the centre must be clockwise (UK, left-hand
    traffic: you give way to the right and go round clockwise);
  * one-way roads: no moving sample runs against a one-way edge of the game's navgraph;
  * the light sequence: the recorded state changes of every signal head follow the UK order, red, red and
    amber, green, amber, red, and no two heads of one sequence show a non-red state together;
  * a car under AI traffic mode stops for a red light and does not cross on red.

State codes (one letter of the game's own `controllerDefinitions.states` name each):
  0 redTrafficLight, 1 redYellowTrafficLight, 2 greenTrafficLight, 3 yellowTrafficLight.
"""
from __future__ import annotations

import math
from collections import defaultdict

RED, RED_AMBER, GREEN, AMBER = 0, 1, 2, 3
CODE_OF = {"redTrafficLight": RED, "redYellowTrafficLight": RED_AMBER, "greenTrafficLight": GREEN,
           "yellowTrafficLight": AMBER}
NAME_OF = {v: k for k, v in CODE_OF.items()}
# UK order: red -> red and amber -> green -> amber -> red
UK_NEXT = {RED: RED_AMBER, RED_AMBER: GREEN, GREEN: AMBER, AMBER: RED}

MOVING_MS = 2.0          # a car counts as moving above this
STOPPED_MS = 0.5         # and as stopped below this
RED_GRACE_S = 1.0        # a car that crosses within this long of a red coming on is not blamed
CROSS_RADIUS_M = 6.0     # a car this close (sideways) to a signal's position, crossing its plane, passed that signal (heads sit on the road's centreline, MapNG places them at the OSM node)


# ------------------------------------------------------------------ roundabouts

def swept_angle_deg(samples: list[dict], centre: tuple[float, float], r_max: float) -> dict:
    """Signed angle (degrees, counter-clockwise positive) a car swept round `centre` while within `r_max`.

    Each run of samples inside the circle is summed separately, so a car that leaves and comes back is not
    credited the angle in between. Returns {sweep_deg, samples, min_r, max_r, direction}."""
    total, prev, n = 0.0, None, 0
    rmin, rmax = float("inf"), 0.0
    for s in samples:
        x, y = s["x"] - centre[0], s["y"] - centre[1]
        r = math.hypot(x, y)
        if r > r_max:
            prev = None
            continue
        n += 1
        rmin, rmax = min(rmin, r), max(rmax, r)
        a = math.atan2(y, x)
        if prev is not None:
            d = a - prev
            while d > math.pi:
                d -= 2 * math.pi
            while d < -math.pi:
                d += 2 * math.pi
            total += d
        prev = a
    deg = math.degrees(total)
    direction = "none" if n < 3 or abs(deg) < 30 else ("clockwise" if deg < 0 else "anticlockwise")
    return {"sweep_deg": round(deg, 1), "samples": n, "min_r": None if not n else round(rmin, 1),
            "max_r": round(rmax, 1), "direction": direction}


def ring_direction(points: list[tuple[float, float]]) -> str:
    """'clockwise' or 'anticlockwise' for a closed ring given as points (shoelace sign)."""
    a = 0.0
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        a += p[0] * q[1] - q[0] * p[1]
    return "anticlockwise" if a > 0 else "clockwise"


# ------------------------------------------------------------------ one-way roads

class OneWayIndex:
    """Directed one-way edges of the game's graph dump, for a wrong-way test."""

    def __init__(self, dump: dict, cell: float = 30.0):
        pos = {n[0]: (n[1], n[2]) for n in dump["nodes"]}
        self.edges: list[tuple[tuple[float, float], tuple[float, float], bool]] = []
        for name, other, one_way, name_is_in, *_ in dump["links"]:
            if name not in pos or other not in pos:
                continue
            a, b = (name, other) if (name_is_in or not one_way) else (other, name)
            self.edges.append((pos[a], pos[b], bool(one_way)))
        self.cell = cell
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for k, (a, b, _) in enumerate(self.edges):
            for cx in range(int(min(a[0], b[0]) // cell) - 1, int(max(a[0], b[0]) // cell) + 2):
                for cy in range(int(min(a[1], b[1]) // cell) - 1, int(max(a[1], b[1]) // cell) + 2):
                    self.grid[(cx, cy)].append(k)

    @property
    def one_way_count(self) -> int:
        return sum(1 for e in self.edges if e[2])

    def near(self, x: float, y: float, r: float):
        for k in self.grid.get((int(x // self.cell), int(y // self.cell)), ()):
            a, b, ow = self.edges[k]
            dx, dy = b[0] - a[0], b[1] - a[1]
            l2 = dx * dx + dy * dy
            if l2 < 1e-9:
                continue
            t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / l2))
            qx, qy = a[0] + t * dx, a[1] + t * dy
            d = math.hypot(qx - x, qy - y)
            if d <= r:
                n = math.sqrt(l2)
                yield d, (dx / n, dy / n), ow, (qx, qy)


def wrong_way(samples: list[dict], index: OneWayIndex, reach_m: float = 3.5, stride: int = 3) -> dict:
    """Samples on a one-way road, and how many of them run against it.

    A sample counts only when the car moves (above MOVING_MS) along an edge within `reach_m` (heading within 45
    degrees of it); it is wrong-way when no edge it runs along allows that direction (two-way edges and one-way
    edges in its direction both allow it, so junction mouths and crossing roads do not count against it)."""
    on = wrong = 0
    first = None
    for s in samples[::stride]:
        sp = math.hypot(s["vx"], s["vy"])
        if sp < MOVING_MS:
            continue
        ux, uy = s["vx"] / sp, s["vy"] / sp
        aligned = [(d, e, ow) for d, e, ow, _q in index.near(s["x"], s["y"], reach_m) if abs(e[0] * ux + e[1] * uy) > 0.7]
        if not any(ow for _, _, ow in aligned):
            continue
        on += 1
        if not any((not ow) or (e[0] * ux + e[1] * uy) > 0 for _, e, ow in aligned):
            wrong += 1
            first = first or (round(s["x"], 1), round(s["y"], 1))
    return {"on_one_way": on, "wrong_way": wrong, "first_at": first}


def lane_side(samples: list[dict], index: OneWayIndex, reach_m: float = 4.0, stride: int = 3) -> dict:
    """Which side of a two-way graph edge a moving car keeps (the edge is the road's centre line).

    Counts samples above MOVING_MS running along the nearest two-way edge within `reach_m`: `left` when the car is
    left of that line in its direction of travel (right-hand drive country rules: UK, `left` is right), `right`
    otherwise, `centre` within 0.3 m. Junction mouths are skipped by the heading test (45 degrees)."""
    left = right = centre = 0
    offs: list[float] = []
    for s in samples[::stride]:
        sp = math.hypot(s["vx"], s["vy"])
        if sp < MOVING_MS:
            continue
        ux, uy = s["vx"] / sp, s["vy"] / sp
        best = None
        for d, e, ow, q in index.near(s["x"], s["y"], reach_m):
            if ow or abs(e[0] * ux + e[1] * uy) < 0.7:
                continue
            if best is None or d < best[0]:
                best = (d, q)
        if best is None:
            continue
        off = ux * (s["y"] - best[1][1]) - uy * (s["x"] - best[1][0])     # left of travel is positive
        offs.append(off)
        if off > 0.3:
            left += 1
        elif off < -0.3:
            right += 1
        else:
            centre += 1
    offs.sort()
    return {"left": left, "right": right, "centre": centre, "median_off_m": round(offs[len(offs) // 2], 2) if offs else None}


def wrong_way_csv(path, index: OneWayIndex) -> dict:
    """wrong_way() over a telemetry CSV (columns x, y, vx, vy as the drive suite writes them)."""
    import csv
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append({k: float(r[k]) for k in ("x", "y", "vx", "vy")})
            except (KeyError, ValueError):
                continue
    return wrong_way(rows, index)


# ------------------------------------------------------------------ the light sequence

def parse_transitions(raw: str) -> dict[int, list[tuple[float, int]]]:
    """'t,id,code,timer;...' (the game-side logger) -> {instance id: [(t, code)]}, in time order."""
    out: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for row in raw.split(";"):
        f = row.split(",")
        if len(f) < 3:
            continue
        try:
            out[int(float(f[1]))].append((float(f[0]), int(float(f[2]))))
        except ValueError:
            continue
    for v in out.values():
        v.sort()
    return dict(out)


def check_order(timeline: list[tuple[float, int]]) -> dict:
    """One head's state changes against the UK order. The first state is where observation began, so the
    first and last durations (cut by the start and the end of the recording) are not measured."""
    bad = []
    for (t0, a), (t1, b) in zip(timeline, timeline[1:]):
        if UK_NEXT.get(a) != b:
            bad.append({"t": round(t1, 2), "from": NAME_OF.get(a, a), "to": NAME_OF.get(b, b)})
    dur: dict[int, list[float]] = defaultdict(list)
    for (t0, a), (t1, _b) in list(zip(timeline, timeline[1:]))[1:]:
        dur[a].append(t1 - t0)
    cycles = sum(1 for (_, a) in timeline if a == RED_AMBER)
    return {"changes": max(0, len(timeline) - 1), "bad_transitions": bad, "cycles": cycles,
            "never_changes": len(timeline) <= 1,
            "state": NAME_OF.get(timeline[0][1], timeline[0][1]) if timeline else None,
            "durations_s": {NAME_OF[c]: {"min": round(min(v), 2), "max": round(max(v), 2), "n": len(v)}
                            for c, v in sorted(dur.items()) if c in NAME_OF}}


def state_at(timeline: list[tuple[float, int]], t: float) -> int | None:
    """State code at time t (None before the first record)."""
    cur = None
    for tt, c in timeline:
        if tt > t:
            break
        cur = c
    return cur


def sequence_conflicts(by_controller: dict[int, list[tuple[float, int]]], margin_s: float = 0.05) -> list[dict]:
    """Moments at which two controllers (heads that should alternate) both show a non-red state.

    `by_controller` holds one head's timeline per controller of one sequence. Checked just after every change."""
    times = sorted({t for tl in by_controller.values() for t, _ in tl})
    out = []
    for t in times:
        live = {cid: state_at(tl, t + margin_s) for cid, tl in by_controller.items()}
        live = {cid: s for cid, s in live.items() if s is not None}
        open_ = [cid for cid, s in live.items() if s != RED]
        if len(open_) > 1:
            out.append({"t": round(t, 2), "controllers": sorted(open_), "states": [NAME_OF.get(live[c]) for c in sorted(open_)]})
    return out


# ------------------------------------------------------------------ AI at a signal

def red_age(timeline: list[tuple[float, int]], t: float) -> float | None:
    """Seconds the head has shown red or red and amber at t (None when it is not red)."""
    cur = state_at(timeline, t)
    if cur not in (RED, RED_AMBER):
        return None
    start = None
    for tt, c in timeline:
        if tt > t:
            break
        if c in (RED, RED_AMBER):
            start = tt if start is None else start
        else:
            start = None
    return None if start is None else t - start


def judge_approach(samples: list[dict], signal: dict, timeline: list[tuple[float, int]]) -> dict:
    """What one car did at one signal.

    signal: {pos: (x, y), dir: (ux, uy)} (`dir` is the direction of travel the signal controls). samples carry
    t, x, y, vx, vy. Reports whether the car crossed the signal's plane near it, its state and speed when it
    did, whether it stopped for a red, and how long a red stop lasted and how soon it went after green."""
    sx, sy = signal["pos"]
    ux, uy = signal["dir"]
    n = math.hypot(ux, uy) or 1.0
    ux, uy = ux / n, uy / n
    prev = None
    cross = None
    stopped = []             # (t, s) samples stationary before the signal while it showed red
    for smp in samples:
        s = (smp["x"] - sx) * ux + (smp["y"] - sy) * uy
        lat = abs(-(smp["x"] - sx) * uy + (smp["y"] - sy) * ux)
        sp = math.hypot(smp["vx"], smp["vy"])
        st = state_at(timeline, smp["t"])
        if prev is not None and prev[0] < 0 <= s and lat <= CROSS_RADIUS_M and cross is None:
            cross = {"t": round(smp["t"], 2), "speed_ms": round(sp, 1), "state": NAME_OF.get(st), "lat_m": round(lat, 1),
                     "red_age_s": None if red_age(timeline, smp["t"]) is None else round(red_age(timeline, smp["t"]), 1)}
        if cross is None and sp < STOPPED_MS and -30.0 <= s < 0 and lat <= CROSS_RADIUS_M and st in (RED, RED_AMBER):
            stopped.append((smp["t"], s))
        prev = (s, smp["t"])
    out: dict = {"crossed": cross is not None, "cross": cross, "stopped_at_red": bool(stopped)}
    if stopped:
        out["stop_dist_m"] = round(-stopped[-1][1], 1)
        out["waited_s"] = round(stopped[-1][0] - stopped[0][0], 1)
    out["ran_red"] = bool(cross and cross["state"] in ("redTrafficLight", "redYellowTrafficLight")
                          and (cross["red_age_s"] or 0) >= RED_GRACE_S and cross["speed_ms"] >= 3.0)
    if cross and stopped:
        out["moved_off_s_after_stop"] = round(cross["t"] - stopped[-1][0], 1)
    # a car that sat still before the signal for the whole recording while it showed red the whole time
    if not cross and samples:
        last = samples[-1]
        s = (last["x"] - sx) * ux + (last["y"] - sy) * uy
        out["end_dist_m"] = round(-s, 1)
    return out


def approach_start(adj: dict[str, list[tuple[str, float]]], pos: dict[str, tuple], n1: str, n2: str | None = None,
                   back_m: float = 60.0, min_m: float = 12.0):
    """A start point up to `back_m` metres up the road before the directed edge n1 -> n2, on a stretch the AI cannot
    leave.

    AI traffic mode picks its own turns, so the car is started at the last junction before the signal (or
    `back_m` back, whichever is nearer): between there and n1 every node has one way on. The walk goes back from
    n1 along edges that run into it, straightest predecessor first. Returns (nodes from the start to n1, start xy,
    next xy) or None when the stretch is shorter than `min_m`."""
    rev: dict[str, list[str]] = defaultdict(list)
    for a, lst in adj.items():
        for b, _ in lst:
            rev[b].append(a)
    chain = [n1]
    total = 0.0
    seen = {n1}
    while total < back_m:
        cur = chain[0]
        cands = [p for p in rev.get(cur, []) if p not in seen]
        if not cands:
            break
        nxt = chain[1] if len(chain) > 1 else n2
        if nxt is not None:
            ahead = (pos[nxt][0] - pos[cur][0], pos[nxt][1] - pos[cur][1])

            def straight(p):          # how well p -> cur continues into cur -> nxt
                into = (pos[cur][0] - pos[p][0], pos[cur][1] - pos[p][1])
                return (into[0] * ahead[0] + into[1] * ahead[1]) / ((math.hypot(*into) * math.hypot(*ahead)) or 1.0)
            cands.sort(key=lambda p: -straight(p))
        p = cands[0]
        if len(chain) > 1:
            # cur is a node the car passes on the way to n1: it must have no other way on than nxt (and back to p)
            if {o for o, _ in adj.get(cur, [])} - {p} != {nxt}:
                break
        total += math.hypot(pos[cur][0] - pos[p][0], pos[cur][1] - pos[p][1])
        chain.insert(0, p)
        seen.add(p)
    if total < min_m or len(chain) < 2:
        return None
    return chain, tuple(pos[chain[0]][:2]), tuple(pos[chain[1]][:2])
