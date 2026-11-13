"""AOI config and transforms: round trips under 1 cm, MapNG's own placement of the slice. No game, no network.

    uv run --no-project --python 3.12 --with pytest --with pyproj --with numpy --with pyyaml python -m pytest tests/pipeline -q
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from pyproj import CRS, Transformer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import aoi  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "belfast_slice_mapng_pairs.json"
CM = 0.01
CRS_NAMES = ["irish_grid", "itm"]


@pytest.fixture(scope="module")
def cfg():
    return aoi.load()


def sample_game(area, n=400, seed=1):
    rng = np.random.default_rng(seed)
    h = area.world_size / 2
    x = rng.uniform(-h, h, n)
    y = rng.uniform(-h, h, n)
    # the corners and the middle too
    x = np.r_[x, -h, h, -h, h, 0.0]
    y = np.r_[y, -h, -h, h, h, 0.0]
    return x, y


# ---- config ----

def test_three_areas_in_growth_order(cfg):
    assert list(cfg.areas) == ["slice", "city_south", "full"]
    assert [a.size_m for a in cfg.areas.values()] == [2048, 4096, 8192]
    for a in cfg.areas.values():
        assert a.size_px == a.size_m / a.mpp


def test_pinned_crs_agree_with_epsg(cfg):
    """The pinned proj4 strings equal pyproj's own EPSG operation to under 1 cm over Belfast."""
    rng = np.random.default_rng(2)
    lat = rng.uniform(54.45, 54.65, 200)
    lon = rng.uniform(-6.1, -5.8, 200)
    for name in CRS_NAMES:
        epsg = cfg.crs[name]["epsg"]
        ref = Transformer.from_crs(4326, epsg, always_xy=True)
        rx, ry = ref.transform(lon, lat)
        x, y = cfg.latlon_to_crs(name, lat, lon)
        assert np.hypot(x - rx, y - ry).max() < CM, name


def test_known_irish_grid_and_itm_values(cfg):
    """Hospital Road centre in both grids (values from the same PROJ operations, pinned here so a change is noticed)."""
    e, n = cfg.latlon_to_crs("itm", 54.5428, -5.93)
    assert (e, n) == pytest.approx((733936.2197, 868019.4873), abs=0.001)
    e, n = cfg.latlon_to_crs("irish_grid", 54.5428, -5.93)
    assert (e, n) == pytest.approx((334013.4683, 368022.0816), abs=0.005)


def test_must_contain_and_nesting(cfg):
    for name, a in cfg.areas.items():
        for p in a.spec.get("must_contain", []):
            pt = cfg.points[p]
            assert a.contains_latlon(pt["lat"], pt["lon"], margin_m=500), f"{name} does not hold {p} with 500 m to spare"
        for inner in a.spec.get("contains", []):
            b = cfg.area(inner).bounds
            for lat, lon in ((b.north, b.west), (b.north, b.east), (b.south, b.west), (b.south, b.east)):
                assert a.contains_latlon(lat, lon), f"{inner} sticks out of {name}"


# ---- game-world frame ----

def test_slice_frozen_bounds_equal_computed(cfg):
    """The bounds MapNG wrote for the slice are what the centre, size and 4 m margin give: under 1 cm."""
    s = cfg.area("slice")
    assert s.bounds_frozen
    c, f = s.computed_bounds(), s.bounds
    m_lat = 111320.0
    m_lon = 111320.0 * math.cos(math.radians(s.lat0))
    for k, scale in (("north", m_lat), ("south", m_lat), ("west", m_lon), ("east", m_lon)):
        assert abs(getattr(c, k) - getattr(f, k)) * scale < CM, k


def test_frame_corners_and_centre(cfg):
    s = cfg.area("slice")
    b = s.bounds
    assert s.latlon_to_game(b.south, b.west) == pytest.approx((-1024.0, -1024.0), abs=1e-6)
    assert s.latlon_to_game(b.north, b.east) == pytest.approx((1024.0, 1024.0), abs=1e-6)
    # the area centre is within a metre of the origin (bounds are symmetric in metres, not in degrees)
    x, y = s.latlon_to_game(s.lat0, s.lon0)
    assert abs(x) < 1.0 and abs(y) < 1.0


def test_ground_scale_slice(cfg):
    sx, sy = cfg.area("slice").ground_scale()
    assert sx == pytest.approx(2056 / 2048, abs=0.0005)
    assert sy == pytest.approx(2056 / 2048, abs=0.0005)


def test_not_clamped_outside(cfg):
    s = cfg.area("slice")
    x, y = s.latlon_to_game(s.bounds.north + 0.01, s.bounds.east + 0.01)
    assert x > 1024 and y > 1024


def test_scalar_in_scalar_out(cfg):
    s = cfg.area("slice")
    x, y = s.latlon_to_game(54.5428, -5.93)
    assert isinstance(x, float) and isinstance(y, float)
    lat, lon = s.game_to_latlon(x, y)
    assert isinstance(lat, float) and isinstance(lon, float)
    e, n = cfg.latlon_to_crs("itm", lat, lon)
    assert isinstance(e, float)


# ---- round trips ----

@pytest.mark.parametrize("area_name", ["slice", "city_south", "full"])
def test_game_latlon_game(cfg, area_name):
    a = cfg.area(area_name)
    x, y = sample_game(a)
    lat, lon = a.game_to_latlon(x, y)
    x2, y2 = a.latlon_to_game(lat, lon)
    assert np.hypot(x2 - x, y2 - y).max() < CM


