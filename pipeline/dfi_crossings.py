#!/usr/bin/env python3
"""DfI controlled pedestrian crossings against OSM crossings and signals, per map square (T-206).

    uv run --no-project --python 3.12 --with pyproj --with numpy --with pyyaml python pipeline/dfi_crossings.py \\
        [--dfi H:/BelfastData/dfi_crossings/raw/pedestriancrossing.geojson] [--cut-dir H:/BelfastData/osm/cuts] \\
        [--out-dir H:/BelfastData/dfi_crossings] [--doc docs/dfi_crossings.md] [--radius 25] [--no-verify]

Inputs
  DfI: the OpenData NI "Controlled Pedestrian Crossings" GeoJSON (manifest data/sources/dfi_crossings.json, SHA-256 checked
       before use). Points are WGS84; CROSSING_TYPE_NAME is SIGNAL CONTROLLED or UNCONTROLLED (or "?").
  OSM: the pinned cuts (docs/osm_pin.md), one per square, read as they are: `<area>.overpass.json` (the MapNG-facing cut,
       which has the highway=traffic_signals nodes) and `<area>.crossings.json` (every highway=crossing / crossing=*
       node in the square plus margin, I-002). Nodes only. Nothing is fetched; Overpass is never used.

Matching: a DfI point and an OSM node match if they are within --radius metres (great circle); each node is used once,
nearest pairs first. Only items inside the square are counted or listed; partners are looked up in the square plus
150 m, so a crossing on the edge is not reported twice or as missing.

Categories
  type_mismatch   matched, and the kinds disagree: DfI signal-controlled vs OSM uncontrolled (zebra, unmarked,
                  uncontrolled, marked), or DfI uncontrolled vs OSM signal (traffic_signals, pelican, puffin, toucan)
  dfi_only        DfI point with no OSM node within the radius
  osm_signal_only OSM signal node (highway=traffic_signals or a signal crossing) with no DfI point within the radius

DfI does not separate pelican, puffin and toucan, so the subtype cannot be checked against DfI; the OSM subtype is
recorded only.

Writes the summary doc (default docs/dfi_crossings.md, full list for the slice) and one CSV per square under --out-dir.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import aoi  # noqa: E402

DEFAULT_DFI = Path("H:/BelfastData/dfi_crossings/raw/pedestriancrossing.geojson")
DEFAULT_CUTS = Path("H:/BelfastData/osm/cuts")
DEFAULT_OUT = Path("H:/BelfastData/dfi_crossings")
DEFAULT_DOC = ROOT / "docs" / "dfi_crossings.md"
MANIFEST = ROOT / "data" / "sources" / "dfi_crossings.json"
AREAS = ["slice", "city_south", "full"]
RADIUS_M = 25.0
PARTNER_MARGIN_M = 150.0

DFI_KIND = {"SIGNAL CONTROLLED": "signal", "UNCONTROLLED": "uncontrolled"}
OSM_SIGNAL_CROSSING = {"traffic_signals", "pelican", "puffin", "toucan", "signals"}
OSM_UNCONTROLLED_CROSSING = {"zebra", "uncontrolled", "unmarked", "marked"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_dfi(path: Path, manifest: Path = MANIFEST) -> str:
    """The file on disk must match the manifest SHA-256; returns the digest."""
    with open(manifest, encoding="utf-8") as f:
        want = json.load(f)["sha256"]
    got = sha256_file(path)
    if got != want:
        raise SystemExit(f"DfI file SHA-256 {got} does not match {manifest.name} ({want}); refusing to use it")
    return got


def load_dfi(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        gj = json.load(f)
    items = []
    for k, ft in enumerate(gj["features"]):
        p = ft["properties"]
        lon, lat = ft["geometry"]["coordinates"]
        kind = DFI_KIND.get(p.get("CROSSING_TYPE_NAME"), "unknown")
        items.append({
            "src": "dfi", "id": p.get("SECTION_NAME", f"feature {k}"), "lat": float(lat), "lon": float(lon),
            "kind": kind, "sub": p.get("CROSSING_TYPE_NAME", ""),
            "e": p.get("EASTING"), "n": p.get("NORTHING"),
        })
    return items


def osm_crossing(tags: dict):
    """(kind, detail) for a node that is a crossing or a signal, else None.

    kind is signal, uncontrolled or untyped. A plain traffic-signal node is kind signal with detail highway=traffic_signals;
    it may be a junction signal rather than a crossing."""
    hw = tags.get("highway")
    cr = tags.get("crossing")
    if hw == "traffic_signals":
        return "signal", "highway=traffic_signals"
    if hw != "crossing" and cr is None:
        return None
    detail = f"crossing={cr}" if cr else "highway=crossing (no crossing tag)"
    if cr in OSM_SIGNAL_CROSSING or tags.get("crossing:signals") == "yes":
        return "signal", detail
    if cr in OSM_UNCONTROLLED_CROSSING:
        return "uncontrolled", detail
    return "untyped", detail


def load_osm(cut_path: Path, *extra: Path) -> list[dict]:
    """Crossing and signal nodes from the MapNG-facing cut plus the crossings side file(s) (I-002), one item per node id.

    The MapNG cut holds highway=traffic_signals nodes; the side file holds every highway=crossing / crossing=* node
    (docs/osm_pin.md). A node in both (traffic signals that are also tagged crossing=*) is counted once."""
    items = []
    seen = set()
    for path in (cut_path, *extra):
        with open(path, encoding="utf-8") as f:
            els = json.load(f)["elements"]
        for e in els:
            if e.get("type") != "node" or e["id"] in seen:
                continue
            tags = e.get("tags", {})
            c = osm_crossing(tags)
            if c is None:
                continue
            seen.add(e["id"])
            kind, detail = c
            items.append({"src": "osm", "id": f"node/{e['id']}", "lat": float(e["lat"]), "lon": float(e["lon"]),
                          "kind": kind, "sub": detail})
    return items


def agree(dfi_kind: str, osm_kind: str) -> bool | None:
    """True agree, False type mismatch, None no judgement (DfI unknown or OSM untyped)."""
    if dfi_kind == "unknown" or osm_kind == "untyped":
        return None
    return dfi_kind == osm_kind


def match(dfi: list[dict], osm: list[dict], radius: float):
    """Greedy one-to-one matching within radius, nearest pairs first. Returns ({dfi_idx: (osm_idx, m)}, {osm_idx: (dfi_idx, m)})."""
    if not dfi or not osm:
        return {}, {}
    olat = np.array([o["lat"] for o in osm])
    olon = np.array([o["lon"] for o in osm])
    pairs = []
    for i, d in enumerate(dfi):
        dist = aoi.haversine_m(d["lat"], d["lon"], olat, olon)
        for j in np.nonzero(dist <= radius)[0]:
            pairs.append((float(dist[j]), i, int(j)))
    pairs.sort()
    di: dict[int, tuple[int, float]] = {}
    oi: dict[int, tuple[int, float]] = {}
    for m, i, j in pairs:
        if i in di or j in oi:
            continue
        di[i] = (j, m)
        oi[j] = (i, m)
    return di, oi


def _row(category, area, d=None, o=None, dist=None):
    src = d if d is not None else o
    row = {"category": category, "area": area,
           "dfi_id": d["id"] if d else "", "dfi_kind": d["kind"] if d else "",
           "dfi_crossing_type": d["sub"] if d else "",
           "dfi_easting": d["e"] if d else "", "dfi_northing": d["n"] if d else "",
           "osm_id": o["id"] if o else "", "osm_kind": o["kind"] if o else "",
           "osm_tag": o["sub"] if o else "",
           "distance_m": "" if dist is None else round(dist, 2),
           "lat": round(src["lat"], 7), "lon": round(src["lon"], 7)}
    return row


def classify(dfi: list[dict], osm: list[dict], inside, area: str, radius: float = RADIUS_M):
    """dfi and osm are the items around one square (square plus margin). inside(lat, lon) -> bool for the square.

    Returns (rows, counts)."""
    di, oi = match(dfi, osm, radius)
    rows = []
    c = Counter()
    for i, d in enumerate(dfi):
        if not inside(d["lat"], d["lon"]):
            continue
        c[f"dfi_{d['kind']}"] += 1
        if i not in di:
            rows.append(_row("dfi_only", area, d=d))
            c[f"dfi_only_{d['kind']}"] += 1
            continue
        j, m = di[i]
        o = osm[j]
        ok = agree(d["kind"], o["kind"])
        if ok is True:
            c["matched_agree"] += 1
            c[f"matched_agree_osm_{o['sub'].split(' ')[0]}"] += 1
        elif ok is False:
            c["type_mismatch"] += 1
            rows.append(_row("type_mismatch", area, d=d, o=o, dist=m))
        else:
            c["matched_no_judgement"] += 1
    for j, o in enumerate(osm):
        if j in oi or not inside(o["lat"], o["lon"]):
            continue
        c[f"osm_{o['kind']}"] += 1
        if o["kind"] == "signal":
            rows.append(_row("osm_signal_only", area, o=o))
            c["osm_signal_only"] += 1
    return rows, c


def square_items(all_dfi, all_osm, area_obj):
    b = area_obj.bounds
    dlat = PARTNER_MARGIN_M / 111_000.0
    dlon = PARTNER_MARGIN_M / (111_000.0 * np.cos(np.radians((b.north + b.south) / 2)))

    def near(it):
        return (b.south - dlat <= it["lat"] <= b.north + dlat) and (b.west - dlon <= it["lon"] <= b.east + dlon)

    return [d for d in all_dfi if near(d)], [o for o in all_osm if near(o)]


def run(dfi_path: Path, cut_dir: Path, areas: list[str], radius: float, cfg=None, verify: bool = True):
    cfg = cfg or aoi.load()
    if verify:
        verify_dfi(dfi_path)
    all_dfi = load_dfi(dfi_path)
    results = {}
    for name in areas:
        a = cfg.area(name)
        side = cut_dir / f"{name}.crossings.json"
        if not side.is_file():
            raise SystemExit(f"{side} is missing: run `pipeline/osm_pin.py cut` (the crossings side file, I-002)")
        osm_all = load_osm(cut_dir / f"{name}.overpass.json", side)
        cut_counts = Counter(o["kind"] for o in osm_all)
        dfi_near, osm_near = square_items(all_dfi, osm_all, a)
        rows, c = classify(dfi_near, osm_near, lambda la, lo, a=a: bool(a.contains_latlon(la, lo)), name, radius)
        results[name] = {"rows": rows, "counts": c, "cut_counts": cut_counts}
    return results


FIELDS = ["category", "area", "dfi_id", "dfi_kind", "dfi_crossing_type", "dfi_easting", "dfi_northing",
          "osm_id", "osm_kind", "osm_tag", "distance_m", "lat", "lon"]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def md_doc(results: dict, out_dir: Path, dfi_sha: str, dfi_n: int) -> str:
    def g(name, key):
        return results[name]["counts"].get(key, 0)

    L = []
    L.append("# DfI controlled pedestrian crossings vs OSM (T-206)\n")
    L.append("Generated by `pipeline/dfi_crossings.py`; do not edit by hand. Source and licence: "
             "`data/sources/dfi_crossings.json` (OpenData NI, DfI, OGL v3.0). Raw file "
             f"`H:/BelfastData/dfi_crossings/raw/pedestriancrossing.geojson`, {dfi_n} points, SHA-256 `{dfi_sha[:16]}...`.")
    L.append("OSM: the pinned cuts and crossings side files in `H:/BelfastData/osm/cuts` (docs/osm_pin.md). Matching radius 25 m, one to one, "
             "nearest first. Items are counted inside each square only.\n")
    L.append("## Counts per square\n")
    L.append("| Square | DfI signal | DfI uncontrolled | DfI unknown | Matched, agree | Type mismatch | "
             "DfI only (signal / uncontrolled / unknown) | OSM signal only | OSM uncontrolled, no DfI | OSM untyped, no DfI |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for name in results:
        c = results[name]["counts"]
        L.append(f"| {name} | {c['dfi_signal']} | {c['dfi_uncontrolled']} | {c['dfi_unknown']} | "
                 f"{c['matched_agree']} | {c['type_mismatch']} | "
                 f"{c['dfi_only_signal']} / {c['dfi_only_uncontrolled']} / {c['dfi_only_unknown']} | "
                 f"{c['osm_signal_only']} | {c['osm_uncontrolled']} | {c['osm_untyped']} |")
    L.append("")
    L.append("Matched but no judgement (DfI unknown, or OSM crossing with no type): "
             + ", ".join(f"{n} {results[n]['counts'].get('matched_no_judgement', 0)}" for n in results) + ".\n")
    L.append("OSM nodes read for each square, cut box (signal / uncontrolled / untyped crossings and traffic signals):\n")
    for name in results:
        cc = results[name]["cut_counts"]
        L.append(f"- {name}: {cc.get('signal', 0)} / {cc.get('uncontrolled', 0)} / {cc.get('untyped', 0)}")
    L.append("")
    L.append("## Full mismatch list, slice\n")
    rows = results["slice"]["rows"] if "slice" in results else []
    L.append(f"{len(rows)} rows. Also in `{out_dir / 'mismatches_slice.csv'}`.\n")
    L.append("| Category | DfI section | DfI kind | OSM | OSM kind / tag | Distance m | Lat, lon |")
    L.append("|---|---|---|---|---|---|---|")
    for r in rows:
        sec = r["dfi_id"][:90].replace("|", "/")
        L.append(f"| {r['category']} | {sec} | {r['dfi_kind']} | {r['osm_id']} | "
                 f"{r['osm_kind']} / {r['osm_tag']} | {r['distance_m']} | {r['lat']}, {r['lon']} |")
    L.append("")
    L.append("Other squares: `mismatches_city_south.csv` and `mismatches_full.csv` in the same folder.\n")
    L.append("## Limits\n")
    L.append("- OSM crossing nodes (`highway=crossing` or any `crossing=*`) come from the side file "
             "`<area>.crossings.json` (I-002); MapNG's own filter never selected them, so the MapNG-facing cut "
             "`<area>.overpass.json` does not hold them. Signal nodes come from both files, once per node id. "
             "A mismatch is still a candidate for a person to check against a photo or the road, not a verdict: "
             "OSM can lag the street, and the DfI list can lag too.")
    L.append("- `highway=traffic_signals` nodes are junction signals as often as pedestrian crossings. A signal with no DfI "
             "point is not an error by itself.")
    L.append("- Ways (`highway=crossing` ways, footways with `footway=crossing`) are not used here.")
    L.append("- DfI covers signalised crossings and junctions with controlled facilities, and only some uncontrolled points; "
             "DfI does not separate pelican, puffin and toucan.")
    L.append("- Coordinates: DfI WGS84 from the portal; distances are great-circle.\n")
    L.append("## Rerun\n")
    L.append("```text\nuv run --no-project --python 3.12 --with pyproj --with numpy --with pyyaml python pipeline/dfi_crossings.py\n"
             "uv run --no-project --python 3.12 --with pytest --with pyproj --with numpy --with pyyaml python -m pytest tests/pipeline/test_dfi_crossings.py -q\n```")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dfi", type=Path, default=DEFAULT_DFI)
    p.add_argument("--cut-dir", type=Path, default=DEFAULT_CUTS)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    p.add_argument("--radius", type=float, default=RADIUS_M)
    p.add_argument("--area", action="append", choices=AREAS)
    p.add_argument("--no-verify", action="store_true", help="skip the SHA-256 check against the manifest")
    a = p.parse_args(argv)
    areas = a.area or AREAS
    sha = verify_dfi(a.dfi) if not a.no_verify else sha256_file(a.dfi)
    results = run(a.dfi, a.cut_dir, areas, a.radius, verify=False)
    dfi_n = len(load_dfi(a.dfi))
    for name, r in results.items():
        write_csv(a.out_dir / f"mismatches_{name}.csv", r["rows"])
        c = r["counts"]
        print(f"{name}: dfi signal {c['dfi_signal']} uncontrolled {c['dfi_uncontrolled']} unknown {c['dfi_unknown']}; "
              f"agree {c['matched_agree']} type_mismatch {c['type_mismatch']} dfi_only {sum(c[k] for k in c if k.startswith('dfi_only_'))} "
              f"osm_signal_only {c['osm_signal_only']}")
    if "slice" not in results:
        a.doc = None
    if a.doc is not None:
        a.doc.parent.mkdir(parents=True, exist_ok=True)
        a.doc.write_text(md_doc(results, a.out_dir, sha, dfi_n), encoding="utf-8", newline="\n")
        print(f"wrote {a.doc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
