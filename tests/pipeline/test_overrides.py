"""Overrides (T-207): schema, evidence rules, apply, determinism, conflict detection. Synthetic cut, no network.

    uv run --no-project --python 3.12 --with pytest --with pyyaml python -m pytest tests/pipeline/test_overrides.py -q
"""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import overrides as ov  # noqa: E402
import osm_pin  # noqa: E402

BOX = {"south": 54.0, "north": 54.1, "west": -6.1, "east": -6.0}
INSIDE = [54.05, -6.05]
OUTSIDE_PT = [55.5, -4.0]


def way(i, tags, pts):
    return {"type": "way", "id": i, "geometry": [{"lat": a, "lon": b} for a, b in pts], "tags": dict(sorted(tags.items()))}


def make_cut():
    sq = [(54.05, -6.05), (54.05, -6.04), (54.06, -6.04), (54.06, -6.05), (54.05, -6.05)]
    return [
        {"type": "node", "id": 5, "lat": 54.05, "lon": -6.05, "tags": {"highway": "traffic_signals"}},
        way(100, {"highway": "tertiary", "lanes": "3", "name": "Test Road", "oneway": "yes"},
            [(54.050, -6.050), (54.051, -6.049)]),
        way(101, {"highway": "residential"}, [(54.060, -6.060), (54.061, -6.059)]),
        way(200, {"building": "yes"}, sq),
        {"type": "relation", "id": 900, "tags": {"type": "multipolygon", "building": "yes"},
         "members": [{"type": "way", "ref": 200, "role": "outer",
                      "geometry": [{"lat": a, "lon": b} for a, b in sq]},
                     {"type": "way", "ref": 999, "role": "inner", "geometry": [{"lat": 54.0, "lon": -6.0}]}]},
    ]


def entry(**kw):
    """A valid raw entry (one tag change on way 100), with overrides from kw."""
    cut = make_cut()
    raw = {
        "id": "test-road-lanes",
        "target": {"type": "way", "id": 100},
        "at": INSIDE,
        "against": {"hash": ov.element_hash(cut[1]), "version": 4, "tags": cut[1]["tags"]},
        "change": {"set_tags": {"lanes": "2"}, "delete_tags": ["oneway"]},
        "evidence": [{"kind": "qa", "ref": "issues/Q-001.md", "note": "fixture"}],
        "author": "tester",
        "date": "2026-11-13",
    }
    raw.update(kw)
    return raw


def valid(raw):
    e, errs = ov.validate_entry(raw, "t.yaml")
    assert errs == [] and e is not None
    return e


def rejected(raw, needle):
    e, errs = ov.validate_entry(raw, "t.yaml")
    assert e is None
    assert any(needle in m for m in errs), errs


# ---------------------------------------------------------------- schema

def test_valid_entry():
    e = valid(entry())
    assert e.key == "way/100" and e.change["delete_tags"] == ["oneway"] and e.date == "2026-11-13"


def test_rejects_entry_without_evidence():
    raw = entry()
    del raw["evidence"]
    rejected(raw, "evidence is required")
    rejected(entry(evidence=[]), "evidence is required")
    rejected(entry(evidence=[{"kind": "qa", "ref": ""}]), "ref must say where to look")
    rejected(entry(evidence=[{"kind": "hunch", "ref": "my head"}]), "kind must be one of")


@pytest.mark.parametrize("ref", [
    "https://www.google.com/maps/@54.5,-5.9,3a,75y/data=!3m1",
    "https://maps.app.goo.gl/abc123",
    "Google Street View, Hospital Road",
    "https://streetviewpixels-pa.googleapis.com/v1/thumbnail",
])
def test_rejects_google_evidence(ref):
    rejected(entry(evidence=[{"kind": "survey", "ref": ref}]), "Google content is not allowed")


def test_rejects_google_anywhere_in_the_entry():
    rejected(entry(note="checked on google earth"), "Google content is not allowed")
    rejected(entry(evidence=[{"kind": "osni", "ref": "OSNI tile 12", "note": "also seen in Street View"}]),
             "Google content is not allowed")


def test_accepts_mapillary_and_osni_evidence():
    valid(entry(evidence=[{"kind": "mapillary", "ref": "https://www.mapillary.com/app/?pKey=123456789"},
                          {"kind": "osni", "ref": "OSNI Open Data Largescale Boundaries, tile J3573"}]))


def test_requires_hash_target_at_author_date_and_change():
    for key, needle in [("against", "against.hash is required"), ("target", "target must be"), ("at", "at is required"),
                        ("author", "author is required"), ("date", "date is required"), ("change", "change is required")]:
        raw = entry()
        del raw[key]
        rejected(raw, needle)


