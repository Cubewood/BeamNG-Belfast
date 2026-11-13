"""A tiny synthetic level (zip + OSM extract) for the static-check tests, with switches that plant one fault each.

World: 512 m square at 1 m per vertex, flat ground at 10 m. Roads (all with drawn `_b` body and invisible `_ai` lanes):

    A  west-east, y = 0, x -200..200            two-way, nodes at -200, -150, 0, 200 in OSM
    B  north-south, x = 0, y -200..200          two-way, crosses A at (0, 0)
    C  west-east, y = 40, x -200..200           one-way eastwards
    D  link, x = -150, y 0..40                  two-way, joins A and C
    E  west-east, y = -120, x -100..100         two-way, on its own (OSM component of its own)

Everything is derived from `Spec`; `build(tmp, Spec(...))` writes `level.zip` and `osm.json` and returns paths.
"""
from __future__ import annotations

import json
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SIZE = 512
N, S, W, E = 54.5446, 54.5400, -5.9330, -5.9270
NAME = "testlvl"


def xy_to_ll(x, y):
    lng = W + (x / SIZE + 0.5) * (E - W)
    lat = N - (0.5 - y / SIZE) * (N - S)
    return {"lat": lat, "lng": lng}


@dataclass
class Spec:
    drop_exported: tuple = ()               # road keys whose DecalRoads are left out of the zip (OSM keeps them)
    unflagged_oneway: bool = False          # C drawn without oneWay
    reverse_c: bool = False                 # C drawn with its nodes running westwards
    c_oneway_in_osm: bool = True            # C is oneway=yes in OSM (False: C two-way in OSM, drawn one-way)
    road_z_bump: float = 0.0                # raise A's node at x=100 by this much
    bridge_tag_a: bool = False              # OSM marks A as a bridge (height and step checks must skip it)
    step_at_x: float | None = None          # a step in the ground across the whole map at this x
    step_height: float = 0.0
    slope: float = 0.0                      # smooth ground slope in the x direction (m per m)
    buildings: list = field(default_factory=list)   # [(x0, y0, x1, y1, kind)] kind: wall | overhang | clear
    shape_path: str = "/art/shapes/stock/ok.dae"    # a TSStatic's shape
    road_material: str = "road_invisible"           # material of the _ai roads
    extra_decal_roads: int = 0
    osm_roads_only: bool = False
    texture_ref: str | None = None          # a material texture path to plant


ROADS = {
    "A": dict(pts=[(-200, 0), (-150, 0), (0, 0), (200, 0)], hw=3.0, oneway=0),
    "B": dict(pts=[(0, -200), (0, 0), (0, 200)], hw=3.0, oneway=0),
    "C": dict(pts=[(-200, 40), (-150, 40), (200, 40)], hw=3.0, oneway=1),
    "D": dict(pts=[(-150, 0), (-150, 40)], hw=3.0, oneway=0),
    "E": dict(pts=[(20, -120), (180, -120)], hw=3.0, oneway=0),
}
WAY_IDS = {"A": "1001", "B": "1002", "C": "1003", "D": "1004", "E": "1005"}
# MapNG splits roads where they meet, so the drawn roads end at the junction; OSM keeps one way with a node there
PIECES = {
    "A": [[(-200, 0), (-150, 0), (0, 0)], [(0, 0), (200, 0)]],
    "B": [[(0, -200), (0, 0)], [(0, 0), (0, 200)]],
    "C": [[(-200, 40), (-150, 40), (200, 40)]],
    "D": [[(-150, 0), (-150, 40)]],
    "E": [[(20, -120), (180, -120)]],
}


