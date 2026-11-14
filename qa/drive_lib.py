"""Telemetry, event detection and metrics for the drive suite (T-302). Pure logic, no game.

The event rules are a Python port of MapNG's `analyseDrive` (third_party/mapng/scripts/lib/
driveAnalysis.mjs, pinned d605117) and of the incident checks in `scripts/ingame/driveTest.mjs`,
with the same default thresholds (cited per constant). They are NOT tuned to make routes pass.
Differences from MapNG, all deliberate: the nearest route point is searched in a window ahead of
the last match (routes here can pass the same place twice), the signed lane offset is kept, and
`reversing` is judged on time rather than on poll counts.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

# ---- MapNG thresholds (file:line in third_party/mapng/scripts) ---------------------------------
BUMP_ACC = 7.0          # lib/driveAnalysis.mjs:247 `bumpAcc = 7`: |vertical accel| in m/s^2 is a bump
AIR_TIME_S = 0.25       # :247 `airTime = 0.25` ... of free fall (az < -8, :277-278) is airborne
AIR_ACC = -8.0          # :277 `az < -8`
OFF_ROUTE_M = 6.0       # :247 `offRouteM = 6`; the event fires past OFF_ROUTE_M + road half width (:268)
STUCK_S = 5.0           # :247 `stuckS = 5`: speed < 0.5 m/s for this long, not within 10 m of the end (:281-282)
STILL_SPEED_MS = 0.5    # :281
STILL_END_M = 10.0      # :281 `n.s < total - 10`
SETTLE_S = 1.5          # ingame/driveTest.mjs:494 `settle: 1.5`: seconds after the start not judged
FELL_M = 3.0            # lib/driveAnalysis.mjs:269 `s.z < n.z - 3`
COMPLETED_M = 15.0      # :286 `progress >= total - 15`
EVENT_GAP_S = 2.0       # :253 one event per kind per 2 s
VZ_WINDOW_S = 0.1       # :270-271 vertical acceleration from velocity over 0.1 s windows
VZ_MIN_DT = 0.05       # :272
DAMAGE_CRASH = 50.0     # ingame/driveTest.mjs:468 `dmg - damage0 > 50` is a crash (also :284, :391)
REVERSE_MS = -1.0       # ingame/driveTest.mjs:51 backing up faster than this is `reversing`
REVERSE_S = 1.0         # driveTest.mjs:467-470 two 0.5 s polls
MAX_SANE_MS = 100.0     # ingame/driveTest.mjs:47: a speed past this is the game's teleport artefact
END_ARRIVE_M = 10.0     # ingame/driveTest.mjs:457: arrived within 10 m + v^2 / (2 * STOP_DECEL)
STOP_DECEL_MS2 = 5.0    # driveTest.mjs:44
ARRIVE_PROGRESS_M = 60.0  # ours: arrival only counts in the last 60 m of the route (loops end where they start)
LOST_M = 60.0           # ours: this far from the route the run is stopped as lost
TIMEOUT_FACTOR = 2.5    # ingame/driveTest.mjs:450 `length / speed * 2.5 + 20` s
TIMEOUT_EXTRA_S = 20.0
CALMING_ZONE_M = 14.0   # T-311: within this of mapped traffic calming a vertical jolt is a `calming` note, not a `bump` (the T-302 hump: jolts 7 and 11 m from its OSM node)
CALMING_SPEED_MS = 4.5  # T-311: a learner's speed over a hump (10 mph); the AI is asked for it (wpSpeeds / script v)
LANE_TOL_M = 0.3        # T-311: over the lane divider by more than this is the wrong side (was: left of the route line)

G3_FAIL_EVENTS = ("crash", "stuck", "airborne", "off route", "fell", "reversing", "bump", "lost",
                  "no spawn", "error", "timeout", "incomplete")
STOP_EVENTS = ("crash", "stuck", "fell", "reversing", "lost")   # the route ends at the first of these


@dataclass
class Route:
    id: str
    points: list          # [[x, y, z], ...]
    half: float = 3.0     # road half width (m)
    layout: list | None = None   # per segment: (lanes with us, lanes against) or None (qa/route_lib.lane_layout)
    cum: list = field(default_factory=list)

    def __post_init__(self):
        self.cum = [0.0]
        for a, b in zip(self.points, self.points[1:]):
            self.cum.append(self.cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))

    @property
    def length(self) -> float:
        return self.cum[-1]


def nearest_on(route: Route, x: float, y: float, lo: int = 0, hi: int | None = None) -> dict:
    """Nearest point on route segments lo..hi: {d, z, s, off (signed, left of travel positive), i}."""
    pts = route.points
    hi = len(pts) - 1 if hi is None else min(hi, len(pts) - 1)
    best = {"d": math.inf, "z": 0.0, "s": 0.0, "off": 0.0, "i": lo}
    for i in range(max(lo, 0), hi):
        ax, ay, az = pts[i]
        bx, by, bz = pts[i + 1]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy or 1e-9
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2))
        qx, qy = ax + dx * t, ay + dy * t
        d = math.hypot(qx - x, qy - y)
        if d < best["d"]:
            L = math.sqrt(l2)
            cross = (dx * (y - qy) - dy * (x - qx)) / L
            best = {"d": d, "z": az + (bz - az) * t, "s": route.cum[i] + L * t, "off": cross, "i": i}
    return best


class Monitor:
    """Feed samples in time order; collects events and derived columns. One Monitor per route."""

    def __init__(self, route: Route, offroute_m: float = OFF_ROUTE_M, settle_s: float = SETTLE_S,
                 calming: list | None = None):
        self.route = route
        self.calming = list(calming or [])   # [{id, kind, pts: [(x, y)]}] mapped traffic calming
        self.max_calming_acc = 0.0
        self.sides: list[float] = []      # offset from the lane divider of moving samples (left of it positive)
        self.known: list[bool] = []       # ... and whether a lane layout was known there
        self.wrong_side_at: list[tuple[int, int]] = []
        self.offroute_m = offroute_m
        self.settle_s = settle_s
        self.samples: list[dict] = []
        self.events: list[dict] = []
        self.progress = 0.0
        self.max_dev = 0.0
        self.max_acc = 0.0
        self.max_speed = 0.0
        self.dmg0: float | None = None
        self._dmg_ref: float | None = None      # damage level the crash rule measures from (moves on a scrape)
        self._last_event: dict[str, float] = {}
        self._still = 0.0
        self._air = 0.0
        self._rev = 0.0
        self._i = 0       # index of the segment last matched
        self._j = 0       # window start for the vertical acceleration
        self.stop_reason: str | None = None
        self.offsets: list[float] = []

    def _once(self, kind: str, t: float, **extra) -> dict | None:
        if t - self._last_event.get(kind, -1e9) < EVENT_GAP_S:
            return None
        self._last_event[kind] = t
        ev = {"type": kind, "t": round(t, 1), **extra}
        self.events.append(ev)
        return ev

    def crash_to_scrape(self) -> bool:
        """Turn the crash that just stopped the route into a `scrape` and let the route go on.

        The damage total also counts scratches (crashDetection.lua:284), so a rise with no damaged part, body
        zone or tyre is the car touching the ground, not a collision. A scrape is logged and does not fail G3.
        The crash rule then measures from the present damage, so a real crash later is still caught."""
        if self.stop_reason != "crash":
            return False
        ev = next((e for e in reversed(self.events) if e["type"] == "crash"), None)
        if ev is None:
            return False
        ev["type"] = "scrape"
        self._last_event.pop("crash", None)
        self._dmg_ref = self.samples[-1].get("dmg") if self.samples else None
        if self._dmg_ref is None:
            self._dmg_ref = (self.dmg0 or 0.0) + ev.get("value", 0)
        self.stop_reason = None
        return True

    def _match(self, x: float, y: float) -> dict:
        n = nearest_on(self.route, x, y, self._i - 3, self._i + 60)
        if n["d"] > 25.0:   # lost the window (teleport, wrong turn): search the whole route
            n = nearest_on(self.route, x, y)
        self._i = n["i"]
        return n

    def feed(self, s: dict) -> list[dict]:
        """Add a sample (t, x, y, z, vx, vy, vz, dx, dy, dmg optional); fills speed, vert_acc, dev,
        off, progress on the dict. Returns the events raised by this sample."""
        new: list[dict] = []
        speed = math.hypot(s["vx"], s["vy"])
        if math.hypot(s["vx"], s["vy"], s["vz"]) > MAX_SANE_MS:   # teleport artefact: not judged, not a reference
            s.update(speed=speed, vert_acc=0.0, dev=0.0, off=0.0, progress=self.progress, judged=False)
            return new
        n = self._match(s["x"], s["y"])
        div = lane_divider(route_layout(self.route, n["i"]), self.route.half)
        s.update(speed=speed, dev=n["d"], off=n["off"], lane_off=n["off"] - div)
        self.max_speed = max(self.max_speed, speed)
        # vertical acceleration over a 0.1 s window (driveAnalysis.mjs:270-275)
        smp = self.samples
        az = 0.0
        while self._j + 1 < len(smp) and s["t"] - smp[self._j + 1]["t"] >= VZ_WINDOW_S:
            self._j += 1
        if smp and s["t"] - smp[self._j]["t"] >= VZ_MIN_DT + 1e-9:
            az = (s["vz"] - smp[self._j]["vz"]) / (s["t"] - smp[self._j]["t"])
        s["vert_acc"] = az
        t0 = smp[0]["t"] + self.settle_s if smp else s["t"] + self.settle_s
        judged = bool(smp) and s["t"] >= t0
        s["judged"] = judged
        prev_t = smp[-1]["t"] if smp else s["t"]
        smp.append(s)
        if self.dmg0 is None and s.get("dmg") is not None:
            self.dmg0 = s["dmg"]
        if not judged:
            s["progress"] = self.progress
            return new
        dt = s["t"] - prev_t
        self.progress = max(self.progress, n["s"])
        s["progress"] = self.progress
        self.max_dev = max(self.max_dev, n["d"])
        at = {"x": round(s["x"]), "y": round(s["y"])}
        if speed > 2.0:
            self.offsets.append(n["off"])
            self.sides.append(n["off"] - div)
            self.known.append(bool(route_layout(self.route, n["i"])))
            if n["off"] - div < -LANE_TOL_M and n["d"] < self.route.half + 2:
                self.wrong_side_at.append((round(s["x"]), round(s["y"])))
        ev = None
        if n["d"] > self.offroute_m + self.route.half:
            ev = self._once("off route", s["t"], **at, value=round(n["d"], 1))
        if ev:
            new.append(ev)
        if n["d"] > LOST_M:
            ev = self._once("lost", s["t"], **at, value=round(n["d"], 1))
            if ev:
                new.append(ev)
        if s["z"] < n["z"] - FELL_M and n["d"] < self.route.half + 2:
            ev = self._once("fell", s["t"], **at, value=round(n["z"] - s["z"], 1))
            if ev:
                new.append(ev)
        calming = self.calming_at(s["x"], s["y"])
        if calming:
            self.max_calming_acc = max(self.max_calming_acc, abs(az))
        elif abs(az) > self.max_acc:
            self.max_acc = abs(az)
        if abs(az) > BUMP_ACC:
            if calming:   # mapped hump, table, cushion or bump: noted with the speed, not judged as a bump
                ev = self._once("calming", s["t"], **at, value=round(az, 1), speed=round(speed, 1),
                                feature=f"{calming['kind']} {calming['id']}")
            else:
                ev = self._once("bump", s["t"], **at, value=round(az, 1))
            if ev:
                new.append(ev)
        self._air = self._air + dt if az < AIR_ACC else 0.0
        if self._air > AIR_TIME_S:
            ev = self._once("airborne", s["t"], **at, value=round(self._air, 2))
            if ev:
                new.append(ev)
        self._still = (self._still + dt) if (speed < STILL_SPEED_MS and n["s"] < self.route.length - STILL_END_M) else 0.0
        if self._still > STUCK_S:
            ev = self._once("stuck", s["t"], **at)
            if ev:
                new.append(ev)
        fwd = s["vx"] * s.get("dx", 0.0) + s["vy"] * s.get("dy", 0.0)
        self._rev = self._rev + dt if fwd < REVERSE_MS else 0.0
        if self._rev >= REVERSE_S:
            ev = self._once("reversing", s["t"], **at, value=round(fwd, 1))
            if ev:
                new.append(ev)
        ref = self.dmg0 if self._dmg_ref is None else self._dmg_ref
        if s.get("dmg") is not None and ref is not None and s["dmg"] - ref > DAMAGE_CRASH:
            ev = self._once("crash", s["t"], **at, value=round(s["dmg"] - ref))
            if ev:
                new.append(ev)
        for e in new:
            if e["type"] in STOP_EVENTS and self.stop_reason is None:
                self.stop_reason = e["type"]
        return new

    def calming_at(self, x: float, y: float) -> dict | None:
        """The mapped traffic calming within CALMING_ZONE_M of (x, y), if any."""
        for c in self.calming:
            if _dist_pts((x, y), c["pts"]) <= CALMING_ZONE_M:
                return c
        return None

    def arrived(self) -> bool:
        """MapNG's arrival test (driveTest.mjs:457): near the end, inside the stopping distance."""
        if not self.samples:
            return False
        s = self.samples[-1]
        end = self.route.points[-1]
        if self.progress < self.route.length - ARRIVE_PROGRESS_M:   # loops start where they end
            return False
        return math.hypot(s["x"] - end[0], s["y"] - end[1]) < END_ARRIVE_M + (s["speed"] ** 2) / (2 * STOP_DECEL_MS2)

    def completed(self) -> bool:
        return self.progress >= self.route.length - COMPLETED_M


