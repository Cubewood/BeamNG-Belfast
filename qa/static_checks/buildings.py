"""Building footprints: what the export actually built (collision meshes of the building tiles), and the OSM
outlines they came from.

MapNG puts the buildings in 256 m tile shapes (`buildings/bld_<x>_<y>.dae`), each with a `Colmesh` collision
geometry in tile-local coordinates; the TSStatic's `position` is the tile origin. A car can only hit the walls,
so a building counts as standing in the lanes when a wall (a vertical face) of that collision mesh does. The roof
faces give the footprint area (roofs can overhang a lane; overhang alone is not a hit). That is what the game
collides with, after any cut-back MapNG did. OSM outlines say which building it is, and are the fallback when a
level has no tile meshes.
"""
from __future__ import annotations

import re

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.strtree import STRtree

from .level import Level, OsmFeature

_POS = re.compile(rb'<float_array id="[^"]*" count="(\d+)">([^<]*)</float_array>')
_P = re.compile(rb"<p>([^<]*)</p>")
ROOF_NORMAL_Z = 0.3     # faces at least this far from vertical are roof
WALL_NORMAL_Z = 0.15    # faces within this of vertical are walls
MIN_TRI_AREA = 0.02     # m2 on the ground (roof) or on the face (wall)


def _colmesh_block(raw: bytes) -> bytes | None:
    """The collision geometry of a COLLADA file as bytes (the last geometry whose id starts with Colmesh)."""
    start = raw.rfind(b'<geometry id="Colmesh')
    if start < 0:
        return None
    end = raw.find(b"</geometry>", start)
    return raw[start:end] if end > 0 else None


