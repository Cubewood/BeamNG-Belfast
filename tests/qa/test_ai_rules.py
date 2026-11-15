"""T-407: AI rules judged from recorded data (roundabout circulation, one-way roads, light sequence, AI at a
signal) and the signal-data fix. No game."""
import json
import math
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qa"))
sys.path.insert(0, str(ROOT / "pipeline"))
import ai_rules_lib as lib  # noqa: E402
import signals_fix as sf  # noqa: E402

RED, RED_AMBER, GREEN, AMBER = lib.RED, lib.RED_AMBER, lib.GREEN, lib.AMBER


# ---------------------------------------------------------------- roundabouts

def ring(centre, r, a0, a1, n=60, clockwise=True):
    out = []
    for k in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * k / n)
        out.append({"x": centre[0] + r * math.cos(a), "y": centre[1] + r * math.sin(a)})
    return out


def test_clockwise_circuit_is_clockwise():
    # angle falls from 140 to -80 degrees: clockwise
    res = lib.swept_angle_deg(ring((0, 0), 11, 140, -80), (0, 0), 17)
    assert res["direction"] == "clockwise" and -225 < res["sweep_deg"] < -215


def test_anticlockwise_circuit_is_flagged():
    res = lib.swept_angle_deg(ring((0, 0), 11, -80, 140), (0, 0), 17)
    assert res["direction"] == "anticlockwise" and res["sweep_deg"] > 215


def test_passing_by_is_not_circulation():
    straight = [{"x": float(x), "y": 30.0} for x in range(-40, 41)]
    assert lib.swept_angle_deg(straight, (0, 0), 17)["direction"] == "none"


def test_sweep_wraps_through_pi_and_ignores_outside():
    res = lib.swept_angle_deg(ring((5, 5), 10, 170, 250), (5, 5), 17)     # crosses +-180
    assert res["direction"] == "anticlockwise" and 75 < res["sweep_deg"] < 85
    far = lib.swept_angle_deg(ring((5, 5), 40, 0, 200), (5, 5), 17)
    assert far["samples"] == 0


def test_ring_direction_shoelace():
    pts = [(math.cos(math.radians(a)), math.sin(math.radians(a))) for a in range(0, 360, 30)]
    assert lib.ring_direction(pts) == "anticlockwise"
    assert lib.ring_direction(pts[::-1]) == "clockwise"


# ---------------------------------------------------------------- one-way roads

def dump(links):
    nodes = {}
    out_links = []
    for a, b, ow in links:
        nodes[a[0]] = a
        nodes[b[0]] = b
        out_links.append([a[0], b[0], 1 if ow else 0, 1, 1.0, 13.0, ""])
    return {"nodes": [[n[0], n[1], n[2], 0.0, 3.0] for n in nodes.values()], "links": out_links}


def drive_east(speed=10.0, y=0.0, x0=5.0, x1=95.0):
    return [{"t": i * 0.1, "x": x0 + i, "y": y, "vx": speed, "vy": 0.0} for i in range(int(x1 - x0))]


def test_wrong_way_on_one_way_edge_is_counted():
    idx = lib.OneWayIndex(dump([(("a", 0.0, 0.0), ("b", 100.0, 0.0), True)]))     # a -> b is east
    assert idx.one_way_count == 1
    ok = lib.wrong_way(drive_east(), idx)
    assert ok["on_one_way"] > 20 and ok["wrong_way"] == 0
    west = [{**s, "vx": -s["vx"]} for s in drive_east()]
    bad = lib.wrong_way(west, idx)
    assert bad["wrong_way"] == bad["on_one_way"] > 20 and bad["first_at"] is not None


def test_two_way_and_crossing_roads_do_not_count_against_a_car():
    two = lib.OneWayIndex(dump([(("a", 0.0, 0.0), ("b", 100.0, 0.0), False)]))
    west = [{**s, "vx": -s["vx"]} for s in drive_east()]
    assert lib.wrong_way(west, two) == {"on_one_way": 0, "wrong_way": 0, "first_at": None}
    # a one-way road crossing at right angles: the car runs along the two-way road, not against the crossing one
    both = lib.OneWayIndex(dump([(("a", 0.0, 0.0), ("b", 100.0, 0.0), False), (("c", 50.0, -50.0), ("d", 50.0, 50.0), True)]))
    assert lib.wrong_way(west, both)["wrong_way"] == 0
    # slow cars are ignored
    idx = lib.OneWayIndex(dump([(("a", 0.0, 0.0), ("b", 100.0, 0.0), True)]))
    assert lib.wrong_way(drive_east(speed=0.5), idx)["on_one_way"] == 0


