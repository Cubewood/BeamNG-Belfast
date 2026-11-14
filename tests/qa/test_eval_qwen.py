"""Scoring of the image judge against the evaluation set (qa/eval_qwen.py)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qa"))
sys.path.insert(0, str(ROOT / "tools"))

import eval_qwen as eq  # noqa: E402


def planted(fid, ty, visible="yes"):
    return {"frame": fid, "image": f"planted/{fid}.jpg", "source_frame": "r-J01-30m", "planted": True,
            "planted_fault": {"type": ty}, "visual_label": {"defect_type": ty, "planted_visible": visible}}


def clean(fid, visual="none"):
    return {"frame": fid, "image": f"clean/{fid}.jpg", "source_frame": "r-J01-10m", "planted": False,
            "planted_fault": None, "visual_label": {"defect_type": visual}}


LABELS = ([planted(f"P{i}", "object-in-road") for i in range(6)] + [planted("P6", "terrain-step", "partly")]
          + [clean("C0"), clean("C1"), clean("C2", "missing-markings"), clean("C3")])
PREDS = {"P0": ("object-in-road", 0.9), "P1": ("object-in-road", 0.8), "P2": ("object-in-road", 0.75),
         "P3": ("object-in-road", 0.6), "P4": ("object-in-road", 0.55), "P5": ("none", 0.9),
         "P6": ("road-geometry", 0.8),
         "C0": ("object-in-road", 0.5), "C1": ("none", 0.9), "C2": ("missing-markings", 0.7), "C3": ("none", 0.6)}


def test_manifest_takes_distance_from_source_frame():
    m = eq.build_manifest(LABELS)
    assert len(m["shots"]) == len(LABELS)
    assert m["shots"][0]["distance_m"] == 30 and m["shots"][-1]["distance_m"] == 10
    assert m["shots"][0]["image"] == "planted/P0.jpg"


def test_recall_and_family():
    sc = eq.score(LABELS, PREDS)
    r = sc["recall"]["object-in-road"]
    assert r["n"] == 6 and r["0"]["strict"] == round(5 / 6, 4)
    assert r["0.7"]["strict"] == round(3 / 6, 4)
    assert r["0.5"]["any_defect"] == round(5 / 6, 4)
    t = sc["recall"]["terrain-step"]
    assert t["n_visible_partly"] == 1 and t["0.5"]["strict"] == 0.0 and t["0.5"]["family"] == 1.0
    assert t["0.5"]["strict_visible_yes"] is None


def test_precision_counts_clean_frames_as_false_positives():
    sc = eq.score(LABELS, PREDS)
    p = sc["precision"]["object-in-road"]
    assert p["0.5"]["predicted"] == 6 and p["0.5"]["tp"] == 5 and p["0.5"]["fp_clean"] == 1
    assert p["0.5"]["precision"] == round(5 / 6, 4)
    assert p["0.6"]["precision"] == 1.0 and p["0.6"]["predicted"] == 4
    mm = sc["precision"]["missing-markings"]["0.7"]
    assert mm["precision"] == 0.0 and mm["precision_clean_visual_ok"] == 1.0   # real defect, not planted


def test_clean_false_positive_rates_are_separate():
    sc = eq.score(LABELS, PREDS)
    fp = sc["clean_false_positive"]["0.5"]
    assert fp["vs_planted_false"] == {"n": 4, "flagged": 2, "rate": 0.5}
    assert fp["vs_visual_labels"]["visual_none"]["flagged"] == 1
    assert fp["vs_visual_labels"]["visual_defect"]["type_matches_visual"] == 1


def test_confusion_table_rows():
    sc = eq.score(LABELS, PREDS)
    assert sc["confusion"]["object-in-road"]["none"] == 1
    assert sc["confusion"]["none (clean)"]["object-in-road"] == 1
    assert sum(sc["confusion"]["none (clean)"].values()) == 4


def test_threshold_choice_needs_precision_and_support():
    sc = eq.score(LABELS, PREDS)
    ch = eq.choose_thresholds(sc)
    assert ch["object-in-road"]["threshold"] is None or ch["object-in-road"]["precision"] >= 0.9
    assert ch["missing-markings"]["threshold"] is None           # never right against the planted truth
    # enough predictions at 0.5 with one clean false positive: 5/6 < 0.9, so the first qualifying threshold is higher
    assert ch["object-in-road"]["threshold"] in (None, 0.6, 0.7, 0.8, 0.9)


def test_disagreements_most_confident_first_and_capped():
    rows, total = eq.disagreements(LABELS, PREDS, cap=2)
    assert total == 4 and len(rows) == 2
    assert rows[0]["frame"] == "P5" and rows[0]["confidence"] == 0.9
    assert rows[0]["image"].endswith("P5.jpg")
