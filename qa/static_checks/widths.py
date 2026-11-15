"""Road widths against the DfI figure (T-406, G0: within 0.75 m of DfI where DfI data exists).

DfI (pipeline/dfi.py, T-205) gives one carriageway width per OSM way: the whole carriageway, kerb to kerb, of the
section matched to that way. The level draws every carriageway as a visible body DecalRoad (`_b`) whose node width is
the full carriageway; its invisible `_ai` twin is the driving width and is not what DfI measures. So the drawn width is
the body road's node width at the OSM way, sampled along the way and reduced to the median per way.

Where the two do not describe one thing the check leaves the sample out rather than guess:

- the first and last `width_end_skip_m` of each OSM segment (junction mouths, flares and the tapers MapNG draws
  between two widths);
- samples with no body road within `width_match_m` that runs along the way (a way MapNG did not draw, or drew away
  from its line: `road-missing` reports those);
- a way with fewer than `width_min_samples` samples.

A dual carriageway is two one-way OSM ways, each with its own DfI section, so each is compared with its own body road.
Lead-in pieces and slip roads are drawn as body roads and are compared like any other.
Findings: `road-width-off-dfi`, `medium` when the DfI match is high or medium confidence, `low` when low confidence.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString

from .geometry import LineIndex, dedupe_points, finding, resample
from .level import DRIVABLE_HIGHWAYS, Level

DEFAULT_DFI = Path(r"H:\BelfastData\dfi\widths_full.json")


def way_of(osm_id: str) -> str:
    return str(osm_id).split("_seg")[0]


def _body_roads(level: Level) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """(name, deduplicated xy, node widths) of every visible vehicle carriageway."""
    out = []
    for r in level.roads:
        if not r.is_vehicle_body:
            continue
        keep = [0]
        for i in range(1, len(r.xy)):
            if np.hypot(*(r.xy[i] - r.xy[keep[-1]])) > 1e-6:
                keep.append(i)
        if len(keep) > 1:
            out.append((r.name, r.xy[keep], r.nodes[keep, 3]))
    return out


def _width_at(xy: np.ndarray, widths: np.ndarray, line: LineString, x: float, y: float) -> float:
    seg = np.hypot(*np.diff(xy, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    s = line.project(shapely.Point(x, y))
    return float(np.interp(s, cum, widths))


def load_dfi(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["ways"]


def drawn_widths(level: Level, c: dict) -> dict[str, dict]:
    """Per OSM way: the drawn carriageway width at each sample ({samples: [(x, y, width, road)], name, highway, tags})."""
    bodies = _body_roads(level)
    idx = LineIndex([b[1] for b in bodies])
    out: dict[str, dict] = {}
    for f in level.osm:
        if f.type != "road" or f.tags.get("highway") not in DRIVABLE_HIGHWAYS or len(f.xy) < 2:
            continue
        pts, tans = resample(f.xy, c["width_sample_m"])
        total = float(np.sum(np.hypot(*np.diff(dedupe_points(f.xy), axis=0).T)))
        skip = min(c["width_end_skip_m"], total / 4.0)
        # distance along the segment of each resampled point
        along = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]) if len(pts) > 1 else np.zeros(1)
        rec = out.setdefault(way_of(f.id), {"samples": [], "tags": f.tags, "length_m": 0.0})
        rec["length_m"] += total
        for p, t, s in zip(pts, tans, along):
            if s < skip or s > along[-1] - skip:
                continue
            hits = idx.parallel_all(p[0], p[1], t, c["width_match_m"], min_cos=0.9)
            if not hits:
                continue
            k, _ = hits[0]
            name, xy, w = bodies[k]
            rec["samples"].append((float(p[0]), float(p[1]), _width_at(xy, w, idx.lines[k], p[0], p[1]), name))
    return out


def check_road_widths(level: Level, c: dict, dfi: dict | None = None) -> tuple[list[dict], dict]:
    """(findings, info). `dfi` is `ways` of a pipeline/dfi.py widths file (way id to {width_m, tier, ...})."""
    if dfi is None:
        path = Path(c.get("dfi_widths") or DEFAULT_DFI)
        if not path.exists():
            return ([finding("check-not-run", where="widths", severity="medium",
                             detail=f"no DfI widths file at {path}: run pipeline/dfi.py match")], {})
        dfi = load_dfi(path)
    tol = c["width_tol_m"]
    drawn = drawn_widths(level, c)
    findings: list[dict] = []
    info = {"dfi_ways_in_level": 0, "compared": 0, "within": 0, "dfi_km": 0.0, "compared_km": 0.0, "within_km": 0.0,
            "not_compared": 0, "by_tier": {}, "worst_m": 0.0}
    for wid, rec in sorted(drawn.items()):
        d = dfi.get(wid)
        if not d or not d.get("width_m"):
            continue
        info["dfi_ways_in_level"] += 1
        info["dfi_km"] += rec["length_m"] / 1000.0
        if len(rec["samples"]) < c["width_min_samples"]:
            info["not_compared"] += 1
            continue
        w = np.array([s[2] for s in rec["samples"]])
        med = float(np.median(w))
        diff = med - float(d["width_m"])
        tier = d.get("tier", "?")
        t = info["by_tier"].setdefault(tier, {"compared": 0, "within": 0})
        info["compared"] += 1
        info["compared_km"] += rec["length_m"] / 1000.0
        t["compared"] += 1
        if abs(diff) <= tol:
            info["within"] += 1
            info["within_km"] += rec["length_m"] / 1000.0
            t["within"] += 1
            continue
        info["worst_m"] = max(info["worst_m"], abs(diff))
        mid = rec["samples"][len(rec["samples"]) // 2]
        name = rec["tags"].get("name") or f"way {wid}"
        src = rec["tags"].get("belfast:width_source", "-")
        findings.append(finding(
            "road-width-off-dfi", mid[0], mid[1], f"{name} (OSM way {wid})", "low" if tier == "low" else "medium", diff,
            f"drawn {med:.1f} m, DfI {d['width_m']:.1f} m",
            f"{rec['tags'].get('highway')}, {len(rec['samples'])} samples on {mid[3]}; DfI {tier} confidence "
            f"{d.get('confidence')}, section {d.get('section_main')}; width source {src}"))
    for k in ("dfi_km", "compared_km", "within_km", "worst_m"):
        info[k] = round(info[k], 2)
    return findings, info