def test_rejects_bad_values():
    rejected(entry(against={"hash": "abc"}), "against.hash must look like")
    rejected(entry(date="13/11/2026"), "date must be YYYY-MM-DD")
    rejected(entry(target={"type": "way", "id": -1}), "positive integer")
    rejected(entry(target={"type": "area", "id": 1}), "target.type")
    rejected(entry(change={"set_tags": {"lanes": 2}}), "non-empty strings")
    rejected(entry(change={"set_tags": {"lanes": "2"}, "delete_tags": ["lanes"]}), "both set and deleted")
    rejected(entry(change={"delete": True, "set_tags": {"a": "b"}}), "cannot be combined")
    rejected(entry(change={"position": [54.0, -6.0]}), "nodes only")
    rejected(entry(change={"geometry": [[54.0, -6.0]]}), "at least 2")
    rejected(entry(change={"bogus": 1}), "unknown keys")
    rejected(entry(colour="red"), "unknown keys")
    rejected(entry(id="X"), "id must be")
    rejected(entry(change={"extra": {"Bad-Key": 1}}), "lower_snake_case")


def test_relations_take_no_geometry():
    raw = entry(target={"type": "relation", "id": 900}, change={"geometry": [[54.0, -6.0], [54.1, -6.1]]})
    e, errs = ov.validate_entry(raw)
    assert e is None and any("ways only" in m or "relations take" in m for m in errs)


def test_yaml_file_round_trip_and_checks(tmp_path):
    good = """
version: 1
overrides:
  - id: test-road-lanes
    target: {type: way, id: 100}
    at: [54.05, -6.05]
    against: {hash: "sha256:%s", version: 4}
    change:
      set_tags: {lanes: "2"}
    evidence:
      - {kind: qa, ref: docs/decisions/001-generator.md}
    author: tester
    date: 2026-11-13
""" % ("0" * 64)
    (tmp_path / "a.yaml").write_text(good, encoding="utf-8")
    entries, errs = ov.load_dir(tmp_path)
    assert errs == [] and len(entries) == 1 and entries[0].date == "2026-11-13"
    assert ov.check_refs(entries) == []

    # unquoted number is read by YAML as an int and refused
    (tmp_path / "b.yaml").write_text(good.replace('{lanes: "2"}', "{lanes: 2}").replace("test-road-lanes", "other-id")
                                     .replace("id: 100", "id: 101"), encoding="utf-8")
    entries, errs = ov.load_dir(tmp_path)
    assert any("b.yaml" in m and "non-empty strings" in m for m in errs)

    # same target twice, and the same id twice
    (tmp_path / "b.yaml").write_text(good.replace("test-road-lanes", "another-id"), encoding="utf-8")
    _, errs = ov.load_dir(tmp_path)
    assert any("already the target" in m for m in errs)
    (tmp_path / "b.yaml").write_text(good.replace("id: 100", "id: 101"), encoding="utf-8")
    _, errs = ov.load_dir(tmp_path)
    assert any("id already used" in m for m in errs)

    # a qa ref to a missing file
    (tmp_path / "b.yaml").unlink()
    (tmp_path / "a.yaml").write_text(good.replace("docs/decisions/001-generator.md", "issues/Q-9999.md"), encoding="utf-8")
    entries, _ = ov.load_dir(tmp_path)
    assert any("not a file in the repo" in m for m in ov.check_refs(entries))

    # wrong file shape
    (tmp_path / "a.yaml").write_text("- just: a list\n", encoding="utf-8")
    _, errs = ov.load_dir(tmp_path)
    assert any("must be a mapping" in m for m in errs)


def test_committed_overrides_validate():
    entries, errs = ov.load_dir(ov.OVERRIDES_DIR)
    assert errs == [] and ov.check_refs(entries) == []


# ---------------------------------------------------------------- apply

def test_apply_sets_and_deletes_tags_and_leaves_input_alone():
    cut = make_cut()
    before = copy.deepcopy(cut)
    r = ov.apply_overrides(cut, [valid(entry())])
    assert cut == before
    assert [o.status for o in r.outcomes] == [ov.APPLIED]
    w = next(e for e in r.elements if e["id"] == 100)
    assert w["tags"] == {"highway": "tertiary", "lanes": "2", "name": "Test Road"}
    assert list(w["tags"]) == sorted(w["tags"])
    assert [(e["type"], e["id"]) for e in r.elements] == [(e["type"], e["id"]) for e in cut]


def test_changed_way_is_a_conflict_not_applied():
    """OSM changed the way after the fix was written: report it, apply nothing."""
    cut = make_cut()
    e = valid(entry())
    cut[1]["tags"]["lanes"] = "4"            # a newer pin has lanes=4
    cut[1]["tags"] = dict(sorted(cut[1]["tags"].items()))
    r = ov.apply_overrides(cut, [e])
    (o,) = r.outcomes
    assert o.status == ov.CONFLICT_CHANGED and o.is_conflict
    assert "lanes: '3' -> '4'" in o.detail and "way/100" in o.detail and "version 4" in o.detail
    assert next(x for x in r.elements if x["id"] == 100)["tags"]["lanes"] == "4"   # untouched
    assert r.conflicts == [o]


