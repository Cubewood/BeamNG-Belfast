# MapNG patches: the `belfast` branch (T-412)

Decision: **patch files in this repo, applied to the pinned commit by a script.** This confirms the recommendation in `docs/decisions/001-generator.md`. No fork. `third_party/mapng` stays a submodule at the upstream pin `d605117d77857e91acb8234335e9f89263a94756`, so the repository never holds MapNG's code and the submodule can always be fetched from upstream.

## What is where

| Path | Content |
|---|---|
| `patches/mapng/*.patch` | One `git format-patch` file per patch (author, message, diff). Stored byte for byte (`.gitattributes`: `-text`) |
| `patches/mapng/series` | Patch names, in apply order |
| `patches/mapng/BASE` | The upstream commit the patches were made against. Must equal `PINNED_COMMIT` in `tools/mapng_export.py` |
| `tools/mapng_patches.py` | `status`, `apply [--reset]`, `save` |
| `tests/mapng/test_mapng_patches.py` | Tests on throwaway git repos: round trip, changed pin, extra or edited commit, a patch that no longer applies, dirty tree |

In the submodule, `apply` creates the local branch `belfast`: the pin, then one commit per patch (`git am`). This is the `belfast` branch of T-403, but it exists only in the local submodule; what is committed here is the patch files. The superproject's `git status` shows the submodule as "new commits" while the patches are applied; that is expected. Never `git add third_party/mapng`.

## How the wrapper enforces it

`tools/mapng_export.py check_pin` (used by `fetch`, `export` and `tools/mapng_ingame.py`) now requires "pin plus exactly these patches", and exits 3 (`EXIT_PIN`) with the reason otherwise:

- `BASE` differs from `PINNED_COMMIT`: the pin changed and the patches were not rebased;
- the submodule is at the pin with none of the patches applied (run `apply`);
- the number of commits above the pin differs from the series;
- a commit's `git patch-id --stable` differs from its patch file (a commit was edited or reordered);
- any tracked MapNG file has uncommitted changes.

With no `series` file the rule is the old one (HEAD is the pin, tree clean). `export.json` now records `mapng_pin` and `mapng_patches`.

## Workflow

```text
git submodule update --init third_party/mapng      # the pin (use --reference <other checkout>/.git/modules/third_party/mapng to skip the network)
cd third_party/mapng; npm ci --ignore-scripts      # see docs/mapng_export.md
python tools/mapng_patches.py apply                # branch belfast = pin + series (idempotent; fails if the state is unknown)

# add or change a patch (T-403, T-413, ...)
cd third_party/mapng; edit; git commit             # on branch belfast; add the test next to the change
python tools/mapng_patches.py save                 # rewrites patches/mapng/*.patch, series, BASE
git add patches/mapng                              # in the repo root, by name
```

`apply --reset` rebuilds `belfast` from the pin (it drops commits that were not `save`d). `git submodule update` moves the submodule back to the pin and drops the patches; the next `export` then exits 3 until `apply` is run again.

### Re-pinning

1. Move the submodule to the new upstream commit and set `PINNED_COMMIT` in `tools/mapng_export.py`.
2. `export` now exits 3 ("the pin changed"). `apply --reset` stops at the first patch that no longer applies and leaves the submodule clean at the new pin.
3. Rebase by hand: `git checkout -B belfast <new pin>`, `git am --3way patches/mapng/<name>` for each, fix conflicts, then `save` (this writes the new `BASE`).
4. Run MapNG's tests (806 of 812 pass on Windows at the bare pin, 822 of 828 with patches 0001 to 0004, 825 of 831 with 0001 to 0005, 832 of 838 with 0001 to 0006, 837 of 843 with 0001 to 0007, `docs/mapng_export.md`; the six failures are environment) and the new tests of each patch.

Offering generic patches upstream (the building rule, the junction step, mph signs) needs the user's GitHub account and stays a follow-up.

## Patch 0001: buildings cut back by overlap area and depth (gap 1)

File `services/buildingRoadConflicts.js` (and one report line in `services/exportBeamNGLevel.js`).

