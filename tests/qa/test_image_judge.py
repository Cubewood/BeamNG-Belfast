"""Image judge (T-304) with the model call mocked. No GPU, no game."""
import argparse
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qa"))
import digest  # noqa: E402
import image_judge as ij  # noqa: E402
import vlm  # noqa: E402


def make_sweep(tmp_path, n=4):
    shots = []
    for i in range(n):
        rel = f"r1/r1-J{i:02d}-10m.jpg"
        (tmp_path / "r1").mkdir(exist_ok=True)
        (tmp_path / rel).write_bytes(b"jpg")
        shots.append({"id": f"r1-J{i:02d}-10m", "route": "r1", "junction_id": f"r1-J{i:02d}", "distance_m": 10.0,
                      "actual_distance_m": 10.0, "image": rel, "junction": {"x": 100.0 * i, "y": 5.0}})
    manifest = {"started": "2026-11-13T22:04:28", "shots": shots}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def fake_judge(table=None, calls=None):
    table = table or {}

    def judge(image, schema, prompt):
        if calls is not None:
            calls.append(image)
        name = Path(image).stem
        res = table.get(name, {"defect_type": "none", "confidence": 0.9, "reason": "road looks fine"})
        if isinstance(res, Exception):
            raise res
        return {"result": res, "tokens": 300, "seconds": 9.0, "attempts": 1}
    return judge


def fresh(tmp_path):
    return ij.load_results(tmp_path / ij.RESULTS_NAME, ij.config_hash(), False)


# ---- schema
def test_schema_has_none_and_fixed_list():
    assert "none" in ij.DEFECT_TYPES and len(set(ij.DEFECT_TYPES)) == len(ij.DEFECT_TYPES)
    ok = {"defect_type": "terrain-step", "confidence": 0.8, "reason": "ridge across the lane"}
    assert ij.check_result(ok) == []
    assert ij.check_result({**ok, "defect_type": "potholes"})
    assert ij.check_result({**ok, "confidence": 1.5})
    assert ij.check_result({**ok, "reason": "  "}) == ["reason is empty"]
    assert ij.check_result({"defect_type": "none", "confidence": 0.5})


def test_prompt_mentions_distance_and_every_type():
    p = ij.prompt_for({"actual_distance_m": 22.0})
    assert "22 m" in p
    assert all(t in p for t in ij.DEFECT_TYPES if t != "none")


# ---- judging and resume
def test_every_frame_gets_a_valid_result_and_file_is_saved(tmp_path):
    m = make_sweep(tmp_path)
    res = fresh(tmp_path)
    st = ij.judge_frames(m, tmp_path, res, tmp_path / ij.RESULTS_NAME, fake_judge(), log=lambda *_: None)
    assert st == {"judged": 4, "errors": 0, "stopped": None}
    saved = json.loads((tmp_path / ij.RESULTS_NAME).read_text())
    assert len(saved["frames"]) == 4
    for rec in saved["frames"].values():
        assert not ij.check_result(rec["result"])


def test_resume_skips_finished_and_retries_failed(tmp_path):
    m = make_sweep(tmp_path)
    path = tmp_path / ij.RESULTS_NAME
    res = fresh(tmp_path)
    boom = {"r1-J01-10m": vlm.VlmError("timeout")}
    ij.judge_frames(m, tmp_path, res, path, fake_judge(boom), limit=3, log=lambda *_: None)   # interrupted: 3 of 4
    assert sum(1 for r in res["frames"].values() if "result" in r) == 2 and len(res["frames"]) == 3
    calls = []
    res2 = ij.load_results(path, ij.config_hash(), False)                                       # a new process
    ij.judge_frames(m, tmp_path, res2, path, fake_judge(calls=calls), log=lambda *_: None)
    assert sorted(Path(c).stem for c in calls) == ["r1-J01-10m", "r1-J03-10m"]                # failed + never reached
    assert all("result" in r for r in res2["frames"].values()) and len(res2["frames"]) == 4
    assert ij.pending_shots(m, res2) == []


def test_missing_image_is_an_error_not_a_crash(tmp_path):
    m = make_sweep(tmp_path, 2)
    (tmp_path / m["shots"][0]["image"]).unlink()
    res = fresh(tmp_path)
    st = ij.judge_frames(m, tmp_path, res, tmp_path / ij.RESULTS_NAME, fake_judge(), log=lambda *_: None)
    assert st["errors"] == 1 and st["judged"] == 1
    assert "image missing" in res["frames"]["r1-J00-10m"]["error"]