def densify(pts, step=10.0):
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(np.hypot(b[0] - a[0], b[1] - a[1]) // step))
        for k in range(1, n + 1):
            out.append((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n))
    return out


def ground(spec: Spec) -> np.ndarray:
    xs = np.arange(SIZE) - SIZE / 2
    h = 10.0 + spec.slope * xs[None, :] + np.zeros((SIZE, 1))
    if spec.step_at_x is not None:
        h = h + np.where(xs[None, :] >= spec.step_at_x, spec.step_height, 0.0)
    return h


def ter_bytes(h: np.ndarray, max_height: float) -> bytes:
    stored = np.clip(np.round((h - 0.0) / max_height * 65536), 0, 65535).astype("<u2")
    return b"\x09" + struct.pack("<I", SIZE) + stored.tobytes()


def box_dae(boxes) -> bytes:
    """Colmesh with a box per building (walls and roof), in the tile's local (= world) coordinates."""
    verts, tris = [], []
    for (x0, y0, x1, y1, z0, z1, roof_only) in boxes:
        b = len(verts)
        verts += [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                  (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        for q in ((0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):   # four walls
            if roof_only:
                continue
            tris += [(b + q[0], b + q[1], b + q[2]), (b + q[0], b + q[2], b + q[3])]
        tris += [(b + 4, b + 5, b + 6), (b + 4, b + 6, b + 7)]                   # roof
    pos = " ".join(f"{v:.4f}" for p in verts for v in p)
    idx = " ".join(str(i) for t in tris for i in t)
    return (f'<?xml version="1.0"?><COLLADA><library_geometries><geometry id="Mesh1"><mesh></mesh></geometry>'
            f'<geometry id="Colmesh-1-mesh" name="Colmesh-1"><mesh><source id="Colmesh-1-mesh-position">'
            f'<float_array id="Colmesh-1-mesh-position-array" count="{len(verts) * 3}">{pos}</float_array></source>'
            f'<triangles material="m" count="{len(tris)}"><input semantic="VERTEX" source="#v" offset="0"/>'
            f'<p>{idx}</p></triangles></mesh></geometry></library_geometries></COLLADA>').encode()


def decal(name, material, pts, hw_total, z=10.0, **props):
    nodes = [[round(x, 3), round(y, 3), z, hw_total] for x, y in pts]
    o = {"name": name, "class": "DecalRoad", "persistentId": name, "__parent": "Decal_Roads",
         "position": nodes[0][:3], "material": material, "nodes": nodes}
    o.update(props)
    return o


def build(tmp: Path, spec: Spec | None = None) -> tuple[Path, Path]:
    spec = spec or Spec()
    base = f"levels/{NAME}/"
    objs = [{"class": "SimGroup", "name": "MissionGroup", "persistentId": "g0"}]
    max_height = 64.0
    objs.append({"class": "TerrainBlock", "name": "theTerrain", "persistentId": "tb", "__parent": "MissionGroup",
                 "position": [-SIZE / 2, -SIZE / 2, 0], "squareSize": 1, "maxHeight": max_height,
                 "terrainFile": f"/{base}art/terrains/terrain.ter"})
    objs.append({"class": "TSStatic", "name": "sign1", "persistentId": "s1", "__parent": "MissionGroup",
                 "position": [30, 10, 10], "shapeName": spec.shape_path})
    objs.append({"class": "TSStatic", "name": "bridge_b001", "persistentId": "br", "__parent": "MissionGroup",
                 "position": [9999, 9999, 0], "shapeName": f"/{base}map_assets/custom_assets/bridges/bridge_b001.dae"})
    roads = []
    for key, r in ROADS.items():
        if key in spec.drop_exported:
            continue
        for piece, piece_pts in enumerate(PIECES[key]):
            pts = densify(piece_pts)
            z = [10.0] * len(pts)
            if key == "A" and spec.road_z_bump:
                z = [10.0 + (spec.road_z_bump if abs(p[0] - 100) < 15 else 0.0) for p in pts]
            if key == "C" and spec.reverse_c:
                pts = pts[::-1]
            for suffix, mat, w in (("b", "road_asphalt_2lane", 10.6), ("ai", spec.road_material, r["hw"] * 2)):
                nodes = [[round(x, 3), round(y, 3), zz, w] for (x, y), zz in zip(pts, z)]
                name = f"dr_{key}{piece}_s1_{suffix}"
                o = {"name": name, "class": "DecalRoad", "persistentId": name,
                     "__parent": "Decal_Roads", "position": nodes[0][:3], "material": mat, "nodes": nodes}
                if suffix == "ai":
                    o.update({"drivability": 0.9, "lanesLeft": 0 if r["oneway"] else 1,
                              "lanesRight": 1 if not r["oneway"] else 2, "autoLanes": False, "speedLimit": 13.4})
                    if r["oneway"] and not spec.unflagged_oneway:
                        o["oneWay"] = True              # C is drawn one-way even when OSM says two-way (a planted fault)
                roads.append(o)
    for i in range(spec.extra_decal_roads):
        roads.append(decal(f"dr_extra{i}_s1_b", "road_asphalt_2lane", [(-250 + i, 250), (-249 + i, 251)], 2.0))
    boxes = [(x0, y0, x1, y1, 10.0, 16.0, False) for (x0, y0, x1, y1, kind) in spec.buildings if kind in ("wall", "clear")]
    # an overhang: a roof that sticks out over the lanes with no wall under it (a roof-only slab, 4 m up)
    slabs = [(x0, y0, x1, y1, 14.0, 14.5, True) for (x0, y0, x1, y1, kind) in spec.buildings if kind == "overhang"]
    objs.append({"class": "TSStatic", "name": "buildings_0_0", "persistentId": "bt", "__parent": "MissionGroup",
                 "position": [0, 0, 0], "shapeName": f"/{base}map_assets/custom_assets/osm_objects/buildings/bld_0_0.dae",
                 "collisionType": "Collision Mesh"})
    mats = {"road_asphalt_2lane": {"name": "road_asphalt_2lane"}, "m_line_white": {"name": "m_line_white"}}
    if spec.road_material == "road_invisible":
        mats["road_invisible"] = {"name": "road_invisible"}
    if spec.texture_ref:
        mats["tex_user"] = {"name": "tex_user", "Stages": [{"colorMap": spec.texture_ref}]}

    osm = []
    for key, r in ROADS.items():
        tags = {"highway": "residential", "name": f"Road {key}"}
        if key == "C" and spec.c_oneway_in_osm:
            tags["oneway"] = "yes"
        if key == "A" and spec.bridge_tag_a:
            tags["bridge"] = "yes"
        osm.append({"id": f"{WAY_IDS[key]}_seg_0", "type": "road", "tags": tags,
                    "geometry": [xy_to_ll(*p) for p in r["pts"]]})
    for k, (x0, y0, x1, y1, kind) in enumerate(spec.buildings):
        if kind == "overhang":
            continue
        osm.append({"id": f"900{k}", "type": "building", "tags": {"building": "yes"},
                    "geometry": [xy_to_ll(x0, y0), xy_to_ll(x1, y0), xy_to_ll(x1, y1), xy_to_ll(x0, y1), xy_to_ll(x0, y0)]})

    h = ground(spec)
    zf_path = tmp / "level.zip"
    with zipfile.ZipFile(zf_path, "w") as zf:
        zf.writestr(f"{base}mapng.json", json.dumps({"bounds": {"north": N, "south": S, "west": W, "east": E},
                                                     "sizeMeters": SIZE}))
        zf.writestr(f"{base}main/MissionGroup/items.level.json", "\n".join(json.dumps(o) for o in objs))
        zf.writestr(f"{base}main/MissionGroup/Decal_Roads/items.level.json", "\n".join(json.dumps(o) for o in roads))
        zf.writestr(f"{base}art/terrains/terrain.ter", ter_bytes(h, max_height))
        zf.writestr(f"{base}art/roads/main.materials.json", json.dumps(mats))
        zf.writestr(f"{base}map_assets/custom_assets/osm_objects/buildings/bld_0_0.dae",
                    box_dae(boxes + slabs) if (boxes or slabs) else box_dae([(400, 400, 401, 401, 10, 11, False)]))
        zf.writestr(f"{base}export_report.txt", "Shapes: 1 building tile(s), 12 triangles")
        zf.writestr(f"{base}map_assets/custom_assets/bridges/bridge_b001.dae", b"<COLLADA/>")
    osm_path = tmp / "osm.json"
    osm_path.write_text(json.dumps(osm), encoding="utf-8")
    return zf_path, osm_path
