"""Junction approach derivation for the camera sweep (T-303). No game."""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qa"))
import junction_sweep as js  # noqa: E402

ROUTES = Path(__file__).resolve().parents[2] / "qa" / "routes" / "belfast_slice.json"


def doc(junction_at=(100.0,)):
    """A 200 m road heading north (+y), half width 3, with junctions at the given distances."""
    return {"routes": [{"id": "r1", "name": "north", "half_width_m": 3.0,
                        "path": [[0.0, float(i * 10), 5.0] for i in range(21)],
                        "junctions": [{"x": 0.0, "y": a, "at_m": a, "from": "A", "to": "B", "turn": "straight",
                                       "angle": 0, "features": [], "branches": 3} for a in junction_at]}]}


def test_two_shots_per_junction_in_the_left_lane():
    shots = js.derive_shots(doc((100.0, 150.0)))
    assert [s["id"] for s in shots] == ["r1-J01-30m", "r1-J01-10m", "r1-J02-30m", "r1-J02-10m"]
    s30 = shots[0]["intended"]
    assert math.isclose(s30["y"], 70.0, abs_tol=1e-6)
    assert math.isclose(s30["x"], -1.5, abs_tol=1e-6)          # heading north: left is -x, half width 3 x 0.5
    assert s30["heading_deg"] == 0.0
    assert shots[1]["intended"]["y"] == 90.0
    assert not shots[0]["clamped"]


def test_short_approach_is_clamped_to_the_route_start():
    s30, s10 = js.derive_shots(doc((22.0,)))
    assert s30["clamped"] and s30["actual_distance_m"] == 22.0 and s30["intended"]["y"] == 0.0
    assert not s10["clamped"] and s10["intended"]["y"] == 12.0


def test_one_way_carriageway_uses_left_lane_of_n():
    graph = {"nodes": [["a", 0.0, 0.0, 5.0, 3.0], ["b", 0.0, 200.0, 5.0, 3.0]],
             "links": [["a", "b", 1, 1, 1.0, 13.0, "++"], ["b", "a", 1, 0, 1.0, 13.0, "++"]]}
    s = js.derive_shots(doc(), graph_dump=graph)[0]["intended"]
    assert s["one_way"] and s["one_way_lanes"] == 2 and math.isclose(s["lane_offset_m"], 1.5)
    graph["links"] = [["a", "b", 1, 1, 1.0, 13.0, "+"], ["b", "a", 1, 0, 1.0, 13.0, "+"]]
    s = js.derive_shots(doc(), graph_dump=graph)[0]["intended"]
    assert s["one_way"] and s["lane_offset_m"] == 0.0


def test_one_way_link_across_the_road_is_ignored():
    graph = {"nodes": [["a", -20.0, 70.0, 5.0, 3.0], ["b", 20.0, 70.0, 5.0, 3.0]],
             "links": [["a", "b", 1, 1, 1.0, 13.0, "++"]]}
    assert not js.derive_shots(doc(), graph_dump=graph)[0]["intended"]["one_way"]


def test_real_routes_every_junction_has_both_distances():
    d = json.loads(ROUTES.read_text(encoding="utf-8"))
    shots = js.derive_shots(d)
    n = sum(len(r["junctions"]) for r in d["routes"])
    assert len(shots) == 2 * n
    assert len({s["id"] for s in shots}) == len(shots)
    assert all(s["clamped"] == (s["at_m"] < s["distance_m"]) for s in shots)


def test_blocked_hours():
    import datetime as dt
    assert js.in_blocked_hours(dt.datetime(2026, 11, 13, 9, 0))
    assert js.in_blocked_hours(dt.datetime(2026, 11, 13, 17, 59))
    assert not js.in_blocked_hours(dt.datetime(2026, 11, 13, 18, 0))
    assert not js.in_blocked_hours(dt.datetime(2026, 11, 13, 8, 59))
