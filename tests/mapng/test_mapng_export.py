"""Tests for tools/mapng_export.py guard logic and tools/mapng_preload.mjs. No network, no MapNG run."""
import os
import json
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import mapng_export as me  # noqa: E402

PRELOAD = Path(me.__file__).with_name("mapng_preload.mjs")


# ---------------------------------------------------------------- environment stripping

def test_strip_env_removes_google_gpxz_and_mapng_switches():
    base = {
        "PATH": "C:\\bin", "GOOGLE_STREETVIEW_KEY": "k", "google_maps_api_key": "k2", "GOOGLE_API_KEY": "k3",
        "GPXZ_API_KEY": "g", "TRACESTRACK_API_URL": "https://x", "MAPILLARY_TOKEN": "t",
        "BASE": "game", "LIDAR": "1", "RAW": "1", "EXPORT_OPTS": "{}", "NODE_OPTIONS": "--require evil.js",
        "MAPNG_AREAS": "C:\\elsewhere", "TREEMIX": "inat", "KEEP_ME": "yes",
    }
    out = me.strip_env(base)
    assert out == {"PATH": "C:\\bin", "KEEP_ME": "yes"}


def _env(**kw):
    defaults = dict(areas=me.AREAS_DIR, out=me.OUT_DIR, home=me.HOME_DIR, netlog=me.NETLOG_DIR / "x.log")
    defaults.update(kw)
    return me.build_env({"PATH": "p", "GOOGLE_STREETVIEW_KEY": "secret", "BASE": "game", "LIDAR": "1"}, **defaults)


def test_build_env_forces_base_none_and_drops_google():
    env = _env()
    assert env["BASE"] == "none"
    assert env["TREEMIX"] == "climate"
    assert "LIDAR" not in env
    assert not [k for k in env if k.upper().startswith("GOOGLE")]
    assert "secret" not in env.values()


def test_build_env_sets_explicit_folders_and_home():
    env = _env()
    assert env["MAPNG_AREAS"] == str(me.AREAS_DIR)
    assert env["MAPNG_SWEEP_OUT"] == str(me.OUT_DIR)
    assert env["HOME"] == env["USERPROFILE"] == str(me.HOME_DIR)  # ~/.config/mapng/mapng.env is not found
    assert env["MAPNG_COUNTRY_RULES"].endswith("country_rules.json")


def test_build_env_trees_off_by_default_and_not_switched_on_by_caller_opts():
    env = _env(export_opts={"includeTrees": True, "shapeTerrain": False})
    opts = json.loads(env["EXPORT_OPTS"])
    assert opts["includeTrees"] is False
    assert opts["shapeTerrain"] is False
    assert env["MAPNG_ALLOW_HOSTS"] == ""


def test_build_env_trees_on_allows_only_worldcover():
    env = _env(trees=True)
    assert json.loads(env["EXPORT_OPTS"])["includeTrees"] is True
    assert env["MAPNG_ALLOW_HOSTS"] == "esa-worldcover.s3.eu-central-1.amazonaws.com"


def test_fetch_allow_list_has_no_satellite_google_or_overture_host():
    for host in me.FETCH_ALLOW_HOSTS + me.TREES_ALLOW_HOSTS:
        assert not any(bad in host for bad in ("arcgis", "google", "overture", "mapillary", "gpxz", "inaturalist"))


# ---------------------------------------------------------------- folders under H:\BelfastData

@pytest.mark.parametrize("path", [r"H:\BelfastData\mapng\areas", r"h:\belfastdata\x", r"H:\BelfastData"])
def test_require_under_data_accepts(path):
    assert me.require_under_data(Path(path))


@pytest.mark.parametrize("path", [
    r"H:\BelfastDataX\areas", r"H:\Claude\BeamNGBelfast\scratch", r"C:\Users\x", r"H:\BelfastData\..\Other",
])
def test_require_under_data_rejects(path):
    with pytest.raises(me.GuardError):
        me.require_under_data(Path(path))


# ---------------------------------------------------------------- driving-side assertion

GOOD = {"country": "United Kingdom", "region": "europe", "roadRules": {"rightHandDrive": True, "turnOnRed": False}}


def test_assertion_passes_for_uk():
    assert me.assert_left_hand_traffic(GOOD) == {"rightHandDrive": True, "region": "europe", "country": "United Kingdom"}


@pytest.mark.parametrize("info,needle", [
    ({"country": "levels.common.country.usa", "region": "northAmerica", "roadRules": {"rightHandDrive": False}}, "rightHandDrive"),
    ({"region": "europe", "roadRules": {"rightHandDrive": False}}, "rightHandDrive"),
    ({"region": "northAmerica", "roadRules": {"rightHandDrive": True}}, "region"),
    ({"region": "europe"}, "rightHandDrive"),
    ({"region": "europe", "roadRules": {"rightHandDrive": "true"}}, "rightHandDrive"),
    ({}, "region"),
])
def test_assertion_fails_loudly(info, needle):
    with pytest.raises(me.DrivingSideError) as exc:
        me.assert_left_hand_traffic(info)
    assert needle in str(exc.value)
    assert exc.value.code == me.EXIT_DRIVING_SIDE


def _zip(tmp_path, files):
    path = tmp_path / "level.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def test_check_zip_reads_info_json(tmp_path):
    z = _zip(tmp_path, {"levels/belfast_x/info.json": json.dumps(GOOD)})
    assert me.check_zip(z)["region"] == "europe"


