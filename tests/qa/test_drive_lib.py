"""Event detection, metrics summary and G3 on synthetic telemetry. No game."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qa"))
import drive_lib as dl  # noqa: E402

# straight road along +x, 200 m, 10 m points
ROUTE = dl.Route("t", [[float(i * 10), 0.0, 0.0] for i in range(21)], half=3.0)


def drive(fn, seconds=10.0, rate=50.0, mon=None):
    """fn(t) -> dict of overrides of {x,y,z,vx,vy,vz}; default: 10 m/s straight along the route."""
    mon = mon or dl.Monitor(ROUTE)
    n = int(seconds * rate)
    events = []
    for k in range(n + 1):
        t = k / rate
        s = {"t": t, "x": 10.0 * t, "y": 0.0, "z": 0.0, "vx": 10.0, "vy": 0.0, "vz": 0.0, "dx": 1.0, "dy": 0.0,
             "dmg": 0.0}
        s.update(fn(t))
        events += mon.feed(s)
    return mon, events


def kinds(mon):
    return [e["type"] for e in mon.events]


def test_clean_drive_has_no_events():
    mon, _ = drive(lambda t: {})
    assert mon.events == []
    assert mon.max_acc == 0.0 and mon.max_dev < 1e-6
    assert round(mon.progress) == 100


def test_bump_over_7_is_reported_with_position_and_value():
    # vz jumps 1.0 m/s in 0.1 s at t = 5: az 10 m/s2 > 7
    mon, _ = drive(lambda t: {"vz": 1.0 if t >= 5.0 else 0.0})
    b = [e for e in mon.events if e["type"] == "bump"]
    assert len(b) == 1 and b[0]["value"] > 7 and b[0]["x"] == 50
    assert mon.max_acc > 7


def test_bump_under_7_is_not_an_event_but_counts_in_max():
    mon, _ = drive(lambda t: {"vz": 0.5 if t >= 5.0 else 0.0})   # 5 m/s2
    assert "bump" not in kinds(mon)
    assert 3.5 < mon.max_acc < 5.5      # 0.5 m/s over a 0.12 s window


def test_one_event_per_kind_per_two_seconds():
    mon, _ = drive(lambda t: {"vz": (1.0 if int(t * 10) % 2 else 0.0)})   # square wave: many spikes
    ts = [e["t"] for e in mon.events if e["type"] == "bump"]
    assert all(b - a >= 1.9 for a, b in zip(ts, ts[1:])) and len(ts) <= 5


def test_airborne_needs_quarter_second_of_free_fall():
    short = drive(lambda t: {"vz": -9.81 * (t - 4.0) if 4.0 <= t < 4.15 else 0.0})[0]
    assert "airborne" not in kinds(short)
    long = drive(lambda t: {"vz": -9.81 * (t - 4.0) if 4.0 <= t < 4.6 else 0.0})[0]
    assert "airborne" in kinds(long)


def test_off_route_beyond_6_m_plus_half_width():
    near = drive(lambda t: {"y": 8.0})[0]       # 8 m < 6 + 3
    assert "off route" not in kinds(near)
    far = drive(lambda t: {"y": 10.0})[0]       # 10 m > 9
    e = [e for e in far.events if e["type"] == "off route"]
    assert e and e[0]["value"] == 10.0
    assert far.max_dev == 10.0


def test_lane_offset_is_signed_left_positive():
    mon, _ = drive(lambda t: {"y": 1.5})        # heading +x, left is +y
    assert mon.samples[-1]["off"] == 1.5
    lane = dl.lane_summary(mon.offsets)
    assert lane["left"] == lane["samples"] and lane["median_offset_m"] == 1.5
    mon2, _ = drive(lambda t: {"y": -1.5})
    assert dl.lane_summary(mon2.offsets)["right"] > 0


def test_stuck_after_five_seconds_not_near_the_end():
    def f(t):
        return {"x": 40.0, "vx": 0.0} if t >= 1.0 else {}
    mon, _ = drive(f, seconds=10)
    assert "stuck" in kinds(mon) and mon.stop_reason == "stuck"
    s = [e for e in mon.events if e["type"] == "stuck"][0]
    assert 6.0 <= s["t"] <= 7.5


def test_standing_still_at_the_end_is_not_stuck():
    mon = dl.Monitor(ROUTE)
    for k in range(0, 800):
        t = k / 50
        x = min(10.0 * t, 195.0)
        mon.feed({"t": t, "x": x, "y": 0.0, "z": 0.0, "vx": 0.0 if x >= 195 else 10.0, "vy": 0.0, "vz": 0.0,
                  "dx": 1.0, "dy": 0.0, "dmg": 0.0})
    assert "stuck" not in kinds(mon)


def test_fell_under_the_road():
    mon, _ = drive(lambda t: {"z": -4.0} if t > 5 else {})
    assert "fell" in kinds(mon) and mon.stop_reason == "fell"


def test_crash_is_damage_over_50_since_start():
    mon, _ = drive(lambda t: {"dmg": 30.0})
    assert "crash" not in kinds(mon)
    mon, _ = drive(lambda t: {"dmg": 10.0 + (80.0 if t > 4 else 0.0)})
    assert "crash" in kinds(mon) and mon.stop_reason == "crash"
    assert [e for e in mon.events if e["type"] == "crash"][0]["value"] == 80


def test_reversing_for_a_second():
    mon, _ = drive(lambda t: {"vx": -3.0} if t > 3 else {})
    assert "reversing" in kinds(mon)


def test_settle_period_is_not_judged():
    mon, _ = drive(lambda t: {"vz": 5.0} if t < 1.0 else {})
    assert "bump" not in kinds(mon)


def test_teleport_artefact_speed_is_ignored():
    mon, _ = drive(lambda t: {"vx": 500.0, "vz": 300.0} if 4.0 < t < 4.1 else {})
    assert mon.events == []


def test_loop_route_does_not_arrive_at_the_start():
    pts = [[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0], [0, 0, 0]]
    mon = dl.Monitor(dl.Route("loop", [[float(a), float(b), float(c)] for a, b, c in pts]))
    mon.feed({"t": 0.0, "x": 0.0, "y": 0.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0, "dx": 1.0, "dy": 0.0, "dmg": 0})
    mon.feed({"t": 3.0, "x": 5.0, "y": 0.0, "z": 0.0, "vx": 1.0, "vy": 0.0, "vz": 0.0, "dx": 1.0, "dy": 0.0, "dmg": 0})
    assert not mon.arrived()
    mon.feed({"t": 60.0, "x": 1.0, "y": 0.5, "z": 0.0, "vx": 1.0, "vy": 0.0, "vz": 0.0, "dx": 0.0, "dy": -1.0, "dmg": 0})
    mon.progress = 395.0
    assert mon.arrived()


def make_result(rid, **kw):
    mon, _ = drive(kw.pop("fn", lambda t: {}))
    return dl.route_result({"id": rid, "name": rid}, mon, mode="graph", end_reason=kw.pop("end", "arrived"),
                           started_at="x", duration_s=10.0, telemetry_file=f"{rid}.csv")


def test_route_result_and_g3_pass():
    r = make_result("a")
    assert r["completed"] and r["events"] == [] and r["frames"] == 501 and r["frame_rate_hz"] == 50.0
    s = dl.summarise([r, make_result("b")], ["a", "b"])
    assert s["g3"]["pass"] and s["completed"] == 2 and s["routes"] == 2


def test_g3_fails_on_each_kind_of_event():
    for name, fn, end in (("crash", lambda t: {"dmg": 100.0 if t > 4 else 0.0}, "crash"),
                          ("bump", lambda t: {"vz": 1.0 if t >= 5 else 0.0}, "arrived"),
                          ("off", lambda t: {"y": 10.0}, "arrived"),
                          ("stuck", lambda t: {"x": 40.0, "vx": 0.0} if t > 1 else {}, "stuck")):
        r = make_result(name, fn=fn, end=end)
        g = dl.g3([r])
        assert not g["pass"], name
    # a bump under 7 does not fail
    assert dl.g3([make_result("ok", fn=lambda t: {"vz": 0.5 if t >= 5 else 0.0})])["pass"]


def test_incomplete_and_timeout_are_failures_with_an_event():
    r = make_result("slow", end="timeout")
    assert not r["completed"]
    assert {e["type"] for e in r["events"]} >= {"timeout"}
    assert not dl.g3([r])["pass"]


def test_missing_route_fails_g3():
    g = dl.g3([make_result("a")], ["a", "b"])
    assert not g["pass"] and g["failed"] == [{"id": "b", "why": ["not run"]}]
    assert not dl.g3([])["pass"]


def test_summary_counts_events_and_maxima():
    a = make_result("a", fn=lambda t: {"vz": 1.0 if t >= 5 else 0.0})
    b = make_result("b", fn=lambda t: {"y": 10.0})
    s = dl.summarise([a, b])
    assert s["events"]["bump"] == 1 and s["events"]["off route"] >= 1
    assert s["max_bump_ms2"] == a["max_bump_ms2"] and s["max_deviation_m"] == 10.0


def test_telemetry_row_matches_columns_and_compass_heading():
    row = dl.telemetry_row({"t": 1.0, "x": 1.0, "y": 2.0, "z": 3.0, "vx": 0, "vy": 5, "vz": 0, "speed": 5.0,
                            "dx": 1.0, "dy": 0.0, "dmg": 0.0})
    assert len(row) == len(dl.TELEMETRY_COLUMNS)
    assert row[dl.TELEMETRY_COLUMNS.index("heading_deg")] == 90.0      # east
    assert row[dl.TELEMETRY_COLUMNS.index("steer")] == ""


def test_route_timeout_is_mapng_formula():
    assert dl.route_timeout_s(1000, 10) == 1000 / 10 * 2.5 + 20
