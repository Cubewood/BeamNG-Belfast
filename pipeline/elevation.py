#!/usr/bin/env python3
"""Elevation pipeline (T-203): Belfast City LiDAR + OSNI 10 m DTM -> MapNG's height file.

    uv run --no-project --python 3.12 --with numpy --with pyproj --with pyyaml python pipeline/elevation.py download
    ... mosaic            # LiDAR DTM tiles -> one 1 m raster (H:\\BelfastData\\lidar\\mosaic)
    ... osni              # OSNI 10 m sheets -> one raster
    ... coverage slice city_south full     # coverage numbers and map, no height file
    ... build slice [--install belfast_slice]   # coverage + fill + gpxz.f32 (+ copy into MapNG's area folder)

Sources (Open Government Licence v3.0; data/sources/elevation.json records URL, date, size, SHA-256 and the
attribution text, and T-208 builds the credits from it):
  * LiDAR Belfast City 2006: 0.5 m ESRI ASCII DTM in four zones, Irish Grid (EPSG:29903), heights above
    Mean Sea Level Belfast, nodata -999. Owner Cyient Europe Ltd, published by DfI Rivers.
  * OSNI Open Data 10 m DTM: "x y z" text at 10 m cell centres, Irish Grid, OSNI 10k sheets.
Raw data stays under H:\\BelfastData; nothing downloaded is committed.

Pipeline: tiles are averaged to 1 m (a 1 m cell is valid if any of its four 0.5 m cells is) and laid in one
raster; the OSNI sheets in another. For each pixel of the area's heightmap the position is turned into
Irish Grid and both rasters are sampled. Where LiDAR has no value the OSNI value is used, corrected by the
LiDAR-minus-OSNI difference measured around it (smoothly fading to the area's median difference far from any
LiDAR), so there is no step at the seam. The result is written as `gpxz.f32` for MapNG (see write_height_file).
Explained in docs/elevation.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aoi  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = Path(r"H:\BelfastData")
LIDAR_DIR = DATA_DIR / "lidar"
ELEV_DIR = DATA_DIR / "elevation"
AREAS_DIR = DATA_DIR / "mapng" / "areas"
MANIFEST = REPO / "data" / "sources" / "elevation.json"

NODATA_VALUE = -99999.0     # what MapNG's resampler writes for "no data" (terrainResampler.js)
USER_AGENT = "Mozilla/5.0 (BeamNGBelfast elevation fetch; private use)"

# OSNI 10k sheets that cover the three squares (config/aoi.yaml) with a margin; chosen from the OSNI
# 10k coverage grid (sheet 129 is E 322400-332000, N 373600-380000, and so on).
OSNI_SHEETS = (129, 130, 146, 147, 165, 166)

OGL_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
LIDAR_ATTRIBUTION = ("Belfast City LIDAR is provided by the Department for Infrastructure (Rivers) with the "
                     "permission of the copyright owner, Cyient Europe Limited, and licensed under the Open "
                     "Government Licence v3.0")
OSNI_ATTRIBUTION = ("Contains OSNI Open Data (Land & Property Services, Ordnance Survey of Northern Ireland), "
                    "licensed under the Open Government Licence v3.0")
_LIDAR_BLOB = "https://opendatani.blob.core.windows.net/dfiriverslidar/"
_ADMIN = "https://admin.opendatani.gov.uk/dataset/"


def _lidar(zone: str, size: int) -> dict:
    return {
        "id": f"lidar_belfast_city_2006_{zone.lower()}",
        "title": f"LIDAR - Belfast City 2006, {zone} zone (DTM and DSM, 0.5 m ESRI ASCII grid)",
        "role": "primary elevation: DTM tiles inside the zip are used, DSM is not",
        "publisher": "Department for Infrastructure (Rivers), Northern Ireland",
        "copyright_owner": "Cyient Europe Ltd",
        "page_url": "https://www.opendatani.gov.uk/dataset/lidar-belfast-city-2006",
        "url": f"{_LIDAR_BLOB}BelfastCityLidar_{zone}_18032006.zip",
        "local": f"zip/BelfastCityLidar_{zone}_18032006.zip",
        "expected_bytes": size,
        "licence": "UK Open Government Licence v3.0",
        "licence_url": OGL_URL,
        "attribution_text": LIDAR_ATTRIBUTION,
        "attribution_required": True,
        "notes": ("Surveyed 18 March 2006. Irish Grid, heights above Mean Sea Level Belfast, quoted accuracy "
                  "+-0.15 m RMSE (less under trees and on steep ground). The copyright holder is Cyient Europe "
                  "Ltd: credit them. 'There are no plans to update the data.'"),
    }


def _osni(first: int, last: int, path: str, size: int, created: str) -> dict:
    return {
        "id": f"osni_10m_dtm_sheets_{first}_{last}",
        "title": f"OSNI Open Data - 10M DTM - Sheets {first}-{last}",
        "role": "gap fill where the LiDAR has no data",
        "publisher": "Land & Property Services - Ordnance Survey of Northern Ireland",
        "copyright_owner": "Land & Property Services - Ordnance Survey of Northern Ireland",
        "page_url": f"https://www.opendatani.gov.uk/dataset/osni-open-data-10m-dtm-sheets-{first}-{last}",
        "url": f"{_ADMIN}{path}",
        "local": f"osni10/osni_10m_dtm_sheets_{first}-{last}.zip",
        "expected_bytes": size,
        "licence": "UK Open Government Licence v3.0",
        "licence_url": OGL_URL,
        "attribution_text": OSNI_ATTRIBUTION,
        "attribution_required": True,
        "notes": ("Interpolated 10 m grid, Irish Grid, 'x y z' text at cell centres; the publisher says it is "
                  "interpolated from the OSNI Enhanced DTM (2014-2016). Catalogue entry created " + created + ". "
                  "No attribution wording is given on the dataset page: the text here is the OGL v3.0 form."),
    }


SOURCES = [
    _lidar("NorthEast", 295463130),
    _lidar("NorthWest", 402031779),
    _lidar("SouthEast", 317734448),
    _lidar("SouthWest", 400236616),
    _osni(101, 150, "64ccceb5-c06c-484d-8b9c-699357fc6b13/resource/a48821ab-8388-48ef-a9aa-97b8158cbc9b/"
          "download/osni_10m_dtm_sheets_101-150.zip", 179998372, "2025-11-20"),
    _osni(151, 200, "adea7269-2a86-4953-94bd-7b4434baba26/resource/a6392f75-d45a-4d2b-a8e8-678d86419f7a/"
          "download/osni_10m_dtm_sheets_151-200.zip", 188393176, "2025-11-21"),
    {
        "id": "lidar_belfast_city_notes",
        "title": "River Basin LIDAR - Belfast City - Notes (June 2019), the publisher's attribution statement",
        "role": "documentation (the attribution text above is quoted from it)",
        "publisher": "Department for Infrastructure (Rivers), Northern Ireland",
        "copyright_owner": "Department for Infrastructure (Rivers)",
        "page_url": "https://www.opendatani.gov.uk/dataset/lidar-belfast-city-2006",
        "url": _ADMIN + "1af1f042-2934-4912-8548-9186303ddb5f/resource/7616ee7c-fd91-42e5-866f-8a4abab2fbee/"
                        "download/river-basin-lidar-belfast-city-note.pdf",
        "local": "docs/river-basin-lidar-belfast-city-note.pdf",
        "expected_bytes": 137267,
        "licence": "UK Open Government Licence v3.0",
        "licence_url": OGL_URL,
        "attribution_text": LIDAR_ATTRIBUTION,
        "attribution_required": False,
        "notes": "Not used as data.",
    },
    {
        "id": "osni_coverage_grid_10k",
        "title": "OSNI Open Data - Coverage Grid - 10K (GeoJSON)",
        "role": "which OSNI sheets cover which ground (used to pick OSNI_SHEETS)",
        "publisher": "Land & Property Services - Ordnance Survey of Northern Ireland",
        "copyright_owner": "Land & Property Services - Ordnance Survey of Northern Ireland",
        "page_url": "https://www.opendatani.gov.uk/dataset/osni-open-data-coverage-grid-10k",
        "url": _ADMIN + "18a0c1f3-0e2a-406f-9d5f-ce190a166895/resource/3400824a-ae20-483d-b633-a936d8f45f8b/"
                        "download/osni_open_data_coverage_grid_10k.geojson",
        "local": "osni10/grid10k.geojson",
        "expected_bytes": 548781,
        "licence": "UK Open Government Licence v3.0",
        "licence_url": OGL_URL,
        "attribution_text": OSNI_ATTRIBUTION,
        "attribution_required": False,
        "notes": "Index only; no elevation data.",
    },
]


# ------------------------------------------------------------------ download and manifest

def sha256_file(path: Path, block: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(block):
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, dest: Path, expected_bytes: int | None = None, log=print) -> Path:
    """Fetch `url` to `dest` (resumes a .part file; skips a complete file). Raises on any HTTP or size error."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and (expected_bytes is None or dest.stat().st_size == expected_bytes):
        log(f"have {dest.name} ({dest.stat().st_size} bytes)")
        return dest
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if have:
        req.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(req, timeout=120) as resp:
        mode = "ab" if (have and resp.status == 206) else "wb"
        with open(part, mode) as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
    size = part.stat().st_size
    if expected_bytes is not None and size != expected_bytes:
        raise RuntimeError(f"{url}: got {size} bytes, expected {expected_bytes}")
    os.replace(part, dest)
    log(f"downloaded {dest.name} ({size} bytes)")
    return dest


