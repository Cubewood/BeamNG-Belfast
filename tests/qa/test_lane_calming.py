"""T-311: lane side judged against the lane, traffic calming slowed for and not judged as a bump. No game."""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qa"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import drive_lib as dl  # noqa: E402
import drive_suite as ds  # noqa: E402
import route_lib as rl  # noqa: E402

PATH = [[float(i * 10), 0.0, 0.0] for i in range(21)]     # 200 m east along y = 0


def road(rid, tags, pts):
    return {"id": rid, "tags": tags, "pts": pts}


def run(route, fn, calming=None, seconds=10.0, rate=50.0):
    mon = dl.Monitor(route, calming=calming)
    for k in range(int(seconds * rate) + 1):
        t = k / rate
        s = {"t": t, "x": 10.0 * t, "y": 0.0, "z": 0.0, "vx": 10.0, "vy": 0.0, "vz": 0.0, "dx": 1.0, "dy": 0.0,
             "dmg": 0.0}
        s.update(fn(t))
        mon.feed(s)
    return mon


# ---- lane layout ------------------------------------------------------------------------------

def test_lane_counts():
    assert rl.lane_counts({"oneway": "yes", "lanes": "3"}, True) == (3, 0)
    assert rl.lane_counts({"oneway": "yes"}, True) == (1, 0)
    assert rl.lane_counts({"oneway": "yes", "lanes": "2"}, False) == (0, 2)        # against a one-way road
    assert rl.lane_counts({"oneway": "-1", "lanes": "2"}, False) == (2, 0)         # one-way the way's other end
    assert rl.lane_counts({"junction": "roundabout"}, True) == (1, 0)
    t = {"lanes": "4", "lanes:forward": "3", "lanes:backward": "1", "oneway": "no"}
    assert rl.lane_counts(t, True) == (3, 1) and rl.lane_counts(t, False) == (1, 3)
    assert rl.lane_counts({"lanes": "2"}, True) is None                            # tags give no layout
    assert rl.lane_counts({"lanes": "3"}, True) is None                            # unequal but not stated


def test_lane_divider():
    assert dl.lane_divider(None, 3.0) == 0.0
    assert dl.lane_divider((1, 1), 3.0) == 0.0
    assert dl.lane_divider((3, 1), 4.0) == -2.0       # 3 of 4 lanes ours: divider right of the route line
    assert dl.lane_divider((1, 3), 4.0) == 2.0
    assert dl.lane_divider((2, 0), 3.0) == -3.0       # one way: the right edge


def test_layout_follows_way_direction():
    t = {"lanes": "4", "lanes:forward": "3", "lanes:backward": "1"}
    w = road("a", t, [(0.0, 0.5), (200.0, 0.5)])
    assert rl.lane_layout(PATH, [w])[5] == (3, 1)
    assert rl.lane_layout(PATH[::-1], [w])[5] == (1, 3)


def test_layout_ignores_side_roads_and_far_ways():
    side = road("s", {"oneway": "yes"}, [(100.0, -50.0), (100.0, 50.0)])     # crosses at 90 degrees
    far = road("f", {"oneway": "yes"}, [(0.0, 20.0), (200.0, 20.0)])
    assert rl.lane_layout(PATH, [side, far]) == [None] * 20


def test_layout_ambiguity_and_wrong_way_ways():
    here = road("h", {"oneway": "yes", "lanes": "2"}, [(0.0, 0.2), (200.0, 0.2)])
    opposite = road("o", {"oneway": "yes", "lanes": "2"}, [(200.0, 1.0), (0.0, 1.0)])   # other carriageway, 1 m away
    assert set(rl.lane_layout(PATH, [here, opposite])) == {(2, 0)}      # the wrong-way one-way is no choice
    two_way = road("t", {"lanes": "4", "lanes:forward": "1", "lanes:backward": "3"}, [(0.0, 1.0), (200.0, 1.0)])
    assert set(rl.lane_layout(PATH, [here, two_way])) == {None}         # both fit and differ: no answer
    alone = road("w", {"oneway": "yes"}, [(200.0, 0.2), (0.0, 0.2)])
    assert set(rl.lane_layout(PATH, [alone])) == {(0, 1)}               # only way is the wrong way: say so


