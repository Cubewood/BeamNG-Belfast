"""T-407: the static checks for traffic signals and roundabouts, and the one-way check's end-to-end case.
Synthetic levels built by hand (no zip of a real level, no game)."""
import json
import math
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qa"))
sys.path.insert(0, str(ROOT / "pipeline"))
from static_checks import checks as ck  # noqa: E402
from static_checks.level import DecalRoad, Level, OsmFeature  # noqa: E402

C = ck.cfg(None)
NAME = "lvl"


def ctrl(cid, order=None):
    order = order or ["redYellowTrafficLight", "greenTrafficLight", "yellowTrafficLight", "redTrafficLight"]
    return {"id": cid, "name": f"c{cid}", "type": "lightsBasicEU", "isSimple": False, "defaultIndex": 4,
            "states": [{"state": s, "duration": 3} for s in order]}


def level(tmp_path, signals=None, osm=(), roads=()):
    z = tmp_path / "l.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(f"levels/{NAME}/info.json", "{}")
        if signals is not None:
            zf.writestr(f"levels/{NAME}/signals.json", json.dumps(signals))
    return Level(z, NAME, {}, 512.0, list(roads), [], 0, None, list(osm), "", lambda a, b: (a, b), zipfile.ZipFile(z))


def head(i, c, s, x, y=0.0):
    return {"id": i, "name": f"trafficLight {i}", "controllerId": c, "sequenceId": s, "pos": [x, y, 0], "dir": [1, 0, 0]}


def sig_node(osm_id, x, y):
    return OsmFeature(str(osm_id), "street_furniture", {"highway": "traffic_signals"}, np.array([[x, y]]), [])


def good():
    return {"instances": [head(1, 10, 20, 0.0), head(2, 11, 20, 10.0)],
            "controllers": [ctrl(10), ctrl(11)],
            "sequences": [{"id": 20, "name": "s", "phases": [{"controllerIds": [10], "totalDuration": 12},
                                                              {"controllerIds": [11], "totalDuration": 12}]}]}


def kinds(found):
    return sorted(f["kind"] for f in found)


def test_good_signals_have_no_findings(tmp_path):
    assert ck.check_signals(level(tmp_path, good(), osm=[sig_node(1, 1.0, 0.0)]), C) == []


def test_light_with_no_sequence_is_red_for_ever(tmp_path):
    d = good()
    d["instances"].append(head(3, 12, 0, 80.0, 5.0))
    d["controllers"].append(ctrl(12))
    found = ck.check_signals(level(tmp_path, d), C)
    assert kinds(found) == ["signal-never-changes"] and found[0]["x"] == 80.0 and found[0]["severity"] == "high"


def test_stop_sign_without_sequence_is_fine(tmp_path):
    d = good()
    d["instances"].append(head(3, 13, 0, 80.0))
    d["controllers"].append({"id": 13, "name": "stop", "type": "signStop", "isSimple": True, "states": [{"state": "basicStop", "duration": -1}]})
    assert ck.check_signals(level(tmp_path, d), C) == []


def test_us_order_is_not_uk(tmp_path):
    d = good()
    d["controllers"][0] = ctrl(10, ["greenTrafficLight", "yellowTrafficLight", "redTrafficLight"])
    assert kinds(ck.check_signals(level(tmp_path, d), C)) == ["signal-not-uk-order"]


def test_phase_shorter_than_its_controller(tmp_path):
    d = good()
    d["sequences"][0]["phases"][1]["totalDuration"] = 5
    assert kinds(ck.check_signals(level(tmp_path, d), C)) == ["signal-short-phase"]


def test_osm_signal_with_no_head_near_is_missing(tmp_path):
    found = ck.check_signals(level(tmp_path, good(), osm=[sig_node(7, 200.0, 0.0), sig_node(8, 5.0, 0.0)]), C)
    assert kinds(found) == ["signal-missing"] and "7" in found[0]["where"]


def test_osm_signals_and_no_signals_file(tmp_path):
    found = ck.check_signals(level(tmp_path, None, osm=[sig_node(7, 0.0, 0.0)]), C)
    assert kinds(found) == ["signals-file-missing"]
    assert ck.check_signals(level(tmp_path, None), C) == []


