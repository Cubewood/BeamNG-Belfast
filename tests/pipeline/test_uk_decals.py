"""T-402: UK decal materials. Static checks only; nothing here loads the game."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))

import uk_decals as U  # noqa: E402
import uk_decals_check as C  # noqa: E402

OUT = ROOT / ".pytest_cache" / "uk_decals"


@pytest.fixture(scope="module")
def built():
    specs = U.write_all(OUT, "belfast_slice", write_fragment=False)
    folder = OUT / U.FOLDER
    mats = json.loads((folder / "main.materials.json").read_text(encoding="utf-8"))
    return specs, folder, mats


@pytest.fixture(scope="module")
def manifest():
    return json.loads((U.FRAGMENT_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rules():
    return U.load_rules()


def test_every_texture_exists(built):
    _, folder, mats = built
    errors, n = C.check_textures(mats, folder, "belfast_slice")
    assert n > 0
    assert not errors, errors


def test_texture_paths_follow_level_name(tmp_path_factory):
    out = OUT / "other_level"
    U.write_all(out, "some_other_level", write_fragment=False)
    mats = json.loads((out / U.FOLDER / "main.materials.json").read_text(encoding="utf-8"))
    errors, n = C.check_textures(mats, out / U.FOLDER, "some_other_level")
    assert n > 0 and not errors, errors


def test_material_names_unique_and_prefixed(built):
    specs, _, mats = built
    keys = [s.material for s in specs]
    assert len(keys) == len(set(keys))
    assert set(keys) == set(mats)
    assert not C.check_names(mats)


@pytest.mark.skipif(not C.GAME.is_dir(), reason="game install not found")
def test_names_do_not_collide_with_stock_or_mapng(built):
    _, _, mats = built
    errors, files = C.check_stock(mats)
    assert files > 100, "stock scan found almost nothing"
    assert not errors, errors


def test_texture_files_are_small(built):
    _, folder, _ = built
    for p in folder.glob("*.png"):
        assert p.stat().st_size < 200 * 1024, p.name


def test_periods_match_yaml(built, manifest, rules):
    _, folder, _ = built
    errors, lines = C.check_periods(manifest, folder, rules)
    assert len(lines) >= 10, "fewer dashed materials measured than expected"
    assert not errors, errors


def test_repeat_lengths_are_yaml_modules(manifest, rules):
    expect = {
        "uk_centre_1008_cubewood": "centre_1008", "uk_centre_1008_1_cubewood": "centre_1008_1",
        "uk_lane_1005_cubewood": "lane_1005", "uk_lane_1005_1_cubewood": "lane_1005_1",
        "uk_warning_1004_cubewood": "warning_1004", "uk_warning_1004_1_cubewood": "warning_1004_1",
        "uk_dash_600_300_cubewood": "edge_1009A", "uk_dash_1000_1000_cubewood": "edge_1010",
        "uk_giveway_1003a_cubewood": "giveway_1003a",
    }
    for mat, rid in expect.items():
        assert manifest["materials"][mat]["texture_length_m"] * 1000 == rules[rid]["geometry"]["module_mm"]


def test_committed_fragment_is_current():
    assert C.check_drift("belfast_slice") == []


def test_every_rule_is_drawn_or_listed_as_not_drawn(manifest, rules):
    mapped = set(manifest["rule_to_materials"])
    assert mapped <= set(rules), sorted(mapped - set(rules))
    missing = set(rules) - mapped - set(manifest["rules_not_drawn"])
    assert not missing, sorted(missing)


def test_manifest_lists_unverified_rules(manifest, rules):
    unverified = sorted(r for r, v in rules.items() if not v.get("verified", True))
    assert manifest["rules_verified_false_in_yaml"] == unverified
    assert unverified, "expected the YAML to flag unverified rules"


def test_annotations_exist_in_game_list():
    game = C.GAME / "tech" / "annotations.json"
    if not game.is_file():
        pytest.skip("game install not found")
    known = set(json.loads(game.read_text(encoding="utf-8")))
    for s in U.build_specs(U.load_rules()):
        assert s.annotation in known, s.annotation


def test_render_is_deterministic(built):
    specs, _, _ = built
    import numpy as np
    s = specs[0]
    assert np.array_equal(U.render(s), U.render(s))


def test_hatch_tile_repeats_seamlessly(built):
    import numpy as np
    from PIL import Image
    _, folder, _ = built
    for key in ("box_hatch_1043", "box_hatch_1043_wide"):
        img = np.array(Image.open(folder / f"uk_{key}_cubewood_o.data.png")).astype(int)
        # the last row must continue into the first row, and the last column into the first
        assert np.abs(img[-1] - img[0]).max() < 80
        assert np.abs(img[:, -1] - img[:, 0]).max() < 80


def test_zip_holds_every_texture(built):
    import zipfile
    _, folder, _ = built
    z = OUT / "level_copy.zip"
    prefix = f"levels/belfast_slice/art/{U.FOLDER}/"
    with zipfile.ZipFile(z, "w") as zf:
        for p in folder.iterdir():
            zf.write(p, prefix + p.name)
    errors, n = C.check_zip(z, "belfast_slice")
    assert n > 0 and not errors, errors
    # drop one texture and the check must notice
    with zipfile.ZipFile(z) as zin, zipfile.ZipFile(OUT / "level_short.zip", "w") as zout:
        for item in zin.infolist():
            if "uk_zigzag" not in item.filename or item.filename.endswith(".json"):
                zout.writestr(item, zin.read(item.filename))
    errors, _ = C.check_zip(OUT / "level_short.zip", "belfast_slice")
    assert errors and "uk_zigzag" in errors[0]
