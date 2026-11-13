#!/usr/bin/env python3
"""Speed limits in mph for every road way of an osm.json (T-408).

MapNG reads `tags.maxspeed` of each OSM road and writes it as `speedLimit` (m/s) on the DecalRoad of the road's
carriageway (`services/beamngMapNavigation.js` parseMaxspeedMps, `exportBeamNGLevel.js` getDecalRoadNavMetadata).
With no `maxspeed` it writes no limit and BeamNG works one out for itself. So the step is: read what OSM says,
fill what it does not say with the Northern Ireland rule for that road, and write the answer back as
`maxspeed = "N mph"` (the one form MapNG reads without a unit mistake), with two extra tags that say where the
number came from. The step changes only tags; geometry and ids stay as they were.

Tags written (all roads that cars use):

    maxspeed                 "30 mph" (canonical; the OSM text moves to maxspeed:osm when it differed)
    belfast:speed_source     tagged | tagged_type | tagged_directional | inherited | default
    belfast:speed_rule       short name of the rule that gave the number (see RULES below)

Ways that cars cannot use (footway, path, steps, cycleway, pedestrian, bridleway ...) get
`belfast:speed_source = "n/a"` and keep their tags otherwise.

The defaults follow the national limits of Northern Ireland (the speed-limit table in the Highway Code for
Northern Ireland, nidirect.gov.uk; the law is the Road Traffic Regulation (Northern Ireland) Order 1997):

    restricted road (street lamps close together; built-up)   30 mph, unless signed otherwise
    single carriageway                                         60 mph
    dual carriageway                                           70 mph
    motorway                                                   70 mph

Where a rule below is ours and not the law's (living streets, service roads, tracks) docs/speed_limits.md says so.

    python pipeline/speed_limits.py apply AREA_OSM_JSON [--report REPORT.json]
    python pipeline/speed_limits.py report AREA_OSM_JSON            # print the table, change nothing
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

KMH_PER_MPH = 1.609344

SOURCE_KEY = "belfast:speed_source"
RULE_KEY = "belfast:speed_rule"
ORIGINAL_KEY = "maxspeed:osm"

# Highway classes cars use. Everything else with a `highway` tag is "n/a".
VEHICLE_HIGHWAYS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential", "living_street", "service", "track", "road",
}
LINKS = {"motorway_link": "motorway", "trunk_link": "trunk", "primary_link": "primary",
         "secondary_link": "secondary", "tertiary_link": "tertiary"}

# --- defaults -----------------------------------------------------------------------------------------------
# (mph, rule name). A rule name is a stable key for the report.
RULES = {
    "motorway": "motorway: 70 (Highway Code NI, motorways)",
    "restricted": "restricted road (lit or built up): 30 (Highway Code NI, built-up areas)",
    "single": "single carriageway outside built-up areas: 60 (Highway Code NI)",
    "dual": "dual carriageway outside built-up areas: 70 (Highway Code NI)",
    "residential": "residential street: 30 (always built up)",
    "living_street": "living street / home zone: 20 (our choice, not a national limit)",
    "service": "service road: 20 (our choice for AI; private land, no national limit)",
    "service_minor": "driveway, parking aisle, drive-through: 10 (our choice)",
    "track": "track: 20 (our choice; not a public road)",
}
BUILT_UP_RADIUS_M = 50.0     # a building this close to the way counts toward "built up"
BUILT_UP_MIN_BUILDINGS = 4   # ... at least this many within the radius of a sample point
BUILT_UP_MIN_SHARE = 0.5     # ... at half or more of the sample points of the way
SAMPLE_STEP_M = 30.0

# --- reading what OSM says ----------------------------------------------------------------------------------
_VALUE = re.compile(r"^(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?$")
BARE_MPH_VALUES = {20, 30, 40, 50, 60, 70}   # a bare number of these in Northern Ireland is a mph limit with the unit left off
STANDARD_MPH = (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70)
TYPE_KEYS = ("maxspeed:type", "source:maxspeed", "zone:maxspeed", "zone:traffic")
_TYPE_FIXED = {"nsl_restricted": 30, "urban": 30, "nsl_single": 60, "nsl_dual": 70, "motorway": 70}


def _round5(mph: float) -> int:
    return max(5, int(round(mph / 5.0)) * 5)


def parse_value(raw) -> tuple[int, str] | None:
    """One `maxspeed` value to (mph, how). `how` is "mph", "kmh", "bare_mph" (unit left off, read as mph), or "walk".

    A list ("30;40") gives its lowest entry. Anything else (none, signals, variable, conditional text) is None.
    """
    text = str(raw or "").strip().lower()
    if not text:
        return None
    best = None
    for part in text.split(";"):
        part = part.strip()
        if part == "walk":
            got = (5, "walk")
        else:
            m = _VALUE.match(part)
            if not m:
                return None
            value = float(m.group(1))
            if value <= 0:
                return None
            if m.group(2) == "mph":
                got = (int(round(value)), "mph")
            elif m.group(2):
                got = (_round5(value / KMH_PER_MPH), "kmh")
            elif int(value) == value and int(value) in BARE_MPH_VALUES:
                got = (int(value), "bare_mph")
            else:
                got = (_round5(value / KMH_PER_MPH), "kmh")
        if best is None or got[0] < best[0]:
            best = got
    return best


def parse_type(raw) -> int | None:
    """maxspeed:type, source:maxspeed, zone:maxspeed values that name a number: GB:nsl_single, GB:20, zone30 ..."""
    for part in str(raw or "").strip().lower().split(";"):
        part = part.strip()
        m = re.fullmatch(r"(?:gb|uk):([a-z_]+)", part)
        if m and m.group(1) in _TYPE_FIXED:
            return _TYPE_FIXED[m.group(1)]
        m = re.fullmatch(r"(?:(?:gb|uk):)?(?:zone:?)?(\d{2})(?:\s*mph)?(?:\s*zone)?", part)
        if m and int(m.group(1)) in STANDARD_MPH and (part.startswith(("gb:", "uk:", "zone")) or part.endswith("zone")):
            return int(m.group(1))
    return None


def tagged_limit(tags: dict) -> tuple[int, str] | None:
    """(mph, source) from the tags, or None. Source is tagged, tagged_type or tagged_directional."""
    got = parse_value(tags.get("maxspeed"))
    if got:
        return got[0], "tagged"
    fwd, bwd = parse_value(tags.get("maxspeed:forward")), parse_value(tags.get("maxspeed:backward"))
    if fwd or bwd:
        return min(g[0] for g in (fwd, bwd) if g), "tagged_directional"
    for key in TYPE_KEYS:
        n = parse_type(tags.get(key))
        if n:
            return n, "tagged_type"
    return None


# --- defaults -----------------------------------------------------------------------------------------------
def is_dual(tags: dict) -> bool:
    if str(tags.get("dual_carriageway", "")).lower() == "yes":
        return True
    base = LINKS.get(tags.get("highway", ""), tags.get("highway", ""))
    return (str(tags.get("oneway", "")).lower() in ("yes", "true", "1") and base in ("motorway", "trunk", "primary")
            and tags.get("junction") != "roundabout" and tags.get("highway") not in LINKS)


def default_limit(tags: dict, built_up: bool) -> tuple[int, str]:
    """(mph, rule) for a road way with no limit in OSM."""
    highway = tags.get("highway", "")
    base = LINKS.get(highway, highway)
    if base == "motorway":
        return 70, "motorway"
    if base == "living_street":
        return 20, "living_street"
    if base == "residential":
        return 30, "residential"
    if base == "service":
        if str(tags.get("service", "")).lower() in ("driveway", "parking_aisle", "drive-through", "drive_through"):
            return 10, "service_minor"
        return 20, "service"
    if base == "track":
        return 20, "track"
    # trunk, primary, secondary, tertiary, unclassified, road
    if str(tags.get("lit", "")).lower() in ("yes", "24/7", "automatic", "sunset-sunrise") or built_up:
        return 30, "restricted"
    return (70, "dual") if is_dual(tags) else (60, "single")


# --- built-up test ------------------------------------------------------------------------------------------
def _local(lat0: float):
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    return lambda p: (p["lng"] * kx, p["lat"] * ky)


def _length_m(geom: list[dict]) -> float:
    to = _local(geom[0]["lat"]) if geom else None
    pts = [to(p) for p in geom]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


class BuildUpIndex:
    """Building density around a way, from the building footprints in the same osm.json."""

    def __init__(self, features: list[dict], radius_m: float = BUILT_UP_RADIUS_M):
        self.radius = radius_m
        self.cell = max(radius_m, 1.0)
        self.cells: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
        buildings = [f for f in features if f.get("type") == "building" and f.get("geometry")]
        self.to = _local(buildings[0]["geometry"][0]["lat"]) if buildings else None
        for f in buildings:
            g = f["geometry"]
            x = sum(self.to(p)[0] for p in g) / len(g)
            y = sum(self.to(p)[1] for p in g) / len(g)
            self.cells[(int(x // self.cell), int(y // self.cell))].append((x, y))
        self.buildings = len(buildings)

    def _count(self, x: float, y: float) -> int:
        cx, cy = int(x // self.cell), int(y // self.cell)
        n = 0
        for i in (cx - 1, cx, cx + 1):
            for j in (cy - 1, cy, cy + 1):
                n += sum(1 for bx, by in self.cells.get((i, j), ()) if math.hypot(bx - x, by - y) <= self.radius)
        return n

    def built_up(self, geom: list[dict]) -> bool:
        if not self.buildings or not geom:
            return False
        pts = [self.to(p) for p in geom]
        samples: list[tuple[float, float]] = [pts[0]]
        carry = 0.0
        for a, b in zip(pts, pts[1:]):
            d = math.dist(a, b)
            t = SAMPLE_STEP_M - carry
            while t <= d and d > 0:
                samples.append((a[0] + (b[0] - a[0]) * t / d, a[1] + (b[1] - a[1]) * t / d))
                t += SAMPLE_STEP_M
            carry = (carry + d) % SAMPLE_STEP_M if d else carry
        samples.append(pts[-1])
        hit = sum(1 for s in samples if self._count(*s) >= BUILT_UP_MIN_BUILDINGS)
        return hit / len(samples) >= BUILT_UP_MIN_SHARE


# --- the step -----------------------------------------------------------------------------------------------
def is_road(f: dict) -> bool:
    return f.get("type") == "road" and bool((f.get("tags") or {}).get("highway"))


def _key(p: dict) -> tuple[int, int]:
    return (round(p["lat"] * 1e6), round(p["lng"] * 1e6))


def _inherit(untagged: dict, ends: dict) -> int | None:
    """Limit from tagged ways of the same name and class that meet this way at an end, if they agree.

    Only when the way has a name and at least one such neighbour is tagged: a road keeps its limit through a
    split in OSM, and a limit only changes at a junction with another road, not at a split.
    """
    t = untagged["tags"]
    name = t.get("name")
    if not name:
        return None
    g = untagged["geometry"]
    values = set()
    for end in (g[0], g[-1]):
        for other in ends.get(_key(end), ()):
            if other is untagged:
                continue
            ot = other["tags"]
            if ot.get("name") == name and ot.get("highway") == t.get("highway"):
                values.add(other["_mph"])
    return values.pop() if len(values) == 1 else None


def apply(features: list[dict]) -> dict:
    """Write mph limits into the tags of every road feature in place; return the per-feature records."""
    idx = BuildUpIndex(features)
    roads = [f for f in features if is_road(f)]
    state: dict[int, dict] = {}
    for f in roads:
        t = f["tags"]
        rec = {"highway": t["highway"], "len_m": _length_m(f.get("geometry") or [])}
        if t["highway"] not in VEHICLE_HIGHWAYS:
            rec.update(source="n/a", mph=None, rule=None)
        elif SOURCE_KEY in t:      # already done (the step is safe to run twice)
            rec.update(source=t[SOURCE_KEY], rule=t.get(RULE_KEY), mph=parse_value(t.get("maxspeed"))[0])
        else:
            got = tagged_limit(t)
            if got:
                rec.update(mph=got[0], source=got[1], rule=None)
            else:
                rec.update(mph=None, source=None, rule=None)
        state[id(f)] = rec
    # untagged ways: a tagged neighbour of the same road first, then the national rule
    ends: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for f in roads:
        rec = state[id(f)]
        if rec["mph"] and rec["source"] != "n/a" and f.get("geometry"):
            f["_mph"] = rec["mph"]
            for p in (f["geometry"][0], f["geometry"][-1]):
                ends[_key(p)].append(f)
    for f in roads:
        rec = state[id(f)]
        if rec["source"] is not None:
            continue
        t = f["tags"]
        inherited = _inherit(f, ends) if f.get("geometry") else None
        if inherited:
            rec.update(mph=inherited, source="inherited", rule="same road, tagged neighbour")
        else:
            mph, rule = default_limit(t, idx.built_up(f.get("geometry") or []))
            rec.update(mph=mph, source="default", rule=rule)
    for f in roads:
        f.pop("_mph", None)
        rec = state[id(f)]
        t = f["tags"]
        t[SOURCE_KEY] = rec["source"]
        if rec["source"] == "n/a":
            continue
        t[RULE_KEY] = rec["rule"] or "osm"
        text = f"{rec['mph']} mph"
        if "maxspeed" in t and t["maxspeed"] != text and ORIGINAL_KEY not in t:
            t[ORIGINAL_KEY] = t["maxspeed"]
        t["maxspeed"] = text
    return state


# --- the report ---------------------------------------------------------------------------------------------
def summarise(features: list[dict], state: dict) -> dict:
    """Counts by highway class: ways (features), kilometres, and the source of each number."""
    by: dict[str, dict] = {}
    for f in features:
        rec = state.get(id(f))
        if rec is None:
            continue
        row = by.setdefault(rec["highway"], {"features": 0, "km": 0.0, "source": Counter(), "mph": Counter()})
        row["features"] += 1
        row["km"] += rec["len_m"] / 1000.0
        row["source"][rec["source"]] += 1
        if rec["mph"]:
            row["mph"][rec["mph"]] += 1
    vehicle = {k: v for k, v in by.items() if k in VEHICLE_HIGHWAYS}
    missing = sum(1 for f in features if is_road(f) and f["tags"]["highway"] in VEHICLE_HIGHWAYS
                  and not parse_value(f["tags"].get("maxspeed")))
    return {
        "vehicle_road_features": sum(r["features"] for r in vehicle.values()),
        "vehicle_road_km": round(sum(r["km"] for r in vehicle.values()), 2),
        "vehicle_road_features_without_limit": missing,
        "by_class": {k: {"features": v["features"], "km": round(v["km"], 2), "source": dict(v["source"]),
                         "mph": {str(m): n for m, n in sorted(v["mph"].items())}} for k, v in sorted(by.items())},
    }


def markdown_table(summary: dict) -> str:
    cols = ("tagged", "tagged_type", "tagged_directional", "inherited", "default")
    head = "| Class | Ways | km | tagged | via type or zone | via direction | inherited | defaulted | limits (mph: ways) |"
    rows = [head, "|---|---|---|---|---|---|---|---|---|"]
    tot = Counter()
    for k, v in summary["by_class"].items():
        if v["source"].keys() == {"n/a"}:
            continue
        s = v["source"]
        lim = ", ".join(f"{m}: {n}" for m, n in v["mph"].items())
        rows.append(f"| {k} | {v['features']} | {v['km']} | " + " | ".join(str(s.get(c, 0)) for c in cols) + f" | {lim} |")
        tot["features"] += v["features"]
        tot["km"] += v["km"]
        for c in cols:
            tot[c] += s.get(c, 0)
    rows.append(f"| **all** | {tot['features']} | {round(tot['km'], 2)} | " + " | ".join(str(tot[c]) for c in cols) + " | |")
    return "\n".join(rows)


def run(path: Path, write: bool, report: Path | None = None) -> dict:
    features = json.loads(Path(path).read_text(encoding="utf-8"))
    state = apply(features)
    summary = summarise(features, state)
    if write:
        Path(path).write_text(json.dumps(features), encoding="utf-8")
    if report:
        Path(report).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("apply", "report"))
    ap.add_argument("osm_json", type=Path)
    ap.add_argument("--report", type=Path, help="write the summary as JSON")
    a = ap.parse_args(argv)
    summary = run(a.osm_json, a.command == "apply", a.report)
    print(markdown_table(summary))
    print(f"\nvehicle road ways: {summary['vehicle_road_features']}, "
          f"without a limit after the step: {summary['vehicle_road_features_without_limit']}")
    return 0 if summary["vehicle_road_features_without_limit"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