def build_manifest(sources: list[dict], root: Path, previous: dict | None = None, today: str | None = None) -> dict:
    """The committed manifest: one entry per file with url, retrieval date, size, SHA-256 and licence text."""
    today = today or dt.date.today().isoformat()
    prev = {e["id"]: e for e in (previous or {}).get("files", [])}
    files = []
    for src in sources:
        path = root / src["local"]
        entry = {k: v for k, v in src.items() if k not in ("local", "expected_bytes")}
        entry["local_path"] = f"H:/BelfastData/lidar/{src['local']}"
        size = path.stat().st_size
        digest = sha256_file(path)
        old = prev.get(src["id"])
        same = old and old.get("sha256") == digest
        entry.update({"retrieved": old["retrieved"] if same else today, "bytes": size, "sha256": digest})
        files.append(entry)
    return {
        "version": 1,
        "description": ("Elevation sources for the Belfast map (T-203). Raw files live under H:/BelfastData/lidar and "
                        "are not in git. T-208 builds the credits from `attribution_text`."),
        "credits_required": [e["attribution_text"] for e in files if e.get("attribution_required")],
        "files": files,
    }


def cmd_download(args) -> int:
    root = Path(args.root)
    for src in SOURCES:
        download_file(src["url"], root / src["local"], src["expected_bytes"])
    previous = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else None
    manifest = build_manifest(SOURCES, root, previous)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total = sum(e["bytes"] for e in manifest["files"])
    print(f"manifest {MANIFEST}: {len(manifest['files'])} files, {total} bytes")
    return 0


