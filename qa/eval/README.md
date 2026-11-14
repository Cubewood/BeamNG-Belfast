# Evaluation set for the image judge (T-305)

172 frames: **97 planted** (a fault broken in the running game, then re-photographed at a sweep pose) and **75 clean** (untouched frames of the junction sweep `H:\BelfastData\qa\sweep\2026-11-13_220428`). Planted is 56% of the set. The local model (Qwen) never saw these frames and must not label them; T-306 measures it against this set. How it was built and how to rebuild it: `docs/eval_set.md`; the script is `qa/eval_plant.py`.

Images are not in git: `H:\BelfastData\qa\eval\planted\Pnnn.jpg` (with a pose file each) and `H:\BelfastData\qa\eval\clean\Cnnn.jpg`. `labels.jsonl` has the `image` path relative to that folder and the `image_sha256` prefix.

## Counts

Planted frames by planted defect type (the ground truth):

| Type | Frames |
|---|---|
| building-in-road | 7 |
| buried-object | 10 |
| floating-object | 10 |
| missing-markings | 10 |
| missing-texture | 10 |
| object-in-road | 12 |
| road-geometry | 10 |
| terrain-step | 10 |
| wrong-markings | 10 |
| wrong-side-sign | 8 |

Clean frames by my visual label (Claude vision; `none` is the plain scene):

| Visual label | Frames |
|---|---|
| building-in-road | 2 |
| missing-markings | 9 |
| none | 63 |
| terrain-step | 1 |

`other` was not planted (nothing here fits no type). Planted frames whose visual label equals the planted type: 95 of 97. 16 planted frames are marked `planted_visible: partly` (the fault is subtle or small; they stay in the set because the planting record is the truth, and they are on the spot-check list).

## labels.jsonl

One JSON object per line: `frame` (Pnnn or Cnnn), `image`, `image_sha256`, `source_frame` (the sweep shot id whose pose was used), `planted` (true or false), `planted_fault` (null for clean; else `type`, `variant`, `detail` = exactly what was done, `objects`, `offset_m`), `diff_vs_source` (planted only: mean absolute grey difference and the share of windscreen pixels that differ by more than 30 levels against the untouched sweep frame of the same pose), `visual_label` (`defect_type`, `confidence`, `reason`, and for planted frames `planted_visible` yes or partly, `other_defects`, `unsure`, `how`), `spot_check` (list of reasons this frame is on the spot-check list; empty if none). The ground truth for a planted frame is `planted_fault.type`. The ground truth for a clean frame is my visual label, which is a judgement and weaker; see below. Defect types are those of `qa/image_judge.py`.

## How the labels were made

- Every frame in the set was looked at by Claude (vision) on contact sheets (planted frames beside their untouched source frame for the marking faults, six per sheet otherwise). Viewed: all 108 sweep frames (the 75 of the set plus 33 repeats of shared approaches that are left out), and all 108 planted candidates once, plus the 21 re-takes a second time. So `visually checked` is true for every frame in `labels.jsonl`.
- Planted candidates whose fault could not be seen were dropped, not kept (11 frames left in the final plan, listed in `visual_labels.json` under `dropped` with the reason), and 21 were re-planted with stronger parameters (the plan file keeps the old parameters in `retake_of`). The sweep's own frames and the planted frames differ in cloud light, because the game animates clouds.
- Clean frames: the slice has real defects, so a clean (nothing planted) frame can carry a defect label. 12 of 75 are labelled with a defect at confidence 0.3 to 0.55, all marked unsure: junctions with no give-way or stop line, ragged verge edges, houses standing on the road end. Treat the clean labels as opinion.

## Console of the capture runs

Two runs: all 108 candidates, then 21 re-takes. Game stopped cleanly both times, the slice zip hash is unchanged (`1528102d...ce8c8` before and after), no game or gpu lock left behind. Console errors: 67 in the first run, all from the planted missing-texture faults themselves (65 `Failed to load 'diffuse' map` for the planted material, 2 `[NO-MATERIAL]` for the unmapped industrial building shape); none in the second run; none from the other faults or our Lua. Warnings: only the usual slow-extension-load notes and the achievements baseline line. Untouched re-take of one pose after the faults gave a mean absolute grey difference of 5.1 to 8.7 of 255 against the sweep image (cloud movement); the faults are reverted (`reverted:` lines in `H:\BelfastData\qa\eval\captures.json`).

## Spot-check list (deferred)

The user has deferred their checks. Do nothing with this list now. Later, after T-306 has run Qwen on the whole set, only the frames where Qwen and the planted truth disagree need a Claude or user look; the list below is the frames where my own visual label and the planted truth disagree or where I was unsure (79 frames, reasons in `labels.jsonl` field `spot_check`).

