"""G0 static checks (T-301): one planted fault per check, plus a clean level that must pass. No game, no model.

    uv run --no-project --python 3.12 --with numpy --with shapely --with pytest python -m pytest tests/static_checks -q
"""
import json
import math
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qa"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture as fx  # noqa: E402
from static_checks import checks as ck  # noqa: E402
from static_checks import cli  # noqa: E402
from static_checks.game_index import GameIndex  # noqa: E402
from static_checks.level import load_level  # noqa: E402

C = ck.cfg(None)
INDEX = GameIndex({"art/shapes/stock/ok.dae"}, set())


def level(tmp_path, **kw):
    z, o = fx.build(tmp_path, fx.Spec(**kw))
    return load_level(z, o)


def kinds(findings):
    return sorted({f["kind"] for f in findings})


def near(findings, kind, x, y, r=20.0):
    return [f for f in findings if f["kind"] == kind and "x" in f and math.hypot(f["x"] - x, f["y"] - y) <= r]


def run_all(lv, budget=None):
    out = []
    out += ck.check_roads_present(lv, C)
    out += ck.check_connection(lv, C)
    out += ck.check_oneway(lv, C)
    out += ck.check_road_height(lv, C)
    out += ck.check_budget(lv, budget or {"decal_roads": 100, "objects": 100}, 1000)[0]
    out += ck.check_buildings(lv, C, "auto")[0]
    out += ck.check_terrain_steps(lv, C)
    out += ck.check_stock_assets(lv, INDEX)[0]
    return out


# ---------------------------------------------------------------- the clean level

def test_clean_level_passes_every_check(tmp_path):
    lv = level(tmp_path)
    assert run_all(lv) == []


def test_clean_level_with_buildings_that_do_not_touch_the_lanes(tmp_path):
    lv = level(tmp_path, buildings=[(50, 3.0, 60, 12, "wall"),        # flush with the lanes' edge
                                    (80, 0.5, 90, 8, "overhang"),     # roof over the lanes, no wall in them
                                    (20, 20, 30, 30, "clear")])
    assert ck.check_buildings(lv, C, "mesh")[0] == []


# ---------------------------------------------------------------- 1 public roads present

def test_missing_road_is_found(tmp_path):
    lv = level(tmp_path, drop_exported=("E",))
    found = ck.check_roads_present(lv, C)
    assert kinds(found) == ["road-missing"]
    assert near(found, "road-missing", 100, -120, 90) and "1005" in found[0]["where"]


# ---------------------------------------------------------------- 2 connection

def test_disconnected_island_is_found(tmp_path):
    lv = level(tmp_path, drop_exported=("D",))
    found = ck.check_connection(lv, C)
    assert kinds(found) == ["road-disconnected"]
    assert found[0]["value"] > 300            # all of C is cut off from A and B


# ---------------------------------------------------------------- 3 one-way

def test_oneway_not_drawn_is_found(tmp_path):
    found = ck.check_oneway(level(tmp_path, unflagged_oneway=True), C)
    assert kinds(found) == ["oneway-missing"] and "1003" in found[0]["where"]


def test_oneway_drawn_the_wrong_way_is_found(tmp_path):
    found = ck.check_oneway(level(tmp_path, reverse_c=True), C)
    assert kinds(found) == ["oneway-wrong-direction"] and "1003" in found[0]["where"]


def test_oneway_drawn_on_a_two_way_road_is_found(tmp_path):
    found = ck.check_oneway(level(tmp_path, c_oneway_in_osm=False), C)
    assert kinds(found) == ["oneway-extra"] and "1003" in found[0]["where"]


# ---------------------------------------------------------------- 4 road height

def test_road_floating_over_the_ground_is_found(tmp_path):
    found = ck.check_road_height(level(tmp_path, road_z_bump=1.2), C)
    assert kinds(found) == ["road-height"] and near(found, "road-height", 100, 0, 20)
    assert found[0]["value"] == pytest.approx(1.2, abs=0.01)


def test_road_height_off_a_bridge_is_not_a_fault(tmp_path):
    assert ck.check_road_height(level(tmp_path, road_z_bump=1.2, bridge_tag_a=True), C) == []


def test_road_height_uses_a_given_reference(tmp_path):
    lv = level(tmp_path)
    found = ck.check_road_height(lv, C, reference=lambda x, y: x * 0 + 10.8)   # LiDAR 0.8 m above the roads
    assert kinds(found) == ["road-height"]


