# Qwen on the evaluation set (T-306)

Measured 2026-11-14. Qwen3.8 27B (`tools/vlm.py`, reasoning `low`) judged all 172 frames of the evaluation set (`docs/eval_set.md`: 97 planted faults, 75 clean frames) with the **unchanged** T-304 prompt and schema (`qa/image_judge.py`, config hash unchanged). Every frame has a valid result. Model unloaded afterwards (video memory 1150 MiB before, 18428 loaded, 1061 after), `gpu.lock` released.

Run: `qa/eval_qwen.py judge` then `qa/eval_qwen.py score`. Results, metrics and the full disagreement list are outside git in `H:\BelfastData\qa\eval\qwen\` (`image_judge_results.json`, `metrics.json`, `disagreements.json`).

## Verdict

Qwen is **not** a usable gate for most defect types. Only two types reach 90% precision with enough predictions: **terrain-step at confidence 0.7 and above** and **missing-texture at 0.8 and above**. Both have 6 predictions, so the true precision could be much lower (a 6 of 6 result has a 95% lower bound of about 0.61). Everything else goes to Sonnet (review queue) or is dropped. On clean frames Qwen flags a defect in 77% of frames at any confidence and 44% at 0.7 and above, nearly all of them `missing-markings`.

## Mapping of planted types to the judge's types

The judge's schema already has all ten planted types (`object-in-road`, `buried-object`, `wrong-side-sign`, `road-geometry` included; `qa/eval_plant.py` was written against `image_judge.DEFECT_TYPES`). So the mapping is the identity, and the judge prompt was not changed. A planted frame counts as detected only when Qwen's type equals the planted type (strict, used for every trust decision). Two looser views are shown but never used for decisions: *any defect* (anything but `none`) and *family* (the planted type or a neighbour it can reasonably look like, `FAMILY` in `qa/eval_qwen.py`, for example buried-object read as object-in-road). `other` was never planted, so it can only be a false positive. For clean frames the truth is `none` ("planted: false"). The agent's visual labels of the clean frames are scored separately because the slice has real defects.

## Trust table

Rule: a type is trusted at the lowest threshold in 0.5, 0.6, 0.7, 0.8, 0.9 where precision against the planted truth is at least 0.90 with at least 5 predictions. Precision counts every clean-frame hit and every wrong-type hit as a false positive. Precision and recall are strict. `n` is the planted frames of that type (frames with `planted_visible: partly` are included; split further down).

| Defect type | n planted | Threshold | Precision at threshold (predictions) | Recall at threshold | Best precision at any threshold (predictions) | Decision |
|---|---|---|---|---|---|---|
| terrain-step | 10 | **0.7** | 1.00 (6) | 0.60 | 1.00 (6 at 0.7; 3 at 0.8) | trusted: findings |
| missing-texture | 10 | **0.8** | 1.00 (6) | 0.60 | 1.00 (6 at 0.8; 2 at 0.9) | trusted: findings |
| wrong-markings | 10 | none | 0.86 (7) at 0.8 | 0.60 at 0.8 | 0.86 (7 at 0.8) | under 90%: Sonnet |
| building-in-road | 7 | none | 0.60 (10) at 0.5 | 0.86 at 0.5 | 1.00 (4 at 0.9, too few) | under 90%: Sonnet |
| object-in-road | 12 | none | 0.50 (20) at 0.5 | 0.83 | 0.58 (12 at 0.9) | under 90%: Sonnet (see merge note) |
| floating-object | 10 | none | 0.36 (11) at 0.5 | 0.40 | 1.00 (2 at 0.9, too few) | under 90%: Sonnet or drop |
| missing-markings | 10 | none | 0.14 (63) at 0.5 | 0.90 | 0.36 (14 at 0.8) | under 90%: Sonnet or drop |
| buried-object | 10 | none | never predicted | 0.00 | none | drop (read as object-in-road) |
| road-geometry | 10 | none | 1.00 (1) | 0.10 | 1 prediction | drop |
| wrong-side-sign | 8 | none | 1.00 (1) | 0.12 | 1 prediction | drop |
| other | 0 | none | 0.00 (1) | n/a | n/a | never a finding |

Chosen thresholds are in `qa/image_judge.py` as `TYPE_MIN_CONFIDENCE` (terrain-step 0.7, missing-texture 0.8, all other types `None` = never a finding). Findings of a `None` type at 0.7 or more (the old provisional floor) go to a `review_queue` list in the findings file instead, which the digest does not read. `--min-confidence X` on the command line still sets one floor for every type.

The thresholds were picked on this same set, so the two precision figures are optimistic, and n is small. Treat the trusted pair as "probably right", not as measured at 90%.

### Precision by predicted type and threshold (predictions in brackets)

| Predicted type | P@0.5 | P@0.6 | P@0.7 | P@0.8 | P@0.9 |
|---|---|---|---|---|---|
| building-in-road | 0.60 (10) | 0.60 (10) | 0.60 (10) | 0.67 (6) | 1.00 (4) |
| buried-object | - (0) | - (0) | - (0) | - (0) | - (0) |
| floating-object | 0.36 (11) | 0.36 (11) | 0.40 (10) | 0.50 (6) | 1.00 (2) |
| missing-markings | 0.14 (63) | 0.15 (61) | 0.18 (39) | 0.36 (14) | - (0) |
| missing-texture | 0.50 (16) | 0.53 (15) | 0.80 (10) | 1.00 (6) | 1.00 (2) |
| object-in-road | 0.50 (20) | 0.50 (20) | 0.53 (19) | 0.53 (17) | 0.58 (12) |
| other | 0.00 (1) | 0.00 (1) | - (0) | - (0) | - (0) |
| road-geometry | 1.00 (1) | 1.00 (1) | - (0) | - (0) | - (0) |
| terrain-step | 0.78 (9) | 0.78 (9) | 1.00 (6) | 1.00 (3) | - (0) |
| wrong-markings | 0.56 (16) | 0.56 (16) | 0.67 (12) | 0.86 (7) | - (0) |
| wrong-side-sign | 1.00 (1) | 1.00 (1) | - (0) | - (0) | - (0) |

Nothing was predicted at 0.9 or above for most types, and the model rarely says below 0.5, so 0.5 is the same as "every prediction". Precision counting clean-frame hits as correct when they match my visual label (`precision_clean_visual_ok` in `metrics.json`) only moves `missing-markings` (0.14 to 0.24 at 0.5) and `building-in-road` (0.60 to 0.70); no type crosses 0.90 that way.

### Recall by planted type (strict)

`n` is all planted frames (visible + partly visible).

| Planted type | n | R@0.5 | R@0.7 | R@0.8 | R@0.9 | any defect @0.5 | family @0.5 | R@0.5 visible: yes | R@0.5 visible: partly |
|---|---|---|---|---|---|---|---|---|---|
| building-in-road | 7 (7+0) | 0.86 | 0.86 | 0.57 | 0.57 | 1.00 | 0.86 | 0.86 | - |
| buried-object | 10 (8+2) | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0.90 | 0.00 | 0.00 |
| floating-object | 10 (9+1) | 0.40 | 0.40 | 0.30 | 0.20 | 0.70 | 0.40 | 0.44 | 0.00 |
| missing-markings | 10 (5+5) | 0.90 | 0.70 | 0.50 | 0.00 | 1.00 | 0.90 | 0.80 | 1.00 |
| missing-texture | 10 (10+0) | 0.80 | 0.80 | 0.60 | 0.20 | 1.00 | 1.00 | 0.80 | - |
| object-in-road | 12 (12+0) | 0.83 | 0.83 | 0.75 | 0.58 | 1.00 | 0.83 | 0.83 | - |
| road-geometry | 10 (7+3) | 0.10 | 0.00 | 0.00 | 0.00 | 0.80 | 0.10 | 0.14 | 0.00 |
| terrain-step | 10 (10+0) | 0.70 | 0.60 | 0.30 | 0.00 | 1.00 | 0.70 | 0.70 | - |
| wrong-markings | 10 (8+2) | 0.90 | 0.80 | 0.60 | 0.00 | 0.90 | 0.90 | 1.00 | 0.50 |
| wrong-side-sign | 8 (5+3) | 0.12 | 0.00 | 0.00 | 0.00 | 0.88 | 0.25 | 0.20 | 0.00 |

Qwen notices that *something* is wrong on nearly every planted frame (any-defect recall 0.70 to 1.00), but it names the type correctly for only some. The 16 `partly` visible frames do not explain the failures: fully visible `road-geometry` (0.14), `wrong-side-sign` (0.20) and `buried-object` (0.00) fail as well. The `partly` frames of `missing-markings` were found at 1.00, probably because Qwen says `missing-markings` for almost any frame (below).

### Confusion table (truth rows, Qwen columns, every prediction)

| Truth \ Qwen | building-in-road | floating-object | missing-markings | missing-texture | none | object-in-road | other | road-geometry | terrain-step | wrong-markings | wrong-side-sign |
|---|---|---|---|---|---|---|---|---|---|---|---|
| building-in-road | 6 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| buried-object | 1 | 0 | 0 | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 0 |
| floating-object | 0 | 4 | 1 | 1 | 3 | 0 | 1 | 0 | 0 | 0 | 0 |
| missing-markings | 0 | 0 | 9 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| missing-texture | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 2 | 0 |
| object-in-road | 0 | 1 | 1 | 0 | 0 | 10 | 0 | 0 | 0 | 0 | 0 |
| road-geometry | 0 | 0 | 4 | 3 | 2 | 0 | 0 | 1 | 0 | 0 | 0 |
| terrain-step | 2 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 7 | 0 | 0 |
| wrong-markings | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 9 | 0 |
| wrong-side-sign | 0 | 0 | 5 | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 1 |
| none (clean) | 1 | 6 | 41 | 3 | 17 | 0 | 0 | 0 | 2 | 5 | 0 |

## False positives on the clean frames

Two views, kept apart. **Against "planted: false"** (75 frames, everything flagged is a false positive). **Against my visual labels**: 63 frames I labelled `none` (a flag there is a false positive) and 12 I labelled with a real slice defect, at confidence 0.3 to 0.55 (a flag there is not a false positive, and "right type" means Qwen's type equals my label).

| Threshold | Flagged of 75 | Rate | Flagged of the 63 visual-none | Rate | Of the 12 visual-defect: flagged / right type |
|---|---|---|---|---|---|
| any | 58 | 0.77 | 47 | 0.75 | 11 / 7 |
| 0.5 | 58 | 0.77 | 47 | 0.75 | 11 / 7 |
| 0.6 | 55 | 0.73 | 44 | 0.70 | 11 / 7 |
| 0.7 | 33 | 0.44 | 25 | 0.40 | 8 / 4 |
| 0.8 | 11 | 0.15 | 8 | 0.13 | 3 / 2 |
| 0.9 | 0 | 0.00 | 0 | 0.00 | 0 / 0 |

The false positives are mostly `missing-markings` (41 of 75 clean frames), then `floating-object` (6), `wrong-markings` (5) and `missing-texture` (3). `terrain-step` fires on 2 clean frames but only below 0.7. Because the eval set is 56% planted and the real map is nearly all clean, real-world precision is lower than the table. A prevalence-adjusted precision (5% of frames faulty, `precision_at_prevalence` in `metrics.json`) is 1.00 for the two trusted types at their thresholds, but that rests on 0 false positives in 75 clean frames, which is also thin.

## Notes for the next step

- `object-in-road` has precision 0.50 mostly because 9 of 10 buried objects are read as object-in-road. Counted together, "object-in-road or buried-object" has 19 correct of 20 at 0.5, with 0 clean false positives. That merge was spotted after the measurement and is not applied. If the user agrees, merge the two types in the schema and re-measure on the same set before trusting it. Likewise `wrong-markings` reaches 0.86 at 0.8 and `building-in-road` 1.00 at 0.9 with only 4 predictions; a prompt change plus a re-run could move them.
- `missing-markings` is the model's default complaint; with 41 of 75 clean frames flagged it cannot be used until the prompt asks for it only where a junction needs a line. Static checks on the data (`qa/static_checks`) should own markings.
- `road-geometry` and `wrong-side-sign` are hardly seen (1 of 10 and 1 of 8). They need a different check (geometry from data, signs from the OSM-based rules).

## What this means for gate G4

G4 reads "No open high-confidence defects on route junctions, using the thresholds set in T-306" (`task.md`). With these numbers:

1. G4 can block only on `terrain-step` at 0.7 or more and `missing-texture` at 0.8 or more. Everything else Qwen says is a hint, not a gate.
2. The review queue (untrusted types at 0.7 or more) is **not** part of G4; a Sonnet pass over it (capped, with the images) decides, and only confirmed defects become issue cards. Expect a long queue: 33 of the 75 clean frames are flagged at 0.7 or more, almost all of them in untrusted types.
3. G4 as written would otherwise pass slices with unseen road-geometry, wrong-side-sign, buried-object and marking defects. Do not count a green G4 as evidence for those types; they need data checks (G1 to G3) or the user's VR drive.
4. Re-measure after any change to the prompt, schema or model (`qa/eval_qwen.py judge --restart`, about 1.5 hours of GPU time on this PC, 20 to 35 s a frame).

## Disagreements list

`docs/spikes/vlm-eval-disagreements.txt`: the 20 frames where Qwen and the planted truth disagree with the highest Qwen confidence, with the image path of each (100 frames disagree in all; this run is capped at 20; the full list is `H:\BelfastData\qa\eval\qwen\disagreements.json`). Only these are for a later Claude or user look. 9 of the 20 are buried objects (8 read as object-in-road, 1 as building-in-road), 1 is a missing texture read as wrong-markings, and 10 are clean frames flagged as `missing-markings`, `floating-object` or `building-in-road`.
