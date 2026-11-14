"""T-403: the UK marking materials in an exported level (tools/mapng_export.py add_uk_markings), and the UK
pattern table of MapNG patch 0004 (services/ukRoadMarkings.js) against rules/uk_decals/manifest.json.
No network, no MapNG run, no game."""
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import mapng_export as me  # noqa: E402

MANIFEST = json.loads((ROOT / "rules" / "uk_decals" / "manifest.json").read_text(encoding="utf-8"))
LEVEL = "uk_test"
PREFIX = f"levels/{LEVEL}/art/uk_markings_cubewood/"


def _zip(path: Path, materials: list[str]) -> Path:
    roads = "\n".join(json.dumps({"class": "DecalRoad", "name": f"r{i}", "material": m}) for i, m in enumerate(materials))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"levels/{LEVEL}/info.json", json.dumps({"roadRules": {"rightHandDrive": True}, "region": "europe"}))
        z.writestr(f"levels/{LEVEL}/main/MissionGroup/Decal_Roads/a/items.level.json", roads + "\n")
        z.writestr(f"levels/{LEVEL}/main/MissionGroup/items.level.json", json.dumps({"class": "SimGroup", "name": "x"}) + "\n")
    return path


def _fake_builder(names: list[str], drop_texture: str | None = None):
    calls = []

    def build(level: str, build_dir: Path) -> Path:
        calls.append(level)
        folder = build_dir / me.UK_MARKING_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        base = f"/levels/{level}/art/uk_markings_cubewood/"
        mats = {}
        for n in names:
            files = [f"{n}_o.data.png", "uk_paint_white_cubewood_b.color.png"]
            mats[n] = {"name": n, "class": "Material", "Stages": [{"opacityMap": base + files[0], "baseColorMap": base + files[1]}, {}, {}, {}]}
            for f in files:
                if f != drop_texture:
                    (folder / f).write_bytes(b"\x89PNG")
        (folder / "main.materials.json").write_text(json.dumps(mats), encoding="utf-8")
        return folder

    build.calls = calls
    return build


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "DATA_DIR", tmp_path)
    return tmp_path


def test_uk_materials_are_shipped_beside_the_level(data_root):
    z = _zip(data_root / "lvl.zip", ["uk_centre_1008_cubewood", "uk_centre_1008_cubewood", "uk_giveway_1003a_cubewood",
                                     "road_asphalt_2lane", "m_line_white"])
    build = _fake_builder(["uk_centre_1008_cubewood", "uk_giveway_1003a_cubewood", "uk_lane_1005_cubewood"])
    result = me.add_uk_markings(z, data_root / "build", builder=build)
    assert build.calls == [LEVEL]
    assert result["materials"] == {"uk_centre_1008_cubewood": 2, "uk_giveway_1003a_cubewood": 1}
    with zipfile.ZipFile(z) as zz:
        names = zz.namelist()
        assert PREFIX + "main.materials.json" in names
        assert PREFIX + "uk_giveway_1003a_cubewood_o.data.png" in names
        assert f"levels/{LEVEL}/info.json" in names and len([n for n in names if n.startswith(PREFIX)]) == result["files"]
    # a second run replaces the folder, it does not duplicate it
    me.add_uk_markings(z, data_root / "build", builder=build)
    with zipfile.ZipFile(z) as zz:
        assert len(zz.namelist()) == len(set(zz.namelist()))


def test_level_without_uk_paint_is_left_alone(data_root):
    z = _zip(data_root / "lvl.zip", ["m_line_white", "road_asphalt_2lane"])
    before = z.read_bytes()
    build = _fake_builder([])
    assert me.add_uk_markings(z, data_root / "build", builder=build) is None
    assert build.calls == [] and z.read_bytes() == before


def test_a_used_material_the_generator_lacks_fails(data_root):
    z = _zip(data_root / "lvl.zip", ["uk_centre_1008_cubewood", "uk_made_up_cubewood"])
    with pytest.raises(me.GuardError, match="uk_made_up_cubewood") as exc:
        me.add_uk_markings(z, data_root / "build", builder=_fake_builder(["uk_centre_1008_cubewood"]))
    assert exc.value.code == me.EXIT_ERROR


def test_a_missing_texture_fails(data_root):
    z = _zip(data_root / "lvl.zip", ["uk_centre_1008_cubewood"])
    with pytest.raises(me.GuardError, match="uk_centre_1008_cubewood_o.data.png"):
        me.add_uk_markings(z, data_root / "build",
                           builder=_fake_builder(["uk_centre_1008_cubewood"], drop_texture="uk_centre_1008_cubewood_o.data.png"))


def test_build_folder_must_be_under_the_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "DATA_DIR", tmp_path / "data")
    z = _zip(tmp_path / "lvl.zip", ["uk_centre_1008_cubewood"])
    with pytest.raises(me.GuardError, match="not under"):
        me.add_uk_markings(z, tmp_path / "elsewhere", builder=_fake_builder(["uk_centre_1008_cubewood"]))


# ---------------------------------------------------------------- MapNG patch 0004 table against the manifest

def _patched_file(name: str) -> str:
    """The new file `name` as MapNG patch 0004 creates it (all '+' lines of its hunk)."""
    patches = sorted((ROOT / "patches" / "mapng").glob("0004-*.patch"))
    assert patches, "patch 0004 (UK markings) is missing from patches/mapng"
    lines = patches[0].read_text(encoding="utf-8").splitlines()
    start = lines.index(f"+++ b/{name}")
    hunk = re.match(r"@@ -0,0 \+1,(\d+) @@", lines[start + 1])
    assert hunk, f"{name} is not a new file in {patches[0].name}"
    body = lines[start + 2: start + 2 + int(hunk.group(1))]
    assert all(ln.startswith("+") for ln in body)
    return "\n".join(ln[1:] for ln in body)


def _table() -> dict[str, dict]:
    src = _patched_file("services/ukRoadMarkings.js")
    rows = re.findall(r"^\s+(\w+): \{ material: '([^']+)', textureLength: ([\d.]+)(?:, width: ([\d.]+))? \},$", src, re.M)
    return {material: {"key": key, "textureLength": float(tl), "width": float(w) if w else None}
            for key, material, tl, w in rows}


def test_patch_table_covers_every_material_of_the_manifest():
    table = _table()
    assert set(table) == set(MANIFEST["materials"]), set(table) ^ set(MANIFEST["materials"])


def test_patch_table_texture_lengths_and_widths_match_the_manifest():
    for material, row in _table().items():
        m = MANIFEST["materials"][material]
        assert row["textureLength"] == pytest.approx(m["texture_length_m"], abs=1e-6), material
        if isinstance(m["decal_width_m"], (int, float)):
            assert row["width"] == pytest.approx(m["decal_width_m"], abs=1e-6), f"{material}: fixed width"
        elif row["width"] is not None:  # a composite drawn at its nominal width (double white lines)
            assert row["width"] == pytest.approx(m["decal_width_nominal_m"], abs=1e-6), material
        else:
            assert m["fill_across"] == 0.75, f"{material}: the patch scales width by 1 / 0.75"
