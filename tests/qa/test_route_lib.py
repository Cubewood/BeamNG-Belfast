"""Route graph, resolution, junctions, route file validation and the drive_suite helpers. No game."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qa"))
sys.path.insert(0, str(ROOT / "qa" / "routes"))
sys.path.insert(0, str(ROOT / "tools"))
import build_routes as br  # noqa: E402
import route_lib as rl  # noqa: E402


def node(name, x, y):
    return [name, float(x), float(y), 0.0, 3.0]


def link(a, b, one_way=0, a_is_in=0):
    return [a, b, one_way, a_is_in, 0.9, 16.7, "+-"]


# a plus-shaped network: Main Street west-east through J, Side Road north-south through J,
# a one-way spur east of Main Street that cannot be driven back, and an unconnected island.
DUMP = {
    "nodes": [node("dr_Main_Street_s1_ai1", -100, 0), node("dr_Main_Street_s1_ai2", 0, 0),
              node("dr_Main_Street_s1_ai3", 100, 0), node("dr_Side_Road_s1_ai1", 0, 100),
              node("dr_Side_Road_s1_ai2", 0, -100), node("dr_Spur_s1_ai1", 200, 0),
              node("dr_Island_s1_ai1", 500, 500), node("dr_Island_s1_ai2", 600, 500)],
    "links": [link("dr_Main_Street_s1_ai1", "dr_Main_Street_s1_ai2"), link("dr_Main_Street_s1_ai2", "dr_Main_Street_s1_ai3"),
              link("dr_Side_Road_s1_ai1", "dr_Main_Street_s1_ai2"), link("dr_Side_Road_s1_ai2", "dr_Main_Street_s1_ai2"),
              # one way: travel from ai3 to the spur only (stored on the exit node, entry = the other node)
              link("dr_Spur_s1_ai1", "dr_Main_Street_s1_ai3", one_way=1, a_is_in=0),
              link("dr_Island_s1_ai1", "dr_Island_s1_ai2")],
}


def graph():
    return rl.Graph(DUMP)


def test_graph_edges_are_directed_for_one_way():
    g = graph()
    assert g.one_way_edges == 1
    assert g.shortest("dr_Main_Street_s1_ai3", "dr_Spur_s1_ai1") is not None
    assert g.shortest("dr_Spur_s1_ai1", "dr_Main_Street_s1_ai3") is None
    assert g.shortest("dr_Main_Street_s1_ai1", "dr_Island_s1_ai1") is None


def test_components_separate_the_island():
    comps = graph().components()
    assert len(comps) == 2 and len(comps[1]) == 2


def test_nearest_respects_limit():
    g = graph()
    assert g.nearest(5, 5)[0] == "dr_Main_Street_s1_ai2"
    assert g.nearest(3000, 3000, limit=60) is None


def test_street_names():
    assert rl.street_name("dr_Hospital_Road_5_s1_ai12") == "Hospital Road"
    assert rl.street_name("dr_Gray_s_Park_s1_ai3") == "Gray's Park"
    assert rl.street_name("dr_road_802875024_seg_0_s1_ai3") == "unnamed way 802875024"
    assert rl.street_name("dr_autojunction_4_s1_b2").startswith("junction")


def test_latlon_roundtrip_and_origin():
    assert rl.latlon_to_xy(54.5428, -5.93, 54.5428, -5.93) == (0.0, 0.0)
    x, y = rl.latlon_to_xy(54.5438, -5.929, 54.5428, -5.93)
    lat, lon = rl.xy_to_latlon(x, y, 54.5428, -5.93)
    assert abs(lat - 54.5438) < 1e-9 and abs(lon + 5.929) < 1e-9 and x > 0 and y > 0   # east and north


def spec(*pts):
    return {"id": "r", "name": "r", "via": [{"name": str(i), "x": p[0], "y": p[1]} for i, p in enumerate(pts)]}


def test_build_route_turns_left_and_right_and_length():
    g = graph()
    feats = [{"kind": "signals", "x": 5.0, "y": 5.0}]
    # driving east on Main Street, turn north onto Side Road: a left turn (x east, y north)
    r = rl.build_route(g, spec((-100, 0), (0, 100)), feats, 54.5428, -5.93)
    assert r["length_m"] == 200
    assert [j["turn"] for j in r["junctions"]] == ["left"] and r["junctions"][0]["features"] == ["signals"]
    assert r["counts"]["left"] == 1 and r["counts"]["signals"] == 1
    r = rl.build_route(g, spec((-100, 0), (0, -100)), [], 54.5428, -5.93)
    assert r["junctions"][0]["turn"] == "right" and r["counts"]["right"] == 1
    r = rl.build_route(g, spec((-100, 0), (100, 0)), [], 54.5428, -5.93)
    assert r["junctions"][0]["turn"] == "straight" and r["streets"] == ["Main Street"]
    assert max(rl.dist2(a, b) for a, b in zip(r["path"], r["path"][1:])) <= 10.01      # densified


def test_resolve_raises_when_not_connected_or_far():
    g = graph()
    with pytest.raises(ValueError, match="no drivable path"):
        rl.resolve_via(g, spec((-100, 0), (500, 500))["via"])
    with pytest.raises(ValueError, match="no drivable path"):
        rl.resolve_via(g, spec((200, 0), (-100, 0))["via"])           # against the one-way
    with pytest.raises(ValueError, match="not near any drivable road"):
        rl.resolve_via(g, spec((-100, 0), (9000, 9000))["via"])


def doc_for(g):
    r = rl.build_route(g, {"id": "ok-1", "name": "n", "via": [{"name": "a", "x": -100, "y": 0},
                                                              {"name": "b", "x": 0, "y": 100}]}, [], 54.5428, -5.93)
    return {"routes": [r]}


def test_validate_routes_accepts_a_good_file_and_names_each_problem():
    g = graph()
    doc = doc_for(g)
    assert rl.validate_routes(doc, g) == [] and rl.validate_routes(doc) == []
    bad = json.loads(json.dumps(doc))
    bad["routes"][0]["id"] = "bad id!"
    assert any("id must use" in e for e in rl.validate_routes(bad, g))
    bad = json.loads(json.dumps(doc))
    del bad["routes"][0]["path"]
    assert any("missing path" in e for e in rl.validate_routes(bad))
    bad = json.loads(json.dumps(doc))
    bad["routes"][0]["length_m"] = 999
    assert any("length_m" in e for e in rl.validate_routes(bad))
    bad = json.loads(json.dumps(doc))
    bad["routes"][0]["via"][1] = {"name": "island", "x": 500, "y": 500}
    assert any("no drivable path" in e for e in rl.validate_routes(bad, g))
    bad = json.loads(json.dumps(doc))
    bad["routes"][0]["path"] = [[q[0], q[1] + 50, q[2]] for q in bad["routes"][0]["path"]]
    assert any("run build_routes.py rebuild" in e for e in rl.validate_routes(bad, g))
    assert rl.validate_routes({"routes": doc["routes"] * 2}, g) == ["ok-1: duplicate id"]
    assert rl.validate_routes({"routes": []}) == ["no routes"]


def test_load_osm_features_kinds(tmp_path):
    osm = [{"type": "street_furniture", "geometry": [{"lat": 54.5428, "lng": -5.93}], "tags": {"highway": "traffic_signals"}},
           {"type": "street_furniture", "geometry": [{"lat": 54.5428, "lng": -5.93}], "tags": {"highway": "give_way"}},
           {"type": "road", "geometry": [{"lat": 54.5428, "lng": -5.93}], "tags": {"junction": "roundabout"}},
           {"type": "road", "geometry": [{"lat": 54.5428, "lng": -5.93}], "tags": {"highway": "residential"}},
           {"type": "building", "geometry": [], "tags": {}}]
    p = tmp_path / "osm.json"
    p.write_text(json.dumps(osm))
    assert [f["kind"] for f in rl.load_osm_features(p, 54.5428, -5.93)] == ["signals", "give_way", "roundabout"]


# ---- the committed route file --------------------------------------------------------------

ROUTES = ROOT / "qa" / "routes" / "belfast_slice.json"


def test_committed_route_file_is_valid_and_complete():
    doc = json.loads(ROUTES.read_text(encoding="utf-8"))
    assert rl.validate_routes(doc) == []
    assert 6 <= len(doc["routes"]) <= 12
    assert "not confirmed" in doc["provisional"]
    for r in doc["routes"]:
        assert any(v["name"] == "TC" for v in r["via"])
        assert all("lat" in v and "lon" in v for v in r["via"])
        assert r["length_m"] > 200
    assert sum(r["counts"]["left"] for r in doc["routes"]) > 0 and sum(r["counts"]["right"] for r in doc["routes"]) > 0
    assert sum(r["counts"]["roundabouts"] for r in doc["routes"]) > 0 and sum(r["counts"]["signals"] for r in doc["routes"]) > 0


def test_committed_route_file_is_connected_on_the_dumped_game_graph():
    gp = br.DEFAULT_GRAPH
    if not gp.is_file():
        pytest.skip("graph dump not present (drive_suite.py dump-graph writes it; stays out of git)")
    g = br.load_graph(gp)
    doc = json.loads(ROUTES.read_text(encoding="utf-8"))
    assert rl.validate_routes(doc, g) == []


# ---- drive_suite helpers -------------------------------------------------------------------

def suite():
    import drive_suite
    return drive_suite


def test_parse_samples_and_bad_rows():
    ds = suite()
    raw = "1.000,1.00,2.00,3.00,4.000,5.000,6.000,0.600,0.800,0.100,0.200,0.300;garbage;1,2,3"
    s = ds.parse_samples(raw)
    assert len(s) == 1 and s[0]["steer"] == 0.1 and abs(s[0]["dx"] - 0.6) < 1e-9
    assert ds.parse_samples("") == []


def test_waypoints_include_junctions_and_endpoints():
    ds = suite()
    g = graph()
    w = ds.waypoints(g, spec((-100, 0), (0, 100))["via"])
    assert w[0] == "dr_Main_Street_s1_ai1" and w[-1] == "dr_Side_Road_s1_ai1" and "dr_Main_Street_s1_ai2" in w


def test_script_points_slow_for_turns_and_ease_at_the_end():
    ds = suite()
    g = graph()
    r = rl.build_route(g, spec((-100, 0), (0, 100)), [], 54.5428, -5.93)
    r["half_width_m"] = 3.0
    pts = ds.script_points(r, 13.4).split("},{")
    speeds = [float(p.split("v=")[1].rstrip("}")) for p in pts]
    assert speeds[-1] <= 4.0 + 1e-6 and speeds[0] <= 13.4 * 0.6 + 0.05 and min(speeds) <= 7.0


def test_link_refuses_non_local_urls():
    ds = suite()
    import beamng_mcp
    with pytest.raises(AssertionError):
        ds.Link(beamng_mcp.McpClient("http://example.com/mcp"), 1e18)
    ds.Link(beamng_mcp.McpClient(), 1e18)


def test_load_routes_only_and_unknown(tmp_path):
    ds = suite()
    doc = ds.load_routes(ROUTES, ["lady-ishbel-loop"])
    assert [r["id"] for r in doc["routes"]] == ["lady-ishbel-loop"]
    with pytest.raises(ValueError, match="unknown route"):
        ds.load_routes(ROUTES, ["nope"])
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"routes": [{"id": "x"}]}))
    with pytest.raises(ValueError, match="invalid"):
        ds.load_routes(bad, None)


def test_source_never_mentions_the_remote_mcp_flag_or_non_loopback_urls():
    src = (ROOT / "qa" / "drive_suite.py").read_text(encoding="utf-8")
    assert "mcpremote" not in src
    assert "http://" not in src.replace("http://127.0.0.1:", "")
