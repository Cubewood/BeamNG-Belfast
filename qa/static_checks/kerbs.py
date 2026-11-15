"""Kerb faces: the vertical faces of the sidewalk collision meshes, with their height.

MapNG builds the sidewalks as 256 m tile shapes (`sidewalks/sidewalks_<x>_<y>.dae`), each with a `Colmesh`
collision geometry in world coordinates (the TSStatic sits at the origin). The kerb a wheel meets is a near-vertical
face of that mesh; its height is the z extent of the face. Roads are not part of the mesh, so the face height is
the kerb's own height, not its height above the road decal: MapNG sinks every face below the road (a curb 0.10 m,
a walkway's edge 0.30 m), so the check measures the face's top against the road beside it (`with_tops`).
"""
from __future__ import annotations

import numpy as np

from .buildings import _P, _POS, WALL_NORMAL_Z, _colmesh_block
from .level import Level

MIN_FACE_LEN_M = 0.05      # a face shorter than this along the ground is a sliver, not a kerb


def _chord_height(p: np.ndarray, w: np.ndarray, segs: np.ndarray) -> np.ndarray:
    """Tallest vertical cut through each vertical triangle (n,).

    The z extent of the corners overstates a face on a slope: a face whose top and bottom both climb along it has a
    corner-to-corner extent larger than its height at any one place. The cut is tallest at one of the corners, so
    the height is the largest of the three cuts there."""
    a = segs[:, 0]
    u = segs[:, 1] - a
    u = u / np.maximum(np.linalg.norm(u, axis=1), 1e-12)[:, None]
    t = np.einsum("nij,nj->ni", w - a[:, None, :], u)                    # position along the face, per corner
    z = p[:, :, 2]
    eps = 1e-6
    best = np.zeros(len(p))
    for k in range(3):
        tk = t[:, k]
        cand = []
        for i, j in ((0, 1), (1, 2), (0, 2)):
            lo, hi = np.minimum(t[:, i], t[:, j]), np.maximum(t[:, i], t[:, j])
            ok = (tk >= lo - eps) & (tk <= hi + eps)
            flat = (hi - lo) < eps                                       # the edge is vertical: both its ends count
            frac = np.clip((tk - t[:, i]) / np.where(flat, 1.0, t[:, j] - t[:, i]), 0, 1)
            zi = z[:, i] + (z[:, j] - z[:, i]) * frac
            cand += [np.where(ok, np.where(flat, z[:, i], zi), np.nan), np.where(ok, np.where(flat, z[:, j], zi), np.nan)]
        c = np.stack(cand, axis=1)
        best = np.maximum(best, np.nanmax(c, axis=1) - np.nanmin(c, axis=1))
    return best


def kerb_faces(raw: bytes, with_tops: bool = False):
    """Vertical faces of a COLLADA file's Colmesh: ground segments (n, 2, 2) and heights (n,), z extent in metres.

    With `with_tops`, also the z of each face's top (n,), so a caller can measure it against the road beside it.
    None when the file has no readable Colmesh."""
    block = _colmesh_block(raw)
    if block is None:
        return None
    pos = _POS.search(block)
    if not pos:
        return None
    verts = np.array(pos.group(2).split(), dtype=np.float64).reshape(-1, 3)
    idx = np.concatenate([np.array(m.group(1).split(), dtype=np.int64) for m in _P.finditer(block)]
                         or [np.zeros(0, np.int64)])
    if len(idx) < 3:
        return None
    p = verts[idx[: len(idx) // 3 * 3].reshape(-1, 3)]                   # (n, 3, 3)
    cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    norm = np.linalg.norm(cross, axis=1)
    nz = np.divide(np.abs(cross[:, 2]), norm, out=np.ones_like(norm), where=norm > 1e-12)
    p = p[(nz < WALL_NORMAL_Z) & (norm > 1e-9)]
    if not len(p):
        return (np.zeros((0, 2, 2)), np.zeros(0)) + ((np.zeros(0),) if with_tops else ())
    w = p[:, :, :2]
    d = np.stack([np.linalg.norm(w[:, 0] - w[:, 1], axis=1), np.linalg.norm(w[:, 1] - w[:, 2], axis=1),
                  np.linalg.norm(w[:, 0] - w[:, 2], axis=1)], axis=1)
    pair = d.argmax(axis=1)
    pairs = np.array([[0, 1], [1, 2], [0, 2]])
    rows = np.arange(len(w))
    segs = np.stack([w[rows, pairs[pair, 0]], w[rows, pairs[pair, 1]]], axis=1)
    height = _chord_height(p, w, segs)
    keep = d.max(axis=1) > MIN_FACE_LEN_M
    if with_tops:
        return segs[keep], height[keep], p[:, :, 2].max(axis=1)[keep]
    return segs[keep], height[keep]


def level_kerb_faces(level: Level, with_tops: bool = False):
    """World-space kerb face segments and heights of every sidewalk tile, and the tiles that could not be read.

    With `with_tops`: (segments, heights, top z, skipped tiles)."""
    names = set(level.zf.namelist())
    segs, heights, tops, skipped = [], [], [], []
    for o in level.objects:
        shape = str(o.get("shapeName", ""))
        if o.get("class") != "TSStatic" or "/sidewalks/" not in shape:
            continue
        rel = shape.lstrip("/")
        faces = kerb_faces(level.zf.read(rel), with_tops=True) if rel in names else None
        if faces is None:
            skipped.append(rel)
            continue
        pos = o.get("position", [0, 0, 0])
        segs.append(faces[0] + np.array([pos[0], pos[1]]))
        heights.append(faces[1])
        tops.append(faces[2] + (pos[2] if len(pos) > 2 else 0.0))
    cat = (np.concatenate(segs) if segs else np.zeros((0, 2, 2)),
           np.concatenate(heights) if heights else np.zeros(0))
    if with_tops:
        return cat + (np.concatenate(tops) if tops else np.zeros(0), skipped)
    return cat + (skipped,)
