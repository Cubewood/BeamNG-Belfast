"""Pinned OSM (T-202): one Geofabrik extract, hashed, cut per map square, no Overpass in the build.

    uv run --no-project --python 3.12 --with osmium --with pyproj --with numpy --with pyyaml --with requests \\
        python pipeline/osm_pin.py download            # fetch the extract, write data/sources/osm.json
    ... python pipeline/osm_pin.py verify              # the file on disk matches the manifest
    ... python pipeline/osm_pin.py cut [--area slice]  # cut each square, record the cut hashes in the manifest
    ... python pipeline/osm_pin.py cut --check         # recut and compare with the manifest (nothing written to it)

What a cut is: the elements OpenStreetMap holds inside a square's bounds plus a margin, in the shape
Overpass returns for `out body geom` (ways and relation members carry their own geometry). MapNG's own
`parseOverpassResponse` reads that shape, so `tools/mapng_osm_parse.mjs` turns a cut into the `osm.json`
the export wants, with MapNG's code and no network. The selection copies MapNG's Overpass query
(`services/overpassQuery.js` OSM_FILTER at the pinned commit; tests/pipeline/test_osm_pin.py compares the two).

A cut is deterministic: same extract, same bounds, same recipe, same bytes. Elements are sorted by type
then id, keys are in a fixed order, coordinates are rounded to 7 decimals (OSM's own precision) and the
file is UTF-8 with LF line ends. Raw data stays under H:\\BelfastData\\osm and never goes in git; the
manifest (data/sources/osm.json) holds URL, date, size, SHA-256 and the cut hashes.

Data: (c) OpenStreetMap contributors, ODbL 1.0. Extract by Geofabrik GmbH.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA_DIR = Path(r"H:\BelfastData")
OSM_DIR = DATA_DIR / "osm"
CUT_DIR = OSM_DIR / "cuts"
MANIFEST_PATH = ROOT / "data" / "sources" / "osm.json"

GEOFABRIK_URL = "https://download.geofabrik.de/europe/ireland-and-northern-ireland-latest.osm.pbf"
USER_AGENT = "BeamNGBelfast-osm-pin/1.0 (one download of a public extract; see docs/osm_pin.md)"
CUT_RECIPE = 1          # bump when the selection or the output shape changes (the MapNG-facing cut only)
CROSSINGS_RECIPE = 1    # same for the side file of crossing nodes (not for MapNG; see crossing_matches)
CUT_MARGIN_M = 100.0    # ground metres added to every side of a square's bounds
METRES_PER_DEGREE = 111_320.0

# MapNG's Overpass filter, as at the pinned commit (services/overpassQuery.js). Same keys, same order.
GATE_BARRIERS = [
    "gate", "swing_gate", "lift_gate", "sliding_gate", "hampshire_gate", "wicket_gate", "kissing_gate", "bump_gate",
    "stile", "entrance", "cattle_grid", "turnstile", "full-height_turnstile",
]
BUSINESS_NODES = (
    [("amenity", v) for v in ("restaurant", "cafe", "bar", "pub", "fast_food", "biergarten", "nightclub", "ice_cream")]
    + [("tourism", v) for v in ("hotel", "motel", "guest_house", "hostel")]
)
WAY_KEYS = [
    "highway", "building", "waterway", "barrier", "aeroway", "man_made", "public_transport",
    "power", "military", "historic", "tourism", "amenity", "landuse", "natural", "leisure",
    "water", "wetland", "surface", "landcover", "golf",
]
WAY_TAGS = [("area", "yes"), ("natural", "tree_row")]
RELATION_KEYS = [
    "building", "waterway", "aeroway", "man_made", "military", "historic", "tourism", "amenity",
    "landuse", "natural", "leisure", "water", "wetland", "landcover",
]
NODE_TAGS = (
    [("natural", "tree"), ("natural", "tree_row"), ("natural", "shrub"),
     ("amenity", "fuel"), ("amenity", "charging_station"), ("man_made", "fuel_pump"),
     ("highway", "traffic_signals"), ("highway", "stop"), ("highway", "give_way"),
     ("highway", "street_lamp"), ("barrier", "bollard"), ("amenity", "bench"),
     ("emergency", "fire_hydrant"), ("amenity", "waste_basket"), ("power", "pole"),
     ("traffic_calming", "bump"), ("traffic_calming", "hump"), ("traffic_calming", "table"),
     ("traffic_calming", "cushion"),
     ("amenity", "fountain"), ("amenity", "drinking_water")]
    + [("barrier", v) for v in GATE_BARRIERS]
    + BUSINESS_NODES
)
_WAY_KEY_SET = frozenset(WAY_KEYS)
_WAY_TAG_SET = frozenset(WAY_TAGS)
_REL_KEY_SET = frozenset(RELATION_KEYS)
_NODE_TAG_SET = frozenset(NODE_TAGS)
NODE_KEYS = sorted({k for k, _ in NODE_TAGS})

# Crossing nodes are NOT in MapNG's Overpass filter, so they are not in the MapNG-facing cut (adding them to NODE_TAGS
# would change what MapNG receives and break the equality test). They go to a side file per square for our own steps.
CROSSING_KEYS = ["highway", "crossing"]
_NODE_FILTER_KEYS = sorted(set(NODE_KEYS) | set(CROSSING_KEYS))


class PinError(Exception):
    pass


# ---------------------------------------------------------------- hashing and manifest

def file_digests(path: Path, algos=("sha256",)) -> dict[str, str]:
    hs = {a: hashlib.new(a) for a in algos}
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            for h in hs.values():
                h.update(block)
    return {a: h.hexdigest() for a, h in hs.items()}


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    if not path.is_file():
        raise PinError(f"{path} does not exist; run `osm_pin.py download` first")
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(manifest: dict, path: Path = MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(manifest, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))


def pbf_path(manifest: dict, osm_dir: Path = OSM_DIR) -> Path:
    return osm_dir / manifest["file"]


def verify_pbf(manifest: dict, osm_dir: Path = OSM_DIR) -> Path:
    """The extract on disk, after checking size and SHA-256 against the manifest."""
    p = pbf_path(manifest, osm_dir)
    if not p.is_file():
        raise PinError(f"{p} is missing; download it again from {manifest['url']} (expected sha256 {manifest['sha256']})")
    if p.stat().st_size != manifest["size_bytes"]:
        raise PinError(f"{p}: size {p.stat().st_size} != manifest {manifest['size_bytes']}")
    got = file_digests(p)["sha256"]
    if got != manifest["sha256"]:
        raise PinError(f"{p}: sha256 {got} != manifest {manifest['sha256']}")
    return p


# ---------------------------------------------------------------- download

def read_pbf_header(path: Path) -> dict:
    import osmium
    reader = osmium.io.Reader(str(path))
    try:
        h = reader.header()
        return {
            "data_timestamp_utc": h.get("osmosis_replication_timestamp") or None,
            "replication_sequence": h.get("osmosis_replication_sequence_number") or None,
            "generator": h.get("generator") or None,
            "box": h.box().__str__() if h.box().valid() else None,
        }
    finally:
        reader.close()


def download(url: str = GEOFABRIK_URL, osm_dir: Path = OSM_DIR, manifest_path: Path = MANIFEST_PATH,
             force: bool = False) -> dict:
    """Download the extract to osm_dir and write the manifest. Streams, hashes as it goes, renames at the end."""
    import requests
    osm_dir.mkdir(parents=True, exist_ok=True)
    dest = osm_dir / url.rsplit("/", 1)[-1]
    if dest.exists() and not force:
        raise PinError(f"{dest} exists; use --force to replace it (the manifest would change)")
    part = dest.with_name(dest.name + ".part")
    headers = {"User-Agent": USER_AGENT}
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        last_modified = r.headers.get("Last-Modified")
        with open(part, "wb") as f:
            for block in r.iter_content(1 << 20):
                f.write(block)
                sha.update(block)
                md5.update(block)
                size += len(block)
    published_md5 = None
    try:
        m = requests.get(url + ".md5", headers=headers, timeout=60)
        if m.ok:
            published_md5 = m.text.split()[0].lower()
    except requests.RequestException:
        pass
    if published_md5 and published_md5 != md5.hexdigest():
        part.unlink(missing_ok=True)
        raise PinError(f"md5 {md5.hexdigest()} differs from Geofabrik's {published_md5}; download discarded")
    os.replace(part, dest)
    header = read_pbf_header(dest)
    dated_url = None
    if header["data_timestamp_utc"]:
        stamp = header["data_timestamp_utc"][2:10].replace("-", "")
        candidate = url.replace("-latest.", f"-{stamp}.")
        try:
            if requests.head(candidate, headers=headers, timeout=30, allow_redirects=True).ok:
                dated_url = candidate
        except requests.RequestException:
            pass
    manifest = {
        "version": 1,
        "source": "Geofabrik GmbH extract of OpenStreetMap: Ireland and Northern Ireland",
        "licence": "ODbL 1.0, (c) OpenStreetMap contributors (https://www.openstreetmap.org/copyright)",
        "url": url,
        "dated_url": dated_url,
        "file": dest.name,
        "downloaded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_modified_header": last_modified,
        "size_bytes": size,
        "sha256": sha.hexdigest(),
        "md5": md5.hexdigest(),
        "md5_matches_geofabrik": bool(published_md5),
        "pbf_header": header,
        "cut_recipe": CUT_RECIPE,
        "cut_margin_m": CUT_MARGIN_M,
        "cuts": {},
    }
    save_manifest(manifest, manifest_path)
    return manifest


# ---------------------------------------------------------------- the cut

def cut_bbox(bounds, margin_m: float = CUT_MARGIN_M) -> dict:
    """A square's bounds widened by margin_m ground metres on every side, rounded to 7 decimals."""
    b = {"south": bounds.south, "west": bounds.west, "north": bounds.north, "east": bounds.east}
    dlat = margin_m / METRES_PER_DEGREE
    dlon = margin_m / (METRES_PER_DEGREE * math.cos(math.radians((b["south"] + b["north"]) / 2)))
    return {
        "south": round(b["south"] - dlat, 7), "west": round(b["west"] - dlon, 7),
        "north": round(b["north"] + dlat, 7), "east": round(b["east"] + dlon, 7),
    }


