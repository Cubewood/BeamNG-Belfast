"""Road widths for MapNG (T-406): DfI where matched, then OSM tags, then Northern Ireland class defaults.

    uv run --no-project --python 3.12 python pipeline/road_widths.py apply H:\\BelfastData\\mapng\\areas\\NAME\\osm.json
    uv run --no-project --python 3.12 python pipeline/road_widths.py derive      # rebuild config/road_width_defaults.json
    uv run --no-project --python 3.12 python pipeline/road_widths.py report  osm.json

MapNG draws a road as wide as its `width` tag (the whole carriageway, kerb to kerb) and otherwise as lanes x a class
lane width, plus a parking lane each side in town, which is why an untagged Belfast street came out 10.6 m wide where
DfI measures 5.6 m. This step writes a `width` tag on every road way of an osm.json (as pipeline/speed_limits.py does
for `maxspeed`), so MapNG reads it through the path it already has. Nothing in MapNG is edited.

Order, per OSM way (one width per way; the `_seg_N` pieces share it):

1. **DfI** (`pipeline/dfi.py`, T-205, `H:\\BelfastData\\dfi\\widths_full.json`): the way's matched width when the
   match is high or medium confidence. A low confidence match is kept only when the width lies within the 10th to
   90th percentile of the DfI widths of that class (a flared junction mouth or a car park edge does not), and never for
   service roads (DfI adopts few of them and its low matches there are mostly car parks).
2. **OSM tags**: an existing `width` stays (the original is kept as `width:osm` when DfI overrides it); a `lanes` tag
   picks the DfI median of that class and direction with that lane count when the table has a row for it (8 ways or
   more), else the class default below. (Scaling a width by the lane count was tried and gave 4.1 m for a one-lane
   trunk road: a lane count is no good as a multiplier.)
3. **Class default**: the DfI median of high and medium confidence ways of that class and direction with no `lanes`
   tag, from `config/road_width_defaults.json` (made by `derive` from the full DfI match, 5,544 ways).
   Service roads and tracks have no default: their width depends on what they serve (driveway, parking aisle), so
   MapNG's own defaults stay.

MapNG limits a `width` to its class range (roadWidth.js `getRoadHalfWidthClamp`; the whole carriageway to twice the
minimum half width up to twice the maximum plus 2.3 m). A DfI width under the minimum is reached with
`mapng:carriageway_max`, MapNG's own cap tag (roadWidth.js: narrows below the class minimum; the extras give way
first); one over the maximum cannot be drawn without a MapNG patch and is listed in the report as `unreachable`.

The lanes' width stays what MapNG would have given the way (lanes x class lane width, within its class range): MapNG
takes `width` as the whole carriageway and, with no parking or shoulder tag, makes ALL of it the driving (AI) width,
so a one-lane one-way carriageway of 14.3 m (DfI, Hospital Road) became one 14.3 m AI lane whose node radius merged
the two carriageways of the dual road 6 m apart: the game's graph lost the eastbound link (T-406, `docs/dfi_widths.md`).
Where the carriageway is more than 1.6 m wider than that driving width, the rest is written as `shoulder:both:width`
(half each side), which MapNG draws as paved edge and keeps out of the AI path, as it does for a parking lane.

Tags written: `width` (metres, one decimal), `belfast:width_source` (`dfi-high`, `dfi-medium`, `dfi-low`, `osm-width`,
`osm-lanes`, `default`, or `none` when left to MapNG), `width:osm` (original, when replaced), `belfast:width_cap`
(`yes` when `mapng:carriageway_max` was set), `shoulder:both:width` with `belfast:shoulder=yes` (see above). Safe to run twice: a way that carries the source tag is restored first.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DFI = Path(r"H:\BelfastData\dfi\widths_full.json")
DEFAULT_TABLE = ROOT / "config" / "road_width_defaults.json"

SOURCE_KEY = "belfast:width_source"
ORIGINAL_KEY = "width:osm"
CAP_FLAG = "belfast:width_cap"
CAP_KEY = "mapng:carriageway_max"
SHOULDER_KEY = "shoulder:both:width"      # MapNG keeps the lanes' width and draws the rest as an edge strip
SHOULDER_FLAG = "belfast:shoulder"
SHOULDER_SLACK_M = 1.6                    # a carriageway this much over the lanes gets no strip: a strip under 0.8 m a side puts the kerb face within 0.5 m of the lane edge (I-006 kerb check)

VEHICLE_HIGHWAYS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential", "living_street", "service", "track", "road",
}
NO_DEFAULT = {"service", "track"}          # width depends on what they serve: left to MapNG
MIN_N = 8                                  # a table row needs this many ways
TIERS_FOR_TABLE = ("high", "medium")

# MapNG roadWidth.js getRoadHalfWidthClamp (half widths, metres) and PARKING_LANE_M; a `width` tag is clamped to
# [2 x min, 2 x (max + PARKING_LANE_M)] as a carriageway.
PARKING_LANE_M = 2.3


def mapng_clamp(highway: str) -> tuple[float, float]:
    if highway in ("motorway", "motorway_link", "trunk", "trunk_link"):
        lo, hi = 3.5, 9.0
    elif highway in ("primary", "primary_link", "secondary", "secondary_link"):
        lo, hi = 2.8, 6.0
    elif highway in ("service", "track"):
        lo, hi = 1.8, 3.5
    else:
        lo, hi = 2.2, 5.0
    return 2 * lo, 2 * (hi + PARKING_LANE_M)


def lane_width_m(highway: str) -> float:
    """MapNG getDefaultLaneWidthMeters."""
    if highway in ("motorway", "motorway_link", "trunk", "trunk_link"):
        return 3.7
    if highway in ("primary", "primary_link", "secondary", "secondary_link"):
        return 3.5
    if highway in ("tertiary", "tertiary_link"):
        return 3.25
    if highway in ("service", "track"):
        return 2.8
    return 3.0


def default_lanes(highway: str, oneway: bool) -> int:
    """MapNG getDefaultLaneCount."""
    if highway in ("motorway", "trunk"):
        return 2 if oneway else 4
    if highway in ("motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "service", "track"):
        return 1
    return 1 if oneway else 2


def driving_width(tags: dict) -> float:
    """The driving width MapNG would give the way from its lanes (roadWidth.js estimateRoadHalfWidth with no `width`):
    max(default lanes, `lanes`, lanes:forward + lanes:backward) x class lane width, within the class range."""
    hw = tags["highway"]
    fwd, bwd = parse_lanes(tags.get("lanes:forward")) or 0, parse_lanes(tags.get("lanes:backward")) or 0
    lanes = max(default_lanes(hw, is_oneway(tags)), parse_lanes(tags.get("lanes")) or fwd + bwd)
    lo, hi = {"motorway": (3.5, 9.0), "trunk": (3.5, 9.0), "primary": (2.8, 6.0), "secondary": (2.8, 6.0),
              "service": (1.8, 3.5), "track": (1.8, 3.5)}.get(hw.replace("_link", ""), (2.2, 5.0))
    return 2 * max(lo, min(hi, lanes * lane_width_m(hw) / 2))


def way_id(feature_id) -> str:
    return str(feature_id).split("_seg")[0]


def is_oneway(tags: dict) -> bool:
    v = str(tags.get("oneway", "")).strip().lower()
    if v in ("yes", "1", "true", "-1", "reverse"):
        return True
    return tags.get("junction") == "roundabout" or tags.get("highway") in ("motorway", "motorway_link")


def parse_width(raw) -> float | None:
    """A plain metre value ('6', '6.5', '6.5 m'); anything else (feet, lists, text) is not trusted."""
    if raw is None:
        return None
    s = str(raw).strip().lower().replace(",", ".")
    if s.endswith("m") and not s.endswith("mm"):
        s = s[:-1].strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return v if 1.5 <= v <= 40 else None


def parse_lanes(raw) -> int | None:
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return v if 1 <= v <= 8 else None


# ------------------------------------------------------------------ the class table

def _q(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(p * (len(s) - 1) + 0.5)))]


def derive_table(dfi_ways: dict, dfi_inputs: dict | None = None) -> dict:
    """Class defaults from DfI: median (and p10, p90) of high and medium confidence ways, by class, direction and lanes."""
    groups: dict[str, list[float]] = defaultdict(list)
    for w in dfi_ways.values():
        if w.get("tier") not in TIERS_FOR_TABLE or not w.get("width_m") or w.get("highway") not in VEHICLE_HIGHWAYS:
            continue
        ow = "oneway" if str(w.get("oneway") or "").lower() in ("yes", "1", "-1", "reverse") or w["highway"] in (
            "motorway", "motorway_link") else "twoway"
        lanes = parse_lanes(w.get("lanes"))
        groups[f"{w['highway']}|{ow}|{lanes if lanes else 'none'}"].append(float(w["width_m"]))
        groups[f"{w['highway']}|{ow}|any"].append(float(w["width_m"]))
    rows = {}
    for key in sorted(groups):
        v = groups[key]
        if len(v) >= MIN_N:
            rows[key] = {"n": len(v), "p10": round(_q(v, 0.1), 2), "median": round(statistics.median(v), 2),
                         "p90": round(_q(v, 0.9), 2)}
    return {
        "source": "DfI Roads carriageway widths matched to OSM ways by pipeline/dfi.py (T-205), the full box "
                  "(three AOI squares), high and medium confidence only; Open Government Licence v3.0",
        "rule": f"median width in metres of the ways of a class, direction and lanes tag (none = no lanes tag); a row needs {MIN_N} ways",
        "dfi_inputs": dfi_inputs or {},
        "rows": rows,
    }


def class_default(table: dict, highway: str, oneway: bool, lanes: int | None) -> tuple[float, str] | None:
    """(width, how) from the table, or None when the class has no usable row."""
    if highway in NO_DEFAULT:
        return None
    rows = table["rows"]
    d = "oneway" if oneway else "twoway"
    if lanes:
        exact = rows.get(f"{highway}|{d}|{lanes}")
        if exact:
            return exact["median"], "osm-lanes"
    none = rows.get(f"{highway}|{d}|none") or rows.get(f"{highway}|{d}|any")
    if none:
        return none["median"], "default"
    return None


def plausible_low(table: dict, highway: str, oneway: bool, width: float) -> bool:
    """A low confidence DfI width is believed only inside the p10 to p90 of its class (never for service roads)."""
    if highway in NO_DEFAULT:
        return False
    row = table["rows"].get(f"{highway}|{'oneway' if oneway else 'twoway'}|any")
    return bool(row) and row["p10"] <= width <= row["p90"]


# ------------------------------------------------------------------ the step

def decide(tags: dict, dfi: dict | None, table: dict) -> tuple[float | None, str, str]:
    """(width, source, note) for one way from its original tags. width None: leave it to MapNG."""
    hw = tags.get("highway")
    ow = is_oneway(tags)
    if dfi and dfi.get("width_m"):
        tier = dfi.get("tier")
        w = float(dfi["width_m"])
        if tier in TIERS_FOR_TABLE:
            return w, f"dfi-{tier}", ""
        if plausible_low(table, hw, ow, w):
            return w, "dfi-low", ""
    orig = parse_width(tags.get("width"))
    if orig:
        return orig, "osm-width", ""
    got = class_default(table, hw, ow, parse_lanes(tags.get("lanes")))
    if got:
        return got[0], got[1], ""
    return None, "none", ""


def restore(tags: dict) -> None:
    if SOURCE_KEY not in tags:
        return
    if ORIGINAL_KEY in tags:
        tags["width"] = tags.pop(ORIGINAL_KEY)
    elif tags[SOURCE_KEY] != "none":
        tags.pop("width", None)
    if tags.pop(CAP_FLAG, None):
        tags.pop(CAP_KEY, None)
    if tags.pop(SHOULDER_FLAG, None):
        tags.pop(SHOULDER_KEY, None)
    del tags[SOURCE_KEY]


def fmt(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def apply(features: list[dict], dfi_ways: dict, table: dict) -> dict:
    """Write the width tags in place. Returns the report."""
    rows = []
    for f in features:
        if f.get("type") != "road":
            continue
        tags = f.get("tags") or {}
        if tags.get("highway") not in VEHICLE_HIGHWAYS or tags.get("area") == "yes":
            continue
        restore(tags)
        f["tags"] = tags
        w, source, _ = decide(tags, dfi_ways.get(way_id(f.get("id"))), table)
        tags[SOURCE_KEY] = source
        capped = False
        unreachable = False
        if w is not None:
            lo, hi = mapng_clamp(tags["highway"])
            if "width" in tags:
                tags[ORIGINAL_KEY] = tags["width"]
            tags["width"] = fmt(w)
            if w < lo - 0.05:
                tags[CAP_KEY] = fmt(w)
                tags[CAP_FLAG] = "yes"
                capped = True
            elif w > hi + 0.05:
                unreachable = True
            if not any(k.startswith(("shoulder", "parking")) for k in tags):
                drive = driving_width(tags)
                if w > drive + SHOULDER_SLACK_M:
                    tags[SHOULDER_KEY] = fmt((w - drive) / 2)
                    tags[SHOULDER_FLAG] = "yes"
        rows.append({"id": str(f.get("id")), "way": way_id(f.get("id")), "highway": tags["highway"], "source": source,
                     "width": w, "capped": capped, "unreachable": unreachable, "km": _length_km(f.get("geometry") or [])})
    return summarise(rows)


def _length_km(geom: list[dict]) -> float:
    import math
    if len(geom) < 2:
        return 0.0
    k = 111320.0 * math.cos(math.radians(geom[0]["lat"]))
    return sum(math.hypot((b["lng"] - a["lng"]) * k, (b["lat"] - a["lat"]) * 111320.0)
               for a, b in zip(geom, geom[1:])) / 1000.0


def summarise(rows: list[dict]) -> dict:
    by_source: dict[str, dict] = {}
    for r in rows:
        s = by_source.setdefault(r["source"], {"pieces": 0, "km": 0.0})
        s["pieces"] += 1
        s["km"] += r["km"]
    by_class: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_class[r["highway"]][r["source"]] += 1
    return {
        "road_pieces": len(rows),
        "by_source": {k: {"pieces": v["pieces"], "km": round(v["km"], 2)} for k, v in sorted(by_source.items())},
        "by_class": {k: dict(v) for k, v in sorted(by_class.items())},
        "capped_below_class_minimum": sorted({r["way"] for r in rows if r["capped"]}),
        "unreachable_over_class_maximum": [{"way": r["way"], "highway": r["highway"], "width": r["width"]}
                                           for r in rows if r["unreachable"]],
        "left_to_mapng": sorted({r["way"] for r in rows if r["width"] is None}),
    }


def run(path: Path, dfi_path: Path = DEFAULT_DFI, table_path: Path = DEFAULT_TABLE, write: bool = True,
        report: Path | None = None) -> dict:
    features = json.loads(Path(path).read_text(encoding="utf-8"))
    dfi_ways = json.loads(Path(dfi_path).read_text(encoding="utf-8"))["ways"]
    table = json.loads(Path(table_path).read_text(encoding="utf-8"))
    summary = apply(features, dfi_ways, table)
    summary["dfi"] = str(dfi_path)
    if write:
        Path(path).write_text(json.dumps(features), encoding="utf-8")
    if report:
        Path(report).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("apply", "report", "derive"))
    ap.add_argument("osm_json", nargs="?", type=Path)
    ap.add_argument("--dfi", type=Path, default=DEFAULT_DFI)
    ap.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    ap.add_argument("--report", type=Path)
    a = ap.parse_args(argv)
    if a.command == "derive":
        doc = json.loads(a.dfi.read_text(encoding="utf-8"))
        table = derive_table(doc["ways"], doc.get("inputs"))
        a.table.parent.mkdir(parents=True, exist_ok=True)
        a.table.write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
        print(f"{len(table['rows'])} rows -> {a.table}")
        return 0
    if not a.osm_json:
        ap.error("osm_json is required")
    summary = run(a.osm_json, a.dfi, a.table, write=a.command == "apply", report=a.report)
    print(json.dumps(summary["by_source"]))
    print(f"capped below class minimum: {len(summary['capped_below_class_minimum'])} ways; "
          f"over class maximum (not reachable): {len(summary['unreachable_over_class_maximum'])}; "
          f"left to MapNG: {len(summary['left_to_mapng'])} ways")
    return 0


if __name__ == "__main__":
    sys.exit(main())