def test_changed_geometry_is_a_conflict_and_says_so():
    cut = make_cut()
    e = valid(entry())
    cut[1]["geometry"][1] = {"lat": 54.0515, "lon": -6.0485}
    (o,) = ov.apply_overrides(cut, [e]).outcomes
    assert o.status == ov.CONFLICT_CHANGED and "geometry or members changed" in o.detail


def test_missing_target_inside_area_is_a_conflict_outside_area_is_not():
    cut = [x for x in make_cut() if x["id"] != 100]
    e_in = valid(entry())
    e_out = valid(entry(id="far-away-fix", at=OUTSIDE_PT))
    r = ov.apply_overrides(cut, [e_in, e_out], bounds=BOX)
    by = {o.id: o for o in r.outcomes}
    assert by["test-road-lanes"].status == ov.CONFLICT_MISSING
    assert by["far-away-fix"].status == ov.OUTSIDE and not by["far-away-fix"].is_conflict


def test_fix_adopted_upstream_is_reported_not_conflict():
    cut = make_cut()
    e = valid(entry())
    cut[1]["tags"] = {"highway": "tertiary", "lanes": "2", "name": "Test Road"}   # OSM now has the fix
    (o,) = ov.apply_overrides(cut, [e]).outcomes
    assert o.status == ov.RESOLVED and not o.is_conflict


def test_conflict_does_not_block_other_entries():
    cut = make_cut()
    good = valid(entry())
    stale_raw = entry(id="residential-name", target={"type": "way", "id": 101},
                      change={"set_tags": {"name": "Other Road"}})
    stale_raw["against"] = {"hash": "sha256:" + "1" * 64}
    r = ov.apply_overrides(cut, [good, valid(stale_raw)])
    assert {o.id: o.status for o in r.outcomes} == {"test-road-lanes": ov.APPLIED, "residential-name": ov.CONFLICT_CHANGED}
    assert next(x for x in r.elements if x["id"] == 101)["tags"] == {"highway": "residential"}


def test_delete_way_drops_it_from_relations():
    cut = make_cut()
    raw = entry(id="remove-building", target={"type": "way", "id": 200}, change={"delete": True})
    raw["against"] = {"hash": ov.element_hash(cut[3])}
    r = ov.apply_overrides(cut, [valid(raw)])
    assert not any(e["id"] == 200 for e in r.elements)
    rel = next(e for e in r.elements if e["type"] == "relation")
    assert [m["ref"] for m in rel["members"]] == [999]
    # a relation left with no way members goes too
    cut2 = make_cut()
    cut2[4]["members"] = cut2[4]["members"][:1]
    raw["against"] = {"hash": ov.element_hash(cut2[3])}
    r2 = ov.apply_overrides(cut2, [valid(raw)])
    assert not any(e["type"] == "relation" for e in r2.elements)
    assert any("had no way members left" in n for o in r2.outcomes for n in o.notes)


def test_geometry_edit_updates_relation_member_copy_and_keeps_closure():
    cut = make_cut()
    pts = [[54.05, -6.05], [54.05, -6.03], [54.06, -6.03], [54.06, -6.05], [54.05, -6.05]]
    raw = entry(id="widen-building", target={"type": "way", "id": 200}, change={"geometry": pts})
    raw["against"] = {"hash": ov.element_hash(cut[3])}
    r = ov.apply_overrides(cut, [valid(raw)])
    w = next(e for e in r.elements if e["id"] == 200)
    assert w["geometry"][1] == {"lat": 54.05, "lon": -6.03}
    rel = next(e for e in r.elements if e["type"] == "relation")
    assert rel["members"][0]["geometry"] == w["geometry"]
    # opening a closed way is refused as an invalid fix, not applied
    raw2 = entry(id="open-building", target={"type": "way", "id": 200}, change={"geometry": pts[:-1]})
    raw2["against"] = {"hash": ov.element_hash(cut[3])}
    (o,) = ov.apply_overrides(cut, [valid(raw2)]).outcomes
    assert o.status == ov.CONFLICT_INVALID and "closed way" in o.detail


def test_node_position_and_delete_of_absent_tag():
    cut = make_cut()
    raw = entry(id="move-signal", target={"type": "node", "id": 5}, change={"position": [54.0501, -6.0501]})
    raw["against"] = {"hash": ov.element_hash(cut[0])}
    r = ov.apply_overrides(cut, [valid(raw)])
    n = next(e for e in r.elements if e["type"] == "node")
    assert (n["lat"], n["lon"]) == (54.0501, -6.0501)
    bad = entry(change={"delete_tags": ["no_such_tag"]})
    (o,) = ov.apply_overrides(cut, [valid(bad)]).outcomes
    assert o.status == ov.CONFLICT_INVALID and "no_such_tag" in o.detail