def route_layout(route: Route, i: int):
    lay = route.layout
    return lay[i] if lay and 0 <= i < len(lay) else None


def lane_divider(layout, half: float) -> float:
    """Offset of the line between our lanes and the oncoming lanes from the route line (left of
    travel positive). We drive on the left, so our lanes lie left of it. No layout or equal lanes:
    0 (the route line). All lanes ours (one-way): the right edge, -half. 3 of 4 lanes ours: -half/2."""
    if not layout:
        return 0.0
    w, a = layout
    return half * (1.0 - 2.0 * w / (w + a)) if w + a else 0.0


def _dist_pts(p, pts) -> float:
    if len(pts) == 1:
        return math.hypot(p[0] - pts[0][0], p[1] - pts[0][1])
    best = math.inf
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
        best = min(best, math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy))
    return best


def lane_summary(offsets: list[float], sides: list[float] | None = None, known: list[bool] | None = None,
                 wrong_at: list | None = None) -> dict:
    """Signed offsets from the route centreline of moving samples (left of travel positive). Only
    meaningful in graph mode on two-way roads; in script mode the car follows the centreline.

    `left`, `right`, `centre` and the median are against the route line (the way). On roads with
    unequal lanes or one way that line is not the lane divider, so `wrong_side` (T-311) counts the
    samples over the divider (offset from the divider, `sides`, below -LANE_TOL_M). `right_explained`
    is the right-of-the-way samples that the layout puts in our own lanes. `layout_samples` is how
    many samples had a lane layout from OSM; the rest use the route line as the divider."""
    if not offsets:
        return {"samples": 0}
    left = sum(1 for o in offsets if o > 0.3)
    right = sum(1 for o in offsets if o < -0.3)
    out = {"samples": len(offsets), "left": left, "right": right, "centre": len(offsets) - left - right,
           "median_offset_m": round(statistics.median(offsets), 2)}
    if sides is not None:
        wrong = sum(1 for d in sides if d < -LANE_TOL_M)
        out.update(wrong_side=wrong,
                   right_explained=right - sum(1 for o, d in zip(offsets, sides) if o < -0.3 and d < -LANE_TOL_M),
                   layout_samples=sum(known or []))
        if wrong_at:
            seen: list = []
            for p in wrong_at:
                if not any(abs(p[0] - q[0]) < 15 and abs(p[1] - q[1]) < 15 for q in seen):
                    seen.append(p)
            out["wrong_side_at"] = seen[:8]
    return out