# ------------------------------------------------------------------ ESRI ASCII grid and block averaging

_HEADER_KEYS = ("ncols", "nrows", "xllcorner", "yllcorner", "xllcenter", "yllcenter", "cellsize", "nodata_value")


def parse_asc_header(data: bytes) -> tuple[dict, int]:
    """(header, offset of the first data byte). Keys lower-cased; corner coordinates always `xll`/`yll`."""
    header: dict = {}
    pos = 0
    while True:
        end = data.find(b"\n", pos)
        if end < 0:
            break
        parts = data[pos:end].split()
        if len(parts) != 2 or parts[0].decode("ascii", "replace").lower() not in _HEADER_KEYS:
            break
        header[parts[0].decode("ascii").lower()] = float(parts[1])
        pos = end + 1
    for need in ("ncols", "nrows", "cellsize"):
        if need not in header:
            raise ValueError(f"ASCII grid header lacks {need}: {header}")
    half = header["cellsize"] / 2
    if "xllcorner" in header and "yllcorner" in header:
        header["xll"], header["yll"] = header["xllcorner"], header["yllcorner"]
    elif "xllcenter" in header and "yllcenter" in header:
        header["xll"], header["yll"] = header["xllcenter"] - half, header["yllcenter"] - half
    else:
        raise ValueError(f"ASCII grid header has no lower-left corner: {header}")
    header["ncols"], header["nrows"] = int(header["ncols"]), int(header["nrows"])
    return header, pos


def parse_asc(data: bytes) -> tuple[dict, np.ndarray]:
    """(header, float32 array [nrows, ncols], row 0 north) with nodata turned into NaN."""
    header, pos = parse_asc_header(data)
    arr = np.fromstring(data[pos:], dtype=np.float32, sep=" ")  # whitespace of any kind separates the values
    n = header["ncols"] * header["nrows"]
    if arr.size != n:
        raise ValueError(f"ASCII grid has {arr.size} values, header says {n}")
    arr = arr.reshape(header["nrows"], header["ncols"])
    nodata = header.get("nodata_value", -9999.0)
    arr[(arr == nodata) | ~np.isfinite(arr)] = np.nan
    return header, arr


def block_mean(a: np.ndarray, f: int) -> np.ndarray:
    """Mean of each f x f block ignoring NaN; a block with no valid value stays NaN."""
    if f == 1:
        return a.astype(np.float32, copy=True)
    h, w = a.shape
    if h % f or w % f:
        raise ValueError(f"shape {a.shape} does not divide by {f}")
    b = a.reshape(h // f, f, w // f, f)
    ok = np.isfinite(b)
    cnt = ok.sum(axis=(1, 3))
    tot = np.where(ok, b, 0.0).sum(axis=(1, 3), dtype=np.float64)
    return np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan).astype(np.float32)


# ------------------------------------------------------------------ rasters

