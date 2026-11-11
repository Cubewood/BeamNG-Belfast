# AI traffic does not move on belfast_slice (I-001)

Question: why did `ai.setMode("traffic")` on `belfast_slice` (T-104) end in `graphpath.lua:268 edgeList nil (edge 3230)` from `mapmgr.lua:281`, and where should the fix live?

Game build 0.40.5.0, build 302427 (log header of `beamng_end.log`; `integrity.json` buildinfo 302427). Line numbers in the trace match the installed Lua. Raw material: `H:\BelfastData\spikes\ai-graph\` (not in git): `probe.json`, `side.txt`, `beamng_probe.log`, `static\drivable_roads.json`, and the two scripts `roads.py`, `aiprobe.py`, `side.py`.

## Verdict

**The level is not broken. The T-104 test was.** The car had just driven MapNG's `driveTest`, which uses `ai.driveUsingPath{script=...}`. That puts a dummy graph into the vehicle's `mapmgr`, and the dummy has no `edgeList`. Switching the same vehicle to `traffic` then makes one AI frame read the real edge kd-tree against the dummy graph. The vehicle Lua throws, and the game disables the vehicle. A fresh vehicle on `belfast_slice` drives AI traffic normally and **keeps left**.

Confidence: high. The in-game run below reproduced the error on demand (same edge 3230, same stack) and avoided it on demand. What would falsify it: the same error on a vehicle that never ran a scripted path. Phase 1 below is exactly that case, and it drove 383 m without an error.

## Evidence

Game code (read-only):

- `lua/common/graphpath.lua:147-168` `import()` always builds `self.edgeList`. `newGraphpath()` (`:123-131`) does not: it only sets `edgeListCount = 1`. `uniEdge` adds no eId, and nothing calls `generateEdgeIndex()` (`:250`).
- `lua/vehicle/ai.lua:6054-6088`: `driveUsingPath` with `script` builds `pathMap = graphpath.newGraphpath()`, adds nodes `wp_1..wp_n` with `uniEdge`, and never builds an edge index. `setScriptedPath` (`ai.lua:3946`) then calls `mapmgr.setCustomMap(pathMap)`.
- `lua/vehicle/mapmgr.lua:72-77`: `setCustomMap` replaces `mapData` and nils `mapBuildSerial`, but leaves `edgeKdTree` as the real level kd-tree. `clearCustomMap` (`:79`) has no caller anywhere in the game's `lua/` (grep).
- `ai.lua:5822-5826`: `setMode("traffic")` runs `resetMapAndRoute()`, then `mapmgr.requestMap()`. That request is asynchronous: GE `map.request` (`lua/ge/map.lua:3739-3741`) queues `mapmgr.setMap` back to the vehicle. Before that reply arrives, `updateGFX` (`ai.lua:5190`) has already taken `mapData = mapmgr.mapData`, which is still the dummy. It reaches `trafficPlan` (`ai.lua:4906`), then `mapmgr.findBestRoad`. Line 280 there queries the real `edgeKdTree` and gets real edge ids such as 3230. Line 281 passes them to `dummy:getNodesFromEdgeId`, which fails because `edgeList` is nil.

Logs:

- T-104 log (`H:\BelfastData\qa\baseline\2026-11-11\beamng_end.log:851,864`): `self = {edgeListCount:1, radius, graph, positions}` with `graph = {wp_16: ...}`. That is the script dummy, not the level graph.
- `driveTest.mjs:444` (MapNG `d605117`) sends exactly that call: `ai.driveUsingPath({script = {...}, driveInLane = "off"})`. The `gridmap_v2` control run was a fresh game with no scripted drive before it (no `driveUsingPath` or `mapngDrive` in `beamng_control_gridmap.log`), so it was never a like-for-like comparison.

Static check of the level (`roads.py` on `belfast_slice.zip`):

- 4052 DecalRoads, 537 drivable. 0 duplicate names, 0 duplicate persistentIds, 0 drivable roads with fewer than 2 nodes, 0 zero-length segments, 0 nodes with width <= 0. 75 oneWay, 0 flipDirection.
- `map.json` has `{"segments": {}}`, which by design is empty (`exportBeamNGLevel.js:13221-13224`). The nav graph comes from drivable DecalRoads: `ASPHALT_BASE` and `AI_PATH_LAYER` set `drivability: 1` (`exportBeamNGLevel.js:2279-2290`), and `getDecalRoadNavMetadata` (`:2688-2710`) sets drivability, lanes, oneWay and speed.
- The left-hand-traffic flag changes only the lane inference (`inferManualLaneData(..., leftHandTraffic)`, `:2690`) and spawn and parking sides. It does not change graph topology.
- The real vehicle graph has `edgeListCount` 4469, which is 2234 edges, matching `itemCount 2234` in the kd-tree in the trace. Edge 3230 is just a Hospital Road edge near (-122, 109) (`dr_Hospital_Road_5_s1_ai` is 0.6 m away). It is not a defect.

**Roads or nodes affected in the slice: none found.** No MapNG export file or line is at fault.

## In-game proof (2026-11-11 22:46-22:52, one launch, QA profile, `gamectl` only)

Run with `aiprobe.py`: Covet `15le_M` spawned with MCP `spawn`. The vehicle `mapmgr` state was read with `run_lua_vehicle`. The lane side was taken from the signed offset of 1 Hz samples against the nearest drivable two-way centreline (`side.py`; positive means left of the direction of travel).

| Phase | What | mapmgr after | FATAL count | Result |
|---|---|---|---|---|
| 1 | fresh Covet on Hospital Road (-121.3, 111.2), `setMode("traffic")`, 30 s | `traffic\|real\|4469` | 0 | drove 383 m, max 16.7 m/s, 27/27 two-way samples **left**, offset +1.5 to +1.7 m |
| 2 | same car: 12-point `driveUsingPath{script}` along Hospital Road, then `setMode("traffic")` | after script `manual\|dummy\|1` | 1 | same trace as T-104 (`eId 3230`, graph `wp_1..wp_12`, `ai.lua:4906` `trafficPlan`), then `*** DISABLING VEHICLE DUE TO EXCEPTION ***`; car coasted 57 m at most 6 m/s, drifting 6 m off the line |
| 3 | respawned Covet on Purdysburn Road (433.9, 227.2): script drive, then `mapmgr.setMap()`, then `setMode("traffic")`, 30 s | after setMap `disabled\|real\|4469` | 1 (no new error) | drove 395 m, max 20.5 m/s, 17/17 two-way samples **left**, offset +1.2 to +1.6 m |

`gamectl log-errors` afterwards showed the Steam provider error (baseline), the phase-2 exception, and a follow-on `GELua.onSpawnError ... "covet"` line printed with the disable. Game stopped through `gamectl stop` (port closed, lock released). Afterwards there were 0 BeamNG processes. No test zip was installed, and `belfast_slice.zip` was not touched.

## Proposed fix, ranked

1. **Our QA tooling (do this).** No level or pipeline change is needed. Any test that sets an AI mode (`traffic`, `random`, `driveUsingPath` with `wpTargetList`) must run on a vehicle whose `mapmgr` holds the level graph. Two ways to ensure that:
   - (a) Spawn or replace the vehicle after `driveTest` (a new vehicle Lua VM), or run AI tests before `driveTest`.
   - (b) After any `driveUsingPath{script}`, queue `mapmgr.setMap()` in the vehicle before `setMode`. Phase 3 proves (b). `setMap` with no serial re-imports the last `mapData` mailbox (`mapmgr.lua:45-66`), and every function involved exists in the game's `lua/`.
   - Put the check into the T-104-style traffic-side test: `run_lua_vehicle` must report `mapmgr.mapData.edgeList ~= nil` before the mode switch.
2. **A post-export pipeline step or export option: not needed.** The level data has no defect (static check above).
3. **MapNG patch (upstream, optional).** `driveTest.mjs` leaves the player vehicle on a dummy graph after its last route (cleanup at `driveTest.mjs:119` and `:282` only calls `ai.setMode("disabled")`). An upstream issue would say: "after driveTest, `ai.setMode('traffic')` on the same vehicle throws `graphpath.lua:268 edgeList nil` and disables the vehicle. Restore the level graph with `mapmgr.setMap()` in the cleanup."
4. **BeamNG (report only).** The root defect is in the game. A `script` path graph is built without `generateEdgeIndex()`, `clearCustomMap` is never called, and `setMode` leaves one frame where the dummy graph is used with the level's `edgeKdTree`. This is outside our control. Workaround 1(b) covers it.

## Corrections for earlier notes

- `docs/spikes/baseline.md` "Traffic side" and T-106 item 3 say the level's vehicle-side road graph is broken. That is wrong: it was a test-order artefact.
- Left-hand lane keeping by AI traffic is now observed on two roads: Hospital Road and Purdysburn Road / Hospital Road 25, 44 moving samples in total, all on the left.

## Not verified

- Roundabouts, one-way streets, junction turns and give-way behaviour under AI traffic. Only straight two-way running was sampled.
- That phase 2's vehicle stays disabled until it is respawned. The log says it is disabled; it was not probed further.
- Other AI modes (`random`, `driveUsingPath` with `wpTargetList`) on a fresh vehicle: only `traffic` was run.