# ---------------------------------------------------------------- roundabouts

def circle(r, clockwise=True, n=24, cx=0.0, cy=0.0):
    pts = []
    for k in range(n + 1):
        a = -2 * math.pi * k / n if clockwise else 2 * math.pi * k / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return np.array(pts)


def ring_feature(pts):
    return OsmFeature("77_seg_0", "road", {"highway": "tertiary", "junction": "roundabout"}, pts, [])


def ring_road(pts, one_way=True, flip=False, name="dr_ring_s1_ai"):
    nodes = np.array([[p[0], p[1], 0.0, 6.0] for p in pts])
    props = {"drivability": 1.0}
    if one_way:
        props["oneWay"] = True
    if flip:
        props["flipDirection"] = True
    return DecalRoad(name, "road_invisible", nodes, props)


def test_clockwise_drawn_ring_is_fine(tmp_path):
    pts = circle(12.0, True)
    assert ck.check_roundabouts(level(tmp_path, osm=[ring_feature(pts)], roads=[ring_road(pts)]), C) == []


def test_anticlockwise_osm_ring_is_reported(tmp_path):
    pts = circle(12.0, False)
    found = ck.check_roundabouts(level(tmp_path, osm=[ring_feature(pts)], roads=[ring_road(pts)]), C)
    assert kinds(found) == ["roundabout-osm-anticlockwise"]


def test_ring_drawn_the_wrong_way_or_two_way_or_not_at_all(tmp_path):
    pts = circle(12.0, True)
    assert kinds(ck.check_roundabouts(level(tmp_path, osm=[ring_feature(pts)], roads=[ring_road(pts, flip=True)]), C)) == ["roundabout-wrong-way"]
    assert kinds(ck.check_roundabouts(level(tmp_path, osm=[ring_feature(pts)], roads=[ring_road(pts, one_way=False)]), C)) == ["roundabout-two-way"]
    found = ck.check_roundabouts(level(tmp_path, osm=[ring_feature(pts)], roads=[]), C)
    assert kinds(found) == ["roundabout-not-drawn"] and "radius about 12" in found[0]["detail"]


def test_a_ring_far_from_any_road_is_not_drawn(tmp_path):
    pts = circle(12.0, True)
    far = circle(12.0, True, cx=100.0)
    found = ck.check_roundabouts(level(tmp_path, osm=[ring_feature(pts)], roads=[ring_road(far)]), C)
    assert kinds(found) == ["roundabout-not-drawn"]


# ---------------------------------------------------------------- one-way: a stub and the road beside it

def test_one_way_stub_next_to_a_two_way_road_is_not_a_missing_one_way(tmp_path):
    """OSM way 1410878962 on the Slice: a 19 m one-way way drawn as a one-way road that starts where a two-way road
    ends. Both roads fit the way's end sample; the old check took the two-way one and reported oneway-missing."""
    way = OsmFeature("5_seg_0", "road", {"highway": "tertiary", "oneway": "yes", "name": "Stub"},
                     np.array([[0.0, 0.0], [-19.0, -3.0]]), [])
    stub = DecalRoad("dr_stub_s1_ai", "road_invisible", np.array([[0.0, 0.0, 0, 6.0], [-19.0, -3.0, 0, 6.0]]),
                     {"drivability": 1.0, "oneWay": True})
    other = DecalRoad("dr_main_s1_ai", "road_invisible", np.array([[35.0, 3.0, 0, 6.0], [0.0, 0.2, 0, 6.0]]),
                      {"drivability": 1.0})
    assert ck.check_oneway(level(tmp_path, osm=[way], roads=[other, stub]), C) == []
    # without the one-way stub the way is not drawn one-way: still found
    bare = DecalRoad("dr_stub_s1_ai", "road_invisible", np.array([[0.0, 0.0, 0, 6.0], [-19.0, -3.0, 0, 6.0]]), {"drivability": 1.0})
    assert kinds(ck.check_oneway(level(tmp_path, osm=[way], roads=[other, bare]), C)) == ["oneway-missing"]