@dataclass
class Raster:
    """A grid of heights in a projected CRS: data[row, col], row 0 north, NaN = no data.

    (x0, y0) is the easting and northing of the CENTRE of cell [0, 0]; `cell` is the spacing in metres."""
    data: np.ndarray
    x0: float
    y0: float
    cell: float

    @classmethod
    def from_corner(cls, data: np.ndarray, xll: float, yur: float, cell: float) -> "Raster":
        """From the lower-left easting and upper-right northing of the grid's outer edge."""
        return cls(data, xll + cell / 2, yur - cell / 2, cell)

    def sample(self, e: np.ndarray, n: np.ndarray) -> np.ndarray:
        """Heights at points (e, n): bilinear from the valid neighbours, NaN where the nearest cell has no data
        or the point is outside the cell centres. Returns float32 with the shape of `e`."""
        e = np.asarray(e, float)
        n = np.asarray(n, float)
        fx = (e - self.x0) / self.cell
        fy = (self.y0 - n) / self.cell
        h, w = self.data.shape
        inside = (fx >= 0) & (fy >= 0) & (fx <= w - 1) & (fy <= h - 1)
        fx = np.clip(fx, 0, w - 1)
        fy = np.clip(fy, 0, h - 1)
        ix = np.minimum(np.floor(fx).astype(np.intp), max(w - 2, 0))
        iy = np.minimum(np.floor(fy).astype(np.intp), max(h - 2, 0))
        tx = fx - ix
        ty = fy - iy
        ix1 = np.minimum(ix + 1, w - 1)
        iy1 = np.minimum(iy + 1, h - 1)
        acc = np.zeros(e.shape)
        wsum = np.zeros(e.shape)
        for jy, wy in ((iy, 1 - ty), (iy1, ty)):
            for jx, wx in ((ix, 1 - tx), (ix1, tx)):
                v = np.asarray(self.data[jy, jx], dtype=float)
                ok = np.isfinite(v)
                wt = wy * wx * ok
                acc += np.where(ok, v, 0.0) * wt
                wsum += wt
        nearest = np.asarray(self.data[np.rint(fy).astype(np.intp), np.rint(fx).astype(np.intp)], dtype=float)
        good = inside & (wsum > 0) & np.isfinite(nearest)
        return np.where(good, acc / np.where(wsum > 0, wsum, 1), np.nan).astype(np.float32)

    def save(self, npy: Path, meta: Path, extra: dict | None = None) -> None:
        np.save(npy, self.data)
        meta.write_text(json.dumps({"x0": self.x0, "y0": self.y0, "cell": self.cell, "shape": list(self.data.shape),
                                    **(extra or {})}, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, npy: Path, meta: Path, mmap: bool = True) -> "Raster":
        m = json.loads(meta.read_text(encoding="utf-8"))
        return cls(np.load(npy, mmap_mode="r" if mmap else None), m["x0"], m["y0"], m["cell"])


# ------------------------------------------------------------------ LiDAR mosaic

def lidar_members(zf: zipfile.ZipFile) -> list[str]:
    """The DTM tiles of a zone zip (the DSM folder is skipped)."""
    return sorted(n for n in zf.namelist() if n.lower().endswith(".asc") and "/dtm_" in n.lower())


def build_lidar_mosaic(zip_paths: list[Path], out_dir: Path, log=print) -> dict:
    """Average every DTM tile to 1 m and lay them in one memory-mapped raster `lidar_1m.npy` (NaN = no data).

    Tiles are placed by their header, not by name. Where tiles overlap the first valid value wins."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tiles = []  # (zip, member, header)
    for zp in zip_paths:
        with zipfile.ZipFile(zp) as zf:
            for m in lidar_members(zf):
                with zf.open(m) as f:
                    hdr, _ = parse_asc_header(f.read(1024))
                tiles.append((zp, m, hdr))
    if not tiles:
        raise RuntimeError("no DTM tiles found in " + ", ".join(map(str, zip_paths)))
    for _, m, h in tiles:
        if abs(1.0 / h["cellsize"] - round(1.0 / h["cellsize"])) > 1e-9:
            raise ValueError(f"{m}: cellsize {h['cellsize']} does not divide 1 m")
        if h["xll"] != round(h["xll"]) or h["yll"] != round(h["yll"]):
            raise ValueError(f"{m}: corner {h['xll']}, {h['yll']} is not on the 1 m grid")
    e0 = min(h["xll"] for *_, h in tiles)
    n0 = min(h["yll"] for *_, h in tiles)
    e1 = max(h["xll"] + h["ncols"] * h["cellsize"] for *_, h in tiles)
    n1 = max(h["yll"] + h["nrows"] * h["cellsize"] for *_, h in tiles)
    cols, rows = int(round(e1 - e0)), int(round(n1 - n0))
    mosaic = np.lib.format.open_memmap(out_dir / "lidar_1m.npy", mode="w+", dtype=np.float32, shape=(rows, cols))
    mosaic[:] = np.nan
    names = []
    for zp, m, h in tiles:
        with zipfile.ZipFile(zp) as zf:
            hdr, arr = parse_asc(zf.read(m))
        f = int(round(1.0 / hdr["cellsize"]))
        b = block_mean(arr, f)
        c0 = int(round(hdr["xll"] - e0))
        r0 = int(round(n1 - (hdr["yll"] + hdr["nrows"] * hdr["cellsize"])))
        win = mosaic[r0:r0 + b.shape[0], c0:c0 + b.shape[1]]
        win[:] = np.where(np.isfinite(win), win, b)
        names.append(Path(m).name)
        log(f"  {Path(m).name}: {np.isfinite(b).mean() * 100:.1f}% valid")
    mosaic.flush()
    meta = {"x0": e0 + 0.5, "y0": n1 - 0.5, "cell": 1.0, "shape": [rows, cols], "crs": "EPSG:29903 (Irish Grid)",
            "tiles": sorted(names), "zips": [Path(z).name for z in zip_paths],
            "extent": {"e_min": e0, "e_max": e1, "n_min": n0, "n_max": n1}}
    (out_dir / "lidar_1m.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    log(f"mosaic {cols} x {rows} cells at 1 m, {len(names)} tiles, E {e0:.0f}-{e1:.0f} N {n0:.0f}-{n1:.0f}")
    return meta


# ------------------------------------------------------------------ OSNI 10 m

def parse_osni_xyz(text: bytes) -> np.ndarray:
    """(N, 3) float64 array of E, N, Z from an OSNI 10 m sheet."""
    a = np.fromstring(text, dtype=np.float64, sep=" ")
    if a.size % 3:
        raise ValueError("OSNI sheet does not hold whole x y z triples")
    return a.reshape(-1, 3)


def raster_from_xyz(points: np.ndarray, cell: float = 10.0, nodata_below: float = -100.0) -> Raster:
    """Grid scattered points that sit on a regular lattice (cell centres). Missing cells are NaN."""
    e, n, z = points[:, 0], points[:, 1], points[:, 2].copy()
    z[z < nodata_below] = np.nan
    x0, y1 = e.min(), n.max()
    cols = np.rint((e - x0) / cell).astype(np.intp)
    rows = np.rint((y1 - n) / cell).astype(np.intp)
    if np.abs(((e - x0) / cell) - cols).max() > 1e-3 or np.abs(((y1 - n) / cell) - rows).max() > 1e-3:
        raise ValueError("points are not on a regular lattice")
    grid = np.full((rows.max() + 1, cols.max() + 1), np.nan, dtype=np.float32)
    grid[rows, cols] = z
    return Raster(grid, float(x0), float(y1), cell)


def build_osni(zip_paths: list[Path], out_dir: Path, sheets=OSNI_SHEETS, log=print) -> dict:
    """Read the chosen sheets from the OSNI zips and write one raster `osni_10m.npy`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pts = []
    found = []
    for zp in zip_paths:
        with zipfile.ZipFile(zp) as zf:
            for name in zf.namelist():
                m = re.match(r"^Sheet(\d+)v\d+\s*\.txt$", Path(name).name, re.I)
                if m and int(m.group(1)) in sheets:
                    pts.append(parse_osni_xyz(zf.read(name)))
                    found.append(int(m.group(1)))
                    log(f"  sheet {m.group(1)}: {len(pts[-1])} points")
    missing = sorted(set(sheets) - set(found))
    if missing:
        raise RuntimeError(f"OSNI sheets not found in the zips: {missing}")
    r = raster_from_xyz(np.vstack(pts))
    meta = {"sheets": sorted(found), "crs": "EPSG:29903 (Irish Grid)"}
    r.save(out_dir / "osni_10m.npy", out_dir / "osni_10m.json", meta)
    h, w = r.data.shape
    log(f"osni raster {w} x {h} cells at 10 m, {np.isfinite(r.data).mean() * 100:.1f}% valid")
    return meta


# ------------------------------------------------------------------ sampling an area's heightmap grid

def pixel_latlon(area: aoi.Area, rows: np.ndarray, grid: str = "game"):
    """Latitude and longitude of heightmap pixels in the given rows, shape (len(rows), size_px).

    grid "game": where the game draws the sample (world x = col - n/2, y = n/2 - 1 - row, docs/aoi.md).
    grid "mapng": where MapNG's own resampler samples its DEM (spacing span / (n - 1))."""
    n = area.size_px
    cols = np.arange(n, dtype=float)[None, :]
    r = np.asarray(rows, float)[:, None]
    if grid == "game":
        x, y = area.pixel_to_game(*np.broadcast_arrays(cols, r))
        return area.game_to_latlon(x, y)
    if grid == "mapng":
        return area.pixel_to_latlon_mapng(*np.broadcast_arrays(cols, r))
    raise ValueError(f"grid must be 'game' or 'mapng', not {grid!r}")


def sample_area(area: aoi.Area, cfg: aoi.Config, lidar: Raster, osni: Raster, grid: str = "game",
                chunk_rows: int = 128) -> tuple[np.ndarray, np.ndarray]:
    """(lidar, osni) heights at every pixel of the area, float32 [size_px, size_px], NaN where a source has none."""
    n = area.size_px
    out_l = np.full((n, n), np.nan, dtype=np.float32)
    out_o = np.full((n, n), np.nan, dtype=np.float32)
    for r0 in range(0, n, chunk_rows):
        rows = np.arange(r0, min(n, r0 + chunk_rows))
        lat, lon = pixel_latlon(area, rows, grid)
        e, nn = cfg.latlon_to_crs("irish_grid", lat.ravel(), lon.ravel())
        e = np.asarray(e).reshape(lat.shape)
        nn = np.asarray(nn).reshape(lat.shape)
        out_l[rows] = lidar.sample(e, nn)
        out_o[rows] = osni.sample(e, nn)
    return out_l, out_o


# ------------------------------------------------------------------ gap fill

def _smooth_axis(a: np.ndarray, kernel: np.ndarray, axis: int) -> np.ndarray:
    """Convolve along one axis with zero padding (kernel odd length)."""
    r = len(kernel) // 2
    out = np.zeros_like(a)
    n = a.shape[axis]
    for k, wk in enumerate(kernel):
        s = k - r
        if abs(s) >= n:        # shifted completely off a short axis
            continue
        src = [slice(None)] * a.ndim
        dst = [slice(None)] * a.ndim
        if s >= 0:
            src[axis], dst[axis] = slice(s, n), slice(0, n - s)
        else:
            src[axis], dst[axis] = slice(0, n + s), slice(-s, n)
        out[tuple(dst)] += wk * a[tuple(src)]
    return out


def seam_correction(lidar: np.ndarray, osni: np.ndarray, block: int = 8, sigma_m: float = 40.0,
                    mpp: float = 1.0, prior: float = 0.02) -> tuple[np.ndarray, float, int]:
    """Where OSNI stands in for LiDAR it is shifted by the field returned here (metres to ADD to OSNI).

    The field is the LiDAR-minus-OSNI difference seen around each point (Gaussian-weighted over `sigma_m`, on
    `block`-pixel cells), pulled towards the median difference of the whole area where no LiDAR is near.
    Returns (field with the shape of `lidar`, median difference, number of pixels both sources cover)."""
    both = np.isfinite(lidar) & np.isfinite(osni)
    nboth = int(both.sum())
    bias = float(np.median(lidar[both] - osni[both])) if nboth else 0.0
    n_r, n_c = lidar.shape
    if n_r % block or n_c % block:
        raise ValueError("area size must divide by the block size")
    diff = np.where(both, lidar.astype(np.float64) - osni, 0.0)
    cs = diff.reshape(n_r // block, block, n_c // block, block).sum(axis=(1, 3)) / block ** 2
    cw = both.reshape(n_r // block, block, n_c // block, block).sum(axis=(1, 3)) / block ** 2
    sigma_c = max(sigma_m / (block * mpp), 0.5)
    radius = int(np.ceil(3 * sigma_c))
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma_c) ** 2)
    k /= k.sum()
    num = _smooth_axis(_smooth_axis(cs, k, 0), k, 1)
    den = _smooth_axis(_smooth_axis(cw, k, 0), k, 1)
    coarse = (num + bias * prior) / (den + prior)
    return _upsample(coarse, lidar.shape, block), bias, nboth


def _upsample(coarse: np.ndarray, shape: tuple[int, int], block: int) -> np.ndarray:
    """Bilinear from block-sized cells up to pixels (coarse cell centres at (i + 0.5) * block - 0.5 pixels)."""
    n_r, n_c = shape

    def weights(n_fine: int, n_coarse: int):
        pos = (np.arange(n_fine) + 0.5) / block - 0.5
        i0 = np.clip(np.floor(pos).astype(int), 0, n_coarse - 1)
        i1 = np.minimum(i0 + 1, n_coarse - 1)
        t = np.clip(pos - i0, 0.0, 1.0)
        return i0, i1, t
    ry0, ry1, ty = weights(n_r, coarse.shape[0])
    rx0, rx1, tx = weights(n_c, coarse.shape[1])
    out = np.empty((n_r, n_c), dtype=np.float32)
    for r in range(n_r):
        row = (1 - ty[r]) * coarse[ry0[r]] + ty[r] * coarse[ry1[r]]
        out[r] = (1 - tx) * row[rx0] + tx * row[rx1]
    return out


def feather_weight(valid: np.ndarray, block: int = 4, sigma_m: float = 10.0, mpp: float = 1.0) -> np.ndarray:
    """Weight (0..1) to give LiDAR at each pixel: 0 on the edge of its coverage, 1 from about 2 sigma inside.

    The LiDAR-valid fraction is smoothed (Gaussian, `sigma_m`); that is 0.5 on a straight edge, so
    clip((f - 0.5) * 2) is 0 there and rises to 1 inside. Used to blend LiDAR into corrected OSNI so the seam
    has no step."""
    n_r, n_c = valid.shape
    if n_r % block or n_c % block:
        raise ValueError("area size must divide by the block size")
    cw = valid.reshape(n_r // block, block, n_c // block, block).sum(axis=(1, 3)) / block ** 2
    sigma_c = max(sigma_m / (block * mpp), 0.5)
    radius = int(np.ceil(3 * sigma_c))
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma_c) ** 2)
    k /= k.sum()
    # pad with the edge value so the area's own border does not look like the end of the LiDAR
    pad = np.pad(cw, radius, mode="edge")
    f = _smooth_axis(_smooth_axis(pad, k, 0), k, 1)[radius:-radius, radius:-radius]
    return np.clip((_upsample(f, valid.shape, block) - 0.5) * 2.0, 0.0, 1.0)