def test_extra_attributes_go_to_the_sidecar_only():
    cut = make_cut()
    raw = entry(change={"extra": {"lane_divider_offset_m": 1.2, "junction_control": "signals"}})
    r = ov.apply_overrides(cut, [valid(raw)])
    assert r.extras == {"way/100": {"override": "test-road-lanes", "junction_control": "signals", "lane_divider_offset_m": 1.2}}
    assert r.elements == cut        # OSM data unchanged


def test_relation_hash_ignores_member_geometry():
    a, b = make_cut()[4], make_cut()[4]
    b["members"][1]["geometry"] = [{"lat": 1.0, "lon": 1.0}]
    assert ov.element_hash(a) == ov.element_hash(b)
    b["tags"]["building"] = "no"
    assert ov.element_hash(a) != ov.element_hash(b)


def test_apply_is_deterministic_and_order_independent():
    cut = make_cut()
    a = valid(entry())
    raw_b = entry(id="building-tag", target={"type": "way", "id": 200}, change={"set_tags": {"building": "house"}})
    raw_b["against"] = {"hash": ov.element_hash(cut[3])}
    b = valid(raw_b)
    r1 = ov.apply_overrides(cut, [a, b])
    r2 = ov.apply_overrides(copy.deepcopy(cut), [b, a])
    assert osm_pin.cut_bytes(r1.elements) == osm_pin.cut_bytes(r2.elements)
    assert [o.as_dict() for o in r1.outcomes] == [o.as_dict() for o in r2.outcomes]


# ---------------------------------------------------------------- CLI

def _write_yaml(path, raw_entries):
    import yaml
    path.write_text(yaml.safe_dump({"version": 1, "overrides": raw_entries}, allow_unicode=True), encoding="utf-8")


def test_cli_apply_conflict_stops_the_build_and_keep_going_writes(tmp_path, capsys):
    cut_file = tmp_path / "t.overpass.json"
    cut = make_cut()
    cut_file.write_bytes(osm_pin.cut_bytes(cut))
    odir = tmp_path / "ov"
    odir.mkdir()
    _write_yaml(odir / "fixes.yaml", [entry()])
    out = tmp_path / "out" / "t.overpass.json"
    base = ["apply", "--cut", str(cut_file), "--dir", str(odir), "--out", str(out)]

    assert ov.main(base) == 0
    applied = json.loads(out.read_text(encoding="utf-8"))["elements"]
    assert next(e for e in applied if e["id"] == 100)["tags"]["lanes"] == "2"
    report = json.loads(out.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["applied"] == 1 and report["conflicts"] == 0
    assert report["output_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    first = out.read_bytes()
    assert ov.main(base) == 0 and out.read_bytes() == first           # same inputs, same bytes

    # a newer pin changes the way
    cut[1]["tags"]["lanes"] = "4"
    cut_file.write_bytes(osm_pin.cut_bytes(cut))
    out.unlink()
    assert ov.main(base) == 3 and not out.exists()
    assert "CONFLICT" in capsys.readouterr().out
    assert ov.main(base + ["--keep-going"]) == 0
    kept = json.loads(out.read_text(encoding="utf-8"))["elements"]
    assert next(e for e in kept if e["id"] == 100)["tags"]["lanes"] == "4"


def test_cli_validate_and_invalid_files_block_apply(tmp_path, capsys):
    cut_file = tmp_path / "t.overpass.json"
    cut_file.write_bytes(osm_pin.cut_bytes(make_cut()))
    odir = tmp_path / "ov"
    odir.mkdir()
    bad = entry()
    del bad["evidence"]
    _write_yaml(odir / "fixes.yaml", [bad])
    assert ov.main(["validate", "--dir", str(odir)]) == 2
    assert "evidence is required" in capsys.readouterr().err
    assert ov.main(["apply", "--cut", str(cut_file), "--dir", str(odir), "--out", str(tmp_path / "o.json")]) == 2
    assert not (tmp_path / "o.json").exists()


def test_cli_hash(tmp_path, capsys):
    cut_file = tmp_path / "t.overpass.json"
    cut = make_cut()
    cut_file.write_bytes(osm_pin.cut_bytes(cut))
    assert ov.main(["hash", "way", "100", "--cut", str(cut_file)]) == 0
    assert capsys.readouterr().out.strip() == ov.element_hash(cut[1])
    assert ov.main(["hash", "way", "12345", "--cut", str(cut_file)]) == 1