# ---------------------------------------------------------------- the light sequence

UK = [(0.0, RED), (1.5, RED_AMBER), (3.0, GREEN), (15.0, AMBER), (19.0, RED), (39.0, RED_AMBER), (40.5, GREEN)]


def test_uk_order_passes_and_measures_durations():
    r = lib.check_order(UK)
    assert r["bad_transitions"] == [] and not r["never_changes"] and r["cycles"] == 2
    assert r["durations_s"]["greenTrafficLight"] == {"min": 12.0, "max": 12.0, "n": 1}
    assert r["durations_s"]["yellowTrafficLight"]["min"] == 4.0


def test_wrong_order_is_reported():
    # US order: green, yellow, red, green (no red and amber)
    r = lib.check_order([(0.0, GREEN), (10.0, AMBER), (14.0, RED), (30.0, GREEN)])
    assert [b["to"] for b in r["bad_transitions"]] == ["greenTrafficLight"]
    # amber straight to green
    assert lib.check_order([(0.0, AMBER), (3.0, GREEN)])["bad_transitions"]


def test_a_head_that_never_changes_is_flagged():
    assert lib.check_order([(0.0, RED)])["never_changes"]
    assert lib.check_order([])["never_changes"]


def test_parse_transitions():
    got = lib.parse_transitions("2.0,5,2,1.0;1.0,5,0,0.0;1.5,6,3,0.5;junk")
    assert got == {5: [(1.0, 0), (2.0, 2)], 6: [(1.5, 3)]}


def test_sequence_conflicts_detects_two_open_controllers():
    a = [(0.0, RED), (1.0, RED_AMBER), (2.5, GREEN), (14.5, AMBER), (18.5, RED)]
    b_ok = [(0.0, RED), (20.0, RED_AMBER), (21.5, GREEN)]
    assert lib.sequence_conflicts({1: a, 2: b_ok}) == []
    b_bad = [(0.0, RED), (10.0, GREEN), (30.0, RED)]
    got = lib.sequence_conflicts({1: a, 2: b_bad})
    assert got and got[0]["controllers"] == [1, 2]


def test_red_age():
    tl = [(0.0, GREEN), (5.0, AMBER), (9.0, RED), (10.5, RED_AMBER), (12.0, GREEN)]
    assert lib.red_age(tl, 8.0) is None
    assert abs(lib.red_age(tl, 11.0) - 2.0) < 1e-9
    assert lib.red_age(tl, 13.0) is None


# ---------------------------------------------------------------- the AI at a signal

SIGNAL = {"pos": (100.0, 0.0), "dir": (1.0, 0.0)}


def run_car(stop_at=None, stop_s=0.0, speed=8.0):
    """A car driving east from x = 0, optionally standing still at stop_at for stop_s seconds."""
    rows, x, t = [], 0.0, 0.0
    while x < 130:
        if stop_at is not None and x >= stop_at and stop_s > 0:
            for _ in range(int(stop_s / 0.1)):
                rows.append({"t": t, "x": x, "y": 0.0, "vx": 0.0, "vy": 0.0})
                t += 0.1
            stop_s = 0.0
        rows.append({"t": t, "x": x, "y": 0.0, "vx": speed, "vy": 0.0})
        x += speed * 0.1
        t += 0.1
    return rows


def test_car_that_stops_at_red_and_goes_on_green_is_fine():
    tl = [(0.0, RED), (30.0, RED_AMBER), (31.5, GREEN)]
    rows = run_car(stop_at=95.0, stop_s=20.0)
    # the stop happens around t = 12; push the green to 30 s and the car's go to after it
    for r in rows:
        if r["t"] > 12:
            r["t"] += 18.0
    res = lib.judge_approach(rows, SIGNAL, tl)
    assert res["crossed"] and res["stopped_at_red"] and not res["ran_red"]
    assert 4 < res["stop_dist_m"] < 7


def test_car_through_a_red_is_flagged():
    tl = [(0.0, RED)]
    res = lib.judge_approach(run_car(), SIGNAL, tl)
    assert res["crossed"] and res["ran_red"] and res["cross"]["state"] == "redTrafficLight"


def test_car_through_green_or_amber_is_not_flagged():
    for code in (GREEN, AMBER):
        res = lib.judge_approach(run_car(), SIGNAL, [(0.0, code)])
        assert res["crossed"] and not res["ran_red"]


def test_a_red_that_has_just_come_on_is_not_blamed():
    tl = [(0.0, AMBER), (12.0, RED)]     # the car reaches the plane at t of about 12.6
    res = lib.judge_approach(run_car(), SIGNAL, tl)
    assert res["crossed"] and res["cross"]["red_age_s"] < lib.RED_GRACE_S and not res["ran_red"]


