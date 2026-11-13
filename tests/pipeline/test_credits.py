"""Credits generator (T-208). Uses temporary folders and the repo's data/sources; never writes to H:\\BelfastData.

    uv run --no-project --python 3.12 --with pytest python -m pytest tests/pipeline/test_credits.py -q
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import credits as cr  # noqa: E402


def test_every_repo_manifest_gives_an_attribution_line():
    manifests = sorted((ROOT / "data" / "sources").glob("*.json"))
    assert manifests, "no manifests in data/sources"
    sources = cr.load_sources(ROOT / "data" / "sources")
    by_id = {s["id"]: s["lines"] for s in sources}
    for path in manifests:
        assert by_id.get(path.stem), f"{path.name} gave no attribution line"
        assert all(line.strip() for line in by_id[path.stem])


def test_elevation_lines_include_cyient_note_once():
    sources = {s["id"]: s["lines"] for s in cr.load_sources(cr.SOURCES_DIR)}
    cyient = [line for line in sources["elevation"] if "Cyient Europe Limited" in line]
    assert len(cyient) == 1
    assert "Department for Infrastructure (Rivers)" in cyient[0]
    assert any("Contains OSNI Open Data" in line for line in sources["elevation"])


def test_osm_licence_used_as_line():
    sources = {s["id"]: s["lines"] for s in cr.load_sources(cr.SOURCES_DIR)}
    assert any("OpenStreetMap contributors" in line for line in sources["osm"])


def test_manifest_without_attribution_or_licence_fails(tmp_path):
    (tmp_path / "bad.json").write_text(json.dumps({"version": 1, "files": [{"id": "x", "url": "u"}]}), encoding="utf-8")
    with pytest.raises(cr.ManifestError, match="bad.json"):
        cr.load_sources(tmp_path)


def test_cli_fails_and_writes_nothing_for_bad_manifest(tmp_path):
    (tmp_path / "bad.json").write_text(json.dumps({"version": 1}), encoding="utf-8")
    md = tmp_path / "CREDITS.md"
    md.write_text("# Credits\n", encoding="utf-8")
    rc = cr.main(["--sources-dir", str(tmp_path), "--credits-md", str(md), "--out-dir", str(tmp_path / "out")])
    assert rc == 2
    assert md.read_text(encoding="utf-8") == "# Credits\n"
    assert not (tmp_path / "out").exists()


def test_licence_only_manifest_is_accepted(tmp_path):
    (tmp_path / "ok.json").write_text(json.dumps({"version": 1, "licence": "CC0 1.0"}), encoding="utf-8")
    sources = cr.load_sources(tmp_path)
    assert sources[0] == {"id": "ok", "lines": ["CC0 1.0"], "in_level": True}


def test_markdown_keeps_hand_written_sections_and_is_idempotent(tmp_path):
    md = tmp_path / "CREDITS.md"
    md.write_text("# Credits\n\nIntro.\n\n" + cr.BEGIN + "\nold\n" + cr.END + "\n\n## Google Maps\n\nNone.\n",
                  encoding="utf-8")
    sources = cr.load_sources(cr.SOURCES_DIR)
    cr.update_credits_md(md, cr.render_block(sources))
    first = md.read_text(encoding="utf-8")
    assert "Intro." in first and "## Google Maps" in first and "None." in first
    assert "old" not in first.split(cr.BEGIN)[1]
    cr.update_credits_md(md, cr.render_block(sources))
    assert md.read_text(encoding="utf-8") == first


def test_level_credits_written_and_info_string(tmp_path):
    info = cr.write_level_credits(tmp_path / "belfast_slice", cr.SOURCES_DIR)
    text = (tmp_path / "belfast_slice" / "credits.txt").read_text(encoding="utf-8")
    assert "MapNG" in text and "Cyient Europe Limited" in text and "OpenStreetMap" in text
    assert info.startswith("Credits: ") and "Cyient Europe Limited" in info
    assert "Mapillary" not in text  # reference-only sources stay out of the level