# ---------------------------------------------------------------- 5 object budget

def test_object_budget_over_the_limit_is_found(tmp_path):
    lv = level(tmp_path, extra_decal_roads=5)
    found, counts = ck.check_budget(lv, {"decal_roads": 18, "statics": 100}, 1000)
    assert kinds(found) == ["over-budget"] and found[0]["where"] == "decal_roads"
    assert counts["decal_roads"] == 19
    assert ck.check_budget(lv, {"decal_roads": 19}, 1000)[0] == []


def test_missing_budget_is_not_silently_passed(tmp_path):
    found, _ = ck.check_budget(level(tmp_path), {}, 1000)
    assert kinds(found) == ["check-not-run"]


# ---------------------------------------------------------------- 6 buildings in the lanes

PLANTED = [(20, 1.0, 30, 9.0, "wall")]       # a wall 1.7 m into A's lanes over 10 m: about 17 m2


def test_building_wall_in_the_lanes_is_found_in_the_export_mesh(tmp_path):
    found, info = ck.check_buildings(level(tmp_path, buildings=PLANTED), C, "mesh")
    assert info["mode"] == "mesh" and kinds(found) == ["building-in-lane"]
    f = found[0]
    assert near(found, "building-in-lane", 25, 2, 6) and "9000" in f["where"]
    assert f["value"] == pytest.approx(17.0, abs=0.5)         # 10 m x (2.7 - 1.0 m): lanes shrunk by 0.3 m
    assert f["severity"] == "high"


def test_building_in_the_lanes_is_found_from_osm_outlines(tmp_path):
    found, info = ck.check_buildings(level(tmp_path, buildings=PLANTED), C, "osm")
    assert info["mode"] == "osm" and kinds(found) == ["building-in-lane"] and "9000" in found[0]["where"]


def test_building_in_a_driveway_does_not_count(tmp_path):
    z, o = fx.build(tmp_path, fx.Spec(buildings=PLANTED))
    data = json.loads(o.read_text())
    for f in data:
        if f["type"] == "road":
            f["tags"] = {"highway": "service", "service": "driveway"}
    o.write_text(json.dumps(data))
    # every road is now a driveway: nothing counts against buildings
    assert ck.check_buildings(load_level(z, o), C, "mesh")[0] == []


# ---------------------------------------------------------------- 7 terrain steps

def test_terrain_step_across_the_lanes_is_found(tmp_path):
    found = ck.check_terrain_steps(level(tmp_path, step_at_x=60.0, step_height=0.3), C)
    hits = near(found, "terrain-step", 60, 0, 6)
    assert hits and hits[0]["value"] == pytest.approx(0.3, abs=0.02) and hits[0]["severity"] == "high"


def test_small_step_under_the_limit_is_ignored(tmp_path):
    assert ck.check_terrain_steps(level(tmp_path, step_at_x=60.0, step_height=0.1), C) == []


def test_steep_smooth_slope_is_not_a_step(tmp_path):
    assert ck.check_terrain_steps(level(tmp_path, slope=0.15), C) == []


def test_terrain_step_at_a_bridge_is_skipped(tmp_path):
    found = ck.check_terrain_steps(level(tmp_path, step_at_x=60.0, step_height=0.3, bridge_tag_a=True), C)
    assert not near(found, "terrain-step", 60, 0, 12)


# ---------------------------------------------------------------- 8 stock assets

def test_missing_stock_shape_is_found(tmp_path):
    found, info = ck.check_stock_assets(level(tmp_path, shape_path="/art/shapes/stock/gone.dae"), INDEX)
    assert kinds(found) == ["missing-stock-asset"] and "gone.dae" in found[0]["where"]


def test_missing_stock_texture_is_found(tmp_path):
    found, _ = ck.check_stock_assets(level(tmp_path, texture_ref="/assets/materials/nope/t_nope_b.color.png"), INDEX)
    assert kinds(found) == ["missing-stock-asset"] and "t_nope_b" in found[0]["where"]


def test_texture_that_the_game_itself_lacks_is_only_low(tmp_path):
    idx = GameIndex({"art/shapes/stock/ok.dae"}, set(), {"assets/materials/nope/t_nope_b.color.png"})
    found, _ = ck.check_stock_assets(level(tmp_path, texture_ref="/assets/materials/nope/t_nope_b.color.png"), idx)
    assert [f["severity"] for f in found] == ["low"]