def test_car_that_never_arrives_is_not_a_crossing():
    rows = [r for r in run_car() if r["x"] < 80]
    res = lib.judge_approach(rows, SIGNAL, [(0.0, RED)])
    assert not res["crossed"] and not res["ran_red"] and res["end_dist_m"] > 15


def test_a_signal_for_the_other_road_is_not_crossed():
    far = {"pos": (100.0, 60.0), "dir": (1.0, 0.0)}
    assert not lib.judge_approach(run_car(), far, [(0.0, RED)])["crossed"]


# ---------------------------------------------------------------- approach start

def test_approach_start_walks_back_up_the_road():
    pos = {"a": (0.0, 0.0), "b": (30.0, 0.0), "c": (60.0, 0.0), "d": (90.0, 0.0), "side": (60.0, -30.0), "e": (120.0, 0.0)}
    adj = {"a": [("b", 30.0)], "b": [("c", 30.0)], "c": [("d", 30.0)], "side": [("c", 30.0)], "d": [("e", 30.0)], "e": []}
    chain, p0, p1 = lib.approach_start(adj, pos, "d", "e", back_m=60.0)
    assert chain == ["b", "c", "d"] and p0 == (30.0, 0.0) and p1 == (60.0, 0.0)     # straight on, not the side road
    assert lib.approach_start({"x": [("y", 10.0)], "y": []}, {"x": (0.0, 0.0), "y": (10.0, 0.0)}, "y", None) is None   # 10 m is too short


def test_approach_start_stops_at_the_last_junction():
    # c has a way off to "away": a car started before c could turn there, so the start is c itself
    pos = {"a": (0.0, 0.0), "b": (30.0, 0.0), "c": (60.0, 0.0), "d": (90.0, 0.0), "away": (60.0, 40.0), "e": (120.0, 0.0)}
    adj = {"a": [("b", 30.0)], "b": [("c", 30.0)], "c": [("d", 30.0), ("away", 40.0)], "d": [("e", 30.0)], "away": [], "e": []}
    chain, p0, _ = lib.approach_start(adj, pos, "d", "e", back_m=100.0)
    assert chain == ["c", "d"] and p0 == (60.0, 0.0)
    # a signal's own node may be a junction: the walk reaches it all the same
    adj["d"].append(("away", 50.0))
    assert lib.approach_start(adj, pos, "d", "e", back_m=100.0)[0] == ["c", "d"]


# ---------------------------------------------------------------- the signal data fix

def controller(cid, name="c"):
    return {"id": cid, "name": f"{name}{cid}", "type": "lightsBasicEU", "isSimple": False, "defaultIndex": 4,
            "states": [{"state": "redYellowTrafficLight", "duration": 1.5}, {"state": "greenTrafficLight", "duration": 12},
                       {"state": "yellowTrafficLight", "duration": 4}, {"state": "redTrafficLight", "duration": 1.5}]}


def level_signals():
    return {
        "instances": [
            {"id": 4, "name": "trafficLight 4", "controllerId": 1, "sequenceId": 3, "pos": [0, 0, 0], "dir": [1, 0, 0]},
            {"id": 5, "name": "trafficLight 5", "controllerId": 2, "sequenceId": 3, "pos": [5, 0, 0], "dir": [0, 1, 0]},
            {"id": 7, "name": "trafficLight 7", "controllerId": 6, "sequenceId": 0, "pos": [50, 0, 0], "dir": [1, 0, 0]},
            {"id": 8, "name": "trafficLight 8", "controllerId": 6, "sequenceId": 0, "pos": [60, 0, 0], "dir": [-1, 0, 0]},
            {"id": 9, "name": "stop 9", "controllerId": 10, "sequenceId": 0, "pos": [90, 0, 0], "dir": [1, 0, 0]},
        ],
        "controllers": [controller(1), controller(2), controller(6),
                        {"id": 10, "name": "stop", "type": "signStop", "isSimple": True, "states": [{"state": "basicStop", "duration": -1}]}],
        "sequences": [{"id": 3, "name": "trafficLights_cluster_1", "startTime": 0, "startDisabled": False, "ignoreTimer": False,
                       "phases": [{"controllerIds": [1], "totalDuration": 19}, {"controllerIds": [2], "totalDuration": 19}]}],
    }


def test_analyse_finds_lights_with_no_sequence_but_not_stop_signs():
    res = sf.analyse(level_signals())
    assert [e["instance"] for e in res["never_change"]] == [7, 8]
    assert res["not_uk_order"] == [] and res["short_phase"] == []


