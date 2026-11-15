"""Height-profile probe (I-008) on synthetic rasters. No BelfastData folder needed."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import aoi  # noqa: E402
import elevation as el  # noqa: E402
import elevation_probe as ep  # noqa: E402


def test_line_points_spacing_and_end():
    x, y, s = ep.line_points((0, 0), (3, 4), 1.0)
    assert list(s) == [0, 1, 2, 3, 4, 5]
    assert x[-1] == pytest.approx(3) and y[-1] == pytest.approx(4)
    x, y, s = ep.line_points((2, 2), (2, 2), 1.0)       # zero length: one point, no division by zero
    assert len(s) == 1 and x[0] == 2


def test_largest_drop_window_and_nan():
    s = np.arange(0.0, 10.0)
    z = np.array([5, 5, 5, 4, 3, 2, 2, 2, 2, 2.0])       # falls 3 m between s=2 and s=5
    d, s0, s1 = ep.largest_drop(s, z, 3.0)
    assert (d, s0, s1) == (3.0, 2.0, 5.0)
    assert ep.largest_drop(s, z, 2.0)[0] == 2.0           # a shorter window sees less of the fall
    z[3] = np.nan
    assert ep.largest_drop(s, z, 3.0)[0] == 3.0           # NaN skipped, the fall is still found
    assert ep.largest_drop(s, np.full(10, np.nan), 3.0)[0] == 0.0
    assert ep.largest_drop(s, np.arange(10.0), 5.0)[0] == 0.0   # rising ground has no drop


def test_sample_file_follows_game_pixel_grid():
    area = aoi.load().area("slice")
    n = area.size_px
    h = np.zeros((n, n), dtype=np.float32)
    h[:, :] = np.arange(n, dtype=np.float32)[None, :]      # height = column, so height = x + n/2
    z = ep.sample_file(h, area, np.array([-196.0, 0.5]), np.array([-146.0, 10.0]))
    assert z[0] == pytest.approx(-196 + n / 2) and z[1] == pytest.approx(0.5 + n / 2)
    h = np.zeros((n, n), dtype=np.float32)
    h[:, :] = np.arange(n, dtype=np.float32)[:, None]      # height = row, so height = n/2 - 1 - y
    z = ep.sample_file(h, area, np.array([0.0]), np.array([-146.0]))
    assert z[0] == pytest.approx(n / 2 - 1 + 146)


def test_nearest_lidar_distance():
    area = aoi.load().area("slice")
    n = area.size_px
    mask = np.ones((n, n), dtype=np.uint8)
    assert ep.nearest_lidar_m(mask, area, 0, 0) == float("inf")
    c, r = area.game_to_pixel(30.0, 40.0)
    mask[int(r), int(c)] = 0
    assert ep.nearest_lidar_m(mask, area, 0.0, 0.0) == pytest.approx(50.0, abs=1.0)


def test_probe_reports_offset_and_no_lidar():
    cfg = aoi.load()
    area = cfg.area("slice")
    n = area.size_px
    heights = np.full((n, n), 5.0, dtype=np.float32)
    lat, lon = area.game_to_latlon(0.0, 0.0)
    e, nn = cfg.latlon_to_crs("irish_grid", lat, lon)
    osni = el.Raster(np.full((60, 60), 4.5, dtype=np.float32), float(e) - 300, float(nn) + 300, 10.0)
    lidar = el.Raster(np.full((60, 60), np.nan, dtype=np.float32), float(e) - 300, float(nn) + 300, 10.0)
    r = ep.probe(cfg, "slice", (-50, 0), (50, 0), 12.0, heights, lidar, osni)
    assert r["offset_min"] == pytest.approx(0.5, abs=1e-5) and r["offset_max"] == pytest.approx(0.5, abs=1e-5)
    assert r["lidar_points"] == 0 and r["drop_file"][0] == 0.0 and r["drop_osni"][0] == 0.0
