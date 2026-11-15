"""T-407: tools/mapng_export.py fix_signals gives a traffic light with no sequence one (a light with none stays red
for ever in the game). No network, no MapNG run, no game."""
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import mapng_export as me  # noqa: E402

CTRL = {"id": 6, "name": "c", "type": "lightsBasicEU", "isSimple": False, "defaultIndex": 4,
        "states": [{"state": "redYellowTrafficLight", "duration": 1.5}, {"state": "greenTrafficLight", "duration": 12},
                   {"state": "yellowTrafficLight", "duration": 4}, {"state": "redTrafficLight", "duration": 1.5}]}


def make(path: Path, with_signals=True, sequence=0) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("levels/x/info.json", "{}")
        if with_signals:
            z.writestr("levels/x/signals.json", json.dumps({
                "instances": [{"id": 7, "name": "trafficLight 7", "controllerId": 6, "sequenceId": sequence, "pos": [0, 0, 0], "dir": [1, 0, 0]}],
                "controllers": [CTRL], "sequences": []}))
    return path


def test_light_with_no_sequence_gets_one(tmp_path):
    z = make(tmp_path / "a.zip")
    rep = me.fix_signals(z)
    assert rep["lights_fixed"] == 1 and rep["after"]["never_change"] == 0
    with zipfile.ZipFile(z) as zf:
        data = json.loads(zf.read("levels/x/signals.json"))
        assert zf.read("levels/x/info.json") == b"{}"
    seq = data["sequences"][0]
    assert data["instances"][0]["sequenceId"] == seq["id"] == 8
    assert seq["phases"] == [{"controllerIds": [6], "totalDuration": 19.0}]


def test_level_with_no_signals_file_is_left_alone(tmp_path):
    z = make(tmp_path / "b.zip", with_signals=False)
    before = z.read_bytes()
    assert me.fix_signals(z) is None
    assert z.read_bytes() == before


def test_second_run_changes_nothing(tmp_path):
    z = make(tmp_path / "c.zip")
    me.fix_signals(z)
    once = z.read_bytes()
    assert me.fix_signals(z)["lights_fixed"] == 0
    assert z.read_bytes() == once
