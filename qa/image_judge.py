#!/usr/bin/env python3
"""Image judge (T-304): the local vision model judges every junction-sweep frame against a JSON schema.

    uv run --no-project --python 3.12 --with requests --with pillow python qa/image_judge.py \
        [--manifest SWEEP_DIR_OR_manifest.json] [--limit N] [--min-confidence 0.7] [--restart]

Reads the manifest written by qa/junction_sweep.py (default: the newest run under H:\\BelfastData\\qa\\sweep),
loads Qwen through tools/vlm.py, judges each frame (defect type from a fixed list including `none`, confidence,
short reason), and writes next to the sweep (outside git):

    image_judge_results.json    every frame: result, or the error, plus run header (GPU memory before/after)
    findings_image_judge.json   defects at or above --min-confidence, in the format qa/digest.py reads

Resumable: frames that already have a valid result are skipped; frames that failed are tried again. A different
prompt or schema refuses to mix with old results unless --restart is given.

GPU rule: refuses to start while BeamNG runs, holds H:\\BelfastData\\gpu.lock for the whole run, stops (and
unloads) if BeamNG appears mid-run, and always unloads the model on the way out, error or not.
Exit codes: 0 every frame judged; 1 some frame has no valid result or other failure; 2 BeamNG running.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import vlm  # noqa: E402

SWEEP_ROOT = Path(r"H:\BelfastData\qa\sweep")
RESULTS_NAME = "image_judge_results.json"
FINDINGS_NAME = "findings_image_judge.json"
SOURCE = "image_judge"

# Provisional until T-306 measures precision per defect type on the evaluation set.
MIN_CONFIDENCE = 0.7           # findings below this are kept in the results file only
HIGH_CONFIDENCE = 0.85         # severity high at or above; medium below
MAX_CONSECUTIVE_ERRORS = 5     # the server is probably gone
VRAM_SLACK_MIB = 300

DEFECT_TYPES = [
    "none",
    "missing-markings",     # a junction approach with no give-way, stop or centre-line paint where one belongs
    "wrong-markings",       # US-style, wrong colour, wrong layout, broken or smeared paint
    "building-in-road",     # a building or wall standing on or across the carriageway
    "object-in-road",       # pole, sign, tree, fence or other prop in the lane
    "terrain-step",         # a kerb-like step, ridge, hole or ramp across the carriageway
    "floating-object",      # something hovering above the ground
    "buried-object",        # something sunk into the ground
    "missing-texture",      # a pink, white, black, checkered or stretched surface
    "wrong-side-sign",      # a sign facing the wrong way or on the wrong side of the road
    "road-geometry",        # gaps, tears, spikes or twisted road mesh
    "other",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "defect_type": {"type": "string", "enum": DEFECT_TYPES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["defect_type", "confidence", "reason"],
    "additionalProperties": False,
}

PROMPT = (
    "You are checking a generated 3D map of Belfast (UK, traffic drives on the left) for build defects. "
    "The picture is a driver's-eye view from inside a right-hand-drive car, about {distance} m before a junction. "
    "Ignore the car interior (dash, mirrors, pillars, wipers) and the sky. Look only at the road, kerbs, markings, "
    "signs and objects outside.\n"
    "Pick the single most serious defect that is clearly visible, or `none` when the scene looks like a plausible "
    "road. Defect types:\n"
    "- missing-markings: no road paint where a junction needs it (give-way lines, centre line, stop line)\n"
    "- wrong-markings: paint that is not UK style, wrong colour, broken or smeared\n"
    "- building-in-road: a building or wall standing on or across the carriageway\n"
    "- object-in-road: a pole, sign, tree, fence or prop in the driving lane\n"
    "- terrain-step: a step, ridge, hole or ramp across the carriageway\n"
    "- floating-object: something hovering above the ground\n"
    "- buried-object: something sunk into the ground\n"
    "- missing-texture: a pink, white, black, checkered or badly stretched surface\n"
    "- wrong-side-sign: a sign facing the wrong way or on the wrong side of the road\n"
    "- road-geometry: gaps, tears, spikes or twisted road mesh\n"
    "- other: a clear defect that fits none of these\n"
    "Answer with JSON: defect_type (one of the types above or none), confidence (0 to 1, how sure you are of "
    "that answer), reason (one short sentence saying what you see and where). Do not report anything you cannot "
    "see."
)


# ---------------------------------------------------------------- small helpers

class JudgeError(RuntimeError):
    pass


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def prompt_for(shot: dict) -> str:
    d = shot.get("actual_distance_m", shot.get("distance_m"))
    return PROMPT.format(distance=f"{d:g}" if isinstance(d, (int, float)) else "10 to 30")


def config_hash() -> str:
    blob = json.dumps({"schema": SCHEMA, "prompt": PROMPT, "model": vlm.MODEL_KEY}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def gpu_mem_mib() -> int | None:
    """Used video memory (MiB, GPU 0) from nvidia-smi, None when it cannot be read."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
        return int(out[0].strip())
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def find_manifest(arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        return p / "manifest.json" if p.is_dir() else p
    runs = sorted(p for p in SWEEP_ROOT.glob("*/manifest.json") if p.is_file()) if SWEEP_ROOT.is_dir() else []
    if not runs:
        raise JudgeError(f"no sweep manifest under {SWEEP_ROOT}; pass --manifest")
    return runs[-1]


def check_result(result) -> list[str]:
    """Schema errors plus the rule that a reason must say something."""
    errs = vlm.validate(result, SCHEMA)
    if not errs and not str(result["reason"]).strip():
        errs.append("reason is empty")
    return errs


# ---------------------------------------------------------------- results file

def load_results(path: Path, cfg: str, restart: bool) -> dict:
    if path.exists() and not restart:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise JudgeError(f"{path} is not valid JSON ({e}); fix it or use --restart") from e
        if data.get("config_hash") != cfg:
            raise JudgeError(f"{path} was made with a different prompt, schema or model; use --restart")
        data.setdefault("frames", {})
        return data
    return {"tool": "qa/image_judge.py", "task": "T-304", "model": vlm.MODEL_KEY, "config_hash": cfg,
            "schema": SCHEMA, "prompt": PROMPT, "started": now_iso(), "frames": {}}


def frame_done(rec: dict | None) -> bool:
    return bool(rec) and "result" in rec and not check_result(rec["result"])


def pending_shots(manifest: dict, results: dict) -> list[dict]:
    return [s for s in manifest.get("shots", []) if not frame_done(results["frames"].get(s["id"]))]


# ---------------------------------------------------------------- judging

def judge_frames(manifest: dict, sweep_dir: Path, results: dict, results_path: Path, judge,
                 limit: int | None = None, should_stop=lambda: None, log=print) -> dict:
    """Judge pending frames one by one, saving after each. `judge(image_path, schema, prompt)` -> vlm result dict.
    `should_stop()` returns a reason string to stop early. Returns {'judged', 'errors', 'stopped'}."""
    todo = pending_shots(manifest, results)
    if limit is not None:
        todo = todo[:limit]
    judged = errors = consecutive = 0
    stopped = None
    for i, shot in enumerate(todo, 1):
        stopped = should_stop()
        if stopped:
            break
        image = sweep_dir / shot["image"]
        rec = {"id": shot["id"], "route": shot.get("route"), "junction_id": shot.get("junction_id"),
               "distance_m": shot.get("distance_m"), "image": shot["image"], "judged_at": now_iso()}
        try:
            if not image.is_file():
                raise vlm.VlmError(f"image missing: {image}")
            out = judge(str(image), SCHEMA, prompt_for(shot))
            errs = check_result(out["result"])
            if errs:
                raise vlm.VlmError(f"result invalid: {errs}")
            rec.update({"result": out["result"], "tokens": out.get("tokens"), "seconds": out.get("seconds"),
                        "attempts": out.get("attempts")})
            judged += 1
            consecutive = 0
        except vlm.VlmError as e:
            rec["error"] = str(e)
            errors += 1
            consecutive += 1
        results["frames"][shot["id"]] = rec
        results["updated"] = now_iso()
        write_json(results_path, results)
        r = rec.get("result")
        log(f"[{i}/{len(todo)}] {shot['id']}: " + (f"{r['defect_type']} {r['confidence']:.2f}" if r
                                                  else f"ERROR {rec['error'][:120]}"))
        if consecutive >= MAX_CONSECUTIVE_ERRORS:
            stopped = f"{consecutive} errors in a row; stopping"
            break
    return {"judged": judged, "errors": errors, "stopped": stopped}


def severity_for(confidence: float) -> str:
    return "high" if confidence >= HIGH_CONFIDENCE else "medium"


def build_findings(manifest: dict, sweep_dir: Path, results: dict, min_confidence: float) -> dict:
    """Findings file in the format of docs/digest.md: one per defect frame at or above min_confidence."""
    by_id = {s["id"]: s for s in manifest.get("shots", [])}
    findings = []
    for sid, rec in results["frames"].items():
        r = rec.get("result")
        shot = by_id.get(sid)
        if not r or not shot or r["defect_type"] == "none" or r["confidence"] < min_confidence:
            continue
        j = shot.get("junction") or {}
        f = {"kind": r["defect_type"], "where": f"{shot.get('junction_id', sid)} {shot.get('distance_m', 0):g}m",
             "severity": severity_for(r["confidence"]), "value": r["confidence"],
             "metric": f"confidence {r['confidence']:.2f}", "detail": r["reason"],
             "image": str(sweep_dir / shot["image"])}
        if j.get("x") is not None and j.get("y") is not None:
            f["x"], f["y"] = j["x"], j["y"]
        findings.append(f)
    findings.sort(key=lambda f: (-f["value"], f["where"]))
    return {"source": SOURCE, "run": manifest.get("started") or results.get("started"),
            "sweep": str(sweep_dir), "judged_at": results.get("updated"), "min_confidence": min_confidence,
            "frames_total": len(manifest.get("shots", [])),
            "frames_judged": sum(1 for r in results["frames"].values() if "result" in r),
            "findings": findings}


def counts_by_defect(results: dict) -> dict:
    return dict(sorted(Counter(r["result"]["defect_type"] for r in results["frames"].values()
                               if "result" in r).items()))


# ---------------------------------------------------------------- run

def unload_and_verify(baseline: int | None, wait_s: float = 45) -> dict:
    """Unload the model and wait for video memory to settle. Never raises."""
    info = {"unloaded": False, "vram_after_mib": None, "vram_back_to_baseline": None}
    try:
        vlm.unload_model()
        info["unloaded"] = True
    except Exception as e:  # noqa: BLE001 - this is the cleanup path
        info["unload_error"] = str(e)
    deadline = time.time() + wait_s
    while True:
        mem = gpu_mem_mib()
        info["vram_after_mib"] = mem
        if mem is None or baseline is None or mem <= baseline + VRAM_SLACK_MIB or time.time() >= deadline:
            break
        time.sleep(2)
    if mem is not None and baseline is not None:
        info["vram_back_to_baseline"] = mem <= baseline + VRAM_SLACK_MIB
    return info


def run(args, log=print) -> int:
    manifest_path = find_manifest(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sweep_dir = manifest_path.parent
    out_dir = Path(args.out_dir) if args.out_dir else sweep_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path, findings_path = out_dir / RESULTS_NAME, out_dir / FINDINGS_NAME
    results = load_results(results_path, config_hash(), args.restart)
    shots = manifest.get("shots", [])
    todo = pending_shots(manifest, results)
    log(f"manifest {manifest_path}: {len(shots)} frames, {len(shots) - len(todo)} already judged, "
        f"{len(todo)} to do")

    stats = {"judged": 0, "errors": 0, "stopped": None}
    if todo and args.limit != 0:
        if vlm.beamng_running():
            log("BeamNG is running; refusing to start (GPU must not be shared)")
            return 2
        baseline = gpu_mem_mib()
        lock = vlm.GpuLock(owner=f"vlm image_judge pid={os.getpid()} t={int(time.time())}")
        results["gpu"] = {"vram_baseline_mib": baseline}
        lock.acquire()               # raises VlmError after the timeout; nothing to clean up yet
        try:
            if vlm.beamng_running():
                log("BeamNG started while waiting for the lock; not loading")
                return 2
            vlm.load_model(lock=lock)
            results["gpu"]["vram_loaded_mib"] = gpu_mem_mib()
            log(f"model loaded (vram {baseline} -> {results['gpu']['vram_loaded_mib']} MiB)")

            def should_stop():
                return "BeamNG started during the run; stopping" if vlm.beamng_running() else None

            stats = judge_frames(manifest, sweep_dir, results, results_path,
                                 lambda img, schema, prompt: vlm.judge_image(img, schema, prompt, args.reasoning,
                                                                             args.max_side),
                                 limit=args.limit, should_stop=should_stop, log=log)
        except (vlm.VlmError, KeyboardInterrupt) as e:
            stats["stopped"] = f"{type(e).__name__}: {e}"
            log(f"stopped: {stats['stopped']}")
        finally:
            results["gpu"].update(unload_and_verify(baseline))
            results["updated"] = now_iso()
            write_json(results_path, results)
            lock.release()
            log(f"model unloaded; vram {results['gpu'].get('vram_after_mib')} MiB "
                f"(baseline {baseline}; back to baseline: {results['gpu'].get('vram_back_to_baseline')})")
    results["summary"] = {"frames": len(shots), "judged": sum(1 for s in shots if frame_done(results["frames"].get(s["id"]))),
                          "by_defect_type": counts_by_defect(results), "last_run": stats}
    write_json(results_path, results)
    findings = build_findings(manifest, sweep_dir, results, args.min_confidence)
    write_json(findings_path, findings)
    log(f"results  {results_path}\nfindings {findings_path} ({len(findings['findings'])} at confidence >= "
        f"{args.min_confidence})\nby defect type: {json.dumps(results['summary']['by_defect_type'])}")
    complete = results["summary"]["judged"] == len(shots)
    if not complete:
        log(f"INCOMPLETE: {len(shots) - results['summary']['judged']} frames without a valid result")
    return 0 if complete and not stats["stopped"] else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", help="sweep folder or manifest.json (default: newest under the sweep root)")
    p.add_argument("--out-dir", help="where results and findings go (default: the sweep folder)")
    p.add_argument("--limit", type=int, help="judge at most N pending frames this run (0: just rewrite findings)")
    p.add_argument("--min-confidence", type=float, default=MIN_CONFIDENCE,
                   help=f"findings below this stay in the results only (default {MIN_CONFIDENCE}, provisional)")
    p.add_argument("--restart", action="store_true", help="ignore and replace existing results")
    p.add_argument("--reasoning", choices=vlm.REASONING_LEVELS, default="low")
    p.add_argument("--max-side", type=int, default=vlm.MAX_SIDE)
    args = p.parse_args(argv)
    # a terminate signal becomes KeyboardInterrupt so the model is still unloaded
    def on_signal(*_):
        raise KeyboardInterrupt()
    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), on_signal)
    try:
        return run(args)
    except (JudgeError, vlm.VlmError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
