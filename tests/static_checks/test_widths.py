"""The road width check (T-406): drawn carriageway against the DfI figure, on the synthetic level of fixture.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qa"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture as fx  # noqa: E402
from static_checks import checks as ck  # noqa: E402
from static_checks.level import load_level  # noqa: E402
from static_checks.widths import check_road_widths  # noqa: E402

C = ck.cfg(None)


def dfi(**ways):
    return {wid: {"width_m": w, "tier": "high", "confidence": 0.9, "section_main": "S"} for wid, w in ways.items()}


def level(tmp_path, **kw):
    z, o = fx.build(tmp_path, fx.Spec(**kw))
    return load_level(z, o)


def test_drawn_width_equal_to_dfi_is_clean(tmp_path):
    lv = level(tmp_path, body_width=6.0)
    f, info = check_road_widths(lv, C, dfi(**{"1001": 6.0, "1002": 6.0, "1004": 6.0}))
    assert f == []
    assert info["compared"] == 3 and info["within"] == 3 and info["dfi_ways_in_level"] == 3


def test_tolerance_is_075_m(tmp_path):
    lv = level(tmp_path, body_width=6.0)
    f, info = check_road_widths(lv, C, dfi(**{"1001": 6.7, "1002": 6.8}))
    assert [x["where"] for x in f] == ["Road B (OSM way 1002)"]       # 0.8 m off is reported, 0.7 m is not
    assert info["within"] == 1


def test_finding_names_the_way_drawn_dfi_and_tier(tmp_path):
    lv = level(tmp_path, body_width=10.6, body_width_by_road={"B": 6.0})
    f, _ = check_road_widths(lv, C, dfi(**{"1001": 10.6, "1002": 10.6}))
    assert len(f) == 1
    x = f[0]
    assert x["kind"] == "road-width-off-dfi" and x["severity"] == "medium"
    assert x["metric"] == "drawn 6.0 m, DfI 10.6 m"
    assert abs(x["value"] - (-4.6)) < 0.01
    assert "OSM way 1002" in x["where"] and "high confidence" in x["detail"]


def test_low_confidence_match_is_a_low_finding(tmp_path):
    lv = level(tmp_path, body_width=6.0)
    d = {"1001": {"width_m": 12.0, "tier": "low", "confidence": 0.2}}
    f, _ = check_road_widths(lv, C, d)
    assert [x["severity"] for x in f] == ["low"]


def test_ways_without_a_dfi_width_are_not_compared(tmp_path):
    lv = level(tmp_path, body_width=6.0)
    f, info = check_road_widths(lv, C, dfi(**{"9999": 3.0}))
    assert f == [] and info["dfi_ways_in_level"] == 0


def test_a_way_with_no_drawn_road_is_not_a_width_fault(tmp_path):
    lv = level(tmp_path, body_width=6.0, drop_exported=("E",))
    f, info = check_road_widths(lv, C, dfi(**{"1005": 9.0}))        # road-missing reports the absence
    assert f == [] and info["not_compared"] == 1 and info["compared"] == 0


def test_missing_dfi_file_is_check_not_run(tmp_path):
    lv = level(tmp_path)
    f, _ = check_road_widths(lv, ck.cfg({"dfi_widths": tmp_path / "nope.json"}))
    assert [x["kind"] for x in f] == ["check-not-run"]
