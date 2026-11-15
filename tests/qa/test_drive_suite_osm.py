"""T-406: the drive suite reads the osm.json the routes were built from unless --osm names one. No game."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qa"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import drive_suite as ds  # noqa: E402


def test_given_osm_wins():
    doc = {"sources": {"osm_file": r"H:\a\osm.json"}}
    assert ds.osm_file_for(doc, r"H:\b\osm.json") == Path(r"H:\b\osm.json")


def test_routes_file_source_is_the_default():
    doc = {"sources": {"osm_file": r"H:\BelfastData\mapng\areas\t406_slice\osm.json"}}
    assert ds.osm_file_for(doc, None) == Path(r"H:\BelfastData\mapng\areas\t406_slice\osm.json")


def test_old_default_when_the_routes_file_names_none():
    assert ds.osm_file_for({}, None) == ds.OSM_JSON
    assert ds.osm_file_for({"sources": {}}, None) == ds.OSM_JSON
