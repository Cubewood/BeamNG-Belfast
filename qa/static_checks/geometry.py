"""Shared geometry for the static checks: drivable lanes, centreline sampling, spatial lookups."""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.strtree import STRtree

from .level import DecalRoad, Level


def dedupe_points(xy: np.ndarray) -> np.ndarray:
    keep = [0]
    for i in range(1, len(xy)):
        if np.hypot(*(xy[i] - xy[keep[-1]])) > 1e-6:
            keep.append(i)
    return xy[keep]


def nav_roads(level: Level) -> list[DecalRoad]:
    """DecalRoads in the game's navigation graph: `drivability` above 0 (MapNG: the `_ai` roads plus a few body
    roads that have no `_ai` twin). The export report counts 537 of these on the Slice; the game's own graph
    (T-302 dump) has the same number of road islands as this set joined at 3 m."""
    return [r for r in level.roads if (r.props.get("drivability") or 0) > 0 and len(dedupe_points(r.xy)) > 1]


def lane_roads(level: Level) -> list[tuple[DecalRoad, float, bool]]:
    """(road, half width of its driving lanes, in the nav graph) for every road a car can drive on.

    MapNG draws an invisible `_ai` DecalRoad per carriageway: its width is the driving width (lanes x lane
    width) and the game's AI drives on it. That is the lane layout the checks use. A body road with no `_ai`
    twin (junction pieces, service roads and driveways) is used with its drawn width less 2 m (parking, gutter
    and edge paint are not driving lanes)."""
    out: list[tuple[DecalRoad, float, bool]] = []
    ai_names = set()
    for r in level.roads:
        if r.is_ai and r.material == "road_invisible":
            ai_names.add(r.name)
            out.append((r, float(np.median(r.nodes[:, 3])) / 2.0, (r.props.get("drivability") or 0) > 0))
    for r in level.roads:
        if r.is_vehicle_body and r.twin_ai_name not in ai_names:
            out.append((r, max(1.5, float(np.median(r.nodes[:, 3])) / 2.0 - 1.0), (r.props.get("drivability") or 0) > 0))
    return out


def lane_corridor(roads, shrink: float = 0.0):
    """Union polygon of the driving lanes (flat ends, so a dead end does not grow past its last node)."""
    parts = []
    for r, half, *_ in roads:
        pts = dedupe_points(r.xy)
        if len(pts) < 2:
            continue
        h = half - shrink
        if h > 0:
            parts.append(LineString(pts).buffer(h, cap_style="flat", join_style="round"))
    return shapely.unary_union(parts) if parts else shapely.Polygon()


class LineIndex:
    """Nearest-line lookups over many polylines."""

    def __init__(self, lines: list[np.ndarray]):
        self.lines = [LineString(dedupe_points(p)) if len(dedupe_points(p)) > 1 else None for p in lines]
        self.valid = [i for i, l in enumerate(self.lines) if l is not None]
        self.geoms = [self.lines[i] for i in self.valid]
        self.tree = STRtree(self.geoms) if self.geoms else None

    def nearest(self, x: float, y: float, limit: float):
        """(index in the original list, distance) of the nearest line within `limit`, or None."""
        if self.tree is None:
            return None
        pt = shapely.Point(x, y)
        idx = self.tree.query(pt.buffer(limit))
        best = None
        for k in idx:
            d = self.geoms[k].distance(pt)
            if d <= limit and (best is None or d < best[1]):
                best = (self.valid[k], d)
        return best

    def nearest_parallel(self, x: float, y: float, tangent, limit: float, min_cos: float = 0.85):
        """Nearest line within `limit` that runs along `tangent` (either way) at its closest point."""
        if self.tree is None:
            return None
        pt = shapely.Point(x, y)
        best = None
        for k in self.tree.query(pt.buffer(limit)):
            g = self.geoms[k]
            d = g.distance(pt)
            if d > limit or (best is not None and d >= best[1]):
                continue
            s = g.project(pt)
            a, b = g.interpolate(max(0.0, s - 1.0)), g.interpolate(min(g.length, s + 1.0))
            v = np.array([b.x - a.x, b.y - a.y])
            n = np.hypot(*v)
            if n > 0 and abs(float(np.dot(v / n, tangent))) >= min_cos:
                best = (self.valid[k], d)
        return best


def resample(xy: np.ndarray, step: float) -> tuple[np.ndarray, np.ndarray]:
    """Points every `step` metres along a polyline, and the unit tangent at each (x east, y north)."""
    xy = dedupe_points(xy)
    seg = np.diff(xy, axis=0)
    length = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(length)])
    total = cum[-1]
    n = max(2, int(math.floor(total / step)) + 1)
    s = np.linspace(0, total, n) if total > 0 else np.zeros(1)
    px = np.interp(s, cum, xy[:, 0])
    py = np.interp(s, cum, xy[:, 1])
    k = np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(seg) - 1)
    t = seg[k] / np.maximum(length[k], 1e-9)[:, None]
    return np.stack([px, py], axis=1), t


def interp_z(road: DecalRoad, pts: np.ndarray) -> np.ndarray:
    """Road height at resampled points (projected onto the node chain by distance along it)."""
    xy = road.xy
    seg = np.diff(xy, axis=0)
    cum = np.concatenate([[0.0], np.cumsum(np.hypot(seg[:, 0], seg[:, 1]))])
    s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]) if len(pts) > 1 else np.zeros(1)
    return np.interp(s, cum, road.nodes[:, 2])


def dist_point_polyline(p, poly: np.ndarray) -> float:
    return float(LineString(dedupe_points(poly)).distance(shapely.Point(*p))) if len(dedupe_points(poly)) > 1 else float(np.hypot(*(poly[0] - p)))


def finding(kind: str, x=None, y=None, where="", severity="medium", value=None, metric="", detail="") -> dict:
    f = {"kind": kind, "severity": severity}
    if x is not None and y is not None:
        f["x"] = round(float(x), 1)
        f["y"] = round(float(y), 1)
    if where:
        f["where"] = where
    if value is not None:
        f["value"] = round(float(value), 3)
    if metric:
        f["metric"] = metric
    if detail:
        f["detail"] = detail
    return f