def fill_gaps(lidar: np.ndarray, osni: np.ndarray, **kw) -> tuple[np.ndarray, np.ndarray, dict]:
    """Merge: LiDAR where it has data, corrected OSNI elsewhere. Returns (heights, source, info).

    `source` is uint8: 0 LiDAR, 1 OSNI fill, 2 still no data (NaN in heights)."""
    lv = np.isfinite(lidar)
    ov = np.isfinite(osni)
    use_o = ~lv & ov
    source = np.where(lv, 0, np.where(ov, 1, 2)).astype(np.uint8)
    out = lidar.astype(np.float32, copy=True)
    info = {"seam_median_diff_m": 0.0, "pixels_both_sources": int((lv & ov).sum())}
    if use_o.any():
        field, bias, nboth = seam_correction(lidar, osni, **kw)
        out[use_o] = osni[use_o] + field[use_o]
        # inside the LiDAR, near its edge, blend towards the corrected OSNI (no step at the seam)
        w = feather_weight(lv)
        near = lv & ov & (w < 1.0)
        out[near] = w[near] * lidar[near] + (1 - w[near]) * (osni[near] + field[near])
        info["feathered_pixels"] = int(near.sum())
        info.update({"seam_median_diff_m": round(bias, 4), "pixels_both_sources": nboth})
    return out, source, info


