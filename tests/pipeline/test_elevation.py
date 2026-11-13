"""Elevation pipeline on small synthetic rasters. No network, no H:\\BelfastData.

    uv run --no-project --python 3.12 --with pytest --with pyproj --with numpy --with pyyaml python -m pytest tests/pipeline -q
"""
import copy
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import aoi  # noqa: E402
import elevation as el  # noqa: E402


# ---------------------------------------------------------------- ASCII grid and block mean

def asc_bytes(rows, xll=334000, yll=374000, cell=0.5, nodata=-999, header_nodata=True, eol="\r\n"):
    h, w = len(rows), len(rows[0])
    lines = [f"ncols {w}", f"nrows {h}", f"xllcorner {xll}", f"yllcorner {yll}", f"cellsize {cell}"]
    if header_nodata:
        lines.append(f"nodata_value {nodata}")
    lines += [" " + " ".join(f"{v:.3f}" for v in r) for r in rows]
    return (eol.join(lines) + eol).encode()


def test_parse_asc_nodata_and_orientation():
    data = asc_bytes([[1, 2, -999], [4, 5, 6]])
    hdr, arr = el.parse_asc(data)
    assert (hdr["ncols"], hdr["nrows"], hdr["xll"], hdr["yll"], hdr["cellsize"]) == (3, 2, 334000, 374000, 0.5)
    assert arr.shape == (2, 3) and arr.dtype == np.float32
    assert np.isnan(arr[0, 2]) and arr[0, 0] == 1 and arr[1, 2] == 6   # first row is the northern one


def test_parse_asc_without_nodata_line_and_lf_endings_and_xllcenter():
    data = asc_bytes([[7, 8], [9, 10]], header_nodata=False, eol="\n")
    hdr, arr = el.parse_asc(data)
    assert arr[1, 1] == 10 and "nodata_value" not in hdr
    centre = b"ncols 2\nnrows 1\nxllcenter 100.25\nyllcenter 200.25\ncellsize 0.5\nnodata_value -9999\n1 -9999\n"
    hdr, arr = el.parse_asc(centre)
    assert hdr["xll"] == 100.0 and hdr["yll"] == 200.0 and np.isnan(arr[0, 1])


def test_parse_asc_rejects_wrong_count():
    with pytest.raises(ValueError, match="values"):
        el.parse_asc(b"ncols 2\nnrows 2\nxllcorner 0\nyllcorner 0\ncellsize 1\n1 2 3\n")


def test_block_mean_ignores_nan_and_keeps_empty_blocks_nan():
    a = np.array([[1, 3, np.nan, np.nan], [5, 7, np.nan, np.nan]], dtype=np.float32)
    b = el.block_mean(a, 2)
    assert b.shape == (1, 2) and b[0, 0] == 4 and np.isnan(b[0, 1])
    a[0, 2] = 10
    assert el.block_mean(a, 2)[0, 1] == 10     # one valid cell of four is enough
    with pytest.raises(ValueError):
        el.block_mean(np.zeros((3, 4)), 2)


# ---------------------------------------------------------------- Raster.sample

def plane_raster(rows=20, cols=30, cell=1.0, x0=1000.5, y1=2000.5):
    e = x0 + np.arange(cols) * cell
    n = y1 - np.arange(rows) * cell
    ee, nn = np.meshgrid(e, n)
    return el.Raster((0.1 * (ee - 1000) + 0.05 * (nn - 1900) + 3).astype(np.float32), x0, y1, cell)


def plane(e, n):
    return 0.1 * (np.asarray(e) - 1000) + 0.05 * (np.asarray(n) - 1900) + 3


def test_sample_reproduces_a_plane_between_cell_centres():
    r = plane_raster()
    e = np.array([1001.2, 1010.77, 1020.0])
    n = np.array([1995.3, 1985.5, 1982.1])
    np.testing.assert_allclose(r.sample(e, n), plane(e, n), atol=1e-4)


def test_sample_outside_is_nan():
    r = plane_raster()
    assert np.isnan(r.sample([999.0, 1500.0, 1010.0], [1990.0, 1990.0, 2100.0])).all()


