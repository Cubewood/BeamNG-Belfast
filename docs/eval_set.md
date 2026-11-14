# Evaluation set for the image judge (T-305)

A labelled set of driver's-eye frames: half with a fault planted in the running game, half untouched sweep frames. T-306 measures the local vision model (`qa/image_judge.py`, `docs/image_judge.md`) against it. **The local model has never seen these frames and must not label them**; the labels here are Claude's reading of the images plus the planted truth.

Files: `qa/eval/labels.jsonl` (one frame per line), `qa/eval/README.md` (counts and the spot-check list), `qa/eval/plan.json` (what was planted where, seeded), `qa/eval/visual_labels.json` (the visual labels), `qa/eval/clean_selection.json`, and `qa/eval_plant.py` (plan, plant, capture, assemble). Images stay under `H:\BelfastData\qa\eval\` (`planted\Pnnn.jpg`, `clean\Cnnn.jpg`, with a pose file next to each planted frame), not in git.

## Rebuild

```powershell
$env:UV_CACHE_DIR='H:\BelfastData\cache\uv'; $env:UV_PYTHON_INSTALL_DIR='H:\BelfastData\uv-python'
uv run --no-project --python 3.12 --with requests --with pillow python qa/eval_plant.py plan      # needs H:\BelfastData\qa\eval\scene_dump.json (run makes it)
uv run --no-project --python 3.12 --with requests --with pillow python qa/eval_plant.py run       # game, night only; --only P001,P002 to re-take
uv run --no-project --python 3.12 --with requests --with pillow python qa/eval_plant.py assemble  # labels.jsonl + stats.json
```

`run` goes through `tools/gamectl.py` (refuses if any BeamNG process exists or it is 09:00 to 18:00), loads `belfast_slice` in the QA profile, and for each planned frame runs a Lua snippet on the live level, re-takes the sweep's shot at that pose with `qa/junction_sweep.take_shot`, then reverts everything. Nothing on disk changes: the slice zip is hashed before and after (`H:\BelfastData\qa\eval\run_summary.json`), faults exist only in memory. The sweep it re-takes from is `H:\BelfastData\qa\sweep\2026-11-13_220428` (`docs/junction_sweep.md`).

## What is planted

Defect types are the image judge's own list. Only game Lua that exists in the game's `lua/` folder is used (`createObject('TSStatic')`, `setTransform`, `setHidden`, `setField`, `postApply`, DecalRoad `getNodePosition` / `setNodePosition` / `getNodeWidth` / `setNodeWidth`, TerrainBlock `getHeightGrid` / `setHeightGrid` / `updateGrid`).

| Type | How |
|---|---|
| missing-markings | hide the white-line, zebra or chevron DecalRoads near the car (all, only dashed, only solid, only zebra) |
| wrong-markings | white lines re-materialled yellow, widened x7 (smeared paint), widened and yellow, shifted 3 m sideways, or given the chequered-flag material |
| building-in-road | a brick, concrete or stone wall block, scaled up to building size, across the lane 11 to 15 m ahead |
| object-in-road | lamp post, sign, wooden fence, farm gate or bench standing in the car's lane 9 to 13 m ahead |
| terrain-step | a 3 m x 14 m strip of terrain cells across the road raised 0.7 to 1.0 m or cut 0.9 to 1.3 m |
| floating-object | a bench, wall block, sign or lamp post lifted 1.8 to 4 m off the ground beside the road |
| buried-object | the same props sunk so that only the top shows |
| missing-texture | the asphalt (or the markings) given a material whose texture file does not exist (the game draws its orange `NO MATERIAL` surface), or an industrial building shape with no material mapping beside the road |
| wrong-side-sign | a road sign with its back to the driver on the nearside verge, or a sign on the offside facing the driver |
| road-geometry | the asphalt DecalRoads near the lane point hidden (a gap), pinched to 0.35 width, or moved 3 to 4 m sideways (a misaligned piece) |

Each planted frame records exactly what was done in `planted_fault` (type, variant, detail text, objects, offset). Over-planning: more faults were planted than needed; those whose fault is not visible in the frame were dropped (listed in the README).

## Console

The missing-texture faults make the game log engine errors by design (`Failed to load 'diffuse' map` for the planted material, `[NO-MATERIAL]` for the building shape). No other error or warning came from the run beyond the usual baseline; see the README for the exact counts.

## Limits

- The sweep repeats shared approaches on several routes; the clean set keeps one frame per distinct approach and distance (75 of 108).
- Planted frames reuse poses of the clean frames (a pose can have a clean frame and one or two planted ones), so a model can be compared on the same scene with and without a fault.
- The A-pillar of the right-hand-drive car hides the right-hand side of the road; faults are placed in the visible part of the windscreen.
- The "clean" label is Claude's reading, not ground truth: the slice has real defects (ragged verge edges, houses on the carriageway end, missing give-way and stop lines at many junctions) and the labels say so with a lower confidence.
