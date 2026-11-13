"""Speed limits in mph (T-408): parsing, NI defaults, the step on a small osm.json, and MapNG reading the result.

    uv run --no-project --python 3.12 --with pytest python -m pytest tests/pipeline/test_speed_limits.py -q

The MapNG test needs node and a MapNG checkout at the pinned commit (BELFAST_MAPNG_ROOT from a git worktree).
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import speed_limits as sl  # noqa: E402

MAPNG = Path(os.environ.get("BELFAST_MAPNG_ROOT") or ROOT / "third_party" / "mapng")
needs_mapng = pytest.mark.skipif(not (MAPNG / "services" / "beamngMapNavigation.js").is_file() or not shutil.which("node"),
                                 reason="MapNG submodule or node not available")


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("raw, want", [
    ("30 mph", (30, "mph")), ("30mph", (30, "mph")), ("30 MPH", (30, "mph")), ("  40 mph ", (40, "mph")),
    ("50 km/h", (30, "kmh")), ("50km/h", (30, "kmh")), ("100 kmh", (60, "kmh")), ("80 kph", (50, "kmh")),
    ("120 km/h", (75, "kmh")),
    ("30", (30, "bare_mph")), ("70", (70, "bare_mph")),     # unit left off, a Northern Ireland limit
    ("35", (20, "kmh")),                                     # not a limit in mph: read as OSM says, km/h
    ("30;40", (30, "bare_mph")),
    ("30 mph;20 mph", (20, "mph")), ("walk", (5, "walk")),
    ("none", None), ("signals", None), ("", None), (None, None), ("30 mph @ (Mo-Fr 08:00-16:00)", None),
    ("0", None), ("-30", None), ("fast", None),
])
def test_parse_value(raw, want):
    assert sl.parse_value(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("GB:nsl_restricted", 30), ("GB:nsl_single", 60), ("gb:nsl_dual", 70), ("UK:motorway", 70), ("GB:20", 20),
    ("GB:zone20", 20), ("zone30", 30), ("20 mph zone", 20), ("sign", None), ("survey", None), ("GB:99", None),
    ("30", None), ("", None), (None, None),
])
def test_parse_type(raw, want):
    assert sl.parse_type(raw) == want


def test_tagged_limit_order_and_sources():
    assert sl.tagged_limit({"maxspeed": "40 mph", "maxspeed:type": "GB:nsl_single"}) == (40, "tagged")
    assert sl.tagged_limit({"maxspeed:forward": "30 mph", "maxspeed:backward": "20 mph"}) == (20, "tagged_directional")
    assert sl.tagged_limit({"maxspeed": "signals", "maxspeed:type": "GB:nsl_single"}) == (60, "tagged_type")
    assert sl.tagged_limit({"zone:maxspeed": "GB:20"}) == (20, "tagged_type")
    assert sl.tagged_limit({"highway": "residential"}) is None


# ---------------------------------------------------------------- defaults

@pytest.mark.parametrize("tags, built_up, want", [
    ({"highway": "motorway"}, False, (70, "motorway")),
    ({"highway": "motorway_link"}, True, (70, "motorway")),
    ({"highway": "residential"}, False, (30, "residential")),
    ({"highway": "living_street"}, True, (20, "living_street")),
    ({"highway": "service"}, True, (20, "service")),
    ({"highway": "service", "service": "driveway"}, True, (10, "service_minor")),
    ({"highway": "service", "service": "parking_aisle"}, False, (10, "service_minor")),
    ({"highway": "track"}, False, (20, "track")),
    ({"highway": "tertiary"}, True, (30, "restricted")),
    ({"highway": "tertiary", "lit": "yes"}, False, (30, "restricted")),
    ({"highway": "unclassified"}, False, (60, "single")),
    ({"highway": "secondary", "lit": "no"}, False, (60, "single")),
    ({"highway": "primary", "oneway": "yes"}, False, (70, "dual")),
    ({"highway": "trunk", "dual_carriageway": "yes"}, False, (70, "dual")),
    ({"highway": "trunk", "oneway": "yes", "junction": "roundabout"}, False, (60, "single")),
    ({"highway": "trunk_link", "oneway": "yes"}, False, (60, "single")),
    ({"highway": "trunk", "oneway": "yes", "lit": "yes"}, False, (30, "restricted")),
    ({"highway": "road"}, False, (60, "single")),
])
def test_default_limit(tags, built_up, want):
    assert sl.default_limit(tags, built_up) == want


# ---------------------------------------------------------------- the step on a small osm.json

LAT0, LNG0 = 54.54, -5.93


def pt(dx_m, dy_m):
    return {"lat": LAT0 + dy_m / 110_540.0, "lng": LNG0 + dx_m / 64_800.0}


def road(i, highway, a, b, **tags):
    return {"id": f"{i}_seg_0", "type": "road", "tags": {"highway": highway, **tags}, "geometry": [pt(*a), pt(*b)]}


def building(x, y):
    g = [pt(x, y), pt(x + 8, y), pt(x + 8, y + 8), pt(x, y + 8), pt(x, y)]
    return {"id": f"b{x}_{y}", "type": "building", "tags": {"building": "yes"}, "geometry": g}


def features():
    f = [
        road(1, "trunk", (0, 0), (500, 0), name="Main Road", maxspeed="40 mph"),         # tagged
        road(2, "trunk", (500, 0), (900, 0), name="Main Road"),                           # same road, untagged: inherits 40
        road(3, "trunk", (0, 3000), (900, 3000)),                                         # rural, no buildings: single, 60
        road(4, "secondary", (0, 1000), (400, 1000), name="Terrace Road"),                # buildings around: 30
        road(5, "residential", (0, 2000), (300, 2000)),                                   # residential: 30
        road(6, "unclassified", (0, 4000), (300, 4000), maxspeed="80"),                   # km/h: 50 mph
        road(7, "unclassified", (0, 4500), (300, 4500), maxspeed="30"),                   # bare, read as mph
        road(8, "footway", (0, 5000), (300, 5000), maxspeed="10"),                        # not for cars
        road(9, "service", (0, 5500), (300, 5500), service="parking_aisle"),              # 10
        {"id": "x1", "type": "street_furniture", "tags": {"highway": "traffic_signals"}, "geometry": [pt(0, 0)]},
    ]
    f += [building(x, 1010 + dy) for x in range(10, 400, 25) for dy in (0, 30)]
    return f


def test_apply_gives_every_car_road_a_limit_in_mph():
    f = features()
    state = sl.apply(f)
    tags = {x["id"]: x["tags"] for x in f if x["type"] == "road"}
    assert tags["1_seg_0"]["maxspeed"] == "40 mph" and tags["1_seg_0"][sl.SOURCE_KEY] == "tagged"
    assert tags["2_seg_0"]["maxspeed"] == "40 mph" and tags["2_seg_0"][sl.SOURCE_KEY] == "inherited"
    assert tags["3_seg_0"]["maxspeed"] == "60 mph" and tags["3_seg_0"][sl.RULE_KEY] == "single"
    assert tags["4_seg_0"]["maxspeed"] == "30 mph" and tags["4_seg_0"][sl.RULE_KEY] == "restricted"
    assert tags["5_seg_0"]["maxspeed"] == "30 mph" and tags["5_seg_0"][sl.SOURCE_KEY] == "default"
    assert tags["6_seg_0"]["maxspeed"] == "50 mph" and tags["6_seg_0"][sl.ORIGINAL_KEY] == "80"
    assert tags["7_seg_0"]["maxspeed"] == "30 mph" and tags["7_seg_0"][sl.SOURCE_KEY] == "tagged"
    assert tags["8_seg_0"][sl.SOURCE_KEY] == "n/a" and tags["8_seg_0"]["maxspeed"] == "10"
    assert tags["9_seg_0"]["maxspeed"] == "10 mph"
    summary = sl.summarise(f, state)
    assert summary["vehicle_road_features"] == 8 and summary["vehicle_road_features_without_limit"] == 0
    assert summary["by_class"]["footway"]["source"] == {"n/a": 1}


def test_other_features_are_left_alone():
    f = features()
    before = json.dumps([x for x in f if x["type"] != "road"], sort_keys=True)
    sl.apply(f)
    assert json.dumps([x for x in f if x["type"] != "road"], sort_keys=True) == before


def test_apply_twice_changes_nothing():
    f = features()
    sl.apply(f)
    once = json.dumps(f, sort_keys=True)
    sl.apply(f)
    assert json.dumps(f, sort_keys=True) == once


def test_inheritance_needs_agreeing_neighbours_and_a_name():
    a = road(1, "tertiary", (0, 0), (100, 0), name="X Road", maxspeed="40 mph")
    b = road(2, "tertiary", (100, 0), (200, 0), name="X Road")
    c = road(3, "tertiary", (200, 0), (300, 0), name="X Road", maxspeed="50 mph")
    d = road(4, "tertiary", (300, 0), (400, 0))                       # no name: never inherits
    e = road(5, "tertiary", (400, 0), (500, 0), name="X Road", maxspeed="50 mph")
    f = [a, b, c, d, e]
    sl.apply(f)
    assert b["tags"][sl.SOURCE_KEY] == "default"                       # neighbours say 40 and 50: no agreement
    assert d["tags"][sl.SOURCE_KEY] == "default"


def test_report_table_lists_the_classes():
    f = features()
    text = sl.markdown_table(sl.summarise(f, sl.apply(f)))
    assert "| trunk | 3 |" in text and "| footway" not in text and "**all**" in text


# ---------------------------------------------------------------- what MapNG reads

@needs_mapng
def test_mapng_reads_every_limit_we_write(tmp_path):
    values = [f"{n} mph" for n in (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70)]
    script = tmp_path / "read.mjs"
    mod = (MAPNG / "services" / "beamngMapNavigation.js").as_uri()
    script.write_text(
        f"import {{ parseMaxspeedMps }} from {json.dumps(mod)};\n"
        f"const out = {json.dumps(values)}.map((v) => [v, parseMaxspeedMps({{ maxspeed: v }})]);\n"
        "console.log(JSON.stringify(out));\n", encoding="utf-8")
    got = json.loads(subprocess.run([shutil.which("node"), str(script)], capture_output=True, text=True, check=True,
                                    cwd=tmp_path).stdout)
    for text, mps in got:
        mph = float(text.split()[0])
        assert mps is not None and abs(mps / 0.44704 - mph) < 0.05, (text, mps)