def mesh_faces(raw: bytes) -> tuple[np.ndarray, np.ndarray] | None:
    """Roof triangles (n, 3, 2) and wall segments (m, 2, 2) in the ground plane from the Colmesh of a .dae file.

    A wall segment is the horizontal extent of one vertical face. None when the file has no Colmesh."""
    block = _colmesh_block(raw)
    if block is None:
        return None
    pos = _POS.search(block)
    if not pos:
        return None
    verts = np.array(pos.group(2).split(), dtype=np.float64).reshape(-1, 3)
    idx = np.concatenate([np.array(m.group(1).split(), dtype=np.int64) for m in _P.finditer(block)] or [np.zeros(0, np.int64)])
    if len(idx) < 3:
        return None
    tri = idx[: len(idx) // 3 * 3].reshape(-1, 3)
    p = verts[tri]                                     # (n, 3, 3)
    cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    norm = np.linalg.norm(cross, axis=1)
    nz = np.divide(np.abs(cross[:, 2]), norm, out=np.zeros_like(norm), where=norm > 1e-12)
    roof = (nz > ROOF_NORMAL_Z) & (np.abs(cross[:, 2]) / 2 > MIN_TRI_AREA)
    wall = (nz < WALL_NORMAL_Z) & (norm / 2 > MIN_TRI_AREA)
    segs = np.zeros((0, 2, 2))
    if wall.any():
        w = p[wall][:, :, :2]                          # (k, 3, 2): the face's three corners seen from above
        d = np.stack([np.linalg.norm(w[:, 0] - w[:, 1], axis=1), np.linalg.norm(w[:, 1] - w[:, 2], axis=1),
                      np.linalg.norm(w[:, 0] - w[:, 2], axis=1)], axis=1)
        pair = d.argmax(axis=1)
        pairs = np.array([[0, 1], [1, 2], [0, 2]])
        rows = np.arange(len(w))
        segs = np.stack([w[rows, pairs[pair, 0]], w[rows, pairs[pair, 1]]], axis=1)
        segs = segs[d.max(axis=1) > 0.05]
    return p[roof][:, :, :2], segs


def exported_building_geometry(level: Level) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """World-space roof triangles and wall segments of every building tile, and the tiles with no Colmesh."""
    names = set(level.zf.namelist())
    roofs, walls, skipped = [], [], []
    for o in level.objects:
        shape = str(o.get("shapeName", ""))
        if o.get("class") != "TSStatic" or "/osm_objects/buildings/" not in shape:
            continue
        rel = shape.lstrip("/")
        if rel not in names:
            skipped.append(rel)
            continue
        faces = mesh_faces(level.zf.read(rel))
        if faces is None or not len(faces[0]):
            skipped.append(rel)
            continue
        pos = o.get("position", [0, 0, 0])
        off = np.array([pos[0], pos[1]])
        roofs.append(faces[0] + off)
        walls.append(faces[1] + off)
    return (np.concatenate(roofs) if roofs else np.zeros((0, 3, 2)),
            np.concatenate(walls) if walls else np.zeros((0, 2, 2)), skipped)


def osm_buildings(level: Level) -> list[tuple[OsmFeature, Polygon]]:
    out = []
    for f in level.osm:
        if f.type != "building" or len(f.xy) < 3:
            continue
        poly = Polygon(f.xy)
        if not poly.is_valid:
            poly = shapely.make_valid(poly)
        if poly.is_empty or poly.area < 1.0:
            continue
        out.append((f, poly))
    return out


def overlay_in_lanes(footprints, corridor, min_area: float, min_depth: float = 0.0, walls=None,
                     min_wall_m: float = 0.5) -> list[dict]:
    """Pieces of `footprints` (array of shapely polygons) inside `corridor`, grouped by contact.

    Returns [{geom, area, depth, wall_m}]: depth is how far the deepest vertex sits inside the lanes edge; with
    `walls` (array of 2-point segments) a piece only counts when at least `min_wall_m` of wall stands in the lanes
    within 0.6 m of it (a roof that overhangs the lanes is not a building in them)."""
    if corridor.is_empty or not len(footprints):
        return []
    shapely.prepare(corridor)
    hit = footprints[shapely.intersects(footprints, corridor)]
    if not len(hit):
        return []
    pieces = shapely.intersection(hit, corridor)
    pieces = [p for p in pieces if not p.is_empty and p.area > 1e-3]
    merged = shapely.unary_union(pieces)
    comps = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    boundary = corridor.boundary
    lines = None
    wall_tree = None
    if walls is not None:
        lines = shapely.linestrings(walls)
        lines = lines[shapely.intersects(lines, corridor)]
        wall_tree = STRtree(lines) if len(lines) else None
    out = []
    for c in comps:
        if c.geom_type != "Polygon" or c.area < min_area:
            continue
        coords = shapely.points(np.array(c.exterior.coords))
        depth = float(shapely.distance(coords, boundary).max())
        if depth < min_depth:          # a sliver along the lanes' edge, not a wall standing in them
            continue
        wall_m = None
        if walls is not None:
            near = c.buffer(0.6)
            k = wall_tree.query(near, predicate="intersects") if wall_tree is not None else []
            wall_m = 0.0
            if len(k):
                # stacked and split faces of one wall overlap: merge them before measuring
                wall_m = float(shapely.unary_union(shapely.intersection(lines[k], corridor)).length)
            if wall_m < min_wall_m:
                continue
        out.append({"geom": c, "area": float(c.area), "depth": depth, "wall_m": wall_m})
    return out


def attribute(piece, buildings: list[tuple[OsmFeature, Polygon]], tree: STRtree | None) -> tuple[OsmFeature | None, float]:
    """The OSM building that overlaps `piece` most (or lies within 3 m of it), and the overlap in m2."""
    if tree is None:
        return None, 0.0
    best, best_area = None, -1.0
    for k in tree.query(piece.buffer(3.0)):
        f, poly = buildings[k]
        a = poly.intersection(piece.buffer(0.5)).area
        if a > best_area:
            best, best_area = f, a
    return best, max(best_area, 0.0)
