"""Road widths for MapNG (T-406): the order DfI, OSM tags, class defaults; the clamp; idempotence; the table."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import road_widths as rw  # noqa: E402

TABLE = {"rows": {
    "residential|twoway|none": {"n": 100, "p10": 5.0, "median": 5.6, "p90": 7.5},
    "residential|twoway|any": {"n": 120, "p10": 5.0, "median": 5.7, "p90": 7.6},
    "residential|twoway|2": {"n": 20, "p10": 5.0, "median": 5.8, "p90": 7.0},
    "trunk|oneway|any": {"n": 50, "p10": 6.8, "median": 9.4, "p90": 14.3},
    "service|twoway|any": {"n": 126, "p10": 3.7, "median": 5.65, "p90": 9.2},
}}


def way(wid, **tags):
    tags.setdefault("highway", "residential")
    return {"id": f"{wid}_seg_0", "type": "road", "tags": tags,
            "geometry": [{"lat": 54.55, "lng": -5.93}, {"lat": 54.5505, "lng": -5.93}]}


def dfi(wid, w, tier="high"):
    return {wid: {"width_m": w, "tier": tier, "highway": "residential", "oneway": None, "lanes": None}}


def run(features, d=None):
    feats = copy.deepcopy(features)
    summary = rw.apply(feats, d or {}, TABLE)
    return feats, summary


def tags_of(feats, wid):
    return next(f["tags"] for f in feats if f["id"].startswith(f"{wid}_"))


def test_dfi_wins_over_osm_width_and_default():
    feats, _ = run([way("1", width="9")], dfi("1", 6.2))
    t = tags_of(feats, "1")
    assert t["width"] == "6.2" and t["belfast:width_source"] == "dfi-high" and t["width:osm"] == "9"


def test_osm_width_tag_when_no_dfi():
    feats, _ = run([way("1", width="7 m")])
    t = tags_of(feats, "1")
    assert t["width"] == "7" and t["belfast:width_source"] == "osm-width"


def test_lanes_tag_picks_the_row_for_that_lane_count_else_the_class_default():
    feats, _ = run([way("1", lanes="2"), way("2", lanes="3")])
    assert (tags_of(feats, "1")["width"], tags_of(feats, "1")["belfast:width_source"]) == ("5.8", "osm-lanes")
    assert (tags_of(feats, "2")["width"], tags_of(feats, "2")["belfast:width_source"]) == ("5.6", "default")


def test_class_default_by_direction():
    feats, _ = run([way("1"), way("2", highway="trunk", oneway="yes")])
    assert tags_of(feats, "1")["width"] == "5.6"
    assert tags_of(feats, "2")["width"] == "9.4"


def test_service_and_track_are_left_to_mapng():
    feats, _ = run([way("1", highway="service"), way("2", highway="track")])
    for wid in ("1", "2"):
        t = tags_of(feats, wid)
        assert "width" not in t and t["belfast:width_source"] == "none"


def test_dfi_on_a_service_road_is_used_when_confident():
    feats, _ = run([way("1", highway="service")], dfi("1", 5.0, "medium"))
    assert tags_of(feats, "1")["width"] == "5" and tags_of(feats, "1")["belfast:width_source"] == "dfi-medium"


def test_low_confidence_dfi_is_kept_only_inside_the_class_range():
    feats, _ = run([way("1"), way("2"), way("3", highway="service")],
                   {**dfi("1", 6.0, "low"), **dfi("2", 20.0, "low"), **dfi("3", 5.0, "low")})
    assert (tags_of(feats, "1")["width"], tags_of(feats, "1")["belfast:width_source"]) == ("6", "dfi-low")
    assert (tags_of(feats, "2")["width"], tags_of(feats, "2")["belfast:width_source"]) == ("5.6", "default")
    assert "width" not in tags_of(feats, "3")


def test_a_width_under_the_class_minimum_gets_mapng_cap_tag():
    # residential minimum carriageway is 4.4 m (2 x 2.2); trunk 7.0
    feats, s = run([way("1"), way("2", highway="trunk", oneway="yes"), way("3")],
                   {**dfi("1", 4.0), **dfi("2", 6.2), **dfi("3", 4.5)})
    assert tags_of(feats, "1")["mapng:carriageway_max"] == "4" and tags_of(feats, "1")["belfast:width_cap"] == "yes"
    assert tags_of(feats, "2")["mapng:carriageway_max"] == "6.2"
    assert "mapng:carriageway_max" not in tags_of(feats, "3")
    assert s["capped_below_class_minimum"] == ["1", "2"]


def test_a_width_over_the_class_maximum_is_reported_not_drawn():
    _, s = run([way("1")], dfi("1", 17.8))      # residential maximum is 14.6 m
    assert s["unreachable_over_class_maximum"] == [{"way": "1", "highway": "residential", "width": 17.8}]


def test_segments_of_one_way_share_the_width():
    a, b = way("5"), way("5")
    b["id"] = "5_seg_1"
    feats, _ = run([a, b], dfi("5", 6.4))
    assert [f["tags"]["width"] for f in feats] == ["6.4", "6.4"]


def test_footways_and_areas_are_untouched():
    f = way("1", highway="footway")
    g = way("2", area="yes")
    feats, _ = run([f, g], dfi("1", 3.0))
    assert "width" not in tags_of(feats, "1") and "width" not in tags_of(feats, "2")


def test_running_twice_gives_the_same_result_and_restores_the_original_width():
    feats, _ = run([way("1", width="9"), way("2"), way("3", highway="trunk", oneway="yes")], {**dfi("1", 6.2), **dfi("3", 6.2)})
    again = copy.deepcopy(feats)
    rw.apply(again, {**dfi("1", 6.2), **dfi("3", 6.2)}, TABLE)
    assert again == feats
    # DfI gone: the original OSM width comes back, the cap tag goes
    rw.apply(again, {}, TABLE)
    assert tags_of(again, "1")["width"] == "9" and tags_of(again, "1")["belfast:width_source"] == "osm-width"
    assert "mapng:carriageway_max" not in tags_of(again, "3") and tags_of(again, "3")["width"] == "9.4"


def test_deterministic_bytes(tmp_path):
    p = tmp_path / "osm.json"
    d = tmp_path / "dfi.json"
    t = tmp_path / "table.json"
    base = json.dumps([way("1"), way("2", lanes="2")])
    d.write_text(json.dumps({"ways": dfi("1", 6.0)}))
    t.write_text(json.dumps(TABLE))
    out = []
    for _ in range(2):
        p.write_text(base)
        rw.run(p, d, t, write=True)
        out.append(p.read_bytes())
    assert out[0] == out[1]


@pytest.mark.parametrize("raw, want", [("6", 6.0), ("6.5 m", 6.5), ("6,5", 6.5), ("20 ft", None), ("narrow", None), ("0.5", None), (None, None)])
def test_parse_width(raw, want):
    assert rw.parse_width(raw) == want


def test_derive_table_uses_confident_ways_and_needs_eight():
    ways = {str(i): {"highway": "residential", "oneway": None, "lanes": None, "width_m": 5.0 + (i % 3) * 0.1, "tier": "high"}
            for i in range(10)}
    ways["x"] = {"highway": "residential", "oneway": None, "lanes": None, "width_m": 30.0, "tier": "low"}
    for i in range(5):
        ways[f"t{i}"] = {"highway": "trunk", "oneway": "yes", "lanes": "2", "width_m": 8.0, "tier": "high"}
    t = rw.derive_table(ways)
    assert t["rows"]["residential|twoway|none"]["n"] == 10          # the low confidence 30 m is not in it
    assert "trunk|oneway|2" not in t["rows"]                          # five ways are too few


def test_the_committed_table_has_the_main_classes():
    t = json.loads((ROOT / "config" / "road_width_defaults.json").read_text(encoding="utf-8"))
    assert t["rows"]["residential|twoway|none"]["median"] == pytest.approx(5.6, abs=0.3)
    assert "DfI" in t["source"]