def test_lane_side_judged_against_divider():
    # 3 lanes ours of 4, half width 4: divider 2 m right of the route line; the car drives 1 m right of it
    route = dl.Route("r", PATH, half=4.0, layout=[(3, 1)] * 20)
    in_lane = run(route, lambda t: {"y": -1.0})
    lane = dl.lane_summary(in_lane.offsets, in_lane.sides, in_lane.known, in_lane.wrong_side_at)
    assert lane["right"] > 400 and lane["wrong_side"] == 0 and lane["right_explained"] == lane["right"]
    assert lane["layout_samples"] == lane["samples"]
    over = run(route, lambda t: {"y": -2.6})        # 0.6 m over the divider
    lane = dl.lane_summary(over.offsets, over.sides, over.known, over.wrong_side_at)
    assert lane["wrong_side"] > 400 and lane["right_explained"] == 0 and lane["wrong_side_at"]


def test_no_layout_keeps_the_old_rule():
    route = dl.Route("r", PATH, half=4.0)
    mon = run(route, lambda t: {"y": -1.0})
    lane = dl.lane_summary(mon.offsets, mon.sides, mon.known, mon.wrong_side_at)
    assert lane["wrong_side"] == lane["right"] > 400 and lane["layout_samples"] == 0
    assert dl.lane_summary(mon.offsets)["right"] == lane["right"] and "wrong_side" not in dl.lane_summary(mon.offsets)


def test_one_way_road_is_never_wrong_side_inside_its_edges():
    route = dl.Route("r", PATH, half=4.0, layout=[(2, 0)] * 20)
    mon = run(route, lambda t: {"y": -3.0})
    assert dl.lane_summary(mon.offsets, mon.sides)["wrong_side"] == 0
    wrong_way = dl.Route("r", PATH, half=4.0, layout=[(0, 2)] * 20)          # against a one-way road
    mon = run(wrong_way, lambda t: {"y": 0.0})
    assert dl.lane_summary(mon.offsets, mon.sides)["wrong_side"] > 400


# ---- traffic calming --------------------------------------------------------------------------

HUMP = {"id": "h1", "kind": "hump", "pts": [(50.0, 1.0)]}


def jolt(t):    # vertical jolt at t = 4.2 s (x = 42 m), 8 m before the hump
    return {"vz": 9.0 if 4.2 <= t < 4.35 else 0.0}


def test_bump_at_mapped_calming_is_a_note_not_a_bump():
    route = dl.Route("r", PATH, half=3.0)
    plain = run(route, jolt)
    assert [e["type"] for e in plain.events] == ["bump"]
    mon = run(route, jolt, calming=[HUMP])
    types = [e["type"] for e in mon.events]
    assert "bump" not in types and "calming" in types
    ev = next(e for e in mon.events if e["type"] == "calming")
    assert ev["speed"] == 10.0 and "hump h1" in ev["feature"]
    assert mon.max_acc < dl.BUMP_ACC < mon.max_calming_acc
    res = dl.route_result({"id": "r"}, mon, mode="graph", end_reason="arrived", started_at="", duration_s=20, telemetry_file=None)
    assert res["max_bump_ms2"] < dl.BUMP_ACC and res["max_calming_ms2"] > dl.BUMP_ACC
    assert [e["type"] for e in res["events"]] == ["calming"] and dl.g3([res])["pass"]
    assert "calming" not in dl.G3_FAIL_EVENTS


def test_bump_away_from_calming_still_fails():
    far = {"id": "h2", "kind": "hump", "pts": [(150.0, 1.0)]}
    mon = run(dl.Route("r", PATH, half=3.0), jolt, calming=[far])
    assert [e["type"] for e in mon.events] == ["bump"]


def test_calming_way_geometry_counts():
    crossing = {"id": "c", "kind": "table", "pts": [(40.0, -6.0), (40.0, 0.0), (40.0, 6.0)]}
    mon = dl.Monitor(dl.Route("r", PATH), calming=[crossing])
    assert mon.calming_at(45.0, 0.0) and not mon.calming_at(60.0, 0.0)


