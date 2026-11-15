#!/usr/bin/env python3
"""Height profile along a game-frame line: the built height file against the raw OSNI and LiDAR rasters (I-008).

    uv run --no-project --python 3.12 --with numpy --with pyproj --with pyyaml python pipeline/elevation_probe.py \\
        slice --from -215 -146 --to -180 -146 [--window 12]

Answers "is this drop in the height file really in the source data?": for each point it prints the height in
`gpxz.f32`, the raw OSNI 10 m raster (bilinear) and the LiDAR (NaN where it has none), then the largest drop
within `--window` metres of path for each, the spread of (file - OSNI), and the distance to the nearest LiDAR
pixel. Read-only; reads H:\\BelfastData only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aoi  # noqa: E402
import elevation as el  # noqa: E402


def line_points(p0, p1, step: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Points every `step` m (the last one at or before p1) on the line p0 -> p1: x, y, distance along the line."""
    length = float(np.hypot(p1[0] - p0[0], p1[1] - p0[1]))
    s = np.arange(0.0, length + 1e-9, step)
    t = s / length if length > 0 else np.zeros_like(s)
    return p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1]), s


def largest_drop(s: np.ndarray, z: np.ndarray, window: float) -> tuple[float, float, float]:
    """The biggest fall z[i] - z[j] with s[i] < s[j] <= s[i] + window. Returns (drop, s_i, s_j); NaN samples are skipped."""
    s = np.asarray(s, float)
    z = np.asarray(z, float)
    best = (0.0, float("nan"), float("nan"))
    for i in range(len(s)):
        if not np.isfinite(z[i]):
            continue
        for j in range(i + 1, len(s)):
            if s[j] - s[i] > window:
                break
            if np.isfinite(z[j]) and z[i] - z[j] > best[0]:
                best = (float(z[i] - z[j]), float(s[i]), float(s[j]))
    return best


def sample_file(heights: np.ndarray, area: aoi.Area, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Height-file value at game points, bilinear over the game's pixel grid (col = x + n/2, row = n/2 - 1 - y)."""
    col, row = area.game_to_pixel(x, y)
    h, w = heights.shape
    c0 = np.clip(np.floor(col).astype(int), 0, w - 2)
    r0 = np.clip(np.floor(row).astype(int), 0, h - 2)
    tc = np.clip(col - c0, 0, 1)
    tr = np.clip(row - r0, 0, 1)
    return (heights[r0, c0] * (1 - tc) * (1 - tr) + heights[r0, c0 + 1] * tc * (1 - tr)
            + heights[r0 + 1, c0] * (1 - tc) * tr + heights[r0 + 1, c0 + 1] * tc * tr)


def nearest_lidar_m(source_mask: np.ndarray, area: aoi.Area, x: float, y: float) -> float:
    """Distance in game metres from (x, y) to the nearest pixel of the source mask that is 0 (LiDAR); inf if none."""
    rows, cols = np.nonzero(source_mask == 0)
    if rows.size == 0:
        return float("inf")
    c, r = area.game_to_pixel(x, y)
    return float(np.hypot(cols - float(c), rows - float(r)).min() * area.mpp)


def probe(cfg: aoi.Config, area_name: str, p0, p1, window: float, heights: np.ndarray, lidar: el.Raster, osni: el.Raster,
          source_mask: np.ndarray | None = None, step: float = 1.0) -> dict:
    area = cfg.area(area_name)
    x, y, s = line_points(p0, p1, step)
    lat, lon = area.game_to_latlon(x, y)
    e, n = cfg.latlon_to_crs("irish_grid", lat, lon)
    file_z = sample_file(heights, area, x, y)
    osni_z = osni.sample(e, n).astype(float)
    lidar_z = lidar.sample(e, n).astype(float)
    diff = file_z - osni_z
    ok = np.isfinite(diff)
    out = {"x": x, "y": y, "s": s, "file": file_z, "osni": osni_z, "lidar": lidar_z,
           "drop_file": largest_drop(s, file_z, window), "drop_osni": largest_drop(s, osni_z, window),
           "offset_min": float(diff[ok].min()) if ok.any() else float("nan"),
           "offset_max": float(diff[ok].max()) if ok.any() else float("nan"),
           "lidar_points": int(np.isfinite(lidar_z).sum())}
    if source_mask is not None:
        out["nearest_lidar_m"] = nearest_lidar_m(source_mask, area, 0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1]))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("area", help="area name in config/aoi.yaml (the height file must be built for it)")
    ap.add_argument("--from", dest="p0", nargs=2, type=float, required=True, metavar=("X", "Y"))
    ap.add_argument("--to", dest="p1", nargs=2, type=float, required=True, metavar=("X", "Y"))
    ap.add_argument("--window", type=float, default=12.0, help="path length for the largest-drop search (default 12 m)")
    ap.add_argument("--step", type=float, default=2.0, help="print every STEP metres (default 2)")
    ap.add_argument("--root", default=str(el.LIDAR_DIR))
    ap.add_argument("--elev-dir", default=str(el.ELEV_DIR))
    a = ap.parse_args(argv)
    cfg = aoi.load()
    area = cfg.area(a.area)
    lidar, osni = el.load_sources(Path(a.root))
    folder = Path(a.elev_dir) / a.area
    heights = el.read_height_file(folder / "gpxz.f32", area.size_px)
    mask_path = folder / "source_mask.npy"
    mask = np.load(mask_path) if mask_path.is_file() else None
    r = probe(cfg, a.area, a.p0, a.p1, a.window, heights, lidar, osni, mask, step=1.0)
    print(f"{'s':>5} {'x':>8} {'y':>8} {'file':>8} {'osni':>8} {'lidar':>8}")
    for i in range(0, len(r["s"]), max(1, int(round(a.step)))):
        print(f"{r['s'][i]:5.0f} {r['x'][i]:8.1f} {r['y'][i]:8.1f} {r['file'][i]:8.2f} {r['osni'][i]:8.2f} {r['lidar'][i]:8.2f}")
    for k in ("drop_file", "drop_osni"):
        d, s0, s1 = r[k]
        print(f"{k}: {d:.2f} m between s={s0:.0f} and s={s1:.0f} (window {a.window:g} m)")
    print(f"file minus OSNI: {r['offset_min']:.2f} to {r['offset_max']:.2f} m; LiDAR points on the line: {r['lidar_points']}")
    if "nearest_lidar_m" in r:
        print(f"nearest LiDAR pixel to the line's middle: {r['nearest_lidar_m']:.0f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