def test_fix_gives_one_sequence_to_a_controller_and_leaves_the_rest():
    data = level_signals()
    new, rep = sf.fix(data)
    assert rep == {"lights_fixed": 2, "sequences_added": 1, "controllers": [6]}
    seq = new["sequences"][-1]
    assert seq["id"] == 11 and seq["phases"] == [{"controllerIds": [6], "totalDuration": 19.0}]
    assert {i["id"]: i["sequenceId"] for i in new["instances"]} == {4: 3, 5: 3, 7: 11, 8: 11, 9: 0}
    assert sf.analyse(new)["never_change"] == []
    assert data["sequences"].__len__() == 1               # the input is not changed
    assert sf.fix(new)[1]["lights_fixed"] == 0            # and a second run changes nothing


def test_uk_order_check_accepts_rotation_and_rejects_other_orders():
    uk = ["redYellowTrafficLight", "greenTrafficLight", "yellowTrafficLight", "redTrafficLight"]
    assert sf.is_uk_cycle(uk) and sf.is_uk_cycle(uk[2:] + uk[:2])
    assert not sf.is_uk_cycle(["greenTrafficLight", "yellowTrafficLight", "redTrafficLight"])         # US
    assert not sf.is_uk_cycle(["redTrafficLight", "greenTrafficLight", "yellowTrafficLight", "redYellowTrafficLight"])
    d = level_signals()
    d["controllers"][0]["states"][0], d["controllers"][0]["states"][1] = d["controllers"][0]["states"][1], d["controllers"][0]["states"][0]
    assert [e["controller"] for e in sf.analyse(d)["not_uk_order"]] == [1]


def test_short_phase_is_reported():
    d = level_signals()
    d["sequences"][0]["phases"][0]["totalDuration"] = 10
    assert sf.analyse(d)["short_phase"] == [{"sequence": 3, "phase": 1, "totalDuration": 10, "needs": 19.0}]


def test_fix_zip_rewrites_only_signals_json(tmp_path):
    src = tmp_path / "in.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("levels/x/signals.json", json.dumps(level_signals()))
        z.writestr("levels/x/info.json", "{}")
    dst = tmp_path / "out.zip"
    rep = sf.fix_zip(src, dst)
    assert rep["lights_fixed"] == 2 and rep["after"]["never_change"] == 0
    with zipfile.ZipFile(dst) as z, zipfile.ZipFile(src) as o:
        assert z.read("levels/x/info.json") == b"{}"
        assert len(json.loads(z.read("levels/x/signals.json"))["sequences"]) == 2
        assert len(json.loads(o.read("levels/x/signals.json"))["sequences"]) == 1     # the source is untouched
    # in place, with nothing to fix: the file is not rewritten
    assert sf.fix_zip(dst)["lights_fixed"] == 0


def test_a_car_on_a_side_road_twelve_metres_over_is_not_at_the_signal():
    side = {"pos": (100.0, 12.0), "dir": (1.0, 0.0)}
    assert not lib.judge_approach(run_car(), side, [(0.0, RED)])["crossed"]


# ---------------------------------------------------------------- lane side

def test_lane_side_left_is_left_of_travel():
    idx = lib.OneWayIndex(dump([(("a", 0.0, 0.0), ("b", 100.0, 0.0), False)]))
    east_left = [{**s, "y": 1.4} for s in drive_east()]              # driving east, 1.4 m north: left of travel
    res = lib.lane_side(east_left, idx)
    assert res["right"] == 0 and res["left"] > 20 and res["median_off_m"] == 1.4
    west_left = [{**s, "vx": -s["vx"], "y": -1.4} for s in drive_east()]    # driving west, 1.4 m south: also left
    assert lib.lane_side(west_left, idx)["left"] > 20
    right = [{**s, "y": -1.4} for s in drive_east()]
    assert lib.lane_side(right, idx)["right"] > 20
    assert lib.lane_side(drive_east(), idx)["centre"] > 20


def test_lane_side_ignores_one_way_edges_and_slow_cars():
    one = lib.OneWayIndex(dump([(("a", 0.0, 0.0), ("b", 100.0, 0.0), True)]))
    assert lib.lane_side([{**s, "y": 1.4} for s in drive_east()], one)["left"] == 0
    two = lib.OneWayIndex(dump([(("a", 0.0, 0.0), ("b", 100.0, 0.0), False)]))
    assert lib.lane_side([{**s, "y": 1.4} for s in drive_east(speed=0.5)], two)["left"] == 0