def route_timeout_s(length_m: float, speed_ms: float) -> float:
    return length_m / speed_ms * TIMEOUT_FACTOR + TIMEOUT_EXTRA_S


def route_result(route_meta: dict, mon: Monitor, *, mode: str, end_reason: str, started_at: str,
                 duration_s: float, telemetry_file: str | None, extra: dict | None = None) -> dict:
    """One route's entry in metrics.json."""
    events = list(mon.events)
    # MapNG: reaching the end within the stopping distance without an incident is completed
    # (ingame/driveTest.mjs:496 `if (reachedEnd && !incident) result.completed = true`)
    completed = end_reason == "arrived" and not any(e["type"] in STOP_EVENTS for e in events)
    if not completed and end_reason == "timeout":
        events.append({"type": "timeout", "t": round(duration_s, 1)})
    if not completed and not any(e["type"] in G3_FAIL_EVENTS for e in events):
        events.append({"type": "incomplete", "t": round(duration_s, 1),
                       "value": f"{round(mon.progress)} of {round(mon.route.length)} m, ended: {end_reason}"})
    counts: dict[str, int] = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    smp = mon.samples
    hz = round((len(smp) - 1) / (smp[-1]["t"] - smp[0]["t"]), 1) if len(smp) > 2 and smp[-1]["t"] > smp[0]["t"] else 0.0
    return {
        "id": route_meta["id"], "name": route_meta.get("name", ""), "mode": mode, "completed": completed,
        "end_reason": end_reason, "started": started_at, "duration_s": round(duration_s, 1),
        "length_m": round(mon.route.length), "progress_m": round(mon.progress),
        "events": events, "event_counts": counts,
        "max_bump_ms2": round(mon.max_acc, 1), "max_calming_ms2": round(mon.max_calming_acc, 1),
        "max_deviation_m": round(mon.max_dev, 1),
        "max_speed_ms": round(mon.max_speed, 1),
        "mean_speed_ms": round(mon.route.length / duration_s, 1) if completed and duration_s > 0 else None,
        "damage_start": mon.dmg0, "damage_end": smp[-1].get("dmg") if smp else None,
        "lane": lane_summary(mon.offsets, mon.sides, mon.known, mon.wrong_side_at), "frames": len(smp), "frame_rate_hz": hz,
        "telemetry": telemetry_file, **(extra or {}),
    }