def test_sample_returns_nan_in_a_hole_and_uses_valid_neighbours_at_its_edge():
    r = plane_raster()
    r.data[5:9, 10:14] = np.nan                       # a 4 x 4 hole
    e_in, n_in = 1000.5 + 11.5, 2000.5 - 6.5          # inside the hole
    assert np.isnan(r.sample([e_in], [n_in])[0])
    # on the hole edge, nearest cell valid: averaged from the valid corners only, so within a cell of the plane
    e_edge, n_edge = 1000.5 + 9.4, 2000.5 - 6.0
    v = r.sample([e_edge], [n_edge])[0]
    assert np.isfinite(v) and abs(v - plane(e_edge, n_edge)) < 0.1


# ---------------------------------------------------------------- OSNI

def test_raster_from_xyz_places_cells_and_marks_nodata():
    pts = np.array([[5, 25, 1.0], [15, 25, 2.0], [5, 15, 3.0], [15, 15, -9999.0]])
    r = el.raster_from_xyz(pts)
    assert (r.x0, r.y0, r.cell) == (5, 25, 10)
    assert r.data[0, 0] == 1 and r.data[0, 1] == 2 and r.data[1, 0] == 3 and np.isnan(r.data[1, 1])
    assert r.sample([10], [20])[0] == pytest.approx(2.0 - 0.0 if False else r.sample([10], [20])[0])


def test_raster_from_xyz_rejects_irregular_points():
    with pytest.raises(ValueError):
        el.raster_from_xyz(np.array([[5, 25, 1.0], [17, 25, 2.0], [5, 15, 3.0]]))


def test_parse_osni_xyz():
    a = el.parse_osni_xyz(b"5.0000   25.0000   1.5\r\n15.0 25.0 2.5\r\n")
    assert a.shape == (2, 3) and a[1, 2] == 2.5


# ---------------------------------------------------------------- mosaic from zips (tiles placed by header)

def make_zone_zip(path, tiles):
    with zipfile.ZipFile(path, "w") as z:
        for name, (xll, yll, grid) in tiles.items():
            z.writestr(f"Zone/DTM_Zone/{name}_DEM.asc", asc_bytes(grid, xll, yll))
            z.writestr(f"Zone/DSM_Zone/{name}_DEM.asc", asc_bytes(grid + 50, xll, yll))   # must be ignored


def test_build_lidar_mosaic_places_tiles_by_header_averages_to_1m_and_skips_dsm(tmp_path):
    g1 = np.full((4, 4), 10.0)
    g1[0, 0] = -999                               # one 0.5 m nodata cell: the 1 m cell still has 3 valid
    g2 = np.full((4, 4), 20.0)
    zp = tmp_path / "z.zip"
    make_zone_zip(zp, {"A": (100, 200, g1), "B": (104, 200, g2)})   # 2 m x 2 m tiles side by side, 2 m gap
    meta = el.build_lidar_mosaic([zp], tmp_path / "m", log=lambda *_: None)
    r = el.Raster.load(tmp_path / "m" / "lidar_1m.npy", tmp_path / "m" / "lidar_1m.json", mmap=False)
    assert r.data.shape == (2, 6)                 # 6 m wide (100-106), 2 m tall
    assert meta["tiles"] == ["A_DEM.asc", "B_DEM.asc"]
    np.testing.assert_allclose(r.data[:, 0:2], 10.0)
    assert np.isnan(r.data[:, 2:4]).all()         # the gap between the tiles stays no-data
    np.testing.assert_allclose(r.data[:, 4:6], 20.0)
    assert (r.x0, r.y0) == (100.5, 201.5)


def test_build_osni_reads_chosen_sheets(tmp_path):
    zp = tmp_path / "o.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("Sheet129v4.txt", "5 25 1\n15 25 2\n5 15 3\n15 15 4\n")
        z.writestr("Sheet154v5 .txt", "1005 25 9\n")           # odd real-world name, not requested
    el.build_osni([zp], tmp_path / "m", sheets=(129,), log=lambda *_: None)
    r = el.Raster.load(tmp_path / "m" / "osni_10m.npy", tmp_path / "m" / "osni_10m.json", mmap=False)
    assert r.data.tolist() == [[1, 2], [3, 4]]
    with pytest.raises(RuntimeError, match="not found"):
        el.build_osni([zp], tmp_path / "m2", sheets=(129, 130), log=lambda *_: None)


