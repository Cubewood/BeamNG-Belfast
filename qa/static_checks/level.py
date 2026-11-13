"""Read an exported level zip and its OSM data into plain Python objects (no game, no MapNG code).

World coordinates are metres from the map centre, x east, y north (the game's level coordinates).
MapNG maps latitude and longitude linearly over the area's bounds (`exportBeamNGLevel.js:11785`),
so OSM is projected the same way, from the bounds in `mapng.json`: this gives the exact world position,
not an approximation.

Everything is read from the zip; the OSM file is the one the export read (`areas/<name>/osm.json`).
When T-202 lands a pinned OSM extract, pass its path instead: any file in the same feature format works.
"""
from __future__ import annotations

import json
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# DecalRoad materials that carry traffic. Names of footpaths and cycle tracks are excluded below.
DRIVABLE_MATERIALS = {"road_asphalt_2lane", "m_dirt_road"}
NON_VEHICLE_NAME = re.compile(r"^dr_(path|footway|cycleway|steps|pedestrian|bridleway|corridor)_")
BODY_NAME = re.compile(r"_b\d*$")
AI_NAME = re.compile(r"_ai\d*$")
# drivable highways (public network); service roads are listed separately (private, parking aisles, driveways)
PUBLIC_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
                   "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
                   "road"}
DRIVABLE_HIGHWAYS = PUBLIC_HIGHWAYS | {"service", "track"}


def project_factory(bounds: dict, size_m: float):
    """lat/lng -> (x, y) metres, MapNG's own linear mapping over the bounds."""
    n, s, w, e = bounds["north"], bounds["south"], bounds["west"], bounds["east"]

    def project(lat: float, lng: float) -> tuple[float, float]:
        return (((lng - w) / (e - w) - 0.5) * size_m, (0.5 - (n - lat) / (n - s)) * size_m)

    return project


@dataclass
class DecalRoad:
    name: str
    material: str
    nodes: np.ndarray            # (n, 4): x, y, z, width
    props: dict = field(default_factory=dict)

    @property
    def xy(self) -> np.ndarray:
        return self.nodes[:, :2]

    @property
    def is_body(self) -> bool:
        return bool(BODY_NAME.search(self.name))

    @property
    def is_ai(self) -> bool:
        return bool(AI_NAME.search(self.name))

    @property
    def twin_ai_name(self) -> str:
        return BODY_NAME.sub("_ai", self.name)

    @property
    def is_vehicle_body(self) -> bool:
        return (self.is_body and self.material in DRIVABLE_MATERIALS and not NON_VEHICLE_NAME.match(self.name)
                and float(np.median(self.nodes[:, 3])) >= 3.0)


@dataclass
class OsmFeature:
    id: str
    type: str
    tags: dict
    xy: np.ndarray               # (n, 2) world metres
    lonlat: list


