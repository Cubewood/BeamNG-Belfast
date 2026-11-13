# UK decal materials (T-402)

`pipeline/uk_decals.py` draws the UK road-marking textures from `rules/uk_markings.yaml` and writes the materials that use them. Every texture is drawn by the script (numpy, 4x4 supersampling per pixel); nothing is copied from the game or from MapNG. The names are `uk_<pattern>_cubewood`, none of which exists in the stock game or in MapNG's slice (checked against 864 `*.materials.json` files).

Status: static checks pass. **NOT verified in the game.** See "In-game check" at the end.

## Files

| Path | What | Committed |
|---|---|---|
| `pipeline/uk_decals.py` | generator (`--out`, `--level`) | yes |
| `pipeline/uk_decals_check.py` | static checker (textures exist, names, stock collisions, periods, drift) | yes |
| `pipeline/uk_decals_pack.py` | copies a level zip and adds the materials, for the QA profile | yes |
| `rules/uk_decals/main.materials.json` | the materials file for level `belfast_slice` | yes |
| `rules/uk_decals/manifest.json` | rule to material, repeat length, width, unverified rules (T-403 reads this) | yes |
| `H:\BelfastData\uk_decals\uk_markings_cubewood\` | the 23 PNGs and a `main.materials.json` | no (`*.png` is git-ignored) |

The PNGs are all under 8 KB, but `.gitignore` excludes `*.png`, so they are rebuilt: `uv run --no-project --python 3.12 --with pillow --with numpy --with pyyaml python pipeline/uk_decals.py` (2 s, deterministic). In the level they live in `levels/<level>/art/uk_markings_cubewood/` together with `main.materials.json`; paths inside the materials are `/levels/<level>/art/uk_markings_cubewood/<file>`, so build with `--level <name>` for a level other than `belfast_slice`.

Per material there is one opacity map (`*_o.data.png`, the pattern). Colour, normal and roughness are shared 8x8 flat PNGs (`uk_paint_white_cubewood_b.color.png`, `uk_paint_yellow_cubewood_b.color.png`, `uk_flat_cubewood_nm.normal.png`, `uk_paint_cubewood_r.data.png`). The material structure follows the stock decal-road line materials (`art/shapes/common/decalroads/main.materials.json`: translucent, `RoadAndPath`, an annotation, base colour, normal, opacity and roughness maps). Annotations are ones in the game's `tech/annotations.json`: `SOLID_LINE`, `DASHED_LINE`, `RESTRICTED_STREET` (zig-zag, hatch), `DRIVING_INSTRUCTIONS` (triangle).

## How a DecalRoad uses them (for T-403)

- Image columns run across the road, image rows run along it. The texture is stretched once across the DecalRoad `width` and tiled every `textureLength` metres. Stock `m_line_white_discontinue` is 128 x 4096, tall, and MapNG sets `textureLength: 6.4` for it (`exportBeamNGLevel.js:2952`, `LINE_LAYER_BASE`).
- So T-403 must set `textureLength` per material (column "repeat" below), where MapNG uses 6.4 for everything.
- Line materials are **scalable**: the paint fills the middle 75% of the texture width, the rest is clear margin. DecalRoad width = painted width / 0.75. Painted 100 mm gives width 0.1333 m, 150 mm gives 0.2 m, 400 mm (stop line) gives 0.5333 m. One material serves every width in the YAML, so no 100/150/200 variants exist. Currently MapNG passes `width: 0.15` for lines; it would pass `0.2` for a 150 mm line.
- Fixed materials (Give Way, triangle, zig-zag, hatch) have a fixed DecalRoad width, in the table.
- Dashes are centred in the repeat (half a gap at each end of the tile), so the picture does not depend on which way the road runs. A DecalRoad starts with half a gap.
- Symbols drawn once (the triangle) must be a two-node DecalRoad whose length equals `textureLength` (4.286 m).

## Material to rule

"Paint" is the width in the YAML. "Width" is the DecalRoad width to use. "Repeat" is `textureLength` in metres and the period in the texture.

| Material | Rules (YAML id, diagram) | Paint | DecalRoad width | Repeat |
|---|---|---|---|---|
| `uk_centre_1008_cubewood` | `centre_1008` (1008) | 100 or 150, mark 2000 gap 4000 | paint / 0.75 | 6.0 |
| `uk_centre_1008_1_cubewood` | `centre_1008_1` (1008.1) | 100 or 150, mark 3000 gap 6000 | paint / 0.75 | 9.0 |
| `uk_lane_1005_cubewood` | `lane_1005` (1005) | 100 or 150, mark 1000 gap 5000 | paint / 0.75 | 6.0 |
| `uk_lane_1005_1_cubewood` | `lane_1005_1` (1005.1) | 100 or 150, mark 2000 gap 7000 | paint / 0.75 | 9.0 |
| `uk_warning_1004_cubewood` | `warning_1004` (1004) | 100 or 150, mark 4000 gap 2000 | paint / 0.75 | 6.0 |
| `uk_warning_1004_1_cubewood` | `warning_1004_1` (1004.1) | 100 or 150, mark 6000 gap 3000 | paint / 0.75 | 9.0 |
| `uk_dash_600_300_cubewood` | `edge_1009A` (1009A) | 100 to 200, mark 600 gap 300 | paint / 0.75 | 0.9 |
| `uk_dash_1000_1000_cubewood` | `edge_1010` (1010), `giveway_roundabout_1003_1` (1003.1) | 100 to 300, mark 1000 gap 1000 | paint / 0.75 | 2.0 |
| `uk_giveway_mini_1003_3_cubewood` | `giveway_mini_roundabout_1003_3` (1003.3, unverified) | 500, mark 700 gap 300 | paint / 0.75 = 0.667 | 1.0 |
| `uk_solid_white_cubewood` | `edge_1012_1` (1012.1), `stop_1002_1` (1002.1), `stop_signals_1001` (1001), `bus_lane_1049a` (1049A), `hatched_1040` boundary when continuous | any | paint / 0.75 | 2.0 (no pattern) |
| `uk_solid_yellow_cubewood` | `yellow_single_1017` (1017), and the 200 mm border of `yellow_box_1043` (1043) | 50 to 200 | paint / 0.75 | 2.0 (no pattern) |
| `uk_giveway_1003a_cubewood` | `giveway_1003a` (1003A) | two rows of 200 with a 300 gap (700 overall), dash 600 gap 300 | 0.800 fixed | 0.9 |
| `uk_double_white_mixed_1013_1_cubewood` | `double_white_1013_1` (1013.1) with a broken line on one side | 150 + 175 gap + 150 = 475, broken line mark 1000 gap 5000 | 0.6333 (475 / 0.75) | 6.0 |
| `uk_double_white_solid_1013_1_cubewood` | `double_white_1013_1`, both lines continuous | 475 overall | 0.6333 | 2.0 (no pattern) |
| `uk_double_yellow_1018_1_cubewood` | `yellow_double_1018_1` (1018.1) | 75 + 75 gap + 75 = 225 | 0.300 (225 / 0.75) | 2.0 (no pattern) |
| `uk_giveway_triangle_1023a_cubewood` | `triangle_1023a` (1023A) | outline 1250 wide, 3750 long, line 150 | 1.4286 fixed, length 4.2857 | drawn once |
| `uk_zigzag_1001_4_cubewood` | `zigzag_zebra_1001_4` (1001.4), `zigzag_signal_crossing_1001_3` (1001.3) | guide lines 500 apart, marks 2000, gaps 150, line 150 | 0.5714 fixed | 4.3 (one zig plus one zag) |
| `uk_box_hatch_1043_cubewood` | `yellow_box_1043` (1043), shortest side under 9 m | diagonals 150 wide, 2000 apart | 2.8284 fixed | 2.8284 |
| `uk_box_hatch_1043_wide_cubewood` | `yellow_box_1043`, shortest side 9 m or more | diagonals 150 wide, 2500 apart | 3.5355 fixed | 3.5355 |

Scalable materials take the colour from the shared map (white 238, 238, 232; yellow 240, 196, 40), set by eye, not from a standard.

## Where MapNG draws markings today (what T-403 replaces)

`GLOBAL_DECAL_MATERIALS` (`exportBeamNGLevel.js:2132`) names the stock materials `m_line_white`, `m_line_white_discontinue`, `m_line_yellow*`. They are used by `buildLineMarkingLayers` (`:2952` to `:3075`: centre line, lane separators, edge lines, all `textureLength: 6.4`, width 0.15), junction stop bars and crosswalk edge lines (`:6202`, white, 0.3 m wide), and parking bays (`:10136`). Zebras use `ZEBRA_CROSSING_MATERIAL` from `beamngSidewalks.js`, turn arrows use `mapng_roadmarkings`. A patched MapNG adds our materials file to the level's material definitions (the way `ROAD_ARROW_MATERIAL` is added at `:13252`) or ships `main.materials.json` from this folder, and swaps the names above using `rules/uk_decals/manifest.json` (`rule_to_materials`).

## Rules that are `verified: false` in the YAML and what was built

Built from the stated values; listed here as asked.

| Rule | Material | What is uncertain |
|---|---|---|
| `hatched_1040` | only the boundary: `uk_solid_white_cubewood` (continuous) or the 1004 materials (broken) | diagonal width, spacing and taper tables are not transcribed, so no diagonals are drawn |
| `giveway_mini_roundabout_1003_3` | `uk_giveway_mini_1003_3_cubewood` | mark 700 and gap 300 were read from picture proportions; if the mark is really 300 and the gap 700, regenerate after correcting the YAML (the texture follows the YAML) |
| `crossing_studs_1055_1` | none | studs are objects; not drawn |
| `zigzag_zebra_1001_4` | `uk_zigzag_1001_4_cubewood` (also used for 1001.3, same dimensions) | line width 150 and the 500 guide span are read from the figure; marks 2000 and gaps 150 come from text |

## Choices made without a source (check against the manual)

- Box hatch: "diagonal spacing 2000 / 2500" is taken as the perpendicular distance between parallel diagonals, and the hatch is crossed at 90 degrees (the GB aim). If the manual measures along the road, the tile becomes 2.0 m / 2.5 m square.
- Zig-zag: marks run diagonally from one guide line to the other, alternating direction, with a 150 mm gap between marks along the road. The outside span is 500 mm.
- Give Way triangle: apex at the end the driver reaches first, base next to the Give Way line. This is from the usual layout, not re-read from the diagram image.
- Give Way (1003A) and triangle are the full-size forms; the NI half-size forms are not drawn.
- Double yellow: gap between the two lines is taken as equal to the line width (75).
- Double white 1013.1: line 150, gap 175 (the YAML "recommended" values). Version B (wider gap) is not drawn.

## Not drawn (listed under `rules_not_drawn` in the manifest)

Mini-roundabout centre marking 1003.4 (arrows round a disc, a symbol decal), zebra stripes (stripe pitch is not in the YAML, MapNG's own zebra stays), crossing studs, NI/GB yellow bars 1067/1003.6, hatching diagonals 1040.

## Checks

```text
uv run --no-project --python 3.12 --with pillow --with numpy --with pyyaml --with pytest python -m pytest tests/pipeline/test_uk_decals.py
uv run --no-project --python 3.12 --with pillow --with numpy --with pyyaml python pipeline/uk_decals_check.py
```

The second prints, per dashed material, the mark and gap measured in the PNG against the YAML (all within one pixel, for example `uk_warning_1004_cubewood: repeat 6000 mm, measured mark 4002 gap 1998 (YAML 4000/2000) ok`).

## In-game check (not run; the dispatcher does this later)

Done-when is "materials load with no missing textures". That needs the game, which was busy. Steps, night only (outside 09:00 to 18:00), QA profile only:

```powershell
$env:UV_CACHE_DIR='H:\BelfastData\cache\uk'; $env:UV_PYTHON_INSTALL_DIR='H:\BelfastData\uv-python'
# 1. test copy of the slice zip with the materials added (original untouched)
uv run --no-project --python 3.12 --with pillow --with numpy --with pyyaml python pipeline/uk_decals_pack.py
uv run --no-project --python 3.12 --with pillow --with numpy --with pyyaml python pipeline/uk_decals_check.py --folder H:\BelfastData\uk_decals\test\build\uk_markings_cubewood --zip H:\BelfastData\uk_decals\test\belfast_slice.zip
# 2. back up and replace the QA copy only
Copy-Item H:\BeamNG-QA\profile\current\mods\belfast_slice.zip H:\BelfastData\uk_decals\test\belfast_slice.orig.zip
Copy-Item H:\BelfastData\uk_decals\test\belfast_slice.zip H:\BeamNG-QA\profile\current\mods\belfast_slice.zip
# 3. game
uv run python tools/gamectl.py start
uv run python tools/gamectl.py load-level belfast_slice
uv run python tools/gamectl.py log-errors --strict
uv run python tools/gamectl.py stop
```

Pass means `log-errors --strict` exits 0 and `beamng.log` has no line naming `uk_markings_cubewood` or any `uk_*_cubewood` material with "not found", "missing" or "failed to load". Compare against a run with the original zip: any error present in both is not ours. For a positive check that the materials loaded, run in the console (or through MCP `run_lua`) after the level loads: `scenetree.findObject('uk_centre_1008_cubewood') ~= nil` (the same test `lua/ge/extensions/editor/toolUtilities/surfaceKit.lua` uses for materials), for every name in `rules/uk_decals/manifest.json`. Put the original zip back afterwards.

Not covered by that check, and still open for T-403: that the patterns look right on a road (dash phase, which side is "left" on the double white line, which end of the triangle faces the driver, whether the texture row direction follows the node order). A quick look needs a DecalRoad per material; the T-403 build provides them.