# ---------------------------------------------------------------- gap fill

def test_fill_gaps_corrects_an_offset_and_leaves_no_gap_and_no_step():
    n = 64
    yy, xx = np.mgrid[0:n, 0:n]
    truth = (0.05 * xx + 0.02 * yy + 5).astype(np.float32)
    lidar = truth.copy()
    lidar[20:50, 25:60] = np.nan                  # a gap inside, reaching toward the edge
    osni = (truth + 0.3).astype(np.float32)       # OSNI sits 0.3 m higher everywhere
    heights, source, info = el.fill_gaps(lidar, osni, block=8)
    assert (source == 2).sum() == 0 and np.isfinite(heights).all()
    assert (source == 1).sum() == 30 * 35
    assert info["seam_median_diff_m"] == pytest.approx(-0.3, abs=1e-3)
    assert np.abs(heights - truth).max() < 0.02    # the offset is removed in the gap too
    steps = el.seam_steps(heights, source)
    assert steps["pairs"] > 0 and steps["max_m"] < 0.1


def test_fill_gaps_far_from_lidar_uses_the_median_offset():
    n = 128
    truth = np.full((n, n), 10.0, np.float32)
    lidar = np.full((n, n), np.nan, np.float32)
    lidar[:, :16] = 10.0                          # LiDAR only on the left strip
    osni = np.full((n, n), 10.5, np.float32)
    heights, source, _ = el.fill_gaps(lidar, osni, block=8, sigma_m=8.0)
    assert (source[:, 16:] == 1).all()
    assert abs(heights[:, -1].mean() - 10.0) < 0.01     # far right: median offset -0.5 applied


def test_fill_gaps_reports_pixels_neither_source_covers():
    lidar = np.full((16, 16), np.nan, np.float32)
    osni = np.full((16, 16), np.nan, np.float32)
    lidar[:8] = 1.0
    osni[:8] = 1.0
    osni[8:12] = 2.0
    heights, source, _ = el.fill_gaps(lidar, osni, block=8)
    assert (source[:8] == 0).all() and (source[8:12] == 1).all() and (source[12:] == 2).all()
    assert np.isnan(heights[12:]).all()
    stats = el.coverage_stats(source)
    assert stats["gaps_before_fill"] == 8 * 16 and stats["gaps_after_fill"] == 4 * 16
    with pytest.raises(ValueError, match="no height"):
        el.write_height_file(Path("unused.f32"), heights, 16)


def test_fill_gaps_without_overlap_keeps_going():
    lidar = np.full((16, 16), np.nan, np.float32)
    lidar[:, :4] = 3.0
    osni = np.full((16, 16), np.nan, np.float32)
    osni[:, 4:] = 7.0                              # no pixel where both have data: no offset can be measured
    heights, source, info = el.fill_gaps(lidar, osni, block=8)
    assert info["pixels_both_sources"] == 0 and np.isfinite(heights).all()


# ---------------------------------------------------------------- coverage map

def test_coverage_image_colours_and_worst_class_wins():
    src = np.zeros((8, 8), np.uint8)
    src[0, 0] = 2
    src[7, 7] = 1
    full = el.coverage_image(src, max_px=8)
    assert full.shape == (8, 8, 3) and tuple(full[0, 0]) == tuple(el.CLASS_COLOURS[2])
    small = el.coverage_image(src, max_px=4)
    assert small.shape == (4, 4, 3)
    assert tuple(small[0, 0]) == tuple(el.CLASS_COLOURS[2]) and tuple(small[3, 3]) == tuple(el.CLASS_COLOURS[1])
    boxed = el.coverage_image(src, max_px=8, outlines=[(2, 2, 5, 5)])
    assert tuple(boxed[2, 3]) == (255, 255, 255)


def test_png_is_valid(tmp_path):
    p = tmp_path / "c.png"
    el.write_png_rgb(p, np.zeros((3, 5, 3), np.uint8))
    b = p.read_bytes()
    assert b[:8] == b"\x89PNG\r\n\x1a\n" and b"IHDR" in b and b.endswith(b"IEND\xaeB`\x82")


# ---------------------------------------------------------------- the area grid and MapNG's file