def test_model_returning_nonsense_is_an_error(tmp_path):
    m = make_sweep(tmp_path, 1)
    res = fresh(tmp_path)
    bad = {"r1-J00-10m": {"defect_type": "none", "confidence": 0.5, "reason": ""}}
    st = ij.judge_frames(m, tmp_path, res, tmp_path / ij.RESULTS_NAME, fake_judge(bad), log=lambda *_: None)
    assert st["errors"] == 1 and "result" not in res["frames"]["r1-J00-10m"]


def test_stops_after_consecutive_errors_and_when_asked(tmp_path):
    m = make_sweep(tmp_path, 8)
    err = {f"r1-J{i:02d}-10m": vlm.VlmError("down") for i in range(8)}
    res = fresh(tmp_path)
    st = ij.judge_frames(m, tmp_path, res, tmp_path / ij.RESULTS_NAME, fake_judge(err), log=lambda *_: None)
    assert st["errors"] == ij.MAX_CONSECUTIVE_ERRORS and "in a row" in st["stopped"]
    res = fresh(tmp_path)
    flags = iter([None, "BeamNG started", None])
    st = ij.judge_frames(m, tmp_path, res, tmp_path / "x.json", fake_judge(), should_stop=lambda: next(flags),
                         log=lambda *_: None)
    assert st["judged"] == 1 and st["stopped"] == "BeamNG started"


def test_other_exceptions_are_not_swallowed(tmp_path):
    m = make_sweep(tmp_path, 1)
    with pytest.raises(KeyError):
        ij.judge_frames(m, tmp_path, fresh(tmp_path), tmp_path / "x.json", fake_judge({"r1-J00-10m": KeyError("x")}),
                        log=lambda *_: None)


# ---- findings
def test_findings_filter_and_feed_the_digest(tmp_path):
    m = make_sweep(tmp_path)
    table = {"r1-J00-10m": {"defect_type": "building-in-road", "confidence": 0.92, "reason": "house on the lane"},
             "r1-J01-10m": {"defect_type": "terrain-step", "confidence": 0.75, "reason": "ridge"},
             "r1-J02-10m": {"defect_type": "missing-markings", "confidence": 0.4, "reason": "unsure"}}
    res = fresh(tmp_path)
    ij.judge_frames(m, tmp_path, res, tmp_path / ij.RESULTS_NAME, fake_judge(table), log=lambda *_: None)
    f = ij.build_findings(m, tmp_path, res, 0.7)
    assert f["source"] == "image_judge" and f["run"] == "2026-11-13T22:04:28"
    assert [x["kind"] for x in f["findings"]] == ["building-in-road", "terrain-step"]   # none and low dropped
    top = f["findings"][0]
    assert top["severity"] == "high" and f["findings"][1]["severity"] == "medium"
    assert (top["x"], top["y"]) == (0.0, 5.0) and top["image"].endswith("r1-J00-10m.jpg")
    assert f["frames_total"] == 4 and f["frames_judged"] == 4
    p = tmp_path / ij.FINDINGS_NAME
    p.write_text(json.dumps(f))
    src = digest.classify(p)                                   # the real digest reads it
    assert src["source"] == "image_judge" and len(src["findings"]) == 2
    assert ij.counts_by_defect(res) == {"building-in-road": 1, "missing-markings": 1, "none": 1, "terrain-step": 1}


# ---- results file guards
def test_changed_prompt_refuses_to_mix(tmp_path):
    p = tmp_path / ij.RESULTS_NAME
    p.write_text(json.dumps({"config_hash": "old", "frames": {"a": {}}}))
    with pytest.raises(ij.JudgeError):
        ij.load_results(p, ij.config_hash(), False)
    assert ij.load_results(p, ij.config_hash(), True)["frames"] == {}


# ---- run(): guards, unload on every path
def args_for(tmp_path, **kw):
    d = dict(manifest=str(tmp_path), out_dir=None, limit=None, min_confidence=0.7, restart=False,
             reasoning="low", max_side=1280)
    d.update(kw)
    return argparse.Namespace(**d)


