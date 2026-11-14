#!/usr/bin/env python3
"""Measure the local vision model on the evaluation set (T-306).

    uv run --no-project --python 3.12 --with requests --with pillow python qa/eval_qwen.py judge   # ~33 s a frame
    uv run --no-project --python 3.12 --with requests --with pillow python qa/eval_qwen.py score

`judge` builds a manifest for the frames in qa/eval/labels.jsonl (images under H:\\BelfastData\\qa\\eval) and runs
qa/image_judge.py on it (same prompt, schema, GPU lock, unload; resumable) with results under
H:\\BelfastData\\qa\\eval\\qwen. `score` compares the results with the PLANTED truth and writes metrics.json and
disagreements.txt next to them. The model never sees the labels.

Type mapping. The planted types are the judge's own types (qa/eval_plant.py was written against
image_judge.DEFECT_TYPES), so the mapping from a prediction to a planted type is the identity: a planted frame is
detected correctly only when the predicted type equals the planted type (strict). Two looser views are reported next
to it and never used for the trust decision: `any_defect` (anything but none) and `family`, which also accepts the
neighbouring type a planted fault can reasonably look like (FAMILY below). `other` is never a planted type, so it can
only be a false positive. Clean frames: truth is `none` ("planted: false"). Because the slice has real defects, the
agent's visual labels of the clean frames are scored separately and never mixed into that number.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qa"))
import image_judge as ij  # noqa: E402

EVAL_DIR = Path(r"H:\BelfastData\qa\eval")
OUT_DIR = EVAL_DIR / "qwen"
LABELS = ROOT / "qa" / "eval" / "labels.jsonl"
THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9]
TRUST_PRECISION = 0.90         # task row: fault types under this go to Sonnet or are dropped
MIN_PREDICTIONS = 5            # a precision from fewer predictions than this is not trusted
PREVALENCE = 0.05             # assumed share of real sweep frames that carry a fault, for the adjusted column
MAX_DISAGREEMENTS = 20

# planted type -> other predictions that are an understandable reading of the same picture (loose view only)
FAMILY = {
    "building-in-road": {"object-in-road"},
    "object-in-road": {"building-in-road"},
    "buried-object": {"object-in-road", "terrain-step"},
    "floating-object": {"object-in-road"},
    "road-geometry": {"terrain-step"},
    "terrain-step": {"road-geometry"},
    "missing-markings": {"wrong-markings"},
    "wrong-markings": {"missing-markings"},
    "wrong-side-sign": {"object-in-road"},
    "missing-texture": {"wrong-markings"},
}


def load_labels(path: Path = LABELS) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def truth_of(label: dict) -> str:
    return label["planted_fault"]["type"] if label.get("planted") else "none"


def build_manifest(labels: list[dict]) -> dict:
    shots = []
    for lab in labels:
        m = re.search(r"-(\d+)m$", lab.get("source_frame") or "")
        shots.append({"id": lab["frame"], "image": lab["image"].replace("\\", "/"),
                      "distance_m": int(m.group(1)) if m else None,
                      "junction_id": lab.get("source_frame")})
    return {"tool": "qa/eval_qwen.py", "task": "T-306", "started": "evaluation set", "shots": shots}


def predictions(results: dict) -> dict[str, tuple[str, float]]:
    out = {}
    for fid, rec in results.get("frames", {}).items():
        r = rec.get("result")
        if r and not ij.check_result(r):
            out[fid] = (r["defect_type"], float(r["confidence"]))
    return out


# ---------------------------------------------------------------- scoring (pure)

def _rate(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def score(labels: list[dict], preds: dict[str, tuple[str, float]], thresholds=THRESHOLDS,
          prevalence: float = PREVALENCE) -> dict:
    labs = [l for l in labels if l["frame"] in preds]
    planted = [l for l in labs if l.get("planted")]
    clean = [l for l in labs if not l.get("planted")]
    types = sorted({l["planted_fault"]["type"] for l in planted})
    ths = [0.0] + list(thresholds)     # 0.0 = every prediction

    def pred(l):
        return preds[l["frame"]]

    def hit(l, t, want=None):          # prediction at or above t, optionally of a given type
        p, c = pred(l)
        return p != "none" and c >= t and (want is None or p == want)

    out: dict = {"frames": len(labs), "planted": len(planted), "clean": len(clean), "thresholds": thresholds,
                 "recall": {}, "clean_false_positive": {}, "precision": {}, "confusion": {}, "prevalence": prevalence}

    for ty in types:
        rows = [l for l in planted if l["planted_fault"]["type"] == ty]
        parts = [l for l in rows if (l["visual_label"] or {}).get("planted_visible") == "partly"]
        full = [l for l in rows if l not in parts]
        d = {"n": len(rows), "n_visible_yes": len(full), "n_visible_partly": len(parts)}
        for t in ths:
            k = f"{t:g}"
            d[k] = {
                "strict": _rate(sum(hit(l, t, ty) for l in rows), len(rows)),
                "strict_visible_yes": _rate(sum(hit(l, t, ty) for l in full), len(full)),
                "strict_visible_partly": _rate(sum(hit(l, t, ty) for l in parts), len(parts)),
                "family": _rate(sum(hit(l, t) and pred(l)[0] in ({ty} | FAMILY.get(ty, set())) for l in rows),
                                len(rows)),
                "any_defect": _rate(sum(hit(l, t) for l in rows), len(rows)),
            }
        out["recall"][ty] = d

    vis_none = [l for l in clean if l["visual_label"]["defect_type"] == "none"]
    vis_def = [l for l in clean if l["visual_label"]["defect_type"] != "none"]
    for t in ths:
        out["clean_false_positive"][f"{t:g}"] = {
            "vs_planted_false": {"n": len(clean), "flagged": sum(hit(l, t) for l in clean),
                                 "rate": _rate(sum(hit(l, t) for l in clean), len(clean))},
            "vs_visual_labels": {
                "visual_none": {"n": len(vis_none), "flagged": sum(hit(l, t) for l in vis_none),
                                "rate": _rate(sum(hit(l, t) for l in vis_none), len(vis_none))},
                "visual_defect": {"n": len(vis_def),
                                  "flagged_any": sum(hit(l, t) for l in vis_def),
                                  "type_matches_visual": sum(hit(l, t, l["visual_label"]["defect_type"])
                                                             for l in vis_def)},
            },
        }

    pred_types = sorted({p for p, _ in preds.values() if p != "none"} | set(types))
    for ty in pred_types:
        d = {}
        for t in ths:
            flagged = [l for l in labs if hit(l, t, ty)]
            tp = [l for l in flagged if l.get("planted") and l["planted_fault"]["type"] == ty]
            fp_clean = [l for l in flagged if not l.get("planted")]
            fp_other = [l for l in flagged if l.get("planted") and l["planted_fault"]["type"] != ty]
            tp_vis = [l for l in fp_clean if l["visual_label"]["defect_type"] == ty]
            n_ty = sum(1 for l in planted if l["planted_fault"]["type"] == ty)
            tpr = len(tp) / n_ty if n_ty else 0.0
            fpr = len(fp_clean) / len(clean) if clean else 0.0
            denom = tpr * prevalence + fpr * (1 - prevalence)
            d[f"{t:g}"] = {
                "predicted": len(flagged), "tp": len(tp), "fp_clean": len(fp_clean), "fp_other_type": len(fp_other),
                "precision": _rate(len(tp), len(flagged)),
                "precision_clean_visual_ok": _rate(len(tp) + len(tp_vis), len(flagged)),
                "precision_at_prevalence": round(tpr * prevalence / denom, 4) if denom else None,
            }
        out["precision"][ty] = d

    rows_t = types + ["none (clean)"]
    cols = sorted({p for p, _ in preds.values()} | {"none"})
    for rt in rows_t:
        sel = [l for l in labs if (truth_of(l) == rt if rt != "none (clean)" else not l.get("planted"))]
        cnt = Counter(pred(l)[0] for l in sel)
        out["confusion"][rt] = {c: cnt.get(c, 0) for c in cols}
    return out


def choose_thresholds(sc: dict) -> dict:
    """Per predicted type: the lowest threshold whose precision against the planted truth is at least
    TRUST_PRECISION with at least MIN_PREDICTIONS predictions; None when no threshold qualifies."""
    chosen = {}
    for ty, byt in sc["precision"].items():
        pick = None
        for t in sc["thresholds"]:
            row = byt[f"{t:g}"]
            if row["precision"] is not None and row["precision"] >= TRUST_PRECISION and row["predicted"] >= MIN_PREDICTIONS:
                pick = t
                break
        rec = sc["recall"].get(ty)
        chosen[ty] = {"threshold": pick,
                      "precision": byt[f"{pick:g}"]["precision"] if pick is not None else None,
                      "predicted": byt[f"{pick:g}"]["predicted"] if pick is not None else None,
                      "recall": rec[f"{pick:g}"]["strict"] if (pick is not None and rec) else None,
                      "n_planted": rec["n"] if rec else 0,
                      "best_precision_any_threshold": max((byt[f"{t:g}"]["precision"] or 0) for t in sc["thresholds"])}
    return chosen


def disagreements(labels: list[dict], preds: dict, cap: int = MAX_DISAGREEMENTS) -> tuple[list[dict], int]:
    """Frames where the prediction differs from the planted truth, most confident first, capped."""
    rows = []
    for l in labels:
        if l["frame"] not in preds:
            continue
        p, c = preds[l["frame"]]
        tr = truth_of(l)
        if p != tr:
            rows.append({"frame": l["frame"], "truth": tr, "qwen": p, "confidence": c,
                         "visual_label": (l.get("visual_label") or {}).get("defect_type"),
                         "image": str(EVAL_DIR / l["image"])})
    rows.sort(key=lambda r: (-r["confidence"], r["frame"]))
    return rows[:cap], len(rows)


# ---------------------------------------------------------------- commands

def cmd_judge(args) -> int:
    labels = load_labels()
    manifest = build_manifest(labels)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mpath = EVAL_DIR / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    missing = [s["id"] for s in manifest["shots"] if not (EVAL_DIR / s["image"]).is_file()]
    if missing:
        print(f"missing images: {missing[:5]} ... ({len(missing)})", file=sys.stderr)
        return 1
    ns = argparse.Namespace(manifest=str(mpath), out_dir=str(OUT_DIR), limit=args.limit, min_confidence=0.0,
                            restart=args.restart, reasoning="low", max_side=ij.vlm.MAX_SIDE)
    return ij.run(ns)


def cmd_score(args) -> int:
    labels = load_labels()
    results = json.loads((OUT_DIR / ij.RESULTS_NAME).read_text(encoding="utf-8"))
    preds = predictions(results)
    sc = score(labels, preds)
    sc["chosen"] = choose_thresholds(sc)
    dis, total = disagreements(labels, preds)
    sc["disagreements_total"] = total
    (OUT_DIR / "metrics.json").write_text(json.dumps(sc, indent=1), encoding="utf-8")
    (OUT_DIR / "disagreements.txt").write_text("\n".join(r["image"] for r in dis) + "\n", encoding="utf-8")
    (OUT_DIR / "disagreements.json").write_text(json.dumps(dis, indent=1), encoding="utf-8")
    print(f"frames scored {sc['frames']} of {len(labels)}; disagreements {total} (listed {len(dis)})")
    print(json.dumps(sc["chosen"], indent=1))
    return 0 if sc["frames"] == len(labels) else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge")
    j.add_argument("--limit", type=int)
    j.add_argument("--restart", action="store_true")
    sub.add_parser("score")
    args = p.parse_args(argv)
    return cmd_judge(args) if args.cmd == "judge" else cmd_score(args)


if __name__ == "__main__":
    sys.exit(main())