def g3(results: list[dict], expected_ids: list[str] | None = None) -> dict:
    """Gate G3: every route completes with no crash, stuck, airborne or off-route event and no
    vertical bump over BUMP_ACC. A route that is missing from the results fails (no silent skips)."""
    failed = []
    for r in results:
        why = sorted({e["type"] for e in r["events"] if e["type"] in G3_FAIL_EVENTS})
        if not r["completed"]:
            why = sorted(set(why) | {"not completed"})
        if why:
            failed.append({"id": r["id"], "why": why})
    missing = [i for i in (expected_ids or []) if i not in {r["id"] for r in results}]
    for i in missing:
        failed.append({"id": i, "why": ["not run"]})
    return {"pass": not failed and bool(results), "failed": failed,
            "rule": f"every route completed; no crash, stuck, airborne, off route, fell, reversing; "
                    f"vertical bump <= {BUMP_ACC} m/s2"}


def summarise(results: list[dict], expected_ids: list[str] | None = None) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        for k, v in r["event_counts"].items():
            counts[k] = counts.get(k, 0) + v
    return {
        "routes": len(results), "completed": sum(1 for r in results if r["completed"]),
        "events": counts,
        "max_bump_ms2": max((r["max_bump_ms2"] for r in results), default=0.0),
        "max_deviation_m": max((r["max_deviation_m"] for r in results), default=0.0),
        "distance_m": sum(r["progress_m"] for r in results),
        "duration_s": round(sum(r["duration_s"] for r in results), 1),
        "g3": g3(results, expected_ids),
    }


TELEMETRY_COLUMNS = ["t", "x", "y", "z", "vx", "vy", "vz", "speed", "dx", "dy", "heading_deg", "vert_acc",
                     "dev", "off", "lane_off", "progress", "dmg", "steer", "throttle", "brake"]


def telemetry_row(s: dict) -> list:
    h = math.degrees(math.atan2(s.get("dx", 0.0), s.get("dy", 1.0))) % 360   # compass: 0 north, 90 east
    vals = [s["t"], s["x"], s["y"], s["z"], s["vx"], s["vy"], s["vz"], s.get("speed", 0.0), s.get("dx", 0.0),
            s.get("dy", 0.0), h, s.get("vert_acc", 0.0), s.get("dev", 0.0), s.get("off", 0.0),
            s.get("lane_off", s.get("off", 0.0)), s.get("progress", 0.0), s.get("dmg"), s.get("steer"), s.get("throttle"), s.get("brake")]
    return ["" if v is None else (round(v, 3) if isinstance(v, float) else v) for v in vals]
