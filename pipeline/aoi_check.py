#!/usr/bin/env python3
"""Check that pipeline/aoi.py puts things where MapNG put them in the slice export (T-201).

    uv run --no-project --python 3.12 --with pyproj --with numpy --with pyyaml python pipeline/aoi_check.py \
        [--zip H:/BelfastData/mapng/out/belfast_slice.zip] [--osm H:/BelfastData/mapng/areas/belfast_slice/osm.json] \
        [--write-fixture tests/pipeline/fixtures/belfast_slice_mapng_pairs.json]

The evidence is MapNG's own output. Every OSM road vertex in the area's osm.json is projected with the
transform under test; MapNG writes the DecalRoad nodes of the zip from the same vertices (and a vertex that
survives resampling is written unchanged, to 0.001 m). So the nodes that sit on a projected vertex show the
residual of the transform directly: a wrong transform leaves no node within a few millimetres, a right one
leaves a spike (about a quarter of the vertices) at the 0.001 m rounding of MapNG's output.
Two other models are run on the same data for contrast: a local transverse Mercator at the centre, and the
flat-earth scale in qa/route_lib.py.

Also lists the drive-suite crash sites (docs/drive_suite.md) as lat/lon and their distance to the nearest OSM
road vertex, measured by great-circle distance (independent of the transform).

Exit code 0 when the checks pass, 1 when they fail, 2 when inputs are missing.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aoi  # noqa: E402

DEFAULT_ZIP = "H:/BelfastData/mapng/out/belfast_slice.zip"
DEFAULT_OSM = "H:/BelfastData/mapng/areas/belfast_slice/osm.json"
CRASH_SITES = {"Lady Ishbel Avenue west link": (-211.0, -93.0), "Newtownbreda roundabout": (194.0, 284.0)}

# Pass levels. The spike is the share of OSM road vertices with a DecalRoad node within 1 mm.
MIN_SPIKE_SHARE = 0.10
MAX_MEAN_OFFSET_M = 0.001
MAX_CLUSTER_M = 0.01


def decal_nodes(zip_path: str, level: str) -> np.ndarray:
    """x, y of every node of every visible DecalRoad in the zip (MapNG's placement)."""
    out = []
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if f"levels/{level}/main/MissionGroup/Decal_Roads/" in name and name.endswith("items.level.json"):
                for line in z.read(name).decode("utf-8").splitlines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if d.get("class") == "DecalRoad" and d.get("material") != "road_invisible":
                        out.extend(p[:2] for p in d["nodes"])
    return np.asarray(out, float)


def road_vertices(osm_path: str) -> np.ndarray:
    with open(osm_path, encoding="utf-8") as f:
        feats = json.load(f)
    pts = [(p["lat"], p["lng"]) for ft in feats if ft.get("type") == "road" for p in ft["geometry"]]
    return np.asarray(pts, float)


def nearest(points: np.ndarray, nodes: np.ndarray, chunk: int = 64):
    """Distance to and index of the nearest node for each point (brute force, no scipy)."""
    d = np.empty(len(points))
    idx = np.empty(len(points), int)
    for i in range(0, len(points), chunk):
        p = points[i:i + chunk]
        dd = np.hypot(p[:, None, 0] - nodes[None, :, 0], p[:, None, 1] - nodes[None, :, 1])
        j = dd.argmin(axis=1)
        idx[i:i + chunk] = j
        d[i:i + chunk] = dd[np.arange(len(p)), j]
    return d, idx


def compare(xy: np.ndarray, nodes: np.ndarray) -> dict:
    d, idx = nearest(xy, nodes)
    near = d < MAX_CLUSTER_M
    r = nodes[idx[near]] - xy[near] if near.any() else np.zeros((0, 2))
    return {
        "vertices": int(len(xy)),
        "share_within_1mm": float((d < 0.001).mean()),
        "share_within_1cm": float(near.mean()),
        "median_nearest_m": float(np.median(d)),
        "mean_offset_mm": [float(v) for v in (r.mean(0) * 1000 if len(r) else [float("nan")] * 2)],
        "max_abs_offset_mm_in_cluster": [float(v) for v in (np.abs(r).max(0) * 1000 if len(r) else [float("nan")] * 2)],
        "_d": d, "_idx": idx,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zip", default=DEFAULT_ZIP)
    ap.add_argument("--osm", default=DEFAULT_OSM)
    ap.add_argument("--area", default="slice")
    ap.add_argument("--level", default="belfast_slice")
    ap.add_argument("--write-fixture", help="write a small lat/lon/x/y pair file for the offline test")
    a = ap.parse_args(argv)
    for p in (a.zip, a.osm):
        if not Path(p).exists():
            print(f"missing input: {p}", file=sys.stderr)
            return 2

    cfg = aoi.load()
    area = cfg.area(a.area)
    nodes = decal_nodes(a.zip, a.level)
    verts = road_vertices(a.osm)
    print(f"{len(nodes)} DecalRoad nodes, {len(verts)} OSM road vertices, area {a.area}")

    x, y = area.latlon_to_game(verts[:, 0], verts[:, 1])
    res = compare(np.c_[x, y], nodes)
    d, idx = res.pop("_d"), res.pop("_idx")
    print("aoi.py transform:", json.dumps(res))

    # Contrast models on the same vertices.
    from pyproj import Transformer
    tm = Transformer.from_crs("EPSG:4326", f"+proj=tmerc +lat_0={area.lat0} +lon_0={area.lon0} +k=1 +x_0=0 "
                              "+y_0=0 +datum=WGS84 +units=m", always_xy=True)
    tx, ty = tm.transform(verts[:, 1], verts[:, 0])
    other = {"local transverse Mercator (metres)": np.c_[tx, ty]}
    m_lat, m_lon = 111320.0, 111320.0 * np.cos(np.radians(area.lat0))
    other["flat earth (qa/route_lib.py)"] = np.c_[(verts[:, 1] - area.lon0) * m_lon, (verts[:, 0] - area.lat0) * m_lat]
    for name, xy in other.items():
        r = compare(xy, nodes)
        print(f"contrast, {name}: share within 1 mm {r['share_within_1mm']:.3f}, "
              f"median nearest-node distance {r['median_nearest_m']:.2f} m")

    # Crash sites: game -> lat/lon -> distance to the nearest OSM road vertex on the sphere.
    for label, (cx, cy) in CRASH_SITES.items():
        la, lo = area.game_to_latlon(cx, cy)
        dist = aoi.haversine_m(la, lo, verts[:, 0], verts[:, 1])
        print(f"crash site {label}: game ({cx:g}, {cy:g}) -> {la:.6f}, {lo:.6f}; nearest OSM road vertex "
              f"{dist.min():.1f} m (great circle)")

    ok = (res["share_within_1mm"] >= MIN_SPIKE_SHARE
          and all(abs(v) <= MAX_MEAN_OFFSET_M * 1000 for v in res["mean_offset_mm"])
          and all(v <= MAX_CLUSTER_M * 1000 for v in res["max_abs_offset_mm_in_cluster"]))
    print("PASS" if ok else "FAIL",
          f"(spike >= {MIN_SPIKE_SHARE:.0%}, mean offset <= {MAX_MEAN_OFFSET_M * 1000:g} mm, "
          f"cluster max <= {MAX_CLUSTER_M * 1000:g} mm)")

    if a.write_fixture:
        sel = np.flatnonzero(d < 0.005)
        sel = sel[np.linspace(0, len(sel) - 1, min(300, len(sel))).astype(int)]
        pairs = [{"lat": float(verts[i, 0]), "lon": float(verts[i, 1]),
                  "x": float(nodes[idx[i], 0]), "y": float(nodes[idx[i], 1])} for i in sel]
        out = {"note": "OSM road vertex (lat, lon from the slice osm.json) and the DecalRoad node MapNG wrote for it "
                       "(x, y from belfast_slice.zip, 0.001 m); made by pipeline/aoi_check.py --write-fixture",
               "area": a.area, "pairs": pairs}
        Path(a.write_fixture).parent.mkdir(parents=True, exist_ok=True)
        Path(a.write_fixture).write_text(json.dumps(out, indent=0) + "\n", encoding="utf-8")
        print(f"wrote {len(pairs)} pairs to {a.write_fixture}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