Before: an OSM building was cut back along the lanes' edge only when 3% or more of its footprint was on the driving lanes (and under 30%). Below 3% it was kept and not listed, so a large footprint with a small share stayed in the lane. OSM building 1260690275 (678 m2, 1.8 to 2.2% in the lanes) stood in the lane at (-211, -93), where the drive suite crashed.

After: the cut also applies when the overlap is at least **1 m2** and **0.15 m deep** past the cut line (the lanes' edge plus the existing 0.3 m kerb allowance). These are the same limits as the `building-in-lane` check of T-301, so the check and the generator agree. An overlap of that size that cannot be cut (holes, or a straight cut along the road line that would remove more than the overlap plus 10% of the footprint) is listed in `export_report.txt` with its area and depth, whatever its share. Hairline overlaps and walls flush with the kerb still stay and are not listed. Unchanged: ML outlines, the 3% to 30% rule, passages and tunnels.

Tests (`third_party/mapng/tests/buildingRoadConflicts.test.mjs`): a 40 x 20 m footprint with 2.5% in the lanes is cut back; a hairline overlap is left alone and not listed; a courtyard footprint is kept and listed.


## Patch 0002: the lanes are subtracted locally (gap 1, follow-on)

File `services/buildingRoadConflicts.js`. Patch 0001 still cut along the straight line of each road segment. A long line reaches past the segment and across a large footprint, so the cut removed more than the overlap and was refused: Tesco Extra (OSM 148546058, 0.25% in the lanes) stayed in the service roads along its edge.

Now each segment's lanes (a capsule at the lane half width plus the 0.3 m allowance, round ends so bends leave no gap) are subtracted from the footprint with `polygon-clipping`, through MapNG's own `polyClip.js` (1 cm grid). It is used when the result is one ring with no hole. The straight cut remains as the fallback. Also new: a road whose centre line runs 5 m or more through an OSM footprint is a passage or a misplaced line, so the building stays as mapped and is listed whatever its share (before: only from 30%). The `clipped` records gain `run` and `local`.

Tests: a road ending beside a 100 m block takes only that corner (the test fails with the straight cut alone, which would keep about 40% of the block and refuse); a road through a large site is kept and listed.

## Patch 0003: a cut-back building gets a flat roof

File `services/beamngBuildings.js`, `planBuilding`. Why OSM 1260691543 (a house) kept a 0.37 m residual in the T-301 check after patch 0001: its walls were cut back correctly (2.1 to 2.5 m from the service road's centre line, MapNG's lane edge), but a pitched roof is drawn on the footprint's **oriented box plus 0.35 m eaves**. After the cut the footprint is a notched hexagon and its box still covers the cut-off corner. Measured on the exported collision mesh: a roof corner at (-714.6, -797.7), 1.0 m from the road's centre line, and the roof's sloping faces read as 4.2 m of wall in the lanes to the check. A footprint tagged `mapng:clipped_from_road` is now planned with a flat roof, which follows the ring: the nearest roof vertex is then 2.1 m from the centre line.

Test (`tests/beamngBuildings.test.mjs`): a house with the tag, even one tagged `roof:shape=gabled`, plans a flat roof.

## Patches 0001 to 0003: result on the Slice (re-export of `belfast_slice`, same OSM, in `H:\BelfastData\mapng\t412\`)

| | Before (`mapng\out\belfast_slice.zip`) | After patch 0001 | After patches 0001 to 0003 |
|---|---|---|---|
| MapNG report: OSM buildings cut back | 11 | 19 | 20 |
| MapNG report: OSM buildings kept and listed | 0 | 3 | 1 |
| T-301 `building-in-lane` findings | 8 (1 high, 5 medium, 2 low) | 5 (0 high, 5 medium) | 1 (0 high, 1 medium) |

### Buildings that still stay in lanes (patches 0001 to 0003)

One finding remains, by design. OSM 1259387657 (`building=construction`, about 76 x 59 m) at (76, -116): 309 m2, up to 0.88 m deep, on a service road (not in the navigation graph). Plain `highway=service` ways 404213828 and 860893189 run 28.5 m through the footprint (15% of it, 534 m2 by MapNG's lanes). No `tunnel=building_passage` tag. MapNG keeps it as mapped and lists it. It is an OSM data question (a service road mapped through a construction site), not a cut-back case, and no override was written. Any override needs evidence (OSNI or Mapillary, never Google).

The other cases of the first run (148546058 Tesco Extra, 1260691543 house, 1258799627) are cut back now.

### Patches 0001 to 0003: not verified

- No game run. The crash at (-211, -93) is expected to be gone because the walls are cut back, but only the drive suite rerun (T-413 Done-when) confirms it. Cut-back houses and the Tesco footprint now have flat roofs and notched outlines; nobody has looked at them in the game.
- The `building-in-lane` check reads the collision mesh of the tiles, as the car would; it was run on the re-exported zip only, with the OSM extract of the original export (`areas/belfast_slice/osm.json`, not the pinned cut).
- Upstream tests: 817, 811 pass, the same six environment failures as the baseline (`docs/mapng_export.md`).

## Patch 0004: UK road markings (T-403, gap 7)

One table and two hooks in a new file, `services/ukRoadMarkings.js` (239 lines); a small diff in `services/exportBeamNGLevel.js` (19 lines added, 9 removed); test `tests/ukRoadMarkings.test.mjs` (11 tests, one per pattern plus the non-UK and roundabout cases). Switch: `isUkRoadRules(roadRules)`, `countryCode === 'GB'` (`ukRoadMarkings.js:18`). `getMarkingStyle` is not touched (still `white` for GB), so signs (`roadSigns.js` `SIGN_SETS[style]`), traffic-calming and curve signs, crossings and parking paint are unchanged; T-409 can key a UK sign set on the same function.

| Hook (patched `exportBeamNGLevel.js`) | What |
|---|---|
| `:3145` `ukLineLayers` | restyles `buildLineMarkingLayers`' white lines; adds warning stretches |
| `:3194` `ukLineNodes`, `:3209` `levelName` | cuts a layer to its stretch after the junction trim; reverses a mixed double white; warning stretches are not named `cl` (names stay unique) |
| `:5819`, `:5994` | `ukMarkings` into the road options |
| `:6208` | `pushLine` takes a material and `textureLength` |
| `:6387`, `:6403` `ukJunctionLines` | Stop and Give Way lines at the stop-bar approaches, plus MapNG's give-way approaches (`signalApproaches.js:434`, until now used for signs only) |

`UK_MARKINGS` (`ukRoadMarkings.js:25`) names all 19 `uk_*_cubewood` materials with their `textureLength` and fixed widths; `ukMarking(key, paintMm)` gives `{material, textureLength, width}` (width = paint / 0.75 for line patterns). T-404 and T-410 use the same call for the patterns not placed here. `tests/mapng/test_uk_markings_export.py` checks the table in the patch file against `rules/uk_decals/manifest.json`.

**Materials in the level.** MapNG writes only the names. `tools/mapng_export.py export` (`add_uk_markings`) finds `uk_*_cubewood` on the zip's DecalRoads, runs `pipeline/uk_decals.py --level <level>` (its own uv env: numpy, pillow, pyyaml) and adds the 24 files to `levels/<level>/art/uk_markings_cubewood/`. It exits 1 if a used material or a texture is missing; `export.json` records `uk_markings`.

### Patterns covered

| Pattern | Where | Material (`textureLength` m, DecalRoad width m) |
|---|---|---|
| Centre 1008 / 1008.1 | MapNG's centre line (two-way, 5.5 m or wider); small module up to 40 mph, large above | `uk_centre_1008` (6) / `uk_centre_1008_1` (9); 0.1333, or 0.2 (150 mm) on two-way roads with 3+ lanes or 10 m+ driving width, or at 70 mph |
| Lane 1005 / 1005.1 | MapNG's lane separators | `uk_lane_1005` (6) / `uk_lane_1005_1` (9); 0.1333, 0.2 at 70 mph |
| Warning 1004 / 1004.1 | last 5 marks (30 mph), 7 (over 30), 7 or 10 on 4+ lane two-way roads, of a centre line that MapNG's junction trim ends (`startTrim/endTrim.center > 0`), and of the lane lines approaching that end. A road too short for both ends plus one module is warning line throughout | `uk_warning_1004` (6) / `uk_warning_1004_1` (9); width of the line it replaces |
| Edge 1012.1 | MapNG's edge lines (major roads, not urban streets with sidewalks) | `uk_solid_white` (2); 0.1333, 0.2 at 70 mph |
| Double white 1013.1 | `overtaking=no`: both continuous; `overtaking:forward` or `:backward=no`: continuous on that traffic's side | `uk_double_white_solid` (2) / `uk_double_white_mixed` (6); 0.6333 |
| Stop line 1002.1 | MapNG's STOP-sign approaches | `uk_solid_white` (2); 0.5333 (400 mm) |
| Signal Stop line 1001 | MapNG's signal approaches (junction and midblock) | `uk_solid_white` (2); 0.2667 (200 mm), 0.4 (300 mm) at 40 mph and over |
| Give Way 1003A | MapNG's give-way approaches (`highway=give_way` or a give-way sign), kerb half from 0.25 m off the centre, a one-way road's full width | `uk_giveway_1003a` (0.9); 0.8 |
| Triangle 1023A | one per approaching lane (lanes 1.6 m or wider), base 2.4 m behind the line's near edge, nodes in the direction of travel, length = `textureLength` | `uk_giveway_triangle_1023a` (4.2857); 1.4286 |

Left-hand traffic: MapNG puts the forward lanes on the right of node order. For GB the centre and lane line offsets are negated, so on Hospital Road (way 1410878961, `lanes:forward=3`, `lanes:backward=1`) the centre line now sits 2.35 m right of the AI path, with the three forward lanes on its left as the AI's `lanesLeft 3`.

**Not covered** (materials in the table, nothing placed): 1009A and 1010 at mouths and lay-bys, roundabout 1003.1, mini-roundabout 1003.3 and 1003.4 (a give-way at a roundabout or `highway=mini_roundabout` node is left unpainted), zig-zags, zebras (MapNG's Italian decal stays), box junctions, yellow lines, hatching 1040, bus lanes, the STOP legend 1022, warning lines at bends, crests and refuges, give-way lines at junctions with no OSM give-way or stop node, parking bays and crossing lines (stock `m_line_white`). These belong to T-404 and T-410. None of the four `verified: false` YAML rules is placed; two of them have a material in the table and the level (`giveway_mini_roundabout_1003_3`, `zigzag_zebra_1001_4`), two have none (`hatched_1040` diagonals, `crossing_studs_1055_1`).

**Choices without a source:** the triangle gap 2.4 m (TSM 3 range 2.1 to 2.75 m); 300 mm signal lines from a 40 mph limit (TSM 6 says 85th percentile over 35 mph); an unmapped limit is 30 mph (70 on a motorway); warning stretches only at the junction ends MapNG trims; the mixed double white's continuous line is on the image left = left of node order (manifest note, unverified).

### Result on the Slice (`H:\BelfastData\mapng\t403\out\belfast_slice.zip`, area copy, t30, same OSM as T-412)

DecalRoads per UK material, from the zip: `uk_solid_white` 171 (143 edge lines, 27 signal lines at 300 mm, 1 at 200 mm), `uk_lane_1005` 39, `uk_centre_1008_1` 36, `uk_giveway_1003a` 36, `uk_giveway_triangle_1023a` 36, `uk_centre_1008` 26 (10 at 150 mm), `uk_lane_1005_1` 14, `uk_warning_1004` 12, `uk_warning_1004_1` 6. Stock `m_line_white_discontinue` 121 before, 0 after; `m_line_white` 659 before, 488 after (484 parking stalls and 4 crossing lines stay). Everything else is unchanged (asphalt 1176, AI roads 463, gutters 1320). No double white (no overtaking tags) and no STOP sign in the Slice. All 36 triangles run towards their own line, 2.40 m from it; one, at (893, 17), lies 0.46 m from the line of another approach.

`qa/static_checks` on the new zip and on the T-412 zip: 197 against 179 findings, high 134 both. The 18 extra are `lint-textures` (low): MapNG's linter wants square or 2:1 PNGs and ours are 16 x 2048 and the like, as the stock line textures are tall (`docs/uk_decals.md`). `missing-stock-asset` 8 in both (the known sky files); none names a `uk_` file.

### Not verified (no game in this task)

In the game, look at: the dash phase (marks centred in each repeat, half a gap at each end, the joint where a centre line becomes a warning line); which side of the mixed double white is continuous (none in the Slice: needs a tagged road); that the triangle's apex meets the driver (texture row 0 at the first node); z-fighting where give-way lines meet the warning line's end, triangles over the asphalt, and the overlap at (893, 17); and that 0.133 m line widths read as 100 mm.

## Patch 0005: junction mouths blended (T-413, gap 2)

File `services/structures/terrainCompose.js` (`composeTerrain`, structure solver v2 terrain, the stage that writes the ground under every road); test `tests/structureTerrain.test.mjs` (3 new tests).

Before: inside two road cores (half width + 1 m) the nearer centreline owned the pixel outright. Where a side street meets a road on a slope, both flat cross-sections met in a step along the line midway between the centrelines: the side street's level on one half of the main road, the road's own grade on the other. On the Slice: 34 cm at (199, 285) on Hospital Road where Newtownbreda Road meets it (gap 2, the T-104 and T-302 crash site). MapNG's own joint check (`services/structures/metrics.js:105-144`) compares only centreline ends, so it reports 0 steps.

After: where the cores of roads that share a node overlap (directly, or through another road on the pixel), the pixel gets their blend, each road weighted by `smoothstep(1 - d / core)` (1 on its centreline, 0 at its core's edge), so the surface warps across the full width of both and meets each road's own surface where the other's core ends. A claim more than 1 m from the owner's height fades out by 2 m, where MapNG's two-level rule (the lower road keeps its width) takes over. Roads that do not share a node keep the nearest-centreline rule. Past a road's end at a node shared on the ground, its surface now carries on at its end grade (fitted over 8 m; shorter edges stay flat), so a straight road split at a node keeps its grade. `stats.blended` counts the blended pixels (46,000 on the Slice); the terrain stage takes 1.17 s instead of 1.02 s.

Tests: a T-junction with a side street on 6 %: the largest second difference over 1 m in the main road's lanes is 0.24 m before (the test fails on the unpatched file) and under 0.1 m after; a road split at a node on a grade keeps it within 1 cm; two parallel roads that do not meet keep their own surfaces. Upstream: 831 tests, 825 pass, the same six environment failures.

### Result on the Slice (`H:\BelfastData\mapng\t413\`, area copy `t413_slice`: pinned OSM, `gpxz` heights)

| `qa/static_checks --only steps` | Before (0001 to 0004) | After (0001 to 0005) |
|---|---|---|
| `terrain-step` over 0.15 m (all / navigation roads) | 519 / 378 | 403 / 278 |
| high (0.25 m and over, capped list of 150) | 145 | 94 |
| at junction mouths: two cores of roads sharing a node (navigation) | 136 | 41 |
| in-lane only: both ends of the 1 m stencil inside the lanes (all / navigation) | 214 / 118 | 101 / 23 |
| Hospital Road / Newtownbreda Road | 0.34 m in the lane at (199, 285) | none in the lane; 0.28 m at the lane edge at (196, 286), the corner kerb |

MapNG's own report: tilted or humped cross-sections 40 to 26; floating and buried sidewalk samples 413 and 311 to 412 and 308; junction centres off 13 both.

What the check still finds (navigation roads, 278), by where the step comes from (a harness dumped the terrain after `composeTerrain`):

- 183 come after the road terrain, from the sidewalk ground (`gradeSidewalkGround`), at the lane edge: 114 by a kerb or sidewalk run (0.20 to 0.25 m: the 15 cm kerb within 1 m of a lane that reaches the kerb), 69 by a separately mapped footway whose height follows the ground, not the kerb (0.15 to 0.55 m, for example Purdysburn Road at (323, 261)).
- 41 at junction mouths: forks of steep tracks (two cores over 1 m apart in height, where the blend fades out) and the corner where both cores end, which a symmetric blend cannot match (outside the carriageways, but a lane-edge stencil reaches it).
- 28 between roads that do not share a node: parallel carriageways (Milltown Road at (-40, 334), Belvoir Road and its link at (430, 890)).
- 26 on one road: a way that comes back to touch itself (Brerton Crescent at (432, 584): the graph does not join it there, so its two passes have their own heights) and cut banks at the core's edge.

Most (250 of 278) are within 0.5 m of a lane's edge: the check's 1 m stencil at a lane-edge vertex reaches the kerb or bank outside the lane.

### Drive suite: not verified (2026-11-14, `qa/drive_suite.py run --gate`, zip copied to the QA profile's `mods`)

- Run 1 (`H:\BelfastData\qa\drive\2026-11-14_t413`): the game did not answer on MCP within 120 s. Exit 5, the game stopped cleanly, no route driven.
- Run 2 (`...\2026-11-14_t413b`): the game started and dumped its graph (`H:\BelfastData\qa\graph\belfast_slice.graph.json`, 20:19). All 10 routes were rejected before driving: each path lies 4 to 7 m from the new graph, because this build (pinned OSM, `gpxz`, roads snapped to the ground) moved the roads. Exit 20, 0 of 10 completed, no crash recorded at (-211, -93) or (194, 284) because nothing was driven.
- Next step: `qa/routes/build_routes.py rebuild` on the new graph dump, then run the suite again. The pre-T-413 QA zip is kept as `H:\BelfastData\mapng\t413\qa_mods_belfast_slice.before_t413.zip`.

## Patch 0006: kerbs and walkways at the road beside them (I-005, crash 1 of `docs/spikes/drive-crashes.md`)

Files `services/beamngSidewalks.js`, `services/roadMarkingCuts.js`, `services/structures/adapters.js`, `services/exportBeamNGLevel.js`; tests `tests/beamngSidewalks.test.mjs` (4 new), `tests/roadMarkingCuts.test.mjs` (1), `tests/sidewalkGround.test.mjs` (1), `tests/structureAdapters.test.mjs` (new, 1).

Cause. The 0.40 m faces are walkway edges, not curbs: a separately mapped footway is swept with the profile 0.30 m down and 0.10 m up (`WALK_SINK_M`, `WALK_H`), so every walkway face is 0.40 m tall by design (38,172 faces, the quarter of the slice). What made one dangerous was its height. At (139, 274) the footway beside the Hospital Road slip stood with its base at 29.40 m, the slip's decal at 29.08 to 29.10 m: its top 0.40 m over the asphalt. Two faults, measured with a debug print in the export:

1. The walkway took the height of the first carriageway the index listed within 5 m (`carriagewaysAt(...)[0]`, pin `services/beamngSidewalks.js:2119`), not the nearest. Here that was a segment of the climbing road several metres east, 0.26 m higher.
2. The curbs and walkways are swept along the carriageways at their laid-out heights. Structure solver v2 then moves the decals (`applySolvedHeights`, `exportBeamNGLevel.js:11951`) and the terrain to the solved profile, about 0.1 m lower here, but not the carriageways. So curbs stood 0.25 m over the road, not 0.15 m; that is the 0.22 to 0.25 m findings on many streets.

Change.

- Sidewalks and their ground grading get copies of the carriageways at the solved heights (`solvedCarriageways`, `adapters.js:200`; `exportBeamNGLevel.js:12167`). Other users of the carriageways are unchanged.
- `nearestCarriagewayAt` (`roadMarkingCuts.js:82`): the carriageway whose edge is nearest, with its height there. Walkway heights and walkway road ends use it.
- Caps, which only ever lower a height: a curb line is no higher than any road beside it (a street that turns back on itself put one leg's curb on the other leg's lower asphalt, Brerton Crescent and Forest Grove). A curb return is no higher than the road nearest each arc point. A walkway's base is no higher than the street's curb level (+0.15 m top) or a driveway's ground +0.15 m within its half width + 2.5 m (OSM service roads are no carriageways to the sidewalks). It is capped again after its junctions and ends are levelled.
- With the curbs 0.1 m lower, a dip's ground (0.09 m under the road) left a hole in the lane where a crossing or driveway dip lies on drawn asphalt (5 more `terrain-step` findings). The ground under a slab is now never more than 0.04 m under the road it dips to.

### Result on the Slice (`H:\BelfastData\mapng\i005\`, area `t413_slice`: pinned OSM, `gpxz`)

| | T-413 build (0001 to 0005) | I-005 build (0001 to 0006) |
|---|---|---|
| `kerb-height`, top over the road (all / navigation) | 27 / 18, seven at 0.40 m | 2 / 0, max 0.30 m |
| `kerb-height`, face's own height (the I-006 measure) | 48 / 26 | 48 / 26 (walkway faces are 0.40 m by design) |
| HR12 south edge, x 131 to 148: top of the faces within 0.9 m of the slip's edge, over the slip | up to 0.43 m | up to 0.18 m |
| `terrain-step` (all / navigation / navigation high) | 100 / 23 / 16 | 96 / 23 / 16 |
| in-lane step vertices over 0.15 m (navigation / other) | 309 / 605 | 309 / 508 |

Still found, both off the navigation graph: a footway carrying on uphill from the end of a service road, its side edge 0.30 m over the road's end at (-277, 821); and a curb 0.23 m over a 6 m service road's mouth at (635, 172), past the 2.5 m driveway dip.

Both are the check, not MapNG (I-005, 15 November 2026; no patch 0008). At (635, 172) the face is Purdysburn Road's 0.15 m curb, 0.15 m over the terrain on both sides; the check measured it over the service road's centreline decal, 0.12 m lower than the ground at the curb. At (-277, 821) the footway's side edge starts 0.1 m past the service road's end and climbs 0.28 m along its 1 m; the check took its top at the far end against the road's end. The kerb check now measures over the terrain beside the face and only on the parts within 0.5 m of a lane (`docs/static_checks.md`): 0 findings on the I-007 build. Left for later, off every lane: that footway's top climbs about 0.3 m over its own path decal and the ground north of it between x -277.7 and -276.2.

### Drive suite (2026-11-15 00:27, `qa/drive_suite.py run --gate`, `H:\BelfastData\qa\drive\2026-11-15_002703`)

The I-005 zip was put in the QA profile's `mods` (the T-413 zip it replaced is kept as `H:\BelfastData\mapng\i005\qa_mods_belfast_slice.before_i005.zip`). `build_routes.py check`: 10 routes, 0 problems (the roads did not move). Status ok, 10 of 10 routes completed, G3 pass, events: 2 `scrape`.

- `hospital-road-ne-back`: completed 365 of 391 m, through x 131 to 148. It still logs a `scrape` at (141, 276), not a crash: the AI still runs its left wheels about 0.9 m off the slip (the lane count and graph at the fork, crash 1's second defect), now over a 0.15 to 0.18 m edge instead of 0.40 m.
- `lady-ishbel-loop-reverse`: completed, a `scrape` at (-197, -146).
- Console (`H:\BeamNG-QA\profile\current\beamng.log`): no TextureCooker error or warning; the 24 TextureCooker lines for `uk_markings_cubewood` are imports. One error (Steam online provider, the throwaway profile). 15 warnings, 11 of them: no licence plate template for `belfast_slice`.

## Patch 0007: an AI lead-in for a branch of a lane fork (I-007, crash 1's second defect)

New file `services/laneForkLeadIns.js` (`findLaneForkLeadIns`, `leadInEnd`), hooked into `services/exportBeamNGLevel.js` (`getLayeredRoadDecals`: AI layer pieces by `nodeRange`; layer code `lead_ai`). Tests: `tests/laneForkLeadIns.test.mjs` (4, new) and one export test in `tests/beamngExportGuideConformance.test.mjs`.

A lane fork is one one-way way whose lanes split between two or more one-way branches, with the lanes adding up. A branch whose lanes lie off its own line gets its AI path as a short `_lead_ai` piece, 0.15 m wider each side, then the `_ai` rest, so BeamNG's navgraph keeps a node about 10 m past the fork. Cause, numbers and the game result: `docs/spikes/drive-crashes.md` section 4. Build `H:\BelfastData\mapng\i007\` (area `t413_slice`, `gpxz`). Upstream tests: 843, 837 pass, the same six environment failures. Drive suite `2026-11-15_165700`: 10 of 10 completed, G3 passed, one `scrape` (`lady-ishbel-loop-reverse`). The `hospital-road-ne-back` scrape is gone.