def test_check_zip_fails_on_us_level_and_on_missing_info(tmp_path):
    us = {"region": "northAmerica", "roadRules": {"rightHandDrive": False}}
    with pytest.raises(me.DrivingSideError):
        me.check_zip(_zip(tmp_path, {"levels/a/info.json": json.dumps(us)}))
    with pytest.raises(me.DrivingSideError):
        me.check_zip(_zip(tmp_path, {"levels/a/other.json": "{}"}))


def test_main_check_returns_exit_4_for_wrong_side(tmp_path, capsys):
    us = {"region": "northAmerica", "roadRules": {"rightHandDrive": False}}
    z = _zip(tmp_path, {"levels/a/info.json": json.dumps(us)})
    assert me.main(["check", str(z)]) == me.EXIT_DRIVING_SIDE
    assert "driving-side check FAILED" in capsys.readouterr().err


# ---------------------------------------------------------------- pin

def _runner(head, dirty=""):
    def run(cmd, **kw):
        sub = cmd[3]
        out = head if sub == "rev-parse" else dirty
        return SimpleNamespace(returncode=0, stdout=out + "\n", stderr="")
    return run


def test_check_pin_ok():
    assert me.check_pin(runner=_runner(me.PINNED_COMMIT)) == me.PINNED_COMMIT


def test_check_pin_rejects_other_commit():
    with pytest.raises(me.GuardError) as exc:
        me.check_pin(runner=_runner("e15442b" + "0" * 33))
    assert exc.value.code == me.EXIT_PIN


def test_check_pin_rejects_modified_submodule():
    with pytest.raises(me.GuardError) as exc:
        me.check_pin(runner=_runner(me.PINNED_COMMIT, " M services/countryRules.js"))
    assert exc.value.code == me.EXIT_PIN


# ---------------------------------------------------------------- stub fixing

def _png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_grey_png_is_valid():
    assert _png_size(me.grey_png(8)) == (8, 8)


def test_fix_stubs_replaces_stubs_and_adds_facade(tmp_path):
    root = tmp_path / "mapng"
    (root / "public" / "cubemap").mkdir(parents=True)
    (root / "public" / "cubemap" / "skybox0.hdr.dds").write_bytes(b"DDS real content here")
    (root / "public" / "beamng_facade_e.png").write_bytes(b"\x89PNGfacade")
    lv = "levels/lv/"
    mats = {"mapng_bld_facade": {"Stages": [{}]}}
    texset = {"x": {"class": "TerrainMaterialTextureSet", "baseTexSize": [64, 64]}}
    z = _zip(tmp_path, {
        lv + "art/cubemaps/u/cubemap/skybox0.hdr.dds": b"DDS ",
        lv + "art/terrains/terrain.png": b"\x89PNG",
        lv + "art/terrains/main.materials.json": json.dumps(texset),
        lv + "preview.png": b"\x89PNG",
        lv + "map_assets/custom_assets/osm_objects/main.materials.json": json.dumps(mats),
        lv + "main/empty/items.level.json": b"",
        lv + "other.bin": b"tiny",
    })
    result = me.fix_stubs(z, root)
    assert result["fixed"] == 4  # cubemap, terrain, preview, facade
    assert result["left"] == [lv + "other.bin"]
    with zipfile.ZipFile(z) as zz:
        assert zz.read(lv + "art/cubemaps/u/cubemap/skybox0.hdr.dds") == b"DDS real content here"
        assert _png_size(zz.read(lv + "art/terrains/terrain.png")) == (64, 64)
        assert _png_size(zz.read(lv + "preview.png")) == (512, 512)
        assert zz.read(lv + "map_assets/custom_assets/osm_objects/beamng_facade_e.png") == b"\x89PNGfacade"
        m = json.loads(zz.read(lv + "map_assets/custom_assets/osm_objects/main.materials.json"))
        assert m["mapng_bld_facade"]["Stages"][0]["emissiveMap"].endswith("/lv/map_assets/custom_assets/osm_objects/beamng_facade_e.png")


# ---------------------------------------------------------------- network log

def test_read_netlog(tmp_path):
    log = tmp_path / "n.log"
    log.write_text("ALLOW a.example\nALLOW a.example\nBLOCK b.example\n\ngarbage\n", encoding="utf-8")
    assert me.read_netlog(log) == {"ALLOW": {"a.example": 2}, "BLOCK": {"b.example": 1}}
    assert me.read_netlog(tmp_path / "missing.log") == {"ALLOW": {}, "BLOCK": {}}


# ---------------------------------------------------------------- the node preload

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_preload_serves_country_rules_and_blocks_hosts(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text('{"countries": [], "scale": 1000}', encoding="utf-8")
    log = tmp_path / "net.log"
    script = (
        "const r = await fetch('/country_rules.json'); console.log('rules', (await r.json()).scale);"
        "for (const u of ['https://server.arcgisonline.com/x', 'https://maps.googleapis.com/y']) {"
        "  try { await fetch(u); console.log('NOT BLOCKED', u); } catch (e) { console.log('blocked', String(e.message).includes('blocked')); }"
        "}"
    )
    env = {**os.environ, "NODE_OPTIONS": "", "MAPNG_COUNTRY_RULES": str(rules), "MAPNG_NETLOG": str(log),
           "MAPNG_ALLOW_HOSTS": "allowed.example"}
    out = subprocess.run(["node", "--import", PRELOAD.as_uri(), "--input-type=module", "-e", script],
                         env=env, capture_output=True, text=True, cwd=str(tmp_path))
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["rules", "1000", "blocked", "true", "blocked", "true"]
    assert me.read_netlog(log)["BLOCK"] == {"server.arcgisonline.com": 1, "maps.googleapis.com": 1}