def test_calming_wp_speeds_brackets_the_feature():
    dump = {"nodes": [[f"n{i}", i * 30.0, 0.0, 0.0, 3.0] for i in range(6)],
            "links": [[f"n{i}", f"n{i + 1}", 0, 0, 1, 20, "+-"] for i in range(5)]}
    g = rl.Graph(dump)
    path = [f"n{i}" for i in range(6)]
    on = {"id": "x", "kind": "hump", "pts": [(70.0, 1.0)]}                     # between n2 (60) and n3 (90)
    assert rl.calming_wp_speeds(g, path, [on], 4.5) == {"n2": 4.5, "n3": 4.5}
    off = {"id": "y", "kind": "hump", "pts": [(70.0, 30.0)]}                   # not on the route
    assert rl.calming_wp_speeds(g, path, [off], 4.5) == {}
    long_edge = {"id": "z", "kind": "hump", "pts": [(135.0, 0.0)]}             # 15 m from n4 and from n5
    assert set(rl.calming_wp_speeds(g, path, [long_edge], 4.5)) == {"n4", "n5"}
    assert rl.calming_wp_speeds(g, path, [on], 4.5, node_m=15.0) == {"n2": 4.5}      # a farther node is left alone


def test_script_points_slow_over_calming():
    route = {"path": PATH, "junctions": [], "half_width_m": 3.0}
    text = ds.script_points(route, 13.4, [HUMP])
    pts = [dict(kv.split("=") for kv in seg.strip("{}").split(",")) for seg in text.split("},{")]
    slow = [float(p["v"]) for p, q in zip(pts, PATH) if abs(q[0] - 50.0) <= dl.CALMING_ZONE_M and q[0] > 10]
    assert slow and max(slow) <= dl.CALMING_SPEED_MS
    fast = [float(p["v"]) for p, q in zip(pts, PATH) if abs(q[0] - 50.0) > dl.CALMING_ZONE_M + 12 and q[0] < 100]
    assert fast and min(fast) > dl.CALMING_SPEED_MS


# ---- OSM loading and replay -------------------------------------------------------------------

def osm_file(tmp_path):
    lat0, lon0 = 54.5428, -5.9300
    lat, lon = rl.xy_to_latlon(50.0, 1.0, lat0, lon0)
    lat2, lon2 = rl.xy_to_latlon(150.0, 1.0, lat0, lon0)
    items = [
        {"id": "1", "type": "traffic_calming", "geometry": [{"lat": lat, "lng": lon}], "tags": {"traffic_calming": "hump"}},
        {"id": "2_seg_0", "type": "road", "geometry": [{"lat": lat, "lng": lon}, {"lat": lat2, "lng": lon2}],
         "tags": {"highway": "tertiary", "oneway": "yes", "lanes": "2", "traffic_calming": "table"}},
        {"id": "3_seg_0", "type": "road", "geometry": [{"lat": lat, "lng": lon}, {"lat": lat2, "lng": lon2}],
         "tags": {"highway": "footway"}},
    ]
    f = tmp_path / "osm.json"
    f.write_text(json.dumps(items), encoding="utf-8")
    return f, lat0, lon0


def test_load_osm(tmp_path):
    f, lat0, lon0 = osm_file(tmp_path)
    roads = rl.load_osm_roads(f, lat0, lon0)
    assert [r["id"] for r in roads] == ["2_seg_0"]                    # the footway is not drivable
    assert math.hypot(roads[0]["pts"][0][0] - 50.0, roads[0]["pts"][0][1] - 1.0) < 1.5
    cal = rl.load_calming(f, lat0, lon0)
    assert sorted(c["kind"] for c in cal) == ["hump", "table"] and len(cal[0]["pts"]) in (1, 2)
    ctx = ds.OsmContext(f, lat0, lon0)
    assert ctx.layout({"path": [[50 + i * 10.0, 1.0, 0.0] for i in range(10)]})[0] == (2, 0)
    assert ds.OsmContext(tmp_path / "missing.json", lat0, lon0).layout({"path": PATH}) is None


def test_telemetry_has_lane_off_column():
    s = {"t": 0.0, "x": 0.0, "y": 0.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0, "off": -1.0, "lane_off": 0.5}
    row = dl.telemetry_row(s)
    assert len(row) == len(dl.TELEMETRY_COLUMNS)
    assert row[dl.TELEMETRY_COLUMNS.index("lane_off")] == 0.5
