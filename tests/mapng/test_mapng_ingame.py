"""Tests for tools/mapng_ingame.py environment rules. No game, no node."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import mapng_ingame as mi  # noqa: E402


def test_ingame_env_allows_only_localhost_and_strips_keys(tmp_path):
    base = {"PATH": "x", "GOOGLE_STREETVIEW_KEY": "k", "MAPILLARY_TOKEN": "t", "BEAMNG_MCP_URL": "http://evil",
            "NODE_OPTIONS": "--require y", "HOME": "/home/someone"}
    env = mi.build_ingame_env(base, tmp_path / "home", tmp_path / "net.txt")
    assert env["MAPNG_ALLOW_HOSTS"] == "127.0.0.1"
    assert env["HOME"] == str(tmp_path / "home") and env["USERPROFILE"] == env["HOME"]
    assert env["PATH"] == "x"
    for k in ("GOOGLE_STREETVIEW_KEY", "MAPILLARY_TOKEN", "BEAMNG_MCP_URL", "NODE_OPTIONS"):
        assert k not in env


def test_scripts_exist_in_pinned_mapng():
    assert mi.TESTS_SCRIPT.name == "runIngameTests.mjs" and mi.DRIVE_SCRIPT.name == "driveTest.mjs"
