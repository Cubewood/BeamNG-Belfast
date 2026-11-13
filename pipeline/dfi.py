"""DfI road widths (T-205): Highway Network and Carriageway Details, matched to OSM ways, width per way.

    uv run --no-project --python 3.12 --with pyproj --with numpy --with pyyaml --with requests \\
        python pipeline/dfi.py download          # both files to H:\\BelfastData\\dfi, writes data/sources/dfi.json
    ... python pipeline/dfi.py verify            # the files on disk against the manifest
    ... python pipeline/dfi.py parse             # xlsx and csv -> items.csv, sections.csv (stdlib only)
    ... python pipeline/dfi.py match [--area slice]   # match to the pinned OSM cut, write widths_<area>.json
    ... python pipeline/dfi.py sample [--area slice] [-n 30]   # a spread of matches for a hand check

The data
- Highway Network (CSV, live link, rebuilt every evening): one row per road section with a code, a name
  ("7020A0002_02 BELFAST RD: SHORE RD RBT TO ..."), class (A, B, C, U, M), type (single, dual) and length.
  It has NO coordinates.
- Carriageway Details (xlsx, 5 July 2021, four sheets by DfI division): one row per stretch of a section
  with one width (metres), start and end chainage along the section, surface, and ONE point (Easting,
  Northing, whole metres, Irish Grid EPSG:29903). The point is where the stretch starts. Geometry
  therefore comes from these points, names and classes from the Highway Network by section code.

How a width gets onto an OSM way (docs/dfi_widths.md has the numbers)
1. Items of type CARRIAGEWAY are turned into lat/lon (config/aoi.yaml irish_grid) and local metres.
2. Every item point is snapped to the nearest OSM highway segment within SNAP_M. Its section's street name
   (abbreviations expanded, trailing section digit dropped) is compared with the way's name, and the
   section class and the way's ref with each other. A candidate whose name disagrees is dropped when the
   way has a name; the nearest agreeing candidate wins.
3. A way's width is the length-weighted median of its matched items' widths (weight = item length, at
   least MIN_WEIGHT_M so a zero-length junction item still counts a little). Items wider than
   JUNCTION_WIDTH_M, or under MIN_WIDTH_M, are ignored as junction mouths and data slips.
4. Confidence 0..1 is the product of four parts, each explained in `confidence()`.

Data: DfI Roads and Land and Property Services via OpenDataNI, Open Government Licence v3.0. Raw files stay under
H:\\BelfastData\\dfi and never go in git; data/sources/dfi.json holds URL, date, size and SHA-256.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA_DIR = Path(r"H:\BelfastData")
DFI_DIR = DATA_DIR / "dfi"
OSM_DIR = DATA_DIR / "osm"
CUT_DIR = OSM_DIR / "cuts"
MANIFEST_PATH = ROOT / "data" / "sources" / "dfi.json"
OSM_MANIFEST_PATH = ROOT / "data" / "sources" / "osm.json"

USER_AGENT = "BeamNGBelfast-dfi/1.0 (one download of two public OpenDataNI files; see docs/dfi_widths.md)"
RECIPE = 1  # bump when the matching or the output shape changes

SOURCES = {
    "highway_network": {
        "title": "DfI Highway Network",
        "dataset_page": "https://www.opendatani.gov.uk/dataset/highway-network",
        "url": "https://dfi.highway-iams.uk/OpenData/HIGHWAY_NETWORK.CSV",
        "file": "HIGHWAY_NETWORK.CSV",
        "dataset_metadata_modified": "2026-06-15T12:58:52Z",
        "note": "Live link, rebuilt every evening; the SHA-256 below pins the copy that was used. No coordinates.",
    },
    "carriageway_details": {
        "title": "DfI Carriageway Details",
        "dataset_page": "https://www.opendatani.gov.uk/dataset/carriageway-details",
        "url": ("https://admin.opendatani.gov.uk/dataset/1532f7f3-613a-4fb1-91db-008b0b24e990/resource/"
                "ce97f716-f2ff-4540-8033-3536eded8e49/download/carriageway-details-all-export-5-july-2021.xlsx"),
        "file": "carriageway-details-all-export-5-july-2021.xlsx",
        "dataset_metadata_modified": "2021-09-20T13:32:56Z",
        "note": "Export of 5 July 2021. The URL redirects to signed object storage; the signed address is not recorded.",
    },
}
LICENCE = "UK Open Government Licence v3.0 (OGL)"
ATTRIBUTION = ("Contains public sector information licensed under the Open Government Licence v3.0. "
               "Source: Department for Infrastructure (DfI Roads), via OpenDataNI.")

# Item types that are a road surface a car drives on. Anything else (footways, cycle tracks, verges) is not.
CARRIAGEWAY_TYPE = "CARRIAGEWAY"

# Matching knobs. Metres unless said.
SNAP_M = 18.0          # an item point further than this from every candidate way is unmatched
NAME_SNAP_M = 30.0     # a second, wider look for items whose own section name agrees with a way's name
MIN_WIDTH_M = 2.0      # narrower than any road in the data; a slip
JUNCTION_WIDTH_M = 20.0  # wider than this is a junction mouth, turning head or car park, not the running width
MIN_WEIGHT_M = 2.0
# Local metres per degree are computed from the latitude, not fixed.

# Roads a learner can drive on (the OSM classes MapNG builds as roads, minus service and paths).
DRIVABLE = frozenset({
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential", "living_street", "service", "road",
})
ROAD_CLASS_ORDER = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
                    "living_street", "service", "other"]


class DfiError(Exception):
    pass


# ---------------------------------------------------------------- hashing and manifest

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    if not path.is_file():
        raise DfiError(f"{path} does not exist; run `dfi.py download` first")
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(manifest: dict, path: Path = MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(manifest, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))


def verify(manifest: dict, dfi_dir: Path = DFI_DIR) -> None:
    for key, rec in manifest["files"].items():
        p = dfi_dir / rec["file"]
        if not p.is_file():
            raise DfiError(f"{p} is missing; download again from {rec['url']} (expected sha256 {rec['sha256']})")
        if p.stat().st_size != rec["size_bytes"]:
            raise DfiError(f"{p}: size {p.stat().st_size} != manifest {rec['size_bytes']}")
        got = file_sha256(p)
        if got != rec["sha256"]:
            raise DfiError(f"{p}: sha256 {got} != manifest {rec['sha256']}")


# ---------------------------------------------------------------- download

def download(dfi_dir: Path = DFI_DIR, manifest_path: Path = MANIFEST_PATH, force: bool = False) -> dict:
    """Fetch both files, hash them as they arrive, write the manifest. Refuses to replace files without force."""
    import requests
    dfi_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for key, src in SOURCES.items():
        dest = dfi_dir / src["file"]
        if dest.exists() and not force:
            raise DfiError(f"{dest} exists; use --force to replace it (the manifest would change)")
        part = dest.with_name(dest.name + ".part")
        sha, size = hashlib.sha256(), 0
        with requests.get(src["url"], headers={"User-Agent": USER_AGENT}, stream=True, timeout=120) as r:
            if not r.ok:
                raise DfiError(f"{src['url']}: HTTP {r.status_code} {r.reason}")
            final_host = r.url.split("/")[2]
            last_modified = r.headers.get("Last-Modified")
            ctype = r.headers.get("Content-Type")
            with open(part, "wb") as f:
                for block in r.iter_content(1 << 20):
                    f.write(block)
                    sha.update(block)
                    size += len(block)
        part.replace(dest)
        files[key] = {
            "title": src["title"], "dataset_page": src["dataset_page"], "url": src["url"], "file": src["file"],
            "dataset_metadata_modified": src["dataset_metadata_modified"],
            "final_host": final_host, "downloaded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_modified_header": last_modified, "content_type": ctype, "size_bytes": size,
            "sha256": sha.hexdigest(), "note": src["note"],
        }
    manifest = {"version": 1, "licence": LICENCE, "attribution": ATTRIBUTION, "recipe": RECIPE, "files": files}
    save_manifest(manifest, manifest_path)
    return manifest


# ---------------------------------------------------------------- reading the files

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
ITEM_FIELDS = ["road_name", "section_code", "item", "start_m", "end_m", "width_m", "section_desc", "item_type",
               "digital_length_m", "surface", "carriageway", "division", "easting", "northing", "sheet"]
_COLUMN_FOR = {
    "Road Name": "road_name", "Section Code": "section_code", "Item": "item", "Start Metres": "start_m",
    "End Metres": "end_m", "Width": "width_m", "Section Description": "section_desc", "Item Type": "item_type",
    "Digital Length": "digital_length_m", "Surface Code": "surface", "Carriageway": "carriageway",
    "Division": "division", "Easting": "easting", "Northing": "northing",
}


def _col_letters(ref: str) -> str:
    return re.match(r"[A-Z]+", ref).group(0)


def read_xlsx_items(xlsx: Path):
    """Yield (sheet_name, {field: str}) for every data row of the Carriageway Details workbook. Stdlib only.

    Streams the sheet XML, so the 300 MB of XML never sit in memory; the shared-strings table does (about 4 MB).
    """
    with zipfile.ZipFile(xlsx) as z:
        wb = ET.parse(z.open("xl/workbook.xml")).getroot()
        rels = ET.parse(z.open("xl/_rels/workbook.xml.rels")).getroot()
        target = {r.get("Id"): r.get("Target") for r in rels}
        rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        sheets = [(s.get("name"), target[s.get(rid)]) for s in wb.find(_NS + "sheets")]
        strings: list[str] = []
        for _, el in ET.iterparse(z.open("xl/sharedStrings.xml")):
            if el.tag == _NS + "si":
                strings.append("".join(t.text or "" for t in el.iter(_NS + "t")))
                el.clear()
        for name, path in sheets:
            path = path.lstrip("/")
            if not path.startswith("xl/"):
                path = "xl/" + path
            header: dict[str, str] = {}
            for _, el in ET.iterparse(z.open(path)):
                if el.tag != _NS + "row":
                    continue
                cells = {}
                for c in el.findall(_NS + "c"):
                    v = c.find(_NS + "v")
                    if v is None or v.text is None:
                        continue
                    val = strings[int(v.text)] if c.get("t") == "s" else v.text
                    cells[_col_letters(c.get("r"))] = val
                el.clear()
                if not header:
                    header = {col: _COLUMN_FOR[t] for col, t in cells.items() if t in _COLUMN_FOR}
                    missing = set(_COLUMN_FOR.values()) - set(header.values())
                    if missing:
                        raise DfiError(f"sheet {name}: header lacks {sorted(missing)}")
                    continue
                row = {header[c]: v for c, v in cells.items() if c in header}
                row["sheet"] = name
                yield name, row


def parse(dfi_dir: Path = DFI_DIR, manifest: dict | None = None) -> dict:
    """Write items.csv (every Carriageway Details row, clean columns) and sections.csv (Highway Network)."""
    manifest = manifest or load_manifest()
    verify(manifest, dfi_dir)
    xlsx = dfi_dir / manifest["files"]["carriageway_details"]["file"]
    out_items = dfi_dir / "items.csv"
    n = bad = 0
    with open(out_items, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ITEM_FIELDS)
        w.writeheader()
        for _, row in read_xlsx_items(xlsx):
            n += 1
            try:
                float(row["width_m"]), float(row["easting"]), float(row["northing"])
            except (KeyError, ValueError):
                bad += 1
                continue
            w.writerow({k: row.get(k, "") for k in ITEM_FIELDS})
    hn = dfi_dir / manifest["files"]["highway_network"]["file"]
    out_secs = dfi_dir / "sections.csv"
    with open(hn, newline="", encoding="utf-8-sig") as f, open(out_secs, "w", newline="", encoding="utf-8") as g:
        rd = csv.DictReader(f)
        w = csv.writer(g)
        w.writerow(["section_code", "section_name", "division", "section_type", "adoption", "length_m", "class"])
        m = 0
        for r in rd:
            w.writerow([r["SECTION_CODE"], r["SECTION_NAME"], r["DIVISION_NAME"], r["SECTION_TYPE_NAME"],
                        r["ADOPTION_STATUS_NAME"], r["DIGITAL_LENGTH"], r["CLASS"]])
            m += 1
    return {"items": n - bad, "items_dropped_no_number": bad, "sections": m, "items_csv": str(out_items),
            "sections_csv": str(out_secs)}


# ---------------------------------------------------------------- names

ROAD_TYPES = {
    "RD": "ROAD", "ROAD": "ROAD", "ST": "STREET", "STREET": "STREET", "AV": "AVENUE", "AVE": "AVENUE",
    "AVENUE": "AVENUE", "DR": "DRIVE", "DRIVE": "DRIVE", "PK": "PARK", "PARK": "PARK", "GDNS": "GARDENS",
    "GDN": "GARDENS", "GARDENS": "GARDENS", "GARDEN": "GARDENS", "CT": "COURT", "CRT": "COURT", "COURT": "COURT",
    "CRES": "CRESCENT", "CR": "CRESCENT", "CRESCENT": "CRESCENT", "PL": "PLACE", "PLACE": "PLACE",
    "TER": "TERRACE", "TERR": "TERRACE", "TERRACE": "TERRACE", "GRN": "GREEN", "GREEN": "GREEN", "LN": "LANE",
    "LANE": "LANE", "WK": "WALK", "WALK": "WALK", "SQ": "SQUARE", "SQUARE": "SQUARE", "HTS": "HEIGHTS",
    "HEIGHTS": "HEIGHTS", "GR": "GROVE", "GRV": "GROVE", "GROVE": "GROVE", "CL": "CLOSE", "CLOSE": "CLOSE",
    "MNR": "MANOR", "MANOR": "MANOR", "PDE": "PARADE", "PARADE": "PARADE", "BDGE": "BRIDGE", "BRIDGE": "BRIDGE",
    "WY": "WAY", "WAY": "WAY", "VW": "VIEW", "VIEW": "VIEW", "MWS": "MEWS", "MEWS": "MEWS", "CSWY": "CAUSEWAY",
    "CAUSEWAY": "CAUSEWAY", "EMB": "EMBANKMENT", "HILL": "HILL", "RISE": "RISE", "ROW": "ROW", "WALKWAY": "WALK",
    "HWY": "HIGHWAY", "HIGHWAY": "HIGHWAY", "MWAY": "MOTORWAY", "MOTORWAY": "MOTORWAY", "EST": "ESTATE",
    "ESTATE": "ESTATE", "QUAY": "QUAY", "DALE": "DALE", "PATH": "PATH", "LK": "LINK", "LOOP": "LOOP",
    "CRSG": "CROSSING", "CIR": "CIRCLE", "CIRCLE": "CIRCLE", "BYPASS": "BYPASS", "SLIP": "SLIP",
    "GDS": "GARDENS", "CRE": "CRESCENT", "GVE": "GROVE", "TR": "TERRACE", "LINK": "LINK",
}
ABBREVIATIONS = {"NTH": "NORTH", "STH": "SOUTH", "N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST",
                 "UPR": "UPPER", "LWR": "LOWER", "MT": "MOUNT", "FT": "FORT", "GT": "GREAT", "NEW": "NEW"}
TYPE_WORDS = frozenset(ROAD_TYPES.values())
SECTION_CODE_RE = re.compile(r"^\d{4}([A-Z])(\d{4})_\d+$")


def name_tokens(name: str) -> list[str]:
    """Upper-case tokens of a street name with DfI's abbreviations spelled out: 'ST COLUMBAS DR1' -> SAINT COLUMBAS DRIVE.

    The part of a DfI section name before the colon carries a section digit glued on ('DOWNSHIRE RD1'); it is dropped,
    as is a leading section code ('7020A0002_02 BELFAST RD'). OSM names pass through the same function.
    """
    s = (name or "").upper().replace("&", " AND ")
    s = s.split(":", 1)[0]
    s = re.sub(r"^\d{4}[A-Z]\d{4}_\d+\s+", "", s)
    s = re.sub(r"[`'’‘]", "", s)
    # RD1 -> RD, MILL RD W1 -> MILL RD W; but a road number (M2, A55, B205) keeps its digits
    s = re.sub(r"\b([A-Z]+)(\d+)\b", lambda m: m.group(0) if m.group(1) in ("A", "B", "C", "M") else m.group(1), s)
    s = re.sub(r"\([^)]*\)", " ", s)                # (WEST), (CDS)
    toks = [t for t in re.split(r"[^A-Z0-9]+", s) if t]
    out = []
    for i, t in enumerate(toks):
        if t in ("ST",) and i < len(toks) - 1:
            out.append("SAINT")
        elif t in ROAD_TYPES:
            out.append(ROAD_TYPES[t])
        else:
            out.append(ABBREVIATIONS.get(t, t))
    return out


def name_relation(dfi_name: str, osm_names: list[str]) -> str:
    """'exact', 'partial', 'unknown' (one side has no name) or 'conflict'."""
    a = name_tokens(dfi_name)
    bs = [name_tokens(n) for n in osm_names if n]
    bs = [b for b in bs if b]
    if not a or not bs:
        return "unknown"
    best = "conflict"
    for b in bs:
        if a == b:
            return "exact"
        ca = [t for t in a if t not in TYPE_WORDS]
        cb = [t for t in b if t not in TYPE_WORDS]
        if ca and ca == cb:
            best = "partial"          # same name, different road-type word (Road v Street, or one has none)
        elif ca and cb and (set(ca) <= set(cb) or set(cb) <= set(ca)) and best == "conflict":
            best = "partial"          # 'Ormeau Road' v 'Upper Ormeau Road'
    return best


def ref_class(ref: str | None):
    """Set of (letter, number) pairs from an OSM ref such as 'A24' or 'B23;A55'."""
    out = set()
    for part in re.split(r"[;,/ ]+", ref or ""):
        m = re.match(r"^([ABCM])0*(\d+)$", part.strip().upper())
        if m:
            out.add((m.group(1), int(m.group(2))))
    return out


def section_ref(section_code: str):
    m = SECTION_CODE_RE.match(section_code or "")
    if m and m.group(1) in "ABCM":
        return (m.group(1), int(m.group(2)))
    return None


CLASS_RANK_DFI = {"M": 0, "A": 1, "B": 2, "C": 3, "U": 4}
CLASS_RANK_OSM = {"motorway": 0, "motorway_link": 0, "trunk": 1, "trunk_link": 1, "primary": 1, "primary_link": 1,
                  "secondary": 2, "secondary_link": 2, "tertiary": 3, "tertiary_link": 3, "unclassified": 3,
                  "residential": 4, "living_street": 4, "service": 4, "road": 3}


def osm_road_class(highway: str) -> str:
    h = (highway or "").replace("_link", "")
    return h if h in ROAD_CLASS_ORDER else "other"


# ---------------------------------------------------------------- inputs

def load_sections(path: Path) -> dict[str, dict]:
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["section_code"]] = r
    return out


def load_items(path: Path, bbox_ig: tuple[float, float, float, float] | None = None) -> list[dict]:
    """Items as dicts with numbers; with bbox_ig = (e0, n0, e1, n1) only those whose point is inside."""
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            e, n = float(r["easting"]), float(r["northing"])
            if bbox_ig and not (bbox_ig[0] <= e <= bbox_ig[2] and bbox_ig[1] <= n <= bbox_ig[3]):
                continue
            out.append({
                "item": r["item"], "section": r["section_code"], "road": r["road_name"], "desc": r["section_desc"],
                "start": float(r["start_m"] or 0), "end": float(r["end_m"] or 0), "width": float(r["width_m"]),
                "length": float(r["digital_length_m"] or 0), "surface": r["surface"], "e": e, "n": n,
            })
    return out


def load_osm_ways(cut: Path) -> dict[int, dict]:
    """Drivable highway ways of an Overpass-shaped cut: {id: {tags, geometry [(lat, lon)]}}."""
    data = json.loads(cut.read_text(encoding="utf-8"))
    ways = {}
    for el in data["elements"]:
        if el.get("type") != "way":
            continue
        tags = el.get("tags") or {}
        if tags.get("highway") not in DRIVABLE or tags.get("area") == "yes":
            continue
        geom = [(g["lat"], g["lon"]) for g in el.get("geometry", [])]
        if len(geom) < 2:
            continue
        ways[el["id"]] = {"tags": tags, "geometry": geom}
    return ways


# ---------------------------------------------------------------- stretches

def build_stretches(items: list[dict]) -> list[dict]:
    """Turn items into stretches: a straight line from an item's point to the next item's point of the same section
    where the chainage runs on (and the distance agrees with it), else a lone point.

    DfI gives each item the position where it STARTS (checked on the data: the distance between consecutive start
    points equals the chainage difference to within a few metres). All coordinates here are metres in one plane.
    `items` need keys x, y (metres) as well as the load_items keys.
    """
    by_sec: dict[str, list[dict]] = {}
    for it in items:
        by_sec.setdefault(it["section"], []).append(it)
    stretches = []
    for sec, its in by_sec.items():
        its.sort(key=lambda i: (i["start"], i["end"]))
        starts = [i["start"] for i in its]
        for k, it in enumerate(its):
            nxt = None
            # the next item that picks up where this one ends (within 3 m of chainage) and lies a plausible distance away
            for j in range(k + 1, min(k + 6, len(its))):
                o = its[j]
                gap = o["start"] - it["end"]
                if gap > 3.0:
                    break
                if gap < -3.0 or o["start"] <= it["start"]:
                    continue
                d = math.hypot(o["x"] - it["x"], o["y"] - it["y"])
                if d <= (o["start"] - it["start"]) + 4.0 and d >= 0.5:
                    nxt = o
                    break
            stretches.append({"item": it, "end_xy": (nxt["x"], nxt["y"]) if nxt else None})
    return stretches


def sample_stretch(st: dict, spacing_m: float = 8.0, point_cap_m: float = 10.0):
    """[(x, y, weight_m, is_start)] along one stretch: the start point, then one sample every `spacing_m`."""
    it = st["item"]
    length = max(it["length"], MIN_WEIGHT_M)
    if st["end_xy"] is None:
        return [(it["x"], it["y"], min(length, point_cap_m), True)]
    x0, y0 = it["x"], it["y"]
    x1, y1 = st["end_xy"]
    chord = math.hypot(x1 - x0, y1 - y0)
    n = max(1, math.ceil(chord / spacing_m))
    w = length / n
    return [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n, w, k == 0) for k in range(n)]


# ---------------------------------------------------------------- matching

def weighted_quantile(values, weights, q: float) -> float:
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    if total <= 0:
        return float("nan")
    target = q * total
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= target - 1e-9:
            return v
    return pairs[-1][0]


NAME_FACTOR = {"exact": 1.0, "partial": 0.85, "ref": 0.85, "unknown": 0.55}
# When several ways are in reach the cheapest wins: distance plus this, so a way whose name or road number agrees
# beats a nearer way that says nothing, but not a much nearer way that also agrees a little.
NAME_PENALTY_M = {"exact": 0.0, "ref": 0.0, "partial": 4.0, "unknown": 10.0}


def confidence(offset_m: float, name_factor: float, coverage: float, spread_m: float, class_gap: int) -> dict:
    """Confidence in 0..1 that `width_m` is the running width of this OSM way. Product of five parts:

    geometry  1 while the median offset between DfI's own points (not the chord samples) and the way is within 4 m
              (datum shift, whole-metre coordinates and drawing differences live here), falling to 0.3 at the
              snap radius.
    name      1 for the same street name, 0.85 for a near match or the same road number, 0.55 where one side
              has no name (position and class are all that agree), already 0 for a conflicting name (not matched).
    coverage  0.4 + 0.6 x the fraction of the way's length that DfI stretches cover (a width known for 10 per cent
              of a way is a weaker claim than one known for all of it).
    spread    1 down to 0.5 as the inter-quartile range of the widths on the way grows to 2 m or more
              (a way that DfI measures as 7 m in one place and 12 m in another has no single width).
    class     0.8 when DfI class (M A B C U) and OSM highway differ by two ranks or more.
    """
    geo = 1.0 if offset_m <= 4.0 else max(0.3, 1.0 - 0.7 * (offset_m - 4.0) / (SNAP_M - 4.0))
    cov = 0.4 + 0.6 * max(0.0, min(1.0, coverage))
    spr = 1.0 - 0.5 * min(1.0, max(0.0, spread_m) / 2.0)
    cls = 0.8 if class_gap >= 2 else 1.0
    score = geo * name_factor * cov * spr * cls
    return {"score": round(score, 3), "geometry": round(geo, 3), "name": round(name_factor, 3),
            "coverage": round(cov, 3), "spread": round(spr, 3), "class": cls}


def tier(score: float) -> str:
    return "high" if score >= 0.70 else "medium" if score >= 0.40 else "low"


def match_area(items: list[dict], sections: dict[str, dict], ways: dict[int, dict]) -> dict:
    """Match DfI items to OSM ways. Items carry x, y and ways carry xy (a list of (x, y)), all metres in one plane;
    run_match makes them.

    Returns {"ways": {id: record}, "stats": {...}, "offset": {...}}.
    """
    import numpy as np
    import shapely
    from shapely import STRtree

    way_ids = list(ways)
    lines = np.array([shapely.LineString(ways[i]["xy"]) for i in way_ids], dtype=object)
    tree = STRtree(lines)
    way_names = {i: [ways[i]["tags"].get(k) for k in ("name", "alt_name", "official_name", "old_name")] for i in way_ids}
    way_refs = {i: ref_class(ways[i]["tags"].get("ref")) for i in way_ids}
    way_rank = {i: CLASS_RANK_OSM.get(ways[i]["tags"].get("highway"), 3) for i in way_ids}

    usable = [it for it in items if MIN_WIDTH_M <= it["width"] <= JUNCTION_WIDTH_M]
    stretches = build_stretches(items)           # geometry from all items, so a junction item still ends its neighbour
    samples = []                                  # (x, y, weight, is_start, item)
    for st in stretches:
        it = st["item"]
        if not (MIN_WIDTH_M <= it["width"] <= JUNCTION_WIDTH_M):
            continue
        for x, y, w, is_start in sample_stretch(st):
            samples.append((x, y, w, is_start, it))
    stats = {"items": len(items), "items_used": len(usable), "items_ignored_width": len(items) - len(usable),
             "stretches_with_line": sum(1 for s in stretches if s["end_xy"] is not None), "samples": len(samples)}
    acc: dict[int, dict] = {i: {"w": [], "wt": [], "off": [], "off_pt": [], "namef": [], "sections": {}, "items": set(),
                                 "rels": {}, "gap": []} for i in way_ids}
    stats.update({"samples_unmatched_no_way": 0, "samples_unmatched_name": 0, "samples_matched": 0,
                  "weight_total_m": 0.0, "weight_matched_m": 0.0})
    start_off = []
    if samples:
        pts = shapely.points(np.array([[s[0], s[1]] for s in samples]))
        pi, li = tree.query(pts, predicate="dwithin", distance=NAME_SNAP_M)
        dist = shapely.distance(pts[pi], lines[li])
        per_sample: dict[int, list] = {}
        for a, b, d in zip(pi.tolist(), li.tolist(), dist.tolist()):
            per_sample.setdefault(a, []).append((b, d))
        for si, (x, y, w, is_start, it) in enumerate(samples):
            stats["weight_total_m"] += w
            sec = sections.get(it["section"], {})
            dcls = sec.get("class") or ""
            sref = section_ref(it["section"])
            best = None
            n_conflict = 0
            for b, d in per_sample.get(si, []):
                wid = way_ids[b]
                rel = name_relation(it["desc"], way_names[wid])
                if rel in ("unknown", "conflict") and sref and sref in way_refs[wid]:
                    rel = "ref"
                if rel == "conflict":
                    n_conflict += 1
                    continue
                if d > (NAME_SNAP_M if rel in ("exact", "ref") else SNAP_M):
                    continue            # only an exact name or the same road number is trusted out to NAME_SNAP_M
                cost = d + NAME_PENALTY_M[rel]
                if best is None or cost < best[0]:
                    best = (cost, wid, rel, d)
            if best is None:
                stats["samples_unmatched_name" if n_conflict else "samples_unmatched_no_way"] += 1
                continue
            _, wid, rel, d = best
            a = acc[wid]
            a["w"].append(it["width"]); a["wt"].append(w); a["off"].append(d)
            if is_start:
                a["off_pt"].append(d)
            a["namef"].append(NAME_FACTOR[rel]); a["items"].add(it["item"])
            a["sections"][it["section"]] = a["sections"].get(it["section"], 0.0) + w
            a["rels"][rel] = a["rels"].get(rel, 0.0) + w
            if dcls in CLASS_RANK_DFI:
                a["gap"].append(abs(CLASS_RANK_DFI[dcls] - way_rank[wid]))
            stats["samples_matched"] += 1
            stats["weight_matched_m"] += w
            if is_start:
                start_off.append((wid, x, y))
    # offset vector of true DfI points against the nearest point of their way
    idx = {wid: k for k, wid in enumerate(way_ids)}
    vec = []
    for wid, x, y in start_off:
        line = lines[idx[wid]]
        p = shapely.Point(x, y)
        q = line.interpolate(line.project(p))
        vec.append((x - q.x, y - q.y))
    offset = {"n": len(vec)}
    if vec:
        arr = np.array(vec)
        offset.update({"median_dx_m": float(np.median(arr[:, 0])), "median_dy_m": float(np.median(arr[:, 1])),
                       "median_distance_m": float(np.median(np.hypot(arr[:, 0], arr[:, 1])))})
    out = {}
    for wid in way_ids:
        a = acc[wid]
        if not a["w"]:
            continue
        wt = a["wt"]
        total_w = sum(wt)
        width = weighted_quantile(a["w"], wt, 0.5)
        q1, q3 = weighted_quantile(a["w"], wt, 0.25), weighted_quantile(a["w"], wt, 0.75)
        # Offset for the confidence: DfI's own points where the way has any (a chord between two points cuts corners,
        # so chord samples sit further from a curving way than the data does), else the chord samples.
        pts = a["off_pt"]
        off = weighted_quantile(pts, [1.0] * len(pts), 0.5) if pts else weighted_quantile(a["off"], wt, 0.5)
        namef = sum(f * w for f, w in zip(a["namef"], wt)) / total_w
        way_len = float(shapely.length(lines[idx[wid]]))
        cov = min(1.0, total_w / way_len) if way_len > 0 else 0.0
        gap = max(a["gap"]) if a["gap"] else 0
        conf = confidence(off, namef, cov, q3 - q1, gap)
        sec_top = max(a["sections"], key=a["sections"].get)
        out[wid] = {
            "width_m": round(width, 2), "width_q1_m": round(q1, 2), "width_q3_m": round(q3, 2),
            "confidence": conf["score"], "tier": tier(conf["score"]), "parts": conf,
            "offset_median_m": round(off, 2), "covered_m": round(total_w, 1), "way_length_m": round(way_len, 1),
            "coverage": round(cov, 3), "n_items": len(a["items"]), "samples": len(a["w"]),
            "sections": {k: round(v, 1) for k, v in sorted(a["sections"].items(), key=lambda kv: -kv[1])},
            "section_main": sec_top, "name_relation": max(a["rels"], key=a["rels"].get),
        }
    return {"ways": out, "stats": stats, "offset": offset}


# ---------------------------------------------------------------- one square, end to end

def _in_bounds(lat: float, lon: float, b) -> bool:
    return b.south <= lat <= b.north and b.west <= lon <= b.east


def run_match(area_name: str, *, dfi_dir: Path = DFI_DIR, cut_dir: Path = CUT_DIR, check_hash: bool = True) -> dict:
    """Match the DfI items of one square to its pinned OSM cut and write widths_<area>.json under dfi_dir."""
    import aoi
    import numpy as np

    cfg = aoi.load()
    area = cfg.area(area_name)
    b = area.computed_bounds()
    cut = cut_dir / f"{area_name}.overpass.json"
    osm_manifest = json.loads(OSM_MANIFEST_PATH.read_text(encoding="utf-8"))
    if check_hash:
        cut_sha = file_sha256(cut)
        if cut_sha != osm_manifest["cuts"][area_name]["sha256"]:
            raise DfiError(f"{cut}: sha256 {cut_sha} differs from data/sources/osm.json; recut it (pipeline/osm_pin.py cut)")
    manifest = load_manifest()
    verify(manifest, dfi_dir)
    items_csv, secs_csv = dfi_dir / "items.csv", dfi_dir / "sections.csv"
    if not items_csv.is_file():
        raise DfiError(f"{items_csv} is missing; run `dfi.py parse`")

    # Irish Grid box around the square (+250 m so stretches that start just outside still reach in)
    corners_e, corners_n = cfg.latlon_to_crs("irish_grid", np.array([b.south, b.south, b.north, b.north]),
                                             np.array([b.west, b.east, b.west, b.east]))
    pad = 250.0
    bbox = (float(corners_e.min()) - pad, float(corners_n.min()) - pad,
            float(corners_e.max()) + pad, float(corners_n.max()) + pad)
    items = load_items(items_csv, bbox)
    sections = load_sections(secs_csv)
    # DfI Irish Grid -> ITM, the plane everything is matched in (OSM lat/lon go to ITM too)
    xs, ys = cfg.convert("irish_grid", "itm", np.array([i["e"] for i in items]), np.array([i["n"] for i in items]))
    for it, x, y in zip(items, xs, ys):
        it["x"], it["y"] = float(x), float(y)
    ways = load_osm_ways(cut)
    for wid, w in ways.items():
        lat = np.array([p[0] for p in w["geometry"]])
        lon = np.array([p[1] for p in w["geometry"]])
        ex, ny = cfg.latlon_to_crs("itm", lat, lon)
        w["xy"] = list(zip(ex.tolist(), ny.tolist()))
        w["in_area"] = bool(np.any((lat >= b.south) & (lat <= b.north) & (lon >= b.west) & (lon <= b.east)))
    result = match_area(items, sections, ways)

    way_info = {}
    for wid, w in ways.items():
        t = w["tags"]
        xy = np.array(w["xy"])
        length = float(np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1])).sum())
        way_info[wid] = {"highway": t.get("highway"), "name": t.get("name"), "ref": t.get("ref"),
                         "width_tag": t.get("width"), "lanes": t.get("lanes"), "oneway": t.get("oneway"),
                         "length_m": round(length, 1), "in_area": w["in_area"]}
    cov = coverage_report(result, way_info, items, sections)
    out = {
        "version": 1, "recipe": RECIPE, "area": area_name,
        "inputs": {"dfi": {k: v["sha256"] for k, v in manifest["files"].items()},
                   "osm_cut_sha256": osm_manifest["cuts"][area_name]["sha256"],
                   "osm_extract_sha256": osm_manifest["sha256"]},
        "settings": {"snap_m": SNAP_M, "name_snap_m": NAME_SNAP_M, "min_width_m": MIN_WIDTH_M,
                     "junction_width_m": JUNCTION_WIDTH_M,
                     "crs": "DfI Irish Grid EPSG:29903 via the pinned 7-parameter shift"},
        "coverage": cov,
        "ways": {str(w): {**way_info[w], **r} for w, r in sorted(result["ways"].items())},
        "unmatched_in_area": [{"id": w, **i} for w, i in sorted(way_info.items())
                              if i["in_area"] and w not in result["ways"]],
    }
    path = dfi_dir / f"widths_{area_name}.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    out["_path"] = str(path)
    return out


def coverage_report(result: dict, way_info: dict, items: list[dict], sections: dict) -> dict:
    """Numbers for docs/dfi_widths.md: how much of the square's road network got a width, how sure, how it compares
    with the width tags OSM already has."""
    matched = result["ways"]
    keys = ("ways", "km", "matched_ways", "matched_km", "high_km", "medium_km", "low_km")
    by_class: dict[str, dict] = {}
    for wid, i in way_info.items():
        if not i["in_area"]:
            continue
        c = by_class.setdefault(osm_road_class(i["highway"]), {k: 0 for k in keys})
        c["ways"] += 1
        c["km"] += i["length_m"] / 1000
        r = matched.get(wid)
        if r:
            c["matched_ways"] += 1
            c["matched_km"] += i["length_m"] / 1000
            c[r["tier"] + "_km"] += i["length_m"] / 1000
    total = {k: sum(c[k] for c in by_class.values()) for k in keys}
    for c in list(by_class.values()) + [total]:
        for k in ("km", "matched_km", "high_km", "medium_km", "low_km"):
            c[k] = round(c[k], 2)
        c["matched_share_of_km"] = round(c["matched_km"] / c["km"], 3) if c["km"] else None
    # against OSM's own width tags
    cmp_ = []
    for wid, r in matched.items():
        i = way_info[wid]
        if not i["in_area"] or not i["width_tag"]:
            continue
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(m)?\s*$", i["width_tag"])
        if m:
            cmp_.append((float(m.group(1)), r["width_m"]))
    osm_cmp = {"matched_ways_with_osm_width_tag": len(cmp_)}
    if cmp_:
        d = sorted(abs(a - b) for a, b in cmp_)
        osm_cmp.update({"median_abs_diff_m": round(d[len(d) // 2], 2), "within_0_75_m": sum(1 for x in d if x <= 0.75),
                        "tag_over_dfi_by_more_than_0_75": sum(1 for a, b in cmp_ if a > b + 0.75),
                        "tag_under_dfi_by_more_than_0_75": sum(1 for a, b in cmp_ if a < b - 0.75)})
    rel_km: dict[str, float] = {}
    offsets, widths_by_class = [], {}
    for wid, r in matched.items():
        i = way_info[wid]
        if not i["in_area"]:
            continue
        rel_km[r["name_relation"]] = rel_km.get(r["name_relation"], 0.0) + i["length_m"] / 1000
        offsets.append(r["offset_median_m"])
        widths_by_class.setdefault(osm_road_class(i["highway"]), []).append(r["width_m"])
    offsets.sort()
    q = (lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None)
    stats = dict(result["stats"])
    wt, wm = stats["weight_total_m"], stats["weight_matched_m"]
    stats["weight_total_m"], stats["weight_matched_m"] = round(wt), round(wm)
    stats["matched_share_of_dfi_length"] = round(wm / wt, 3) if wt else None
    secs = {i["section"] for i in items}
    return {
        "dfi": stats, "dfi_sections_in_box": len(secs),
        "dfi_sections_found_in_highway_network": sum(1 for s in secs if s in sections),
        "dfi_sections_adoption": _count(sections[s]["adoption"] for s in secs if s in sections),
        "osm_in_area": total,
        "osm_by_class": dict(sorted(by_class.items(), key=lambda kv: ROAD_CLASS_ORDER.index(kv[0]))),
        "osm_ways_with_width_tag_in_area": sum(1 for i in way_info.values() if i["in_area"] and i["width_tag"]),
        "vs_osm_width_tag": osm_cmp, "point_offset_vs_osm": result["offset"],
        "matched_km_by_name_relation": {k: round(v, 2) for k, v in sorted(rel_km.items())},
        "way_offset_median_quantiles_m": {"p50": q(offsets, 0.5), "p90": q(offsets, 0.9), "p99": q(offsets, 0.99)},
        "width_median_by_class_m": {c: {"ways": len(v), "p10": q(sorted(v), 0.1), "p50": q(sorted(v), 0.5),
                                        "p90": q(sorted(v), 0.9)}
                                    for c, v in sorted(widths_by_class.items(), key=lambda kv: ROAD_CLASS_ORDER.index(kv[0]))},
    }


def _count(it) -> dict:
    d: dict = {}
    for x in it:
        d[x] = d.get(x, 0) + 1
    return dict(sorted(d.items()))


# ---------------------------------------------------------------- sample for a hand check

# how many ways of each class to take; leftovers are filled from the longest ways
SAMPLE_QUOTA = {"motorway": 2, "trunk": 2, "primary": 4, "secondary": 4, "tertiary": 3, "unclassified": 3,
                "residential": 8, "living_street": 1, "service": 3}


def pick_sample(widths: dict, n: int = 30) -> list[dict]:
    """A deterministic spread: per OSM class a quota, in each class evenly spaced through the list ordered by
    confidence so high, medium and low confidence all appear."""
    by_class: dict[str, list[dict]] = {}
    for wid, r in widths["ways"].items():
        if r["in_area"]:
            by_class.setdefault(osm_road_class(r["highway"]), []).append({"id": int(wid), **r})
    chosen: list[dict] = []
    for cls, quota in SAMPLE_QUOTA.items():
        pool = sorted(by_class.get(cls, []), key=lambda r: (r["confidence"], r["id"]))
        if not pool:
            continue
        k = min(quota, len(pool))
        idx = sorted({round(j * (len(pool) - 1) / max(k - 1, 1)) for j in range(k)})
        chosen += [pool[j] for j in idx]
    ids = {r["id"] for r in chosen}
    rest = sorted((r for pool in by_class.values() for r in pool if r["id"] not in ids),
                  key=lambda r: (-r["length_m"], r["id"]))
    while len(chosen) < n and rest:
        chosen.append(rest.pop(0))
    return chosen[:n]


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("download", help="download both files and write data/sources/dfi.json")
    s.add_argument("--force", action="store_true")
    sub.add_parser("verify", help="check the files on disk against the manifest")
    sub.add_parser("parse", help="xlsx and csv -> items.csv and sections.csv under H:\\BelfastData\\dfi")
    s = sub.add_parser("match", help="match to OSM and write widths_<area>.json")
    s.add_argument("--area", action="append", help="area from config/aoi.yaml (repeatable; default all)")
    s = sub.add_parser("sample", help="print a spread of matched ways for a hand check")
    s.add_argument("--area", default="slice")
    s.add_argument("-n", type=int, default=30)
    args = p.parse_args(argv)
    try:
        if args.cmd == "download":
            m = download(force=args.force)
            for k, v in m["files"].items():
                print(f"{k}: {v['size_bytes']} bytes sha256 {v['sha256']} ({v['last_modified_header']})")
        elif args.cmd == "verify":
            verify(load_manifest())
            print("ok: both files match data/sources/dfi.json")
        elif args.cmd == "parse":
            print(json.dumps(parse(), indent=1))
        elif args.cmd == "match":
            import aoi
            for name in args.area or list(aoi.load().areas):
                out = run_match(name)
                t = out["coverage"]["osm_in_area"]
                print(f"{name}: {t['matched_ways']}/{t['ways']} ways, {t['matched_km']}/{t['km']} km matched "
                      f"(high {t['high_km']}, medium {t['medium_km']}, low {t['low_km']} km) -> {out['_path']}")
        elif args.cmd == "sample":
            path = DFI_DIR / f"widths_{args.area}.json"
            widths = json.loads(path.read_text(encoding="utf-8"))
            print(json.dumps(pick_sample(widths, args.n), indent=1, ensure_ascii=False))
    except DfiError as exc:
        print(f"dfi: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