@dataclass
class Terrain:
    heights: np.ndarray          # (size, size) metres, row = south to north, column = west to east
    origin: tuple[float, float]  # world x, y of heights[0, 0]
    square: float

    def height(self, x, y):
        """Bilinear height at world x, y (arrays allowed); NaN outside the block."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        fx = (x - self.origin[0]) / self.square
        fy = (y - self.origin[1]) / self.square
        n = self.heights.shape[0]
        ok = (fx >= 0) & (fy >= 0) & (fx <= n - 1) & (fy <= n - 1)
        fxc = np.clip(fx, 0, n - 1.000001)
        fyc = np.clip(fy, 0, n - 1.000001)
        i = fyc.astype(int)
        j = fxc.astype(int)
        ty = fyc - i
        tx = fxc - j
        h = self.heights
        v = (h[i, j] * (1 - tx) * (1 - ty) + h[i, j + 1] * tx * (1 - ty)
             + h[i + 1, j] * (1 - tx) * ty + h[i + 1, j + 1] * tx * ty)
        return np.where(ok, v, np.nan)


@dataclass
class Level:
    zip_path: Path
    name: str
    bounds: dict
    size_m: float
    roads: list[DecalRoad]
    objects: list[dict]          # every scene object that is not a DecalRoad or SimGroup
    object_count: int
    terrain: Terrain | None
    osm: list[OsmFeature]
    report_text: str
    project: object
    zf: zipfile.ZipFile

    @property
    def base(self) -> str:
        return f"levels/{self.name}/"

    def by_name(self) -> dict[str, DecalRoad]:
        return {r.name: r for r in self.roads}


def _ndjson(zf: zipfile.ZipFile, name: str):
    for line in zf.read(name).decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                yield json.loads(line)
            except ValueError:
                continue


def read_terrain(zf: zipfile.ZipFile, base: str, block: dict | None) -> Terrain | None:
    """Heights of terrain.ter (version 9): u16 per vertex times maxHeight / 65536, from the block's z."""
    name = f"{base}art/terrains/terrain.ter"
    if block is None or name not in zf.namelist():
        return None
    raw = zf.read(name)
    size = struct.unpack("<I", raw[1:5])[0]
    stored = np.frombuffer(raw, dtype="<u2", count=size * size, offset=5).reshape(size, size)
    pos = block.get("position", [0, 0, 0])
    sq = float(block.get("squareSize", 1))
    heights = stored.astype(np.float32) * np.float32(float(block["maxHeight"]) / 65536.0) + np.float32(pos[2])
    return Terrain(heights, (float(pos[0]), float(pos[1])), sq)


def read_osm(path: Path, project) -> list[OsmFeature]:
    out = []
    for f in json.loads(Path(path).read_text(encoding="utf-8")):
        geom = f.get("geometry") or []
        if not geom:
            continue
        xy = np.array([project(p["lat"], p["lng"]) for p in geom], dtype=float)
        out.append(OsmFeature(str(f.get("id")), f.get("type", ""), f.get("tags") or {}, xy,
                              [(p["lng"], p["lat"]) for p in geom]))
    return out


def load_level(zip_path: Path, osm_path: Path | None = None, load_terrain: bool = True) -> Level:
    zf = zipfile.ZipFile(zip_path)
    names = zf.namelist()
    levels = sorted({n.split("/")[1] for n in names if n.startswith("levels/") and n.count("/") >= 2})
    if len(levels) != 1:
        raise ValueError(f"expected one level folder under levels/, found {levels}")
    name = levels[0]
    base = f"levels/{name}/"
    meta = json.loads(zf.read(base + "mapng.json"))
    bounds = meta["bounds"]
    size_m = float(meta["sizeMeters"])
    project = project_factory(bounds, size_m)

    roads: list[DecalRoad] = []
    objects: list[dict] = []
    count = 0
    block = None
    for n in names:
        if not n.startswith(base + "main/") or not n.endswith("items.level.json"):
            continue
        for o in _ndjson(zf, n):
            cls = o.get("class")
            if cls == "SimGroup":
                continue
            count += 1
            if cls == "DecalRoad":
                nodes = np.array(o.get("nodes") or [[0, 0, 0, 0]], dtype=float)
                if nodes.ndim != 2 or nodes.shape[1] < 4:
                    continue
                props = {k: v for k, v in o.items() if k not in ("nodes", "name", "material", "class", "position")}
                roads.append(DecalRoad(o.get("name", ""), o.get("material", ""), nodes[:, :4], props))
            else:
                objects.append(o)
                if cls == "TerrainBlock":
                    block = o
    terrain = read_terrain(zf, base, block) if load_terrain else None
    osm: list[OsmFeature] = []
    if osm_path is not None and Path(osm_path).exists():
        osm = read_osm(osm_path, project)
    report = zf.read(base + "export_report.txt").decode("utf-8", "replace") if base + "export_report.txt" in names else ""
    return Level(Path(zip_path), name, bounds, size_m, roads, objects, count, terrain, osm, report, project, zf)
