"""Digest and issue cards on fixture QA results. No game, no model."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qa"))
import digest as dg  # noqa: E402


def suite(started, results, **extra):
    m = {"tool": "qa/drive_suite.py", "level": "belfast_slice", "started": started, "status": "ok",
         "thresholds": {"BUMP_ACC": 7.0, "DAMAGE_CRASH": 50.0}, "results": results,
         "summary": {"routes": len(results), "completed": sum(1 for r in results if r["completed"]),
                     "g3": {"pass": False, "failed": []}},
         "console": {"errors": []}, "game_stopped": {"clean": True}}
    m.update(extra)
    return m


def route(rid, completed=True, end="arrived", events=()):
    return {"id": rid, "completed": completed, "end_reason": end, "events": list(events)}


def crash(x, y, t=10.0, value=5000):
    return {"type": "crash", "t": t, "x": x, "y": y, "value": value}


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run(tmp, inputs, date="2026-11-13"):
    sources = dg.discover(tmp / "none", [Path(i) for i in inputs])
    cards, stats = dg.update_cards(tmp / "issues", sources)
    text = dg.build_digest(date, sources, cards, stats)
    (tmp / "DIGEST.md").write_text(text, encoding="utf-8", newline="\n")
    return cards, stats, text


def snapshot(tmp):
    return {p.name: p.read_bytes() for p in sorted((tmp / "issues").glob("Q-*.md"))}


def basic_run(tmp, started="2026-11-11T23:20:05", name="2026-11-11_232005"):
    return write(tmp / "qa" / "drive" / name / "metrics.json", suite(started, [
        route("a-loop", False, "crash", [crash(-211, -93, 39.2), {"type": "bump", "t": 39.5, "x": -210, "y": -90,
                                                                    "value": -15.2}]),
        route("b-loop", False, "crash", [crash(-211, -92, 53.4, 71774)]),
        route("c-out", True),
        route("d-round", True, events=[{"type": "bump", "t": 5, "x": 300, "y": 300, "value": 9.1}]),
    ]))


def test_rerun_creates_no_duplicate_cards(tmp_path):
    f = basic_run(tmp_path)
    cards, s1, _ = run(tmp_path, [f])
    first = snapshot(tmp_path)
    assert len(first) == 2 and len(s1["new"]) == 2                  # one crash card (two routes), one bump card
    cards, s2, _ = run(tmp_path, [f])
    assert s2["new"] == [] and len(s2["unchanged"]) == 2 and s2["updated"] == []
    assert snapshot(tmp_path) == first                               # byte-identical, nothing rewritten


def test_two_routes_crashing_at_the_same_place_are_one_card(tmp_path):
    cards, _, _ = run(tmp_path, [basic_run(tmp_path)])
    crashes = [c for c in cards.values() if c.kind == "crash"]
    assert len(crashes) == 1
    assert crashes[0].where_all() == ["a-loop", "b-loop"]
    assert crashes[0].latest()["value"] == 71774                     # the worse of the two


def test_bump_at_a_crash_is_folded_into_the_crash(tmp_path):
    f = write(tmp_path / "m.json", suite("2026-11-12T01:00:00", [route("a", False, "crash", [
        crash(1, 1, 39.2), {"type": "bump", "t": 39.5, "x": 3, "y": 4, "value": -15.2}])]))
    cards, _, _ = run(tmp_path, [f])
    assert [c.kind for c in cards.values()] == ["crash"]
    assert "impact bump 15.2" in next(iter(cards.values())).render()


def test_far_apart_crashes_are_separate_cards(tmp_path):
    f = write(tmp_path / "m.json", suite("2026-11-12T01:00:00", [
        route("a", False, "crash", [crash(0, 0)]), route("b", False, "crash", [crash(0, 80)])]))
    cards, _, _ = run(tmp_path, [f])
    assert len(cards) == 2


def test_next_run_updates_the_existing_card_with_a_new_history_row(tmp_path):
    run(tmp_path, [basic_run(tmp_path)])
    ids = set(snapshot(tmp_path))
    later = write(tmp_path / "qa" / "drive" / "2026-11-12_010000" / "metrics.json", suite(
        "2026-11-12T01:00:00", [route("a-loop", False, "crash", [crash(-209, -95, 40, 123)])]))
    cards, stats, _ = run(tmp_path, [later, tmp_path / "qa" / "drive" / "2026-11-11_232005" / "metrics.json"])
    assert set(snapshot(tmp_path)) == ids                            # same card ids, no new file
    assert stats["new"] == [] and len(stats["updated"]) == 1
    c = next(c for c in cards.values() if c.kind == "crash")
    assert len(c.rows) == 2 and c.last_seen() == "2026-11-12T01:00:00" and c.first_seen() == "2026-11-11T23:20:05"
    # only the latest run of a source is read, so the older run was ignored; the bump card was not seen
    assert stats["not_seen"] and stats["not_seen"][0] != c.id


def test_closed_card_reopens_on_a_new_run_but_not_on_a_rerun(tmp_path):
    f = basic_run(tmp_path)
    cards, _, _ = run(tmp_path, [f])
    cid = next(c.id for c in cards.values() if c.kind == "crash")
    p = tmp_path / "issues" / f"{cid}.md"
    p.write_text(p.read_text(encoding="utf-8").replace("status: open", "status: closed")
                 .replace(dg.NOTES_DEFAULT, "fixed by T-999 junction rebuild"), encoding="utf-8", newline="\n")
    _, stats, _ = run(tmp_path, [f])                                  # rerun of the same run: stays closed
    assert stats["reopened"] == [] and "status: closed" in p.read_text(encoding="utf-8")
    later = write(tmp_path / "x" / "metrics.json", suite("2026-11-12T01:00:00", [
        route("a-loop", False, "crash", [crash(-211, -93)])]))
    _, stats, _ = run(tmp_path, [later])
    assert stats["reopened"] == [cid]
    text = p.read_text(encoding="utf-8")
    assert "status: open" in text and "fixed by T-999 junction rebuild" in text   # notes survive


def test_position_less_findings_dedupe_by_kind_and_where(tmp_path):
    f = write(tmp_path / "m.json", suite("2026-11-12T01:00:00", [route("a", False, "timeout")]))
    cards, _, _ = run(tmp_path, [f])
    assert [c.kind for c in cards.values()] == ["timeout"] and next(iter(cards.values())).x is None
    f2 = write(tmp_path / "m2.json", suite("2026-11-13T01:00:00", [route("a", False, "timeout")]))
    cards, stats, _ = run(tmp_path, [f2])
    assert len(cards) == 1 and len(stats["updated"]) == 1


def test_probe_and_mapng_drive_inputs_and_image_path(tmp_path):
    base = tmp_path / "baseline" / "2026-11-11"
    write(base / "tests.json", {"level": "x", "byKind": {"sidewalk": {"n": 10, "fail": 2}}, "failures": [
        {"k": "sidewalk", "why": "object standing on the sidewalk: s_1", "x": 10, "y": 20, "z": 1, "road": "residential",
         "dz": 0.34, "obj": "s_1"},
        {"k": "sidewalk", "why": "sidewalk floating over the terrain", "x": 500, "y": 20, "z": 1,
         "road": "residential", "gap": 0.52}]})
    shot = base / "drive" / "lvl" / "route02_crash_crash_front.jpg"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"x")
    write(base / "drive.json", {"level": "lvl", "results": [
        {"i": 1, "kind": "turns", "road": "secondary", "completed": False, "progress": 3, "length": 9,
         "events": [{"type": "crash", "x": 199, "y": 283}],
         "incident": {"type": "crash", "shots": {"front": shot.name}}}]})
    cards, _, _ = run(tmp_path, [base])
    kinds = sorted(c.kind for c in cards.values())
    assert kinds == ["crash", "sidewalk-floating", "sidewalk-object"]
    crash_card = next(c for c in cards.values() if c.kind == "crash")
    assert crash_card.image == str(shot) and str(shot) in crash_card.render()
    obj = next(c for c in cards.values() if c.kind == "sidewalk-object")
    assert "dz 0.34 m" in obj.latest()["metric"]


def test_findings_file_from_lint_or_image_judge(tmp_path):
    f = write(tmp_path / "findings.json", {"source": "image_judge", "run": "2026-11-13T02:00:00", "findings": [
        {"kind": "missing-markings", "x": 5, "y": 6, "where": "junction J12", "severity": "medium", "value": 0.8,
         "metric": "confidence 0.80", "detail": "no give way line", "image": "H:\\BelfastData\\x.jpg"}]})
    cards, stats, text = run(tmp_path, [f])
    c = next(iter(cards.values()))
    assert c.kind == "missing-markings" and c.image == "H:\\BelfastData\\x.jpg" and c.sources() == {"image_judge"}
    again, stats, _ = run(tmp_path, [f])
    assert stats["new"] == [] and len(again) == 1
    assert "image_judge" in text


def test_card_text_round_trips(tmp_path):
    cards, _, _ = run(tmp_path, [basic_run(tmp_path)])
    for c in cards.values():
        back = dg.Card.parse(c.render())
        assert back.render() == c.render()


def test_digest_stays_under_60_lines_with_many_cards(tmp_path):
    results = [route(f"r{i}", False, "crash", [crash(i * 100, 0, value=100 + i)]) for i in range(150)]
    f = write(tmp_path / "m.json", suite("2026-11-12T01:00:00", results))
    cards, stats, text = run(tmp_path, [f])
    assert len(cards) == 150 and len(stats["new"]) == 150
    lines = text.splitlines()
    assert len(lines) < 60
    assert any("more open cards" in ln for ln in lines)


def test_empty_input_gives_a_short_digest(tmp_path):
    cards, stats, text = run(tmp_path, [])
    assert cards == {} and "none found" in text and len(text.splitlines()) < 60


def test_ids_continue_after_existing_cards(tmp_path):
    run(tmp_path, [basic_run(tmp_path)])
    f = write(tmp_path / "m.json", suite("2026-11-14T01:00:00", [route("z", False, "crash", [crash(900, 900)])]))
    _, stats, _ = run(tmp_path, [f])
    assert stats["new"] == ["Q-003"]