def test_texture_present_as_dds_in_the_game_is_found_by_the_engine_fallback(tmp_path):
    idx = GameIndex({"art/shapes/stock/ok.dae", "assets/materials/x/t_x_b.color.dds"}, set())
    assert ck.check_stock_assets(level(tmp_path, texture_ref="/assets/materials/x/t_x_b.color.png"), idx)[0] == []


def test_road_material_defined_nowhere_is_found(tmp_path):
    found, _ = ck.check_stock_assets(level(tmp_path, road_material="no_such_material"), INDEX)
    assert kinds(found) == ["missing-stock-material"] and found[0]["where"] == "no_such_material"


# ---------------------------------------------------------------- MapNG's linter, the findings file, the digest

def test_lint_result_becomes_findings():
    res = {"findings": [{"level": "error", "check": "textures", "msg": "m: /x.png missing", "where": "a.json"},
                        {"level": "warn", "check": "shapes", "msg": "no Colmesh"}]}
    out = cli.lint_findings(res)
    assert [(f["kind"], f["severity"]) for f in out] == [("lint-textures", "high"), ("lint-shapes", "low")]


def test_mapng_linter_runs_on_a_level_and_sees_a_planted_missing_asset(tmp_path):
    if not shutil.which("node") or not any((d / "scripts" / "lib" / "levelLint.mjs").exists()
                                           for d in (cli.DEFAULT_MAPNG, cli.FALLBACK_MAPNG)):
        pytest.skip("node or third_party/mapng is not available here")
    z, _ = fx.build(tmp_path, fx.Spec(shape_path="/art/shapes/stock/gone.dae"))
    idx = tmp_path / "index.txt"
    idx.write_text("stamp\nart/shapes/stock/ok.dae\nM road_asphalt_2lane\nM road_invisible\n", encoding="utf-8", newline="\n")
    out = cli.run_mapng_lint(z, idx, None, tmp_path / "lint.json")
    assert any(f["kind"] == "lint-shapes" and "gone.dae" in f["detail"] and f["severity"] == "high" for f in out), out


def test_findings_file_is_written_and_the_digest_reads_it(tmp_path):
    z, o = fx.build(tmp_path, fx.Spec(buildings=PLANTED, step_at_x=60.0, step_height=0.3, drop_exported=("E",)))
    idx = tmp_path / "index.txt"
    idx.write_text("stamp\nart/shapes/stock/ok.dae\n", encoding="utf-8", newline="\n")
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"decal_roads": 100}))
    r = cli.run_checks(z, o, only=tuple(c for c in cli.ALL_CHECKS if c != "lint"), budget_path=budget,
                       index_path=idx, out_dir=tmp_path / "run", log=lambda *_: None)
    doc = json.loads((tmp_path / "run" / "findings.json").read_text())
    assert doc["source"] == "lint"
    assert {"road-missing", "building-in-lane", "terrain-step"} <= set(doc["counts"])
    sys.path.insert(0, str(ROOT / "qa"))
    import digest
    src = digest.classify(tmp_path / "run" / "findings.json")
    assert src and src["source"] == "lint" and len(src["findings"]) == len(doc["findings"])
    assert r["doc"]["counts"] == doc["counts"]


def test_cli_exit_code_is_one_when_a_high_finding_exists(tmp_path):
    z, o = fx.build(tmp_path, fx.Spec(drop_exported=("E",)))
    idx = tmp_path / "index.txt"
    idx.write_text("stamp\nart/shapes/stock/ok.dae\n", encoding="utf-8", newline="\n")
    rc = cli.main(["--zip", str(z), "--osm", str(o), "--no-lint", "--game-index", str(idx), "--out-dir", str(tmp_path / "o"),
                   "--budget", str(ROOT / "config" / "static_budget.json")])
    assert rc == 1
    (tmp_path / "clean_dir").mkdir()
    z2, o2 = fx.build(tmp_path / "clean_dir", fx.Spec())
    rc = cli.main(["--zip", str(z2), "--osm", str(o2), "--no-lint", "--game-index", str(idx), "--out-dir", str(tmp_path / "o2"),
                   "--budget", str(ROOT / "config" / "static_budget.json")])
    assert rc == 0