def seam_steps(heights: np.ndarray, source: np.ndarray) -> dict:
    """Height difference across pairs of 4-neighbour pixels where one is LiDAR and the other is OSNI fill."""
    steps = []
    for a, b, sa, sb in ((heights[:, :-1], heights[:, 1:], source[:, :-1], source[:, 1:]),
                         (heights[:-1], heights[1:], source[:-1], source[1:])):
        m = ((sa == 0) & (sb == 1)) | ((sa == 1) & (sb == 0))
        steps.append(np.abs(a[m].astype(np.float64) - b[m]))
    s = np.concatenate(steps)
    if not s.size:
        return {"pairs": 0, "mean_m": 0.0, "max_m": 0.0}
    return {"pairs": int(s.size), "mean_m": round(float(s.mean()), 4), "max_m": round(float(s.max()), 4)}


# ------------------------------------------------------------------ coverage numbers and map

def coverage_stats(source: np.ndarray, lidar_extent_hint: str = "") -> dict:
    """Pixel counts: LiDAR, OSNI fill, no data. `gaps_before_fill` is what the LiDAR alone leaves."""
    total = int(source.size)
    lidar = int((source == 0).sum())
    fill = int((source == 1).sum())
    missing = int((source == 2).sum())
    return {
        "pixels": total,
        "lidar_pixels": lidar,
        "lidar_percent": round(100 * lidar / total, 3),
        "gaps_before_fill": total - lidar,
        "gaps_before_fill_percent": round(100 * (total - lidar) / total, 3),
        "osni_fill_pixels": fill,
        "gaps_after_fill": missing,
    }