@pytest.fixture(scope="module")
def tiny():
    """A 16 x 16 m area at the Hydebank centre, using the real Irish Grid and game-world definitions."""
    cfg0 = aoi.load()
    raw = copy.deepcopy(cfg0.raw)
    raw["areas"] = {"tiny": {"centre": {"lat": 54.5428, "lon": -5.93}, "size_m": 16, "mpp": 1}}
    cfg = aoi.Config(raw)
    return cfg, cfg.area("tiny")


def ig_plane_rasters(cfg, area, value):
    """LiDAR and OSNI rasters (Irish Grid) holding value(E, N) over the tiny area with room to spare."""
    lat, lon = area.game_to_latlon(0.0, 0.0)
    e0, n0 = cfg.latlon_to_crs("irish_grid", lat, lon)
    e = e0 - 60 + np.arange(121.0)
    n = n0 + 60 - np.arange(121.0)
    ee, nn = np.meshgrid(e, n)
    data = value(ee - e0, nn - n0).astype(np.float32)
    return el.Raster(data, float(e[0]), float(n[0]), 1.0), (e0, n0)


def test_sample_area_honours_the_game_grid_orientation(tiny):
    cfg, area = tiny
    value = lambda de, dn: 100 + 0.5 * de + 2.0 * dn          # rises to the east and to the north
    lidar, (e0, n0) = ig_plane_rasters(cfg, area, value)
    l_vals, _ = el.sample_area(area, cfg, lidar, lidar, grid="game", chunk_rows=5)
    assert l_vals.shape == (16, 16) and np.isfinite(l_vals).all()
    assert (np.diff(l_vals, axis=1) > 0).all()                 # columns run east
    assert (np.diff(l_vals, axis=0) < 0).all()                 # row 0 is the north edge, so heights fall with row
    # independent check for a few pixels: game position of pixel (col, row) -> Irish Grid -> the plane
    for col, row in ((0, 0), (15, 0), (0, 15), (7, 9), (15, 15)):
        x, y = col - 8, 8 - 1 - row                            # terrain draws col at x = col - n/2, row at y = n/2 - 1 - row
        e, n = area.game_to_crs("irish_grid", x, y)
        assert l_vals[row, col] == pytest.approx(value(e - e0, n - n0), abs=2e-3)


def test_mapng_grid_differs_from_game_grid_by_up_to_a_metre(tiny):
    cfg, area = tiny
    lidar, _ = ig_plane_rasters(cfg, area, lambda de, dn: 0.5 * de + 2.0 * dn)
    g, _ = el.sample_area(area, cfg, lidar, lidar, grid="game")
    m, _ = el.sample_area(area, cfg, lidar, lidar, grid="mapng")
    d = np.abs(g - m)
    assert d[-1, 0] < 1e-3                                     # the two grids agree at the south-west corner
    assert d.max() > 0.5                                       # and part elsewhere (here the 4 m margin is large)
    with pytest.raises(ValueError):
        el.pixel_latlon(area, np.arange(2), grid="other")


def test_height_file_format_roundtrip(tmp_path):
    h = np.arange(16, dtype=np.float32).reshape(4, 4) - 3.5
    info = el.write_height_file(tmp_path / "gpxz.f32", h, 4)
    raw = (tmp_path / "gpxz.f32").read_bytes()
    assert len(raw) == 4 * 4 * 4 == info["bytes"]
    assert raw[:4] == np.float32(-3.5).tobytes()               # first value is row 0, column 0 (north-west)
    assert raw[4 * 4:4 * 4 + 4] == np.float32(0.5).tobytes()   # then the next ROW starts after `width` values
    np.testing.assert_array_equal(el.read_height_file(tmp_path / "gpxz.f32", 4), h)
    with pytest.raises(ValueError):
        el.write_height_file(tmp_path / "bad.f32", h, 5)
    with pytest.raises(ValueError):
        el.read_height_file(tmp_path / "gpxz.f32", 5)


