# Decision 001: MapNG stays as the generator (T-106 gap report)

Date 13 November 2026. MapNG pinned at `d605117`. Level `belfast_slice` from T-103.

**Question.** What is wrong with `belfast_slice`, and does each gap get fixed by data, by a patch to MapNG, or by replacing part of the generator? Does MapNG stay?

**Basis.** Automated findings only. The user deferred the VR drive (T-105) on 13 November 2026, so nothing here was judged by eye or in a headset. Conclusions marked **(revisit after T-105)** depend on how the map feels in VR.

**Sources.** `docs/spikes/mapng.md` (T-101), `docs/mapng_export.md` (T-102), `docs/belfast_slice.md` (T-103), `docs/spikes/baseline.md` (T-104), `docs/spikes/ai-graph.md` (I-001), `docs/drive_suite.md` (T-302). I also ran new static analysis for this report: read-only scripts over `H:\BelfastData\mapng\out\belfast_slice.zip` (`terrain.ter`, `export_report.txt`, DecalRoads), `H:\BelfastData\mapng\areas\belfast_slice\osm.json` and the T-302 telemetry in `H:\BelfastData\qa\drive\2026-11-11_232005\`. The scripts were throwaway and are not in git. Coordinates are world metres from the map centre, x east, y north.

## Verdict

**MapNG stays as the generator. No part needs replacing now.** Every gap found is one of three things:

- an input problem: elevation, or an OSM footprint;
- our own tooling;
- a contained defect or gap in a MapNG function that we can name.

Nothing points at a structural limit. The road graph, left-hand rules, lane inference, AI navigation, signals and the structure solver all work on the slice (I-001, T-302). A replacement would lose the structure solver, sidewalks, signals, probes, linter and export report, and gain nothing that has been shown to be needed.

**Patches.** Keep them small, each with a unit test, on the `belfast` branch that T-403 names. The first patch task sets the branch up. Recommended setup: store the patches as files in this repo and apply them to the pinned commit with a script. The submodule then stays fetchable from upstream without a fork, and `tools/mapng_export.py` checks "pin plus exactly these patches". Three of the patches below fix generic bugs: the building share rule, the junction-mouth step and mph signs for GB. These are worth offering upstream, which needs the user's account (follow-up).

**When to look at replacing a part again:**

1. T-404 cannot give acceptable junction layouts. T-405 (osm2streets junction polygons) is the gate, and it covers junction geometry only.
2. The patch set grows past what a re-pin can rebase in one session.
3. T-105 turns up feel problems that the automated gates do not see **(revisit after T-105)**.

## Gaps found

"Data" means our inputs or overrides. "Ours" means our tooling outside MapNG. "Patch" means a change to MapNG on the `belfast` branch.

| # | Gap | Evidence | Fix by | Task |
|---|---|---|---|---|
| 1 | **Building standing in the left lane.** Crash at (-211, -93) in `lady-ishbel-loop` and `haddo-woods-loop`, both in lane-following mode. The car hit it at 12.6 m/s, going straight about 8 m after a left turn (heading 100° to 13° in `telemetry_lady-ishbel-loop.csv`, so not a right turn). | OSM building 1260690275 (677.6 m²) comes within 1.32 m of the centreline of unnamed residential way 802875024. About 12.5 to 14.9 m² of it lies in the lanes, a share of 1.8 to 2.2%. MapNG only cuts an OSM building back when its share is between 3% and 30% (`services/buildingRoadConflicts.js:37,39,232`). Below 3% it keeps the building as mapped and does not list it, so a large building with a small overlap stays in the lane. The terrain at the site is flat (within 3 cm), and the centreline script drive passed. My estimate for the whole slice: 19 OSM buildings left in the lanes, 10 of them by 2 m² or more, most on service roads. MapNG itself cut back 11. | **Patch**: cut back by absolute overlap or depth into the lanes, not by share, and list what is left. **Data**: an override for 1260690275 if the footprint or road is wrong (evidence from OSNI or Mapillary, never Google). **Ours**: a G0 check. | T-412, T-301 |
| 2 | **Terrain step across the carriageway where two roads meet**, Hospital Road / Newtownbreda Road. Crash at (194, 284) in T-302 lane mode, and at (199, 283) in T-104 with MapNG's script drive. | `terrain.ter` has a 26 to 28 cm step on the north half of Hospital Road at x 199 to 200, y 283 to 287, where Hospital Road meets Newtownbreda Road. The centre and south side ramp smoothly. Bump 9.9 m/s² at (198, 284). MapNG's own checks miss it. Its joint check compares only the end nodes of carriageway centrelines (`services/structures/metrics.js:105-144`), and its junction-centre check compares only the centre against points 12 m out. So the export report says "Steps where roads meet: 0 of 813 joints". My scan of drivable lanes (centre and ±1.5 m, ±3 m) finds 20 places in 10 m cells where the height changes by more than 0.25 m within 2 m (second difference). Three of them are at the bridge deck (-900, 437), where the step is expected. The rest are on Belvoir Road, Dunseverick Avenue, Milltown Road and Ballylesson Road. | **Patch**: blend the graded corridors of meeting roads across their full width at junction mouths. **Ours**: a G0 check for steps across the lanes. LiDAR will not fix this on its own, because the slope (about 6%) stays. | T-413, T-301 |
| 3 | **Speed humps fail G3.** Script drive bump of -9.1 m/s² at (367, 755). | OSM `traffic_calming=hump` 3762690401 is 3 m from the point. The slice has 12 humps, 15 tables, 4 cushions and 1 bump, all placed (export report). The suite drives over them at up to 30 mph. | **Ours**: slow to a learner's speed over mapped calming, or exempt it from the bump limit. | T-311 |
| 4 | **Lane-side metric on roads with unequal lanes.** `hospital-road-ne-out` had 403 of 1415 samples to the right of the centreline (median +0.28 m). | Hospital Road way 1410878961 has `lanes=4`, 3 forward and 1 backward. Its lane divider is not on the way centreline, so "right of centre" is not "wrong side" there. | **Ours**: judge the side against the lane, not the way. | T-311 |
| 5 | **Sidewalk probes**: 7 of 247 failed (2.8%, limit 2%). | Four probes hit the sidewalk mesh itself 0.31 to 0.45 m above the expected height (rule at `services/ingameProbes.js:174-175`); three of these are near (265, 950), on slopes where the export report also lists humps and tilts (Belvoir Road, Dunseverick Avenue). Two are buildings over the sidewalk; one is sidewalk floating 0.52 m over the terrain. | **Data first**: re-measure on LiDAR. If they persist, **patch**: extend the gap 1 rule to sidewalks, and fix the sidewalk height on slopes. | T-203, then T-412 or T-404 |
| 6 | **Coarse elevation.** 30 m Terrarium. | `tools/mapng_export.py:344` always passes `t30`. That makes `exportArea.mjs:43,57` set `coarseElevation`, so MapNG applies its full smoothing and adds synthetic "fine ground detail" (`exportBeamNGLevel.js:11870-11883`): 74% of the terrain, up to 1.07 m (export log). Export report: 48 tilted or humped cross-sections, 21 of 214 junction centres off by more than 0.25 m, 126 creases. | **Data** (LiDAR) plus **ours**: write the LiDAR heights as `gpxz.f32` with `gpxzSmooth: false` in `meta.json`. MapNG then treats it as fine terrain (snaps roads, light smoothing, no synthetic relief), and the wrapper gets a DEM option. No patch needed. | T-203 |
| 7 | **UK markings.** | The marking style is either `americas` or `white` (`exportBeamNGLevel.js:3475-3477`). Stop bars use the MUTCD thickness and are painted only at signals and stop signs (`:6094`, `:6375-6396`). There are no UK give-way double dashes, hatching, box junctions or zig-zags. Zebras use the Italian decal (14). Slice OSM: 37 give-way nodes, 26 signals, 10 signal crossings, 3 uncontrolled crossings. | **Patch**: a `uk` style, through the same switch. **Ours**: decal materials and rules. | T-401 to T-404 |
| 8 | **UK signs.** | MapNG has only two sign sets: americas and Vienna (km/h, Italian meshes) (`services/roadSigns.js:39-65`). It skips mph limits on a unit mismatch (`:241-243`). Export log, signs not placed: "speed_40mph 9, speed_50mph 10, speed_30mph 9, speed_60mph 1", and 13 give-way and 17 no-entry for lack of room. The Italian roundabout sign shows anticlockwise. AI speed is fine: 226 of 265 public road ways carry `maxspeed`, all in mph, and MapNG converts it (T-101). | **Patch**: a `uk` sign set keyed on the T-403 style. **Ours**: sign assets from DfT images. | T-409, T-408 |
| 9 | **Roundabouts not verified.** | Two in the slice: way 148565335 (tertiary, about (457, 876)) and way 1158967869 (residential). Only the script drive has gone round one. The lane drive crashed first (gap 2). The ring follows the OSM way, which is clockwise. No mini-roundabouts, box junctions, bus lanes or cycle lanes are tagged in the slice. | Verify first (AI); markings come with gap 7. Nothing to replace. | T-407, T-404 |
| 10 | **AI rules not verified.** | `rightHandDrive` is true, and AI keeps left on two roads (44 of 44 samples, I-001). One-ways, turn restrictions and obeying signals are unchecked. The light cycle is MapNG's EU red+amber (matches the UK). 7 signal placements were dropped (export log). | Verify. Does not need UK markings. | T-407 |
| 11 | **Four T-104 crashes not explained**: (-387, 9), (404, 694) and (267, 880) (MapNG script line; (199, 283) is gap 2). | Within 4 m of each there is no static object other than DecalRoads. The terrain steps nearby are kerb-like and also occur at control points. Single runs only. | Unattributed. Rerun after T-412 and T-413, and view them in T-303 frames. | T-413 |
| 12 | **Regional look.** | Biome is "East Coast USA" (no GB biome in `beamngBiomeCatalog.js`). Also 1335 ECA wood fences, 236 ECA lampposts, 288 Italian stone walls, 484 parking stall lines. | Accept for now: buildings only need to look like a city. Later this is a patch or our own props **(revisit after T-105)**. | M7 or M8 |
| 13 | **Licence-plate template warning** at every vehicle spawn. | `baseline.md` item 4. | **Ours**: a post-export file, or an upstream issue. | T-411 |
| 14 | **Silently missing stock assets** not checked. | The linter ran with `--no-game` (`belfast_slice.md`). | **Ours**: a G0 check with a game file index. | T-301 |
| 15 | **Test-centre start is provisional.** | `belfast_slice.md`. | You. | section 0 |

Already closed, and not gaps: driving side (`check` asserts it), the AI graph error (a test-order artefact, I-001), satellite, Google and Overture content (blocked by the wrapper).

## Consequences for the queue

The ranking, highest first:

1. **Cheap static checks before any patching.** T-301 no longer waits for the pinned OSM (T-202). It starts on the current export and adds checks for gaps 1, 2 and 14.
2. **Crash causes before UK detail.** The new T-412 (building cut-back rule) and T-413 (junction-mouth step) open M4. Both are generic MapNG bugs.
3. **Drive-suite accuracy.** The new T-311 makes G3 measure the build, not speed humps or lane layout.
4. **AI behaviour early.** T-407 now needs only T-302, so roundabouts, one-ways and signals get checked before the markings work.
5. UK markings (T-401 to T-404), then signs (T-409) and speed limits (T-408), in the existing order. Mini-roundabouts, box junctions and bus or cycle lanes are not in the slice, so they wait for a larger area **(revisit after T-105)**: the user may rank signs above markings.
6. The image judge (T-304 to T-306) keeps its place. All the defects found so far are geometric and scripts catch them, so G4 matters most once UK markings exist.
7. M2 is unchanged. T-203 must write `gpxz.f32` (gap 6). This note is for that row's author; the row itself was not edited here.

What to revisit after T-105: gap 12 (look), the order of markings against signs, whether junction geometry and kerb radii feel right (the T-405 trigger), and whether the sidewalk and elevation fixes are enough at driving speed.

## Not verified

- No game run for this report. Gaps 1 and 2 are causes inferred from geometry, telemetry and the exported terrain. They become confirmed only when T-412 and T-413 rerun the drive suite with no crash at either site.
- Slice-wide counts (19 buildings, 20 step cells) are estimates. They use my own half-widths and thresholds, not MapNG's `estimateRoadCrossSection`.
- Screenshots and the T-104 crash frames were not viewed (no model, no image review).
