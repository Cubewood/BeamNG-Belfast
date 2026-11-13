"""DfI vs OSM crossing matching on synthetic points (T-206). No files, no network.

    uv run --no-project --python 3.12 --with pytest --with pyproj --with numpy --with pyyaml python -m pytest tests/pipeline/test_dfi_crossings.py -q
"""
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import aoi  # noqa: E402
import dfi_crossings as dc  # noqa: E402

LAT0, LON0 = 54.5428, -5.9300  # slice centre


def at(north_m: float, east_m: float, lat0: float = LAT0, lon0: float = LON0):
    """A (lat, lon) that is north_m and east_m metres from (lat0, lon0)."""
    lat = lat0 + north_m / 111_195.0
    lon = lon0 + east_m / (111_195.0 * math.cos(math.radians(lat0)))
    return lat, lon


def dfi(lat, lon, kind, name="test"):
    return {"src": "dfi", "id": name, "lat": lat, "lon": lon, "kind": kind, "sub": kind, "e": 0, "n": 0}


def osm(lat, lon, kind, tag, oid="node/1"):
    return {"src": "osm", "id": oid, "lat": lat, "lon": lon, "kind": kind, "sub": tag}


@pytest.fixture(scope="module")
def slice_area():
    return aoi.load().area("slice")


def inside(area):
    return lambda la, lo: bool(area.contains_latlon(la, lo))


def test_osm_kind_classifies_tags():
    assert dc.osm_crossing({"highway": "traffic_signals"}) == ("signal", "highway=traffic_signals")
    assert dc.osm_crossing({"highway": "crossing", "crossing": "puffin"})[0] == "signal"
    assert dc.osm_crossing({"highway": "crossing", "crossing": "toucan"})[0] == "signal"
    assert dc.osm_crossing({"highway": "crossing", "crossing": "pelican"})[0] == "signal"
    assert dc.osm_crossing({"highway": "crossing", "crossing": "zebra"})[0] == "uncontrolled"
    assert dc.osm_crossing({"highway": "crossing", "crossing:markings": "zebra"}) == (
        "untyped", "highway=crossing (no crossing tag)")
    assert dc.osm_crossing({"highway": "crossing", "crossing": "traffic_signals"})[0] == "signal"
    assert dc.osm_crossing({"traffic_calming": "table"}) is None
    assert dc.osm_crossing({"highway": "stop"}) is None


def test_agree_rules():
    assert dc.agree("signal", "signal") is True
    assert dc.agree("uncontrolled", "uncontrolled") is True
    assert dc.agree("signal", "uncontrolled") is False
    assert dc.agree("uncontrolled", "signal") is False
    assert dc.agree("unknown", "signal") is None
    assert dc.agree("signal", "untyped") is None


def test_match_within_radius_agrees(slice_area):
    p = at(0, 0)
    rows, c = dc.classify([dfi(*p, "signal")], [osm(*at(0, 10), "signal", "highway=traffic_signals")],
                          inside(slice_area), "slice")
    assert rows == []
    assert c["matched_agree"] == 1


def test_outside_radius_is_dfi_only_and_osm_signal_only(slice_area):
    rows, c = dc.classify([dfi(*at(0, 0), "signal")], [osm(*at(0, 40), "signal", "highway=traffic_signals")],
                          inside(slice_area), "slice")
    cats = sorted(r["category"] for r in rows)
    assert cats == ["dfi_only", "osm_signal_only"]
    assert c["matched_agree"] == 0


def test_radius_edge(slice_area):
    # 20 m is inside 25 m, 30 m is outside
    rows, _ = dc.classify([dfi(*at(0, 0), "signal")], [osm(*at(20, 0), "signal", "x")], inside(slice_area), "slice")
    assert rows == []
    rows, _ = dc.classify([dfi(*at(0, 0), "signal")], [osm(*at(30, 0), "signal", "x")], inside(slice_area), "slice")
    assert {r["category"] for r in rows} == {"dfi_only", "osm_signal_only"}