def node_matches(tags: dict) -> bool:
    return any((k, v) in _NODE_TAG_SET for k, v in tags.items())


def crossing_matches(tags: dict) -> bool:
    """A crossing node: highway=crossing, or any crossing=* tag (OSM's own tagging of a crossing; includes
    crossing=traffic_signals on a node without highway=traffic_signals). Side file only, never MapNG's cut."""
    return tags.get("highway") == "crossing" or "crossing" in tags


def way_matches(tags: dict) -> bool:
    return any(k in _WAY_KEY_SET or (k, v) in _WAY_TAG_SET for k, v in tags.items())


def relation_matches(tags: dict) -> bool:
    return any(k in _REL_KEY_SET for k in tags)


def _inside(lat: float, lon: float, b: dict) -> bool:
    return b["south"] <= lat <= b["north"] and b["west"] <= lon <= b["east"]


def _segments_touch(coords: list[tuple[float, float]], b: dict) -> bool:
    """True when the polyline has a vertex in the box or its extent overlaps the box (a way crossing it)."""
    if not coords:
        return False
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    return not (max(lats) < b["south"] or min(lats) > b["north"] or max(lons) < b["west"] or min(lons) > b["east"])


def _tags(obj) -> dict:
    return {t.k: t.v for t in obj.tags}


