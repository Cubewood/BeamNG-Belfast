#!/usr/bin/env python3
"""Build, rebuild and check the named drive-test routes (T-302).

    uv run --no-project --python 3.12 python qa/routes/build_routes.py new     [--graph G] [--osm O] [--out F]
    uv run --no-project --python 3.12 python qa/routes/build_routes.py rebuild [--tc X,Y | --tc-latlon LAT,LON]
    uv run --no-project --python 3.12 python qa/routes/build_routes.py check

The route file (qa/routes/belfast_slice.json) is data. Each route has hand-editable `via` points
(world metres, level origin at the map centre, x east, y north); everything else (path,
length, junctions, streets, lat/lon) is derived from the game's navgraph by `rebuild`, which
re-resolves every route through its via points by shortest legs. The graph dump
(`drive_suite.py dump-graph`, from `map.getMap()`) and MapNG's OSM file are inputs and stay out
of git (H:\\BelfastData). A via point named "TC" is the provisional test-centre start: change it
for all routes at once with `rebuild --tc X,Y`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import route_lib as rl  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "belfast_slice.json"
DEFAULT_GRAPH = Path(r"H:\BelfastData\qa\graph\belfast_slice.graph.json")
DEFAULT_OSM = Path(r"H:\BelfastData\mapng\areas\belfast_slice\osm.json")
LAT0, LON0 = 54.5428, -5.9300      # level origin (docs/belfast_slice.md); not user-confirmed
# Provisional test-centre start: the Hospital Road point nearest the level origin. The real test
# centre (4 Hospital Road, BT8 8JL) is not located in OSM and not confirmed by the user.
TC = (-122.0, 109.0)


def v(name: str, x: float, y: float) -> dict:
    return {"name": name, "x": x, "y": y}


_TC = v("TC", *TC)
_NE = v("Hospital Rd / Newtownbreda Rd / Purdysburn Rd junction", 204, 280)
_S = v("Hospital Rd / Ballycoan Rd junction", -205, -292)
_MT = v("Milltown Rd / Belvoir Rd (north-west end)", -189, 434)
_RB = v("Newtownbreda Rd roundabout (C302)", 457, 879)
_LI1 = v("Hospital Rd / Lady Ishbel Ave", -189, -147)
_LI2 = v("Lady Ishbel Ave / west link", -266, -115)
_HW1 = v("Lady Ishbel Ave / Haddo Woods", -268, -166)
_HW2 = v("Haddo Woods east", -311, -96)
_LK = v("West link / Hospital Rd", -116, 120)

# (id, name, description, via). Out-and-back needs a U-turn the AI cannot be asked for, so a
# road with no loop is two routes, one per direction (outbound = left-turn side, inbound = right).
DEFAULT_SPECS = [
    ("hospital-road-ne-out", "Hospital Road to the Milltown / Newtownbreda junction",
     "Straight run north-east along Hospital Road through the signalised junction complex.",
     [_TC, _NE]),
    ("hospital-road-ne-back", "Newtownbreda junction back to the test centre",
     "The same road the other way: signals, then Hospital Road south-west to the start.",
     [_NE, _TC]),
    ("hospital-road-south-out", "Hospital Road south to the Ballycoan Road junction",
     "Hospital Road away from the junctions, past Lady Ishbel Avenue, to the T junction at Ballycoan Road.",
     [_TC, _S]),
    ("hospital-road-south-back", "Ballycoan Road junction back to the test centre",
     "Hospital Road north-east, uphill, back to the start.",
     [_S, _TC]),
    ("lady-ishbel-loop", "Lady Ishbel Avenue loop, clockwise",
     "Hospital Road south, right into Lady Ishbel Avenue, right onto the west link, right back to the start.",
     [_TC, _LI1, _LI2, _LK, _TC]),
    ("lady-ishbel-loop-reverse", "Lady Ishbel Avenue loop, anticlockwise",
     "The same loop the other way: the turns across traffic become right turns out of side roads.",
     [_TC, _LK, _LI2, _LI1, _TC]),
    ("haddo-woods-loop", "Haddo Woods residential loop",
     "Lady Ishbel Avenue and Haddo Woods, a residential circuit with left and right turns.",
     [_TC, _LI1, _HW1, _HW2, _LI2, _LK, _TC]),
    ("milltown-road-out", "Milltown Road north-west to Belvoir Road",
     "Hospital Road to the signals, left onto Milltown Road (dual carriageway) through two signalised crossings.",
     [_TC, _MT]),
    ("milltown-road-back", "Milltown Road back to the test centre",
     "Return from Belvoir Road: the other carriageway, signals, then right across traffic where the road allows.",
     [_MT, _TC]),
    ("newtownbreda-roundabout", "Newtownbreda Road to the roundabout",
     "Hospital Road, left onto Newtownbreda Road, 1 km of larger road with give-way junctions, ending at the roundabout.",
     [_TC, _RB]),
]


def load_graph(path: Path) -> rl.Graph:
    return rl.Graph(json.loads(path.read_text(encoding="utf-8")))


def derive(g: rl.Graph, specs: list[dict], features: list[dict]) -> list[dict]:
    routes = []
    for s in specs:
        r = rl.build_route(g, s, features, LAT0, LON0)
        near = [g.radius[n] for n in rl.resolve_via(g, s["via"])]
        r["half_width_m"] = round(sorted(near)[len(near) // 2], 1)
        routes.append(r)
    return routes


def document(routes: list[dict], graph: Path, osm: Path) -> dict:
    return {
        "level": "belfast_slice",
        "origin": {"lat": LAT0, "lon": LON0, "note": "level centre; world x east, y north, metres"},
        "provisional": "Start point TC is a working assumption (Hospital Road nearest the level origin). "
                       "The test centre position is not confirmed by the user.",
        "generated": dt.date.today().isoformat(),
        "sources": {"graph": "game navgraph, map.getMap() on belfast_slice (drive_suite.py dump-graph)",
                    "osm": "MapNG osm.json for the slice (Overpass, OSM data): signals, give-way, roundabouts",
                    "graph_file": str(graph), "osm_file": str(osm)},
        "routes": routes,
    }


def cmd_new(a) -> int:
    g = load_graph(Path(a.graph))
    feats = rl.load_osm_features(Path(a.osm), LAT0, LON0)
    specs = [{"id": i, "name": n, "description": d, "via": [dict(x) for x in vias]}
             for i, n, d, vias in DEFAULT_SPECS]
    doc = document(derive(g, specs, feats), Path(a.graph), Path(a.osm))
    return write(doc, Path(a.out), g)


def cmd_rebuild(a) -> int:
    out = Path(a.out)
    old = json.loads(out.read_text(encoding="utf-8"))
    tc = None
    if a.tc:
        tc = tuple(float(p) for p in a.tc.split(","))
    elif a.tc_latlon:
        lat, lon = (float(p) for p in a.tc_latlon.split(","))
        tc = rl.latlon_to_xy(lat, lon, LAT0, LON0)
    specs = []
    for r in old["routes"]:
        via = [{k: v_ for k, v_ in p.items() if k not in ("lat", "lon")} for p in r["via"]]
        if tc:
            for p in via:
                if p["name"] == "TC":
                    p["x"], p["y"] = round(tc[0], 1), round(tc[1], 1)
        specs.append({"id": r["id"], "name": r["name"], "description": r.get("description", ""), "via": via})
    g = load_graph(Path(a.graph))
    feats = rl.load_osm_features(Path(a.osm), LAT0, LON0)
    doc = document(derive(g, specs, feats), Path(a.graph), Path(a.osm))
    return write(doc, out, g)


def write(doc: dict, out: Path, g: rl.Graph) -> int:
    errs = rl.validate_routes(doc, g)
    for e in errs:
        print("INVALID:", e, file=sys.stderr)
    if errs:
        return 1
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    for r in doc["routes"]:
        c = r["counts"]
        print(f"{r['id']:28} {r['length_m']:5} m  L{c['left']} R{c['right']} signals {c['signals']} "
              f"roundabouts {c['roundabouts']}  {', '.join(r['streets'][:4])}")
    print(f"wrote {out} ({len(doc['routes'])} routes)")
    return 0


def cmd_check(a) -> int:
    doc = json.loads(Path(a.out).read_text(encoding="utf-8"))
    g = load_graph(Path(a.graph)) if Path(a.graph).is_file() else None
    errs = rl.validate_routes(doc, g)
    for e in errs:
        print("INVALID:", e, file=sys.stderr)
    print(f"{len(doc['routes'])} routes, {len(errs)} problems, graph {'checked' if g else 'NOT available (format only)'}")
    return 1 if errs else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["new", "rebuild", "check"])
    ap.add_argument("--graph", default=str(DEFAULT_GRAPH))
    ap.add_argument("--osm", default=str(DEFAULT_OSM))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--tc", help="new test-centre start as world X,Y metres (rebuild)")
    ap.add_argument("--tc-latlon", help="new test-centre start as LAT,LON (rebuild)")
    a = ap.parse_args(argv)
    return {"new": cmd_new, "rebuild": cmd_rebuild, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
