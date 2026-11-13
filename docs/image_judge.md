# Image judge (T-304)

`qa/image_judge.py` sends every frame of a junction sweep (T-303, `docs/junction_sweep.md`) to the local vision model (Qwen3.8 27B through `tools/vlm.py`) and records, per frame, a defect type, a confidence and a one-sentence reason. High-confidence defects go to a findings file that `qa/digest.py` turns into issue cards (`docs/digest.md`). No game is started; the game must not be running.

```powershell
$env:UV_CACHE_DIR='H:\BelfastData\cache\uv'; $env:UV_PYTHON_INSTALL_DIR='H:\BelfastData\uv-python'
uv run --no-project --python 3.12 --with requests --with pillow python qa/image_judge.py [--manifest SWEEP_DIR] [--limit N] [--min-confidence 0.7] [--restart]
uv run --no-project --python 3.12 --with pytest --with requests --with pillow python -m pytest tests/qa/test_image_judge.py -q
```

Without `--manifest` it takes the newest folder under `H:\BelfastData\qa\sweep`. `--limit N` judges at most N pending frames (0 only rewrites the findings file). Exit codes: 0 every frame has a valid result; 1 some frame has none, or the run was stopped; 2 BeamNG is running.

## What it does

1. Reads `manifest.json` and the existing results (resume). Frames that already have a valid result are skipped; frames that errored are tried again. If nothing is pending the model is not touched.
2. Refuses if a BeamNG process exists (exit 2). Takes `H:\BelfastData\gpu.lock` (owner tag `vlm image_judge pid=...`, so `vlm.py unload` can clear it), loads the model through `vlm.load_model`, notes video memory before and after.
3. Judges frame by frame (about 10 s each), saving the results file after every frame. It re-checks for BeamNG before each frame and stops if the game appeared. Five errors in a row stop the run (the server is probably gone).
4. In a `finally`: unloads the model, waits up to 45 s for video memory to come back to within 300 MiB of the starting value (recorded in the results under `gpu`), releases the lock only if it is ours. A terminate signal is turned into an interrupt so the model is still unloaded.
5. Writes the findings file and prints counts by defect type.

## Schema and prompt

Strict JSON schema, all three fields required:

```json
{"defect_type": "none | missing-markings | wrong-markings | building-in-road | object-in-road | terrain-step | floating-object | buried-object | missing-texture | wrong-side-sign | road-geometry | other",
 "confidence": 0.0, "reason": "one short sentence"}
```

The list follows the defect kinds in `docs/decisions/001-generator.md`. The prompt (in `PROMPT`) tells the model it is looking at a generated map of Belfast from a right-hand-drive car, to ignore the car interior and sky, to pick the single most serious clearly visible defect or `none`, and gives one line per type. The only per-frame fact in it is the distance to the junction. Prompt, schema and model name are hashed into the results file; a changed hash refuses to mix with old results unless `--restart`. The reply is re-checked here: enum, 0 to 1 range, a non-empty reason.

## Output (not in git), next to the sweep

| File | Content |
|---|---|
| `image_judge_results.json` | header (model, prompt, schema, config hash, GPU memory baseline / loaded / after, `back_to_baseline`), `frames{shot id: {result{defect_type, confidence, reason}, tokens, seconds, attempts, judged_at} or {error}}`, `summary` (frames, judged, `by_defect_type`, last run) |
| `findings_image_judge.json` | `source: image_judge`, `run` = the sweep's start time, one finding per frame with a defect at or above `--min-confidence`: `kind` = defect type, `x`, `y` = the junction, `where` = junction id and distance, `severity` (high at 0.85 and above, else medium), `value`, `metric`, `detail` = the reason, `image` = frame path |

The digest merges findings of one kind within 20 m, so the 30 m and 10 m frames of a junction give one card. Run it with `qa/digest.py --input <sweep dir>` (or let the nightly run scan `H:\BelfastData\qa`).

## Limits

- `--min-confidence 0.7` and the 0.85 severity split are placeholders. T-306 measures precision per defect type on the evaluation set (T-305) and sets the real thresholds; until then treat findings as unverified.
- The model's accuracy on real game frames is unmeasured. The result counts say what it reports, not what is wrong.
- Frames are downscaled to 1280 px (the sweep frames already are 1280x720). The model always thinks 250 to 350 tokens per frame (`docs/spikes/vlm.md`).
- One verdict per frame: two defects in one frame give the one the model finds worst.
- Frames flagged `ok: false` or clamped by the sweep are judged like the others; the manifest's `clamped` flag is not given to the model.