CLASS_COLOURS = np.array([(70, 150, 60), (240, 165, 0), (215, 0, 0)], dtype=np.uint8)  # lidar, osni fill, none


def coverage_image(source: np.ndarray, max_px: int = 2048, outlines=()) -> np.ndarray:
    """RGB image [h, w, 3] of the coverage classes, shrunk to at most max_px; the worst class in a block wins.

    `outlines` is a list of (col0, row0, col1, row1) boxes in pixels of `source`, drawn as white lines."""
    n = source.shape[0]
    f = 1
    while n // f > max_px and n % (f * 2) == 0:
        f *= 2
    small = source.reshape(n // f, f, source.shape[1] // f, f).max(axis=(1, 3)) if f > 1 else source
    img = CLASS_COLOURS[small]
    for c0, r0, c1, r1 in outlines:
        a, b, c, d = (int(round(v / f)) for v in (c0, r0, c1, r1))
        a, b = max(a, 0), max(b, 0)
        c, d = min(c, img.shape[1] - 1), min(d, img.shape[0] - 1)
        img[b, a:c + 1] = img[d, a:c + 1] = 255
        img[b:d + 1, a] = img[b:d + 1, c] = 255
    return img


def write_png_rgb(path: Path, rgb: np.ndarray) -> None:
    """Write an RGB uint8 array as a PNG using the standard library only."""
    h, w, _ = rgb.shape

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + rgb[r].tobytes() for r in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ------------------------------------------------------------------ MapNG's height file

def write_height_file(path: Path, heights: np.ndarray, size_px: int) -> dict:
    """Write MapNG's `<dem>.f32`: size_px * size_px little-endian float32, row-major, row 0 the NORTH edge.

    This is what scripts/sweep/exportArea.mjs reads (`new Float32Array(fs.readFileSync(<dem>.f32).buffer)`,
    indexed `heightMap[y * width + x]`) and what fetchArea.mjs writes (`Buffer.from(gpxz.heightMap.buffer)`).
    NaN is refused: MapNG's own no-data value is -99999 and the area must have none."""
    if heights.shape != (size_px, size_px):
        raise ValueError(f"heights are {heights.shape}, the area is {size_px} x {size_px}")
    if not np.isfinite(heights).all():
        raise ValueError(f"{int((~np.isfinite(heights)).sum())} pixels have no height; refusing to write")
    data = np.ascontiguousarray(heights, dtype="<f4")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.tobytes())
    return {"path": str(path), "bytes": data.nbytes, "min_m": float(data.min()), "max_m": float(data.max())}


def read_height_file(path: Path, size_px: int) -> np.ndarray:
    """Read a height file back the way MapNG does (flat float32, size_px squared values)."""
    raw = np.fromfile(path, dtype="<f4")
    if raw.size != size_px * size_px:
        raise ValueError(f"{path}: {raw.size} values, expected {size_px * size_px}")
    return raw.reshape(size_px, size_px)


# ------------------------------------------------------------------ commands

def _under_data(path: Path) -> Path:
    p = Path(os.path.abspath(path))
    root = os.path.normcase(os.path.abspath(DATA_DIR))
    if not (os.path.normcase(str(p)) + os.sep).startswith(root + os.sep):
        raise SystemExit(f"{p} is not under {DATA_DIR}")
    return p


def cmd_mosaic(args) -> int:
    root = Path(args.root)
    zips = sorted((root / "zip").glob("BelfastCityLidar_*.zip"))
    if len(zips) != 4:
        raise SystemExit(f"expected four LiDAR zone zips in {root / 'zip'}, found {len(zips)}: run `download`")
    build_lidar_mosaic(zips, _under_data(root / "mosaic"))
    return 0


def cmd_osni(args) -> int:
    root = Path(args.root)
    zips = sorted((root / "osni10").glob("osni_10m_dtm_sheets_*.zip"))
    if not zips:
        raise SystemExit(f"no OSNI zips in {root / 'osni10'}: run `download`")
    build_osni(zips, _under_data(root / "mosaic"))
    return 0


