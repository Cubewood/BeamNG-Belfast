"""T-401: every rule in rules/uk_markings.yaml cites its diagram and source."""

from pathlib import Path

import pytest
import yaml

PATH = Path(__file__).resolve().parents[2] / "rules" / "uk_markings.yaml"
NI_VALUES = {"same", "differs", "partial", "unverified"}
REQUIRED_DIAGRAMS = {
    "1008", "1008.1", "1005", "1005.1", "1004", "1004.1", "1013.1", "1012.1",
    "1003A", "1023A", "1002.1", "1001", "1001.3", "1001.4", "1003.1",
    "1003.3", "1003.4", "1043", "1018.1", "1017", "1049A",
}


@pytest.fixture(scope="module")
def doc():
    return yaml.safe_load(PATH.read_text(encoding="utf-8"))


def test_loads_and_has_rules(doc):
    assert doc["rules"], "no rules"
    ids = [r["id"] for r in doc["rules"]]
    assert len(ids) == len(set(ids)), "duplicate rule ids"


def test_required_diagrams_present(doc):
    have = {r["diagram"] for r in doc["rules"]}
    assert REQUIRED_DIAGRAMS <= have, sorted(REQUIRED_DIAGRAMS - have)


def test_every_rule_cites_diagram_and_source(doc):
    tags = set(doc["sources"])
    for r in doc["rules"]:
        rid = r["id"]
        assert str(r.get("diagram", "")).strip(), f"{rid}: no diagram"
        assert r.get("geometry"), f"{rid}: no dimensions"
        assert r.get("applies") is not None, f"{rid}: no applies"
        assert r.get("colour"), f"{rid}: no colour"
        srcs = r.get("sources")
        assert srcs, f"{rid}: no sources"
        for s in srcs:
            assert s.get("doc") in tags, f"{rid}: unknown source tag {s.get('doc')}"
            assert str(s.get("ref", "")).strip(), f"{rid}: source without section"


def test_ni_field(doc):
    for r in doc["rules"]:
        rid, ni = r["id"], r.get("ni")
        assert ni, f"{rid}: no ni field"
        assert ni.get("same") in NI_VALUES, f"{rid}: bad ni.same"
        assert str(ni.get("citation", "")).strip(), f"{rid}: no ni citation"
        if ni["same"] == "unverified":
            assert str(ni.get("reason", "")).strip(), f"{rid}: unverified ni needs reason"


def test_unverified_rules_say_why(doc):
    for r in doc["rules"]:
        assert isinstance(r.get("verified"), bool), f"{r['id']}: verified must be true or false"
        if not r["verified"]:
            assert str(r.get("unverified_reason", "")).strip(), f"{r['id']}: no reason"


def test_sources_have_urls(doc):
    for tag, s in doc["sources"].items():
        assert s["title"] and s["url"].startswith("https://"), tag


def test_module_dimensions_are_consistent(doc):
    for r in doc["rules"]:
        g = r["geometry"]
        if "module_mm" in g:
            assert g["mark_mm"] + g["gap_mm"] == g["module_mm"], r["id"]