@pytest.fixture
def fakevlm(tmp_path, monkeypatch):
    lock_path = tmp_path / "gpu.lock"
    monkeypatch.setattr(vlm, "LOCK_PATH", lock_path)
    real = vlm.GpuLock
    monkeypatch.setattr(vlm, "GpuLock", lambda **kw: real(path=lock_path, poll=0.01, timeout=0.05, **kw))
    st = mock.Mock()
    st.running = False
    monkeypatch.setattr(vlm, "beamng_running", lambda: st.running)
    st.load = mock.Mock(return_value=None)
    st.unload = mock.Mock()
    monkeypatch.setattr(vlm, "load_model", st.load)
    monkeypatch.setattr(vlm, "unload_model", st.unload)
    monkeypatch.setattr(ij, "gpu_mem_mib", lambda: 1500)
    st.lock_path = lock_path
    return st


def test_run_refuses_while_beamng_runs(tmp_path, fakevlm):
    make_sweep(tmp_path)
    fakevlm.running = True
    assert ij.run(args_for(tmp_path), log=lambda *_: None) == 2
    fakevlm.load.assert_not_called()
    assert not (tmp_path / ij.RESULTS_NAME).exists()


def test_run_full_cycle_unloads_and_releases_lock(tmp_path, fakevlm, monkeypatch):
    make_sweep(tmp_path)
    monkeypatch.setattr(vlm, "judge_image", lambda img, schema, prompt, *a: fake_judge()(img, schema, prompt))
    seen = []
    fakevlm.load.side_effect = lambda lock=None: seen.append(fakevlm.lock_path.exists())
    assert ij.run(args_for(tmp_path), log=lambda *_: None) == 0
    assert seen == [True]                                   # the lock was held while loading
    fakevlm.unload.assert_called_once()
    assert not fakevlm.lock_path.exists()
    res = json.loads((tmp_path / ij.RESULTS_NAME).read_text())
    assert res["summary"]["judged"] == 4 and res["summary"]["by_defect_type"] == {"none": 4}
    assert res["gpu"]["vram_back_to_baseline"] is True
    assert (tmp_path / ij.FINDINGS_NAME).exists()


def test_run_unloads_when_judging_blows_up(tmp_path, fakevlm, monkeypatch):
    make_sweep(tmp_path)

    def judge(img, schema, prompt, *a):
        if "J01" in img:
            raise KeyboardInterrupt()
        return fake_judge()(img, schema, prompt)
    monkeypatch.setattr(vlm, "judge_image", judge)
    assert ij.run(args_for(tmp_path), log=lambda *_: None) == 1
    fakevlm.unload.assert_called_once()
    assert not fakevlm.lock_path.exists()
    res = json.loads((tmp_path / ij.RESULTS_NAME).read_text())
    assert res["summary"]["judged"] == 1                    # progress kept for the next run
    monkeypatch.setattr(vlm, "judge_image", lambda img, schema, prompt, *a: fake_judge()(img, schema, prompt))
    fakevlm.unload.reset_mock()
    assert ij.run(args_for(tmp_path), log=lambda *_: None) == 0
    assert json.loads((tmp_path / ij.RESULTS_NAME).read_text())["summary"]["judged"] == 4


def test_run_unloads_when_load_fails(tmp_path, fakevlm):
    make_sweep(tmp_path)
    fakevlm.load.side_effect = vlm.VlmError("lms load failed")
    assert ij.run(args_for(tmp_path), log=lambda *_: None) == 1
    fakevlm.unload.assert_called_once()
    assert not fakevlm.lock_path.exists()


def test_run_with_everything_done_does_not_touch_the_model(tmp_path, fakevlm, monkeypatch):
    make_sweep(tmp_path)
    monkeypatch.setattr(vlm, "judge_image", lambda img, schema, prompt, *a: fake_judge()(img, schema, prompt))
    assert ij.run(args_for(tmp_path), log=lambda *_: None) == 0
    fakevlm.load.reset_mock()
    fakevlm.unload.reset_mock()
    assert ij.run(args_for(tmp_path, min_confidence=0.5), log=lambda *_: None) == 0
    fakevlm.load.assert_not_called()
    fakevlm.unload.assert_not_called()


def test_run_waits_for_a_held_lock_then_gives_up(tmp_path, fakevlm):
    make_sweep(tmp_path)
    fakevlm.lock_path.write_text("t-005 something")
    with pytest.raises(vlm.VlmError):
        ij.run(args_for(tmp_path), log=lambda *_: None)
    fakevlm.load.assert_not_called()
    fakevlm.unload.assert_not_called()                      # never unload a model we did not load
    assert fakevlm.lock_path.read_text() == "t-005 something"