def _way_coords(way) -> list[tuple[float, float]]:
    out = []
    for n in way.nodes:
        loc = n.location
        if loc.valid():
            out.append((round(loc.lat, 7), round(loc.lon, 7)))
    return out


def cut_many(pbf: Path, boxes: dict[str, dict],
             crossings_out: dict[str, list[dict]] | None = None) -> dict[str, list[dict]]:
    """Elements inside each box, sorted by type then id. One read of the file serves every box.

    crossings_out, when given, is filled with {box: crossing nodes inside it, sorted by id} (crossing_matches). It does
    not alter the returned MapNG-facing elements.

    Selection (Overpass semantics of MapNG's query, `out body geom`):
      nodes      tagged as in NODE_TAGS and inside the box;
      ways       tagged as in WAY_KEYS / WAY_TAGS whose extent overlaps the box (a way crossing the box with no
                 node in it is kept; Overpass keeps it too), whole, with the position of every node;
      relations  keyed as in RELATION_KEYS with at least one member way chosen above, with every member way's
                 geometry (read in a third pass for members outside the box).
    Relations whose only link to the box is a node member are not selected (MapNG's parser ignores node members).
    """
    import osmium

    names = sorted(boxes)
    nodes: dict[str, dict[int, dict]] = {n: {} for n in names}
    ways: dict[str, dict[int, dict]] = {n: {} for n in names}

    crossings: dict[str, dict[int, dict]] = {n: {} for n in names}

    # pass 1a: tagged nodes (no locations needed, filtered in C++ on the keys we care about)
    fp = osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(osmium.filter.KeyFilter(*_NODE_FILTER_KEYS))
    for o in fp:
        tags = _tags(o)
        want_node = node_matches(tags)
        want_crossing = crossings_out is not None and crossing_matches(tags)
        if not (want_node or want_crossing) or not o.location.valid():
            continue
        lat, lon = round(o.location.lat, 7), round(o.location.lon, 7)
        for n in names:
            if _inside(lat, lon, boxes[n]):
                el = {"type": "node", "id": o.id, "lat": lat, "lon": lon, "tags": tags}
                if want_node:
                    nodes[n][o.id] = el
                if want_crossing:
                    crossings[n][o.id] = el

    # pass 1b: tagged ways, with node locations

    class WayHandler(osmium.SimpleHandler):
        def way(self, w):
            tags = _tags(w)
            if not way_matches(tags):
                return
            coords = _way_coords(w)
            for n in names:
                if _segments_touch(coords, boxes[n]):
                    ways[n][w.id] = {"type": "way", "id": w.id,
                                     "geometry": [{"lat": a, "lon": o} for a, o in coords], "tags": tags}

    WayHandler().apply_file(str(pbf), locations=True)

    # pass 2: relations that use a selected way
    rels: dict[str, dict[int, dict]] = {n: {} for n in names}
    wanted: dict[str, set[int]] = {n: set() for n in names}
    fp = osmium.FileProcessor(str(pbf), osmium.osm.RELATION).with_filter(osmium.filter.KeyFilter(*RELATION_KEYS))
    for r in fp:
        tags = _tags(r)
        if not relation_matches(tags):
            continue
        members = [{"type": {"n": "node", "w": "way", "r": "relation"}[m.type], "ref": m.ref, "role": m.role}
                   for m in r.members]
        for n in names:
            if any(m["type"] == "way" and m["ref"] in ways[n] for m in members):
                rels[n][r.id] = {"type": "relation", "id": r.id, "members": [dict(m) for m in members], "tags": tags}
                wanted[n].update(m["ref"] for m in members if m["type"] == "way" and m["ref"] not in ways[n])

    # pass 3: geometry of member ways that were not selected themselves
    need = set().union(*wanted.values()) if wanted else set()
    member_geom: dict[int, list[dict]] = {}
    if need:
        class MemberHandler(osmium.SimpleHandler):
            def way(self, w):
                if w.id in need:
                    member_geom[w.id] = [{"lat": a, "lon": o} for a, o in _way_coords(w)]

        MemberHandler().apply_file(str(pbf), locations=True)

    if crossings_out is not None:
        for n in names:
            crossings_out[n] = [crossings[n][i] for i in sorted(crossings[n])]
    result = {}
    for n in names:
        for rel in rels[n].values():
            for m in rel["members"]:
                if m["type"] != "way":
                    continue
                geom = ways[n][m["ref"]]["geometry"] if m["ref"] in ways[n] else member_geom.get(m["ref"])
                if geom:
                    m["geometry"] = geom
        result[n] = ([nodes[n][i] for i in sorted(nodes[n])] + [ways[n][i] for i in sorted(ways[n])]
                     + [rels[n][i] for i in sorted(rels[n])])
    return result


