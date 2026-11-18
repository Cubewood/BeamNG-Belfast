"""T-404: the junction layout check (qa/static_checks/junctions.py) on a hand-built T junction. No zip of a real level,
no game. Main road A (secondary) runs west-east through (0, 0), side road B (residential) comes up from the south and
stops there; both 10.6 m kerb to kerb. Left-hand traffic: B's northbound lane is its west (negative x) half."""
import math
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qa"))
from static_checks.junctions import check_junctions, corner_radii  # noqa: E402
from static_checks.level import DecalRoad, Level, OsmFeature  # noqa: E402

NAME = "lvl"
HALF = 5.3
MOUTH_Y = -(HALF + 0.1 + 0.35)          # a Give Way line's centre: near edge 0.1 m off A's kerb


def osm_road(osm_id, tags, pts):
    xy = np.array(pts, dtype=float)
    return OsmFeature(osm_id, "road", tags, xy, [(x / 1e5, y / 1e5) for x, y in pts])


def body(name, pts, width=2 * HALF):
    return DecalRoad(f"dr_{name}_s1_b", "road_asphalt_2lane", np.array([[x, y, 10.0, width] for x, y in pts]), {})


def line(name, a, b, material="uk_giveway_1003a_cubewood", width=0.8):
    return DecalRoad(name, material, np.array([[a[0], a[1], 10.0, width], [b[0], b[1], 10.0, width]]), {})


def band(r, centre=(-HALF - 6.0, -HALF - 6.0), turn_deg=90.0, name="corner_paving_0"):
    """A corner paving band 0.5 m outside a kerb arc of radius r round `centre` (cornerPatches' first band)."""
    a0 = math.radians(0.0)
    pts = [(centre[0] + (r + 0.5) * math.cos(a0 + math.radians(turn_deg) * k / 10),
            centre[1] + (r + 0.5) * math.sin(a0 + math.radians(turn_deg) * k / 10)) for k in range(11)]
    return DecalRoad(name, "road_asphalt_2lane", np.array([[x, y, 10.0, 1.2] for x, y in pts]), {})


def level(tmp_path, roads, osm=None):
    z = tmp_path / "l.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(f"levels/{NAME}/info.json", "{}")
    osm = osm if osm is not None else [
        osm_road("A", {"highway": "secondary", "name": "Main"}, [(-200, 0), (0, 0), (200, 0)]),
        osm_road("B", {"highway": "residential", "name": "Side"}, [(0, -200), (0, 0)]),
    ]
    bodies = [body("Main", [(-200, 0), (0, 0), (200, 0)]), body("Side", [(0, -200), (0, 0)])]
    return Level(z, NAME, {}, 1024.0, bodies + list(roads), [], 0, None, osm, "", lambda a, b: (a, b), zipfile.ZipFile(z))


def kinds(found):
    return sorted(f["kind"] for f in found)


GOOD = line("jm_giveway_1", (-0.25, MOUTH_Y), (-5.0, MOUTH_Y))


def test_a_give_way_line_at_the_mouth_has_no_findings(tmp_path):
    found, summary = check_junctions(level(tmp_path, [GOOD]), {})
    assert kinds(found) == []
    assert summary["coverage"]["minor_arms"] == 1 and summary["coverage"]["minor_arms_marked"] == 1
    m = summary["measured"]["giveway"]
    assert abs(m["gap_min_m"] - 0.1) < 0.02
    assert summary["gate"] == {"pass": True, "failed": {}}
    assert check_junctions(level(tmp_path, []), {})[1]["gate"] == {"pass": False, "failed": {"junction-mouth-unmarked": 1}}


def test_a_line_on_the_main_road_or_back_from_the_mouth(tmp_path):
    on = line("jm_giveway_1", (-0.25, -4.5), (-5.0, -4.5))
    assert "junction-line-on-road" in kinds(check_junctions(level(tmp_path, [on]), {})[0])
    back = line("jm_giveway_1", (-0.25, MOUTH_Y - 2.0), (-5.0, MOUTH_Y - 2.0))
    assert "junction-line-off-mouth" in kinds(check_junctions(level(tmp_path, [back]), {})[0])


def test_a_skewed_or_short_line(tmp_path):
    dy = math.tan(math.radians(20)) * 4.75
    skew = line("jm_giveway_1", (-0.25, MOUTH_Y), (-5.0, MOUTH_Y - dy))
    assert "junction-line-not-square" in kinds(check_junctions(level(tmp_path, [skew]), {})[0])
    short = line("jm_giveway_1", (-0.25, MOUTH_Y), (-3.0, MOUTH_Y))
    assert "junction-line-span" in kinds(check_junctions(level(tmp_path, [short]), {})[0])


def test_an_unmarked_minor_arm_and_a_line_on_the_priority_road(tmp_path):
    assert "junction-mouth-unmarked" in kinds(check_junctions(level(tmp_path, []), {})[0])
    # across the main road's west arm, at the side road's mouth distance
    wrong = line("jm_giveway_2", (-HALF - 0.45, 0.25), (-HALF - 0.45, 5.0))
    found = check_junctions(level(tmp_path, [GOOD, wrong]), {})[0]
    hit = [f for f in found if f["kind"] == "junction-line-on-priority-road"]
    assert hit and hit[0]["severity"] == "high"


def test_kerb_radii_from_the_corner_bands(tmp_path):
    assert [round(c[2], 2) for c in corner_radii(level(tmp_path, [band(6.0)]))] == [6.0]
    found, summary = check_junctions(level(tmp_path, [GOOD, band(6.0)]), {})
    assert kinds(found) == [] and summary["kerb_radii"]["corners"] == 1
    tight = check_junctions(level(tmp_path, [GOOD, band(2.0, centre=(-HALF - 2.0, -HALF - 2.0))]), {})[0]
    assert kinds(tight) == ["kerb-radius-small"]
    # a skewed corner (the kerb turns 140 degrees) is measured, not judged
    skewed = check_junctions(level(tmp_path, [GOOD, band(2.0, centre=(-HALF - 2.0, -HALF - 2.0), turn_deg=140)]), {})
    assert kinds(skewed[0]) == [] and skewed[1]["kerb_radii"]["skewed_not_judged"] == 1


def test_a_roundabout_entry_without_its_line(tmp_path):
    ring_pts = [(14 * math.cos(-2 * math.pi * k / 12), 14 * math.sin(-2 * math.pi * k / 12)) for k in range(13)]
    ring_pts[-1] = ring_pts[0]
    osm = [osm_road("R", {"highway": "tertiary", "junction": "roundabout"}, ring_pts),
           osm_road("E", {"highway": "residential", "name": "Entry"}, [(-200, 0), (-14, 0)])]
    ring_pts_l = [(round(x, 6), round(y, 6)) for x, y in ring_pts]
    osm[1] = osm_road("E", {"highway": "residential", "name": "Entry"}, [(-200, 0), ring_pts_l[6]])
    osm[0] = osm_road("R", {"highway": "tertiary", "junction": "roundabout"}, ring_pts_l)
    lv = level(tmp_path, [], osm=osm)
    lv.roads = [body("Ring", ring_pts_l, 8.0), body("Entry", [(-200, 0), ring_pts_l[6]])]
    found = check_junctions(lv, {})[0]
    assert "roundabout-entry-unmarked" in kinds(found)
