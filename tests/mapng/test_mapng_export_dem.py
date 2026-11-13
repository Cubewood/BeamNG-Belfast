"""The --dem option of tools/mapng_export.py (T-203): which height file MapNG reads, and the guards on it."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import mapng_export as me  # noqa: E402


def make_area(tmp_path, size=4, smooth=False, dem="gpxz", nbytes=None):
    d = tmp_path / "area"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"size": size, "gpxzSmooth": smooth}), encoding="utf-8")
    (d / "osm.json").write_text("[]", encoding="utf-8")
    (d / f"{dem}.f32").write_bytes(b"\0" * (size * size * 4 if nbytes is None else nbytes))
    return d


def test_dem_choices_and_default():
    args = me.build_parser().parse_args(["export", "belfast_slice"])
    assert args.dem == "t30"
    assert me.build_parser().parse_args(["export", "belfast_slice", "--dem", "gpxz"]).dem == "gpxz"
    with pytest.raises(SystemExit):
        me.build_parser().parse_args(["export", "belfast_slice", "--dem", "lidar"])


def test_required_files_follow_the_dem():
    assert me.required_area_files("t30") == ("t30.f32", "meta.json", "osm.json")
    assert me.required_area_files("gpxz") == ("gpxz.f32", "meta.json", "osm.json")


def test_good_gpxz_area_passes(tmp_path):
    me.check_dem_inputs(make_area(tmp_path), "gpxz")


def test_wrong_size_is_refused(tmp_path):
    with pytest.raises(me.GuardError) as e:
        me.check_dem_inputs(make_area(tmp_path, nbytes=60), "gpxz")
    assert e.value.code == me.EXIT_INPUTS


def test_gpxz_with_smoothing_flag_is_refused(tmp_path):
    with pytest.raises(me.GuardError, match="gpxzSmooth"):
        me.check_dem_inputs(make_area(tmp_path, smooth=True), "gpxz")


def test_t30_ignores_the_smoothing_flag(tmp_path):
    me.check_dem_inputs(make_area(tmp_path, smooth=True, dem="t30"), "t30")


def test_export_passes_the_dem_to_node(tmp_path, monkeypatch):
    area = make_area(tmp_path)
    monkeypatch.setattr(me, "AREAS_DIR", area.parent)
    monkeypatch.setattr(me, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(me, "check_pin", lambda: "pin")
    monkeypatch.setattr(me, "_prepare", lambda name: (tmp_path / "net.log", tmp_path / "home"))
    seen = {}

    def fake_node(argv, env, mem):
        seen["argv"] = argv
        return 1  # no zip: the command stops after the node call, which is all this test needs

    monkeypatch.setattr(me, "_run_node", fake_node)
    args = me.build_parser().parse_args(["export", "area", "--dem", "gpxz"])
    assert me.cmd_export(args) == me.EXIT_NODE
    assert seen["argv"][1:3] == ["area", "gpxz"]