def load_sources(root: Path) -> tuple[Raster, Raster]:
    m = root / "mosaic"
    for f in ("lidar_1m.npy", "osni_10m.npy"):
        if not (m / f).is_file():
            raise SystemExit(f"{m / f} missing: run `mosaic` and `osni`")
    return Raster.load(m / "lidar_1m.npy", m / "lidar_1m.json"), Raster.load(m / "osni_10m.npy", m / "osni_10m.json")


def analyse_area(cfg: aoi.Config, name: str, lidar: Raster, osni: Raster, grid: str, out_root: Path,
                 write_height: bool, log=print) -> dict:
    area = cfg.area(name)
    out = _under_data(out_root / name)
    out.mkdir(parents=True, exist_ok=True)
    log(f"[{name}] sampling {area.size_px} x {area.size_px} pixels ({grid} grid)")
    l_vals, o_vals = sample_area(area, cfg, lidar, osni, grid)
    heights, source, info = fill_gaps(l_vals, o_vals)
    stats = coverage_stats(source)
    outlines = []
    if name != "slice" and "slice" in cfg.areas:
        s = cfg.area("slice")
        pts = []  # the slice's north-west and south-east corners, in this area's pixels
        for x, y in ((-s.world_size / 2, s.world_size / 2), (s.world_size / 2, -s.world_size / 2)):
            lat, lon = s.game_to_latlon(x, y)
            gx, gy = area.latlon_to_game(lat, lon)
            pts.append(area.game_to_pixel(gx, gy))
        (c0, r0), (c1, r1) = pts
        outlines.append((float(c0), float(r0), float(c1), float(r1)))
    write_png_rgb(out / "coverage.png", coverage_image(source, outlines=outlines))
    np.save(out / "source_mask.npy", source)
    report = {"area": name, "grid": grid, "size_px": area.size_px, **stats, **info, "seam_steps": seam_steps(heights, source),
              "lidar_extent_note": "see H:/BelfastData/lidar/mosaic/lidar_1m.json"}
    if np.isfinite(heights).all():
        report["height_min_m"] = round(float(heights.min()), 3)
        report["height_max_m"] = round(float(heights.max()), 3)
    if write_height:
        report["height_file"] = write_height_file(out / "gpxz.f32", heights, area.size_px)
    (out / "coverage_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    log(f"[{name}] LiDAR {stats['lidar_pixels']} / {stats['pixels']} px ({stats['lidar_percent']}%); "
        f"gaps before fill {stats['gaps_before_fill']}; OSNI filled {stats['osni_fill_pixels']}; "
        f"gaps after fill {stats['gaps_after_fill']}; seam median diff {info['seam_median_diff_m']} m")
    return report


def install_height_file(src: Path, mapng_area: str, size_px: int, areas_dir: Path = AREAS_DIR) -> Path:
    """Copy gpxz.f32 into MapNG's area folder. Refuses unless meta.json is there, matches the size and has
    gpxzSmooth false (true would make MapNG smooth the elevation as 30 m data). Touches no other file."""
    folder = _under_data(areas_dir / mapng_area)
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        raise SystemExit(f"{meta_path} missing: fetch the area first (tools/mapng_export.py fetch)")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("size") != size_px:
        raise SystemExit(f"{meta_path}: size {meta.get('size')} != {size_px}")
    if meta.get("gpxzSmooth"):
        raise SystemExit(f"{meta_path}: gpxzSmooth is true; MapNG would smooth the LiDAR as 30 m data")
    dest = folder / "gpxz.f32"
    shutil.copyfile(src, dest)
    return dest


def cmd_coverage(args, write_height: bool = False) -> int:
    root = Path(args.root)
    cfg = aoi.load()
    lidar, osni = load_sources(root)
    out_root = Path(args.out)
    code = 0
    for name in args.areas:
        rep = analyse_area(cfg, name, lidar, osni, args.grid, out_root, write_height)
        if rep["gaps_after_fill"]:
            code = 1
        if write_height and getattr(args, "install", None) and name == "slice":
            dest = install_height_file(out_root / name / "gpxz.f32", args.install, cfg.area(name).size_px)
            print(f"installed {dest}")
    return code


def cmd_build(args) -> int:
    return cmd_coverage(args, write_height=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=str(LIDAR_DIR), help="raw data folder (default H:\\BelfastData\\lidar)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("download", help="fetch the six zips and notes, write data/sources/elevation.json").set_defaults(
        func=cmd_download)
    sub.add_parser("mosaic", help="LiDAR DTM tiles -> 1 m raster").set_defaults(func=cmd_mosaic)
    sub.add_parser("osni", help="OSNI 10 m sheets -> raster").set_defaults(func=cmd_osni)
    for name, func, hlp in (("coverage", cmd_coverage, "coverage numbers and map for the squares"),
                            ("build", cmd_build, "coverage, gap fill, write gpxz.f32")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("areas", nargs="+", help="names from config/aoi.yaml (slice, city_south, full)")
        s.add_argument("--grid", choices=("game", "mapng"), default="game",
                       help="sample where the game draws the terrain (default) or where MapNG samples its DEM")
        s.add_argument("--out", default=str(ELEV_DIR), help="output folder (default H:\\BelfastData\\elevation)")
        if name == "build":
            s.add_argument("--install", metavar="MAPNG_AREA",
                           help="also copy the slice's gpxz.f32 into H:\\BelfastData\\mapng\\areas\\MAPNG_AREA")
        s.set_defaults(func=func)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