def test_signal_vs_zebra_is_type_mismatch(slice_area):
    rows, c = dc.classify([dfi(*at(0, 0), "signal")], [osm(*at(3, 0), "uncontrolled", "crossing=zebra")],
                          inside(slice_area), "slice")
    assert [r["category"] for r in rows] == ["type_mismatch"]
    assert rows[0]["osm_tag"] == "crossing=zebra"
    assert c["type_mismatch"] == 1


def test_uncontrolled_vs_signal_is_type_mismatch(slice_area):
    rows, _ = dc.classify([dfi(*at(0, 0), "uncontrolled")], [osm(*at(3, 0), "signal", "crossing=puffin")],
                          inside(slice_area), "slice")
    assert [r["category"] for r in rows] == ["type_mismatch"]


def test_untyped_osm_is_no_judgement(slice_area):
    rows, c = dc.classify([dfi(*at(0, 0), "signal")], [osm(*at(3, 0), "untyped", "highway=crossing")],
                          inside(slice_area), "slice")
    assert rows == []
    assert c["matched_no_judgement"] == 1


def test_one_to_one_nearest_wins(slice_area):
    near = dfi(*at(0, 0), "signal", name="near")
    far = dfi(*at(0, 15), "signal", name="far")
    rows, c = dc.classify([far, near], [osm(*at(0, 2), "signal", "highway=traffic_signals")],
                          inside(slice_area), "slice")
    assert c["matched_agree"] == 1
    assert [(r["category"], r["dfi_id"]) for r in rows] == [("dfi_only", "far")]


def test_outside_square_not_counted(slice_area):
    far_lat, far_lon = at(0, 3000)  # 3 km east, outside the 2 km slice
    rows, c = dc.classify([dfi(far_lat, far_lon, "signal")], [osm(*at(0, 2990), "signal", "x")],
                          inside(slice_area), "slice")
    assert rows == []
    assert c["dfi_signal"] == 0
    assert c["osm_signal"] == 0


def test_partner_just_outside_square_still_matches(slice_area):
    # a DfI point 5 m inside the north edge, its OSM node 5 m outside: one agreement, nothing listed
    b = slice_area.bounds
    edge_lat = b.north - 5 / 111_195.0
    edge_lon = (b.west + b.east) / 2
    rows, c = dc.classify([dfi(edge_lat, edge_lon, "signal")],
                          [osm(b.north + 5 / 111_195.0, edge_lon, "signal", "highway=traffic_signals")],
                          inside(slice_area), "slice")
    assert rows == []
    assert c["matched_agree"] == 1


def test_load_osm_reads_cut_and_side_file_once_per_node(tmp_path):
    import json
    cut = tmp_path / "a.overpass.json"
    side = tmp_path / "a.crossings.json"
    sig = {"type": "node", "id": 1, "lat": 54.5, "lon": -5.9, "tags": {"highway": "traffic_signals"}}
    both = {"type": "node", "id": 2, "lat": 54.5, "lon": -5.9,
            "tags": {"highway": "traffic_signals", "crossing": "traffic_signals"}}
    zebra = {"type": "node", "id": 3, "lat": 54.5, "lon": -5.9, "tags": {"highway": "crossing", "crossing": "zebra"}}
    lamp = {"type": "node", "id": 4, "lat": 54.5, "lon": -5.9, "tags": {"highway": "street_lamp"}}
    cut.write_text(json.dumps({"elements": [sig, both, lamp]}), encoding="utf-8")
    side.write_text(json.dumps({"elements": [both, zebra]}), encoding="utf-8")
    items = dc.load_osm(cut, side)
    assert [i["id"] for i in items] == ["node/1", "node/2", "node/3"]
    assert [i["kind"] for i in items] == ["signal", "signal", "uncontrolled"]
    assert [i["id"] for i in dc.load_osm(cut)] == ["node/1", "node/2"]