def cut_bytes(elements: list[dict]) -> bytes:
    """The cut as bytes: {"elements": [...]} compact JSON, one element per line, UTF-8, LF."""
    lines = [json.dumps(e, separators=(",", ":"), ensure_ascii=False) for e in elements]
    return ("{\"elements\":[\n" + ",\n".join(lines) + "\n]}\n").encode("utf-8")


def cut_counts(elements: list[dict]) -> dict:
    c = {"nodes": 0, "ways": 0, "relations": 0}
    for e in elements:
        c[e["type"] + "s"] += 1
    return c


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def area_boxes(names: list[str] | None, margin_m: float = CUT_MARGIN_M) -> dict[str, dict]:
    import aoi
    cfg = aoi.load()
    names = names or list(cfg.areas)
    boxes = {}
    for n in names:
        if n not in cfg.areas:
            raise PinError(f"unknown area {n!r}; config/aoi.yaml has {list(cfg.areas)}")
        boxes[n] = cut_bbox(cfg.area(n).bounds, margin_m)
    return boxes


def cut_path(area: str, cut_dir: Path = CUT_DIR) -> Path:
    return cut_dir / f"{area}.overpass.json"


def crossings_path(area: str, cut_dir: Path = CUT_DIR) -> Path:
    return cut_dir / f"{area}.crossings.json"


