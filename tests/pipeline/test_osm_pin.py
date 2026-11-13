"""Pinned OSM (T-202): the cut, its determinism, the manifest check. A tiny synthetic extract, no network.

    uv run --no-project --python 3.12 --with pytest --with osmium --with pyproj --with numpy --with pyyaml --with requests \
        python -m pytest tests/pipeline/test_osm_pin.py tests/mapng -q

The filter-vs-MapNG test and the parser test need the MapNG submodule: set BELFAST_MAPNG_ROOT to a checkout
at the pinned commit when running from a git worktree (they skip when it is absent).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tools"))
import osm_pin  # noqa: E402

MAPNG = Path(os.environ.get("BELFAST_MAPNG_ROOT") or ROOT / "third_party" / "mapng")
needs_mapng = pytest.mark.skipif(not (MAPNG / "services" / "osm.js").is_file() or not shutil.which("node"),
                                 reason="MapNG submodule or node not available")

BOX = {"south": 54.000, "north": 54.010, "west": -6.010, "east": -6.000}

# (id, lat, lon, tags)
NODES = [
    (1, 54.005, -6.005, {"highway": "traffic_signals"}),      # kept
    (2, 54.005, -6.004, {"shop": "bakery"}),                  # inside, not a MapNG node tag
    (3, 54.050, -6.005, {"highway": "stop"}),                 # outside
    (4, 54.006, -6.006, {"amenity": "pub", "name": "Café Ó"}),   # kept, non-ASCII
    (5, 54.002, -6.002, {"highway": "crossing", "crossing": "zebra"}),            # side file only
    (6, 54.003, -6.003, {"crossing": "traffic_signals"}),                          # side file only (no highway tag)
    (7, 54.050, -6.003, {"highway": "crossing"}),                                  # outside the box
    (8, 54.007, -6.007, {"highway": "traffic_signals", "crossing": "traffic_signals"}),  # both
    (9, 54.008, -6.008, {"highway": "crossing", "crossing:markings": "zebra"}),   # side file, untyped
    (10, 54.001, -6.009, {}), (11, 54.002, -6.008, {}), (12, 54.003, -6.007, {}),
    (20, 54.005, -6.020, {}), (21, 54.005, -5.990, {}),  # west of and east of the box
    (30, 54.100, -6.100, {}), (31, 54.101, -6.101, {}),
    (40, 54.004, -6.004, {}), (41, 54.004, -6.001, {}), (42, 54.020, -6.001, {}), (43, 54.020, -6.004, {}),
    (50, 54.300, -6.300, {}), (51, 54.301, -6.300, {}), (52, 54.301, -6.301, {}),
]
WAYS = [
    (100, [10, 11, 12], {"highway": "residential", "name": "Inside Road"}),   # kept
    (101, [20, 21], {"highway": "primary"}),                                    # crosses the box, no node in it
    (102, [30, 31], {"highway": "service"}),                                    # far away
    (103, [10, 11], {"name": "no matching key"}),                               # inside, untagged for MapNG
    (104, [40, 41, 42, 43, 40], {"building": "yes"}),                           # kept, partly outside the box
    (105, [50, 51, 52, 50], {}),                                                # far member of relation 200
]
RELATIONS = [
    (200, [("way", 104, "outer"), ("way", 105, "inner")], {"type": "multipolygon", "building": "yes"}),  # kept
    (201, [("way", 102, "outer")], {"type": "multipolygon", "building": "yes"}),                          # not kept
    (202, [("way", 100, "outer")], {"type": "route", "route": "bus"}),                                    # no MapNG key
]


def xml(items):
    def esc(s):
        return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")

    def tags(t):
        return "".join(f'<tag k="{esc(k)}" v="{esc(v)}"/>' for k, v in t.items())

    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="test">']
    for i, la, lo, t in items["nodes"]:
        out.append(f'<node id="{i}" version="1" lat="{la}" lon="{lo}">{tags(t)}</node>')
    for i, refs, t in items["ways"]:
        out.append(f'<way id="{i}" version="1">' + "".join(f'<nd ref="{r}"/>' for r in refs) + tags(t) + "</way>")
    for i, mem, t in items["relations"]:
        out.append(f'<relation id="{i}" version="1">'
                   + "".join(f'<member type="{ty}" ref="{r}" role="{ro}"/>' for ty, r, ro in mem) + tags(t) + "</relation>")
    out.append("</osm>")
    return "\n".join(out) + "\n"


@pytest.fixture(scope="module")
def extract(tmp_path_factory):
    nodes = NODES
    p = tmp_path_factory.mktemp("extract") / "tiny.osm"
    p.write_text(xml({"nodes": nodes, "ways": WAYS, "relations": RELATIONS}), encoding="utf-8")
    return p


def ids(elements, kind):
    return [e["id"] for e in elements if e["type"] == kind]


def test_cut_selects_like_the_overpass_query(extract):
    els = osm_pin.cut_many(extract, {"a": BOX})["a"]
    assert ids(els, "node") == [1, 4, 8]               # crossing nodes 5, 6, 9 are not MapNG's; 8 is a signal as well
    assert ids(els, "way") == [100, 101, 104]          # 102 far, 103 no key, 105 not selected on its own
    assert ids(els, "relation") == [200]
    by_id = {(e["type"], e["id"]): e for e in els}
    way100 = by_id[("way", 100)]
    assert [(g["lat"], g["lon"]) for g in way100["geometry"]] == [(54.001, -6.009), (54.002, -6.008), (54.003, -6.007)]
    assert way100["tags"] == {"highway": "residential", "name": "Inside Road"}
    rel = by_id[("relation", 200)]
    assert [m["role"] for m in rel["members"]] == ["outer", "inner"]
    assert all(len(m["geometry"]) >= 3 for m in rel["members"])      # the far inner ring's geometry came along
    assert by_id[("node", 4)]["tags"]["name"] == "Café Ó"


def test_cut_is_sorted_and_byte_identical_between_runs(extract, tmp_path):
    a = osm_pin.cut_bytes(osm_pin.cut_many(extract, {"a": BOX})["a"])
    b = osm_pin.cut_bytes(osm_pin.cut_many(extract, {"a": BOX})["a"])
    assert a == b and hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()
    els = json.loads(a)["elements"]
    order = {"node": 0, "way": 1, "relation": 2}
    assert [(order[e["type"]], e["id"]) for e in els] == sorted((order[e["type"]], e["id"]) for e in els)
    assert b"\r" not in a and a.endswith(b"]}\n")
    assert "Café".encode("utf-8") in a


def test_crossing_side_file_has_every_crossing_node_and_leaves_the_cut_alone(extract):
    side = {}
    with_side = osm_pin.cut_many(extract, {"a": BOX}, crossings_out=side)["a"]
    assert with_side == osm_pin.cut_many(extract, {"a": BOX})["a"]          # MapNG-facing output is unchanged
    assert osm_pin.cut_bytes(with_side) == osm_pin.cut_bytes(osm_pin.cut_many(extract, {"a": BOX})["a"])
    assert ids(side["a"], "node") == [5, 6, 8, 9]                             # 7 is outside, 1 is not a crossing
    assert all(osm_pin.crossing_matches(e["tags"]) for e in side["a"])
    assert not any(osm_pin.node_matches(e["tags"]) and e["id"] in (5, 6, 9) for e in with_side)
    again = {}
    osm_pin.cut_many(extract, {"a": BOX}, crossings_out=again)
    assert osm_pin.cut_bytes(side["a"]) == osm_pin.cut_bytes(again["a"])


def test_crossing_matcher():
    assert osm_pin.crossing_matches({"highway": "crossing"})
    assert osm_pin.crossing_matches({"crossing": "uncontrolled"})
    assert osm_pin.crossing_matches({"highway": "traffic_signals", "crossing": "traffic_signals"})
    assert not osm_pin.crossing_matches({"highway": "traffic_signals"})
    assert not osm_pin.crossing_matches({"crossing:markings": "zebra"})
    assert not osm_pin.node_matches({"highway": "crossing"})                 # still not a MapNG node tag


def test_several_boxes_in_one_read_equal_separate_reads(extract):
    far = {"south": 54.29, "north": 54.31, "west": -6.31, "east": -6.29}
    both = osm_pin.cut_many(extract, {"a": BOX, "far": far})
    assert both["a"] == osm_pin.cut_many(extract, {"a": BOX})["a"]
    assert both["far"] == osm_pin.cut_many(extract, {"far": far})["far"]
    assert both["far"] == []                       # 105 has no tag MapNG wants and its relation lies elsewhere


def test_cut_bbox_adds_the_margin_on_every_side():
    from aoi import Bounds
    b = Bounds(north=54.55, south=54.53, west=-5.95, east=-5.91)
    c = osm_pin.cut_bbox(b, 100.0)
    assert c["north"] > 54.55 and c["south"] < 54.53 and c["east"] > -5.91 and c["west"] < -5.95
    assert abs((c["north"] - 54.55) * osm_pin.METRES_PER_DEGREE - 100.0) < 0.01
    assert osm_pin.cut_bbox(b, 100.0) == c


def test_area_boxes_cover_the_aoi_squares():
    boxes = osm_pin.area_boxes(None)
    assert set(boxes) == {"slice", "city_south", "full"}
    s, c, f = boxes["slice"], boxes["city_south"], boxes["full"]
    assert c["north"] > s["north"] and c["west"] < s["west"]
    assert f["north"] > c["north"] and f["east"] > c["east"]
    with pytest.raises(osm_pin.PinError):
        osm_pin.area_boxes(["nowhere"])


def test_matchers():
    assert osm_pin.node_matches({"barrier": "bollard"}) and not osm_pin.node_matches({"barrier": "fence"})
    assert osm_pin.way_matches({"highway": "no"}) and osm_pin.way_matches({"area": "yes"})
    assert not osm_pin.way_matches({"area": "no"}) and not osm_pin.way_matches({"name": "x"})
    assert osm_pin.relation_matches({"building": "yes"}) and not osm_pin.relation_matches({"highway": "x"})


def test_manifest_verify_and_cut_record(extract, tmp_path):
    osm_dir = tmp_path / "osm"
    osm_dir.mkdir()
    pbf = osm_dir / "tiny.osm"
    shutil.copy(extract, pbf)
    mp = tmp_path / "osm.json"
    manifest = {"file": "tiny.osm", "url": "x", "size_bytes": pbf.stat().st_size,
                "sha256": osm_pin.file_digests(pbf)["sha256"], "cuts": {}, "cut_margin_m": 100.0}
    osm_pin.save_manifest(manifest, mp)
    assert osm_pin.verify_pbf(osm_pin.load_manifest(mp), osm_dir) == pbf
    pbf.write_bytes(pbf.read_bytes() + b" ")
    with pytest.raises(osm_pin.PinError, match="size|sha256"):
        osm_pin.verify_pbf(osm_pin.load_manifest(mp), osm_dir)
    with pytest.raises(osm_pin.PinError, match="missing"):
        osm_pin.verify_pbf({**manifest, "file": "gone.osm"}, osm_dir)
    with pytest.raises(osm_pin.PinError, match="download"):
        osm_pin.load_manifest(tmp_path / "none.json")


def test_committed_manifest_is_complete():
    m = osm_pin.load_manifest()
    assert m["url"].startswith("https://download.geofabrik.de/") and m["url"].endswith(".osm.pbf")
    assert len(m["sha256"]) == 64 and m["size_bytes"] > 100_000_000
    assert m["pbf_header"]["data_timestamp_utc"] and m["downloaded_utc"]
    for name, c in m["cuts"].items():
        assert len(c["sha256"]) == 64 and c["elements"]["ways"] > 0, name
        assert len(c["crossings"]["sha256"]) == 64 and c["crossings"]["nodes"] > 0, name   # I-002 side file


@needs_mapng
def test_filter_copy_equals_mapng_overpass_filter():
    js = ("import {OSM_FILTER} from %s; console.log(JSON.stringify(OSM_FILTER))"
          % json.dumps((MAPNG / "services" / "overpassQuery.js").as_uri()))
    out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, check=True).stdout
    f = json.loads(out)
    assert f["wayKeys"] == osm_pin.WAY_KEYS
    assert [tuple(x) for x in f["wayTags"]] == osm_pin.WAY_TAGS
    assert f["relationKeys"] == osm_pin.RELATION_KEYS
    assert [tuple(x) for x in f["nodeTags"]] == osm_pin.NODE_TAGS


@needs_mapng
def test_mapng_parser_accepts_a_cut_and_is_repeatable(extract, tmp_path):
    cut = tmp_path / "t.overpass.json"
    cut.write_bytes(osm_pin.cut_bytes(osm_pin.cut_many(extract, {"a": BOX})["a"]))
    outs = []
    for run in ("one", "two"):
        area = tmp_path / run
        area.mkdir()
        (area / "meta.json").write_text(json.dumps({"bounds": BOX}), encoding="utf-8")
        env = {**os.environ, "MAPNG_ROOT": str(MAPNG)}
        r = subprocess.run(["node", str(ROOT / "tools" / "mapng_osm_parse.mjs"), str(cut), str(area)],
                           env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        outs.append((area / "osm.json").read_bytes())
    assert outs[0] == outs[1]
    feats = json.loads(outs[0])
    types = sorted({f["type"] for f in feats})
    assert "road" in types and "building" in types and "street_furniture" in types


# ---------------------------------------------------------------- tools/mapng_export.py wiring

def _setup_cut(tmp_path, monkeypatch, content=b'{"elements":[\n]}\n'):
    import mapng_export as me
    monkeypatch.setattr(me, "DATA_DIR", tmp_path)
    cut_dir = tmp_path / "osm" / "cuts"
    cut_dir.mkdir(parents=True)
    (cut_dir / "slice.overpass.json").write_bytes(content)
    manifest = tmp_path / "osm.json"
    manifest.write_text(json.dumps({"cuts": {"slice": {"sha256": hashlib.sha256(content).hexdigest()}}}), encoding="utf-8")
    return me, cut_dir, manifest


def test_pinned_cut_accepts_only_the_recorded_bytes(tmp_path, monkeypatch):
    me, cut_dir, manifest = _setup_cut(tmp_path, monkeypatch)
    assert me.pinned_cut("slice", manifest, cut_dir) == cut_dir / "slice.overpass.json"
    (cut_dir / "slice.overpass.json").write_bytes(b'{"elements":[]}\n')           # edited
    with pytest.raises(me.GuardError) as e:
        me.pinned_cut("slice", manifest, cut_dir)
    assert e.value.code == me.EXIT_INPUTS and "manifest" in str(e.value)
    with pytest.raises(me.GuardError) as e:
        me.pinned_cut("city_south", manifest, cut_dir)                           # no such cut
    assert e.value.code == me.EXIT_INPUTS
    with pytest.raises(me.GuardError):
        me.pinned_cut("../slice", manifest, cut_dir)                              # name must be plain


def test_fetch_with_pinned_osm_gives_the_child_no_overpass(tmp_path, monkeypatch):
    me, cut_dir, manifest = _setup_cut(tmp_path, monkeypatch)
    real = me.pinned_cut
    monkeypatch.setattr(me, "check_pin", lambda *a, **k: "pin")
    monkeypatch.setattr(me, "pinned_cut", lambda a: real(a, manifest, cut_dir))
    monkeypatch.setattr(me, "_prepare", lambda name: (tmp_path / name, tmp_path / "home"))
    seen = {}
    monkeypatch.setattr(me, "_run_node", lambda args, env, mem: seen.update(args=args, env=env) or 0)
    monkeypatch.setattr(me, "apply_speed_limits", lambda area_dir: {})      # no node run, so no osm.json (T-408 step)
    ns = SimpleNamespace(name="belfast_x", lat=54.5, lng=-5.9, size=512, node_memory_mb=1, pinned_osm="slice")
    assert me.cmd_fetch(ns) == 0
    hosts = seen["env"]["MAPNG_ALLOW_HOSTS"].split(",")
    assert hosts == ["s3.amazonaws.com"] and not any("overpass" in h for h in hosts)
    assert seen["env"]["MAPNG_OSM_CUT"].endswith("slice.overpass.json")
    seen.clear()
    ns.pinned_osm = None
    assert me.cmd_fetch(ns) == 0                                                  # the old path still has Overpass
    assert "overpass-api.de" in seen["env"]["MAPNG_ALLOW_HOSTS"] and "MAPNG_OSM_CUT" not in seen["env"]
