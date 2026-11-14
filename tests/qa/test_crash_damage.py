"""I-004: after a crash the drive suite records which parts, body zones and tyres took the damage. No game."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qa"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import drive_suite as ds  # noqa: E402


def test_parts_sorted_worst_first_and_trimmed():
    parts = [[f"slot{i}", f"part {i}", i / 20] for i in range(20)]
    text = json.dumps({"parts": parts, "zones": {"FL": 0.25, "FR": 0, "ML": 0.0123456},
                       "tyres": ["FL"], "damage": 12345.678})
    d = ds.parse_crash_damage(text)
    assert d["parts_damaged"] == 20
    assert len(d["parts"]) == ds.CRASH_PARTS_KEPT
    assert d["parts"][0] == ["slot19", "part 19", 0.95]
    assert [p[2] for p in d["parts"]] == sorted((p[2] for p in d["parts"]), reverse=True)
    assert d["zones"] == {"FL": 0.25, "FR": 0.0, "ML": 0.0123}
    assert d["tyres"] == ["FL"]
    assert d["damage"] == 12345.7


def test_lua_empty_tables_and_bad_answers():
    # jsonEncode of an empty Lua table is {} (an object), not []
    d = ds.parse_crash_damage(json.dumps({"parts": {}, "zones": {}, "tyres": {}, "damage": 0}))
    assert d["parts"] == [] and d["tyres"] == [] and d["zones"] == {} and d["damage"] == 0.0
    assert "error" in ds.parse_crash_damage("")
    assert "error" in ds.parse_crash_damage("error: attempt to index nil")
    assert "error" in ds.parse_crash_damage("[1, 2]")


def test_lua_uses_only_named_game_functions():
    code = ds.LUA_CRASH_DAMAGE
    for name in ("beamstate.getPartDamageData()", "beamstate.getBodyZoneDamage()", "beamstate.damage",
                 "wheels.wheels", "isTireDeflated", "isPunctured", "jsonEncode("):
        assert name in code
    assert "require" not in code and "io." not in code and "os." not in code


def test_scrape_is_a_damage_rise_with_nothing_broken():
    clean = {"parts": [], "parts_damaged": 0, "zones": {"FL": 0.0, "FR": 0.004}, "tyres": [], "damage": 0.1}
    assert ds.is_scrape(clean)
    assert not ds.is_scrape({**clean, "parts": [["s", "Front Spindles", 1.0]]})
    assert not ds.is_scrape({**clean, "zones": {"FL": 0.048}})
    assert not ds.is_scrape({**clean, "tyres": ["FL"]})
    assert not ds.is_scrape({"error": "no answer"})