def write_if_changed(path: Path, data: bytes) -> bool:
    """write_atomic unless the file already holds exactly these bytes (readers of an unchanged cut are not disturbed)."""
    if path.is_file() and path.read_bytes() == data:
        return False
    write_atomic(path, data)
    return True


def run_cut(names: list[str] | None, *, manifest_path: Path = MANIFEST_PATH, osm_dir: Path = OSM_DIR,
            cut_dir: Path = CUT_DIR, record: bool = True) -> dict[str, dict]:
    """Cut the squares, write each to cut_dir, and (record=True) put bbox, counts and SHA-256 in the manifest."""
    manifest = load_manifest(manifest_path)
    pbf = verify_pbf(manifest, osm_dir)
    boxes = area_boxes(names, manifest.get("cut_margin_m", CUT_MARGIN_M))
    side: dict[str, list[dict]] = {}
    cuts = cut_many(pbf, boxes, crossings_out=side)
    info = {}
    for n, elements in cuts.items():
        data = cut_bytes(elements)
        write_if_changed(cut_path(n, cut_dir), data)
        cdata = cut_bytes(side[n])
        write_if_changed(crossings_path(n, cut_dir), cdata)
        info[n] = {"bbox": boxes[n], "elements": cut_counts(elements), "bytes": len(data),
                   "sha256": hashlib.sha256(data).hexdigest(),
                   "crossings": {"nodes": len(side[n]), "bytes": len(cdata),
                                 "sha256": hashlib.sha256(cdata).hexdigest()}}
    if record:
        manifest["cut_recipe"] = CUT_RECIPE
        manifest["crossings_recipe"] = CROSSINGS_RECIPE
        manifest["cut_margin_m"] = manifest.get("cut_margin_m", CUT_MARGIN_M)
        manifest.setdefault("cuts", {}).update(info)
        save_manifest(manifest, manifest_path)
    return info


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("download", help="download the Geofabrik extract and write the manifest")
    s.add_argument("--url", default=GEOFABRIK_URL)
    s.add_argument("--force", action="store_true")
    sub.add_parser("verify", help="check the extract on disk against the manifest")
    s = sub.add_parser("cut", help="cut each map square from the extract")
    s.add_argument("--area", action="append", help="area name from config/aoi.yaml (repeatable; default all)")
    s.add_argument("--check", action="store_true", help="recut and compare with the manifest; write no manifest")
    s.add_argument("--out-dir", type=Path, default=CUT_DIR, help=f"default {CUT_DIR}")
    args = p.parse_args(argv)
    try:
        if args.cmd == "download":
            m = download(args.url, force=args.force)
            print(f"downloaded {m['file']}: {m['size_bytes']} bytes, sha256 {m['sha256']}, data from {m['pbf_header']['data_timestamp_utc']}")
        elif args.cmd == "verify":
            m = load_manifest()
            verify_pbf(m)
            print(f"ok: {m['file']} sha256 {m['sha256']}")
        elif args.cmd == "cut":
            info = run_cut(args.area, cut_dir=args.out_dir, record=not args.check)
            bad = 0
            if args.check:
                recorded = load_manifest().get("cuts", {})
                for n, i in info.items():
                    want = recorded.get(n, {}).get("sha256")
                    same = want == i["sha256"]
                    bad += not same
                    print(f"{n}: {'SAME' if same else 'DIFFERENT'} as manifest ({i['sha256']})")
                    cwant = recorded.get(n, {}).get("crossings", {}).get("sha256")
                    csame = cwant == i["crossings"]["sha256"]
                    bad += not csame
                    print(f"{n} crossings: {'SAME' if csame else 'DIFFERENT'} as manifest ({i['crossings']['sha256']})")
            for n, i in info.items():
                print(f"{n}: {i['elements']} {i['bytes']} bytes sha256 {i['sha256']} -> {cut_path(n, args.out_dir)}")
                print(f"{n}: crossing nodes {i['crossings']['nodes']} {i['crossings']['bytes']} bytes "
                      f"sha256 {i['crossings']['sha256']} -> {crossings_path(n, args.out_dir)}")
            return 1 if bad else 0
    except PinError as exc:
        print(f"osm_pin: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