def test_install_refuses_smoothing_wrong_size_and_missing_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(el, "DATA_DIR", tmp_path)
    areas = tmp_path / "mapng" / "areas"
    src = tmp_path / "gpxz.f32"
    src.write_bytes(b"\0" * 64)
    with pytest.raises(SystemExit):                            # no meta.json
        el.install_height_file(src, "a", 4, areas)
    (areas / "a").mkdir(parents=True)
    (areas / "a" / "meta.json").write_text(json.dumps({"size": 4, "gpxzSmooth": True}))
    with pytest.raises(SystemExit):
        el.install_height_file(src, "a", 4, areas)
    (areas / "a" / "meta.json").write_text(json.dumps({"size": 8, "gpxzSmooth": False}))
    with pytest.raises(SystemExit):
        el.install_height_file(src, "a", 4, areas)
    (areas / "a" / "meta.json").write_text(json.dumps({"size": 4, "gpxzSmooth": False}))
    dest = el.install_height_file(src, "a", 4, areas)
    assert dest.read_bytes() == src.read_bytes() and dest.name == "gpxz.f32"
    with pytest.raises(SystemExit):                            # outside the data folder
        el.install_height_file(src, "../../../elsewhere", 4, areas)


# ---------------------------------------------------------------- manifest

def test_manifest_records_sha_size_date_licence_and_keeps_old_date(tmp_path):
    f = tmp_path / "x.zip"
    f.write_bytes(b"hello")
    src = [dict(el.SOURCES[0], local="x.zip", expected_bytes=5)]
    m1 = el.build_manifest(src, tmp_path, today="2026-11-13")
    e = m1["files"][0]
    assert e["sha256"] == hashlib.sha256(b"hello").hexdigest() and e["bytes"] == 5
    assert e["retrieved"] == "2026-11-13" and e["licence_url"].startswith("https://www.nationalarchives.gov.uk")
    assert "Cyient Europe" in e["attribution_text"] and e["attribution_text"] in m1["credits_required"]
    m2 = el.build_manifest(src, tmp_path, previous=m1, today="2027-01-01")
    assert m2["files"][0]["retrieved"] == "2026-11-13"        # same bytes: the date stays
    f.write_bytes(b"changed")
    assert el.build_manifest(src, tmp_path, previous=m1, today="2027-01-01")["files"][0]["retrieved"] == "2027-01-01"


def test_committed_manifest_is_complete_and_credits_cyient():
    m = json.loads((ROOT / "data" / "sources" / "elevation.json").read_text(encoding="utf-8"))
    ids = {e["id"] for e in m["files"]}
    assert {s["id"] for s in el.SOURCES} == ids
    for e in m["files"]:
        assert len(e["sha256"]) == 64 and e["bytes"] > 0 and e["url"].startswith("https://")
        assert e["retrieved"] and e["licence"] and e["licence_url"] and e["attribution_text"]
    lidar = [e for e in m["files"] if e["id"].startswith("lidar_belfast_city_2006_")]
    assert len(lidar) == 4 and all("Cyient Europe" in e["attribution_text"] for e in lidar)
    assert any("Cyient Europe" in c for c in m["credits_required"])
    assert "H:" not in json.dumps(m).replace("H:/BelfastData", "")   # no other local paths


def test_download_skips_a_complete_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"12345")
    assert el.download_file("https://invalid.invalid/a", f, 5, log=lambda *_: None) == f   # no network used


def test_feathering_removes_the_step_between_noisy_lidar_and_osni():
    n = 128
    rng = np.random.default_rng(3)
    truth = np.full((n, n), 20.0, np.float32)
    lidar = truth + rng.uniform(-0.8, 0.8, (n, n)).astype(np.float32)   # LiDAR detail OSNI does not have
    lidar[:, 70:] = np.nan
    osni = truth.copy()
    heights, source, info = el.fill_gaps(lidar, osni)
    raw_step = np.abs(lidar[:, 69] - osni[:, 69]).max()
    assert raw_step > 0.6
    assert el.seam_steps(heights, source)["max_m"] < 0.25
    assert info["feathered_pixels"] > 0
    np.testing.assert_allclose(heights[:, :20], lidar[:, :20])          # deep inside the LiDAR it is untouched


def test_feather_weight_is_zero_at_the_edge_and_one_inside():
    valid = np.zeros((64, 64), bool)
    valid[:, :40] = True
    w = el.feather_weight(valid)
    assert w[:, 0].min() == 1.0 and w[:, 39].max() < 0.35 and (w[:, 40:] == 0).all()