| Frame | Planted | My label | Why |
|---|---|---|---|
| P004 | missing-markings | missing-markings (0.55) | planted fault visible: partly; unsure |
| P005 | missing-markings | missing-markings (0.4) | planted fault visible: partly; unsure |
| P006 | missing-markings | missing-markings (0.4) | planted fault visible: partly; unsure |
| P009 | missing-markings | missing-markings (0.55) | unsure; other defect also visible: Cloud shadow darkens the whole frame. |
| P011 | missing-markings | missing-markings (0.35) | planted fault visible: partly; unsure |
| P014 | missing-markings | missing-markings (0.45) | planted fault visible: partly; unsure |
| P021 | wrong-markings | wrong-markings (0.5) | planted fault visible: partly; unsure |
| P026 | wrong-markings | wrong-markings (0.5) | planted fault visible: partly; unsure |
| P042 | object-in-road | object-in-road (0.65) | unsure |
| P048 | object-in-road | object-in-road (0.7) | unsure |
| P067 | floating-object | floating-object (0.5) | planted fault visible: partly; unsure |
| P068 | floating-object | floating-object (0.6) | unsure |
| P069 | floating-object | floating-object (0.6) | unsure |
| P071 | buried-object | buried-object (0.5) | unsure |
| P072 | buried-object | buried-object (0.5) | unsure |
| P073 | buried-object | buried-object (0.5) | unsure |
| P074 | buried-object | buried-object (0.5) | unsure |
| P075 | buried-object | object-in-road (0.4) | planted buried-object but visual label object-in-road; planted fault visible: partly; unsure |
| P076 | buried-object | buried-object (0.5) | unsure |
| P077 | buried-object | buried-object (0.5) | unsure |
| P078 | buried-object | object-in-road (0.45) | planted buried-object but visual label object-in-road; planted fault visible: partly; unsure |
| P079 | buried-object | buried-object (0.5) | unsure |
| P080 | buried-object | buried-object (0.5) | unsure |
| P085 | missing-texture | missing-texture (0.5) | unsure |
| P086 | missing-texture | missing-texture (0.5) | unsure |
| P093 | wrong-side-sign | wrong-side-sign (0.4) | planted fault visible: partly; unsure |
| P095 | wrong-side-sign | wrong-side-sign (0.45) | planted fault visible: partly; unsure |
| P096 | wrong-side-sign | wrong-side-sign (0.4) | planted fault visible: partly; unsure |
| P097 | wrong-side-sign | wrong-side-sign (0.5) | unsure |
| P098 | wrong-side-sign | wrong-side-sign (0.5) | unsure |
| P103 | road-geometry | road-geometry (0.55) | unsure |
| P104 | road-geometry | road-geometry (0.55) | unsure |
| P105 | road-geometry | road-geometry (0.6) | unsure |
| P106 | road-geometry | road-geometry (0.5) | planted fault visible: partly; unsure |
| P107 | road-geometry | road-geometry (0.5) | planted fault visible: partly; unsure |
| P108 | road-geometry | road-geometry (0.5) | planted fault visible: partly; unsure |
| C003 | - | missing-markings (0.4) | clean frame (nothing planted) labelled missing-markings; unsure |
| C004 | - | none (0.6) | unsure |
| C005 | - | none (0.55) | unsure |
| C007 | - | none (0.55) | unsure |
| C009 | - | building-in-road (0.4) | clean frame (nothing planted) labelled building-in-road; unsure |
| C010 | - | none (0.5) | unsure |
| C012 | - | building-in-road (0.55) | clean frame (nothing planted) labelled building-in-road; unsure |
| C013 | - | missing-markings (0.35) | clean frame (nothing planted) labelled missing-markings; unsure |
| C015 | - | none (0.5) | unsure |
| C016 | - | none (0.5) | unsure |
| C017 | - | missing-markings (0.4) | clean frame (nothing planted) labelled missing-markings; unsure |
| C018 | - | none (0.5) | unsure |
| C019 | - | missing-markings (0.3) | clean frame (nothing planted) labelled missing-markings; unsure |
| C025 | - | terrain-step (0.4) | clean frame (nothing planted) labelled terrain-step; unsure |
| C026 | - | none (0.5) | unsure |
| C029 | - | none (0.55) | unsure |
| C031 | - | none (0.5) | unsure |
| C033 | - | none (0.5) | unsure |
| C034 | - | none (0.5) | unsure |
| C035 | - | none (0.55) | unsure |
| C036 | - | none (0.5) | unsure |
| C039 | - | none (0.55) | unsure |
| C042 | - | none (0.5) | unsure |
| C044 | - | none (0.55) | unsure |
| C045 | - | missing-markings (0.35) | clean frame (nothing planted) labelled missing-markings; unsure |
| C046 | - | none (0.55) | unsure |
| C047 | - | missing-markings (0.35) | clean frame (nothing planted) labelled missing-markings; unsure |
| C048 | - | none (0.6) | unsure |
| C051 | - | none (0.5) | unsure |
| C053 | - | none (0.5) | unsure |
| C055 | - | missing-markings (0.35) | clean frame (nothing planted) labelled missing-markings; unsure |
| C057 | - | missing-markings (0.35) | clean frame (nothing planted) labelled missing-markings; unsure |
| C058 | - | missing-markings (0.4) | clean frame (nothing planted) labelled missing-markings; unsure |
| C060 | - | none (0.55) | unsure |
| C062 | - | none (0.55) | unsure |
| C064 | - | none (0.5) | unsure |
| C066 | - | none (0.65) | unsure |
| C068 | - | none (0.55) | unsure |
| C069 | - | none (0.55) | unsure |
| C070 | - | none (0.6) | unsure |
| C072 | - | none (0.6) | unsure |
| C074 | - | none (0.55) | unsure |
| C075 | - | none (0.6) | unsure |