@pytest.mark.parametrize("crs_name", CRS_NAMES)
@pytest.mark.parametrize("area_name", ["slice", "city_south", "full"])
def test_game_crs_game(cfg, area_name, crs_name):
    a = cfg.area(area_name)
    x, y = sample_game(a)
    e, n = a.game_to_crs(crs_name, x, y)
    x2, y2 = a.crs_to_game(crs_name, e, n)
    assert np.hypot(x2 - x, y2 - y).max() < CM


@pytest.mark.parametrize("crs_name", CRS_NAMES)
@pytest.mark.parametrize("area_name", ["slice", "city_south", "full"])
def test_crs_game_crs(cfg, area_name, crs_name):
    """Projected -> game -> projected, and the grid-metre error of the game frame is not hidden by lat/lon."""
    a = cfg.area(area_name)
    x, y = sample_game(a)
    e, n = a.game_to_crs(crs_name, x, y)
    gx, gy = a.crs_to_game(crs_name, e, n)
    e2, n2 = a.game_to_crs(crs_name, gx, gy)
    assert np.hypot(e2 - e, n2 - n).max() < CM


@pytest.mark.parametrize("crs_name", CRS_NAMES)
def test_latlon_crs_latlon(cfg, crs_name):
    rng = np.random.default_rng(3)
    lat = rng.uniform(54.45, 54.65, 500)
    lon = rng.uniform(-6.1, -5.8, 500)
    e, n = cfg.latlon_to_crs(crs_name, lat, lon)
    la2, lo2 = cfg.crs_to_latlon(crs_name, e, n)
    err = aoi.haversine_m(lat, lon, la2, lo2)
    assert err.max() < CM


def test_irish_grid_to_itm(cfg):
    """The two grids are about 0.2 to 2 m apart in position, not 0: the conversion works and round-trips."""
    e, n = cfg.latlon_to_crs("irish_grid", 54.5428, -5.93)
    ie, itn = cfg.convert("irish_grid", "itm", e, n)
    ie2, itn2 = cfg.latlon_to_crs("itm", 54.5428, -5.93)
    assert math.hypot(ie - ie2, itn - itn2) < CM
    be, bn = cfg.convert("itm", "irish_grid", ie, itn)
    assert math.hypot(be - e, bn - n) < CM


# ---- heightmap grid ----

def test_pixel_round_trip_and_corners(cfg):
    s = cfg.area("slice")
    col = np.array([0, 1, 1023, 2047.0])
    row = np.array([0, 5, 1024, 2047.0])
    x, y = s.pixel_to_game(col, row)
    c2, r2 = s.game_to_pixel(x, y)
    assert np.allclose(c2, col, atol=1e-9) and np.allclose(r2, row, atol=1e-9)
    assert s.pixel_to_game(0, 0) == (-1024.0, 1023.0)
    assert s.pixel_to_game(2047, 2047) == (1023.0, -1024.0)


def test_terrain_shift_is_up_to_one_metre_at_north_east(cfg):
    """MapNG samples its DEM on a span/(n-1) grid, the game draws it on a 1 m grid: documented, bounded."""
    s = cfg.area("slice")
    assert s.terrain_shift(2047, 0) == pytest.approx((-1.0, -1.0), abs=1e-6)  # north-east: game is 1 m SW of the sample
    assert s.terrain_shift(0, 2047) == pytest.approx((0.0, 0.0), abs=1e-6)  # south-west corner agrees
    col, row = np.meshgrid(np.arange(0, 2048, 97), np.arange(0, 2048, 97))
    dx, dy = s.terrain_shift(col, row)
    assert np.abs(dx).max() <= 1.0001 and np.abs(dy).max() <= 1.0001


# ---- MapNG's own placement of the slice ----

def fixture_pairs():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data["pairs"]


def test_reproduces_mapng_placement_fixture(cfg):
    """300 OSM vertices of the slice and the DecalRoad nodes MapNG wrote for them (fixture, 0.001 m rounding)."""
    pairs = fixture_pairs()
    assert len(pairs) >= 200
    lat = np.array([p["lat"] for p in pairs])
    lon = np.array([p["lon"] for p in pairs])
    mx = np.array([p["x"] for p in pairs])
    my = np.array([p["y"] for p in pairs])
    x, y = cfg.area("slice").latlon_to_game(lat, lon)
    err = np.hypot(x - mx, y - my)
    assert err.max() < CM, err.max()
    assert abs((x - mx).mean()) < 0.001 and abs((y - my).mean()) < 0.001


def test_other_models_do_not_reproduce_mapng(cfg):
    """The same pairs through a local transverse Mercator are metres out, so the test above has teeth."""
    pairs = fixture_pairs()
    s = cfg.area("slice")
    tm = Transformer.from_crs("EPSG:4326", f"+proj=tmerc +lat_0={s.lat0} +lon_0={s.lon0} +k=1 +x_0=0 +y_0=0 "
                              "+datum=WGS84 +units=m", always_xy=True)
    lon = np.array([p["lon"] for p in pairs])
    lat = np.array([p["lat"] for p in pairs])
    x, y = tm.transform(lon, lat)
    err = np.hypot(x - np.array([p["x"] for p in pairs]), y - np.array([p["y"] for p in pairs]))
    assert np.median(err) > 0.5


@pytest.mark.skipif(not Path("H:/BelfastData/mapng/out/belfast_slice.zip").exists(), reason="slice export not on this machine")
def test_reproduces_mapng_placement_full_export():
    import aoi_check
    assert aoi_check.main([]) == 0
