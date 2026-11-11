# In-game baseline of belfast_slice (T-104)

Run 2026-11-11, 22:22 to 22:41 local, QA profile only, MapNG `d605117` unmodified. Level: `belfast_slice.zip` (T-103: 2048 m, 1 m/px, left-hand traffic). Vehicle for the drive test: Ibishu Covet `15le_M`, put in with the MCP `spawn` tool after the level loaded (the scripts drive whatever the player vehicle is; they cannot choose one). Raw results: `H:\BelfastData\qa\baseline\2026-11-11\` (not in git).

## Verdict

| Part | Result |
|---|---|
| G1 level load | Loads, 17.7 s, no level-caused error in the console. One level-caused warning (licence plate template) |
| runIngameTests | 657 probes, **FAIL**: `sidewalk` 7/247 (2.8%, limit 2%). road, deck, spawn all 0 failures |
| driveTest `--no-street` | 12 routes, 8 clean, **4 crashes**, 2 bumps over 7 m/s². No airborne, stuck, fell or off-route events |
| Traffic side | Road rules say left-hand traffic. A lane test with AI could not be run: AI traffic mode throws a fatal Lua error on this level (works on `gridmap_v2`) |

## Commands

```text
uv run python tools/gamectl.py start                        # 6.1 s to MCP ready
uv run python tools/gamectl.py load-level belfast_slice --timeout 900   # 17.7 s
uv run --no-project --python 3.12 python tools/mapng_ingame.py tests belfast_slice H:\BelfastData\qa\baseline\2026-11-11     # 0.7 s
(spawn covet 15le_M via MCP `spawn`)
uv run --no-project --python 3.12 python tools/mapng_ingame.py drive belfast_slice H:\BelfastData\qa\baseline\2026-11-11     # about 8 min, 12 routes
uv run python tools/gamectl.py log-errors ; uv run python tools/gamectl.py stop
```

`tools/mapng_ingame.py` (new, with `tests/mapng/test_mapng_ingame.py`) runs `runIngameTests.mjs` and `driveTest.mjs` under the T-102 rules: pin check, Google/Mapillary/GPXZ/MCP-URL variables stripped, HOME redirected, `tools/mapng_preload.mjs` allowing only `127.0.0.1`, `--no-street` always. Netlogs: 7 requests (tests) and 1671 (drive), all `ALLOW 127.0.0.1`, none blocked. The scripts used the default MCP URL and the already loaded level (their own `--load` is not used). Needed no MapNG patch: only `run_lua`, `run_lua_vehicle`, `screenshot_image`, `get_vehicle_damage` (and `spawn`, `toggle_ui` for my own steps), all present.

## G1: level load

- Game log: level load starts at 15.4 s, done by 29.4 s of game time; terrain block 3.1 s; about 100 `.dae` imports (buildings, sidewalks, signs, poles).
- Errors in `beamng.log` after load: 1, the Steam provider (game baseline). The second baseline error (`track_builder_pieces/items.level.json`) does **not** appear for this level; it belongs to `gridmap_v2` (seen in the control run).
- Level warnings: `could not find vehicles/common/licenseplates/belfast_slice/licensePlate-default.sktemplate.json` (twice, at load and at the Covet spawn). Other warnings are game baseline (swap chain, achievements). Later warnings (`carRot`, `mapngDrive` new globals) come from the MapNG script itself.
- Missing stock assets (Italian signs, road textures, marking atlas): grep of the log for `not found|missing|could not|failed|unable|cannot` finds nothing else. The log does not name stock assets it did find, so a missing-but-silent material is not excluded; T-106 should check by eye.
- One FATAL LUA error appears later in the log, at 580 s, from my own AI experiment, see Traffic side.
- Raw: `log_errors_after_load.json`, `log_errors_end.json`, `beamng_after_load.log`, `beamng_end.log`.

## runIngameTests

`tests.json`, `tests_stdout.txt`. Probe positions are world metres, origin at the level centre.

| Kind | Failed / probes | Rate |
|---|---|---|
| road | 0 / 408 | 0.0% |
| deck | 0 / 1 | 0.0% |
| sidewalk | 7 / 247 | 2.8% (FAIL) |
| spawn | 0 / 1 | 0.0% |

The 7 sidewalk failures (all on residential or tertiary roads):

| Why | Where (x, y), object, value |
|---|---|
| object standing on the sidewalk | (265, 969) sidewalks_1_3 dz 0.34; (247, 919) sidewalks_0_3 dz 0.45; (382, 975) sidewalks_1_3 dz 0.31; (-62, 695) sidewalks_m1_2 dz 0.36 |
| building over the sidewalk | (-992, -981) buildings_-4_-4 dz 6.73; (-728, 588) buildings_-3_2 dz 2.86 |
| sidewalk floating over the terrain | (326, 965) gap 0.52 m |

Four of the seven are within 130 m of (265, 950), north edge of the slice.

## driveTest --no-street

`drive.json`, `drive_stdout.txt`, `drive\belfast_slice\index.html` plus screenshots. 12 routes: 1 deck, 11 turn routes (secondary 4, residential 7). No tunnel or steep routes exist on this level. Max vertical acceleration is MapNG's per-route peak; the bump limit is 7 m/s².

| # | Kind, road | Start (x, y) | Result | Max vert | Event |
|---|---|---|---|---|---|
| 1 | deck, unclassified | (-908, 423) | done | 1.7 | |
| 2 | turns, secondary | (-79, 371) | crash 321/396 m | 2.2 | crash at (199, 283), no impact or part data |
| 3 | turns, secondary | (-116, 120) | done | 2.5 | |
| 4 | turns, secondary | (-189, -147) | done | 1.4 | |
| 5 | turns, secondary | (-32, 216) | done | 6.2 | |
| 6 | turns, residential | (-258, 45) | crash 246/285 m | 7.6 | crash at (-387, 9); bump 7.6 at (-388, 8) |
| 7 | turns, residential | (105, 956) | done | 5.3 | |
| 8 | turns, residential | (335, 706) | crash 96/182 m | 4.1 | crash at (404, 694), no impact data |
| 9 | turns, residential | (-606, 639) | done | 4.5 | |
| 10 | turns, residential | (-172, 840) | done | 4.6 | |
| 11 | turns, residential | (252, 956) | crash 92/276 m | 13.1 | crash at (267, 880): front impact 2.4 m/s, Front Left Inner Fender 100%; bump 9.4 |
| 12 | turns, residential | (269, 576) | done | 3.8 | |

- Attempted 12, completed 8. Crashes 4 (routes 2, 6, 8, 11). Stuck 0, airborne 0, fell under road 0, reversing 0, off-route 0 (max deviation 3.2 to 3.9 m, limit 6 m), bad spawns 0.
- Bumps over 7 m/s²: 2 (route 6, 7.6; route 11, 9.4, at the crash). Highest on a clean route: 6.2 (route 5).
- The AI drives with `driveInLane "off"` in its own script, so this test says nothing about which side traffic uses.
- Crash causes are not established. The four crashes are at (199, 283), (-387, 9), (404, 694), (267, 880). Route 11 is a low-speed (2.4 m/s) front impact, so the car hit something solid at the road edge: look for a kerb, bollard, wall or building on the route line. The T-106 gap report should open the four sites; the `route*_crash_*` shots in `drive\belfast_slice\` have the car 2 s before and at impact.
- **Screenshot defect (tooling):** every shot in `drive\belfast_slice\` has the BeamNG main menu laid over it, because `driveTest.mjs` calls `ui_visibility.setCef(true)` (lines 119, 134, 235, 516) and that shows the menu `gamectl load-level` had hidden. Do not rely on them. Clean views of the four crash sites and two sidewalk sites: `shots\route02_crash.jpg`, `route06_crash.jpg`, `route08_crash.jpg`, `route11_crash.jpg`, `sidewalk_obj_265_969.jpg`, `sidewalk_bld_m728_588.jpg` (free camera 12 m away, UI hidden, not judged here). Route 2 shows a wide junction with white lane lines and bare grey terrain; route 11 a narrow street with houses and a rough sandy terrain edge beside the road.

## Traffic side

- `map.getRoadRules()` in the game: `{rightHandDrive = true, turnOnRed = false}` on `belfast_slice`, `{false, false}` on `gridmap_v2`. The game's traffic and AI code use `rightHandDrive and -1 or 1` as the legal side (e.g. `lua/vehicle/ai.lua:1166`, `lua/ge/extensions/gameplay/traffic/roadTracking.lua:46`), so this level is left-hand traffic.
- **Not verified:** that an AI car keeps left in a lane. I put the Covet on a long road and set `ai.setMode("traffic")`, `"random"` and `driveUsingPath{driveInLane="on", wpTargetList}`: it never moved (parking brake reads 1) and the log shows
  `FATAL LUA ERROR  lua/common/graphpath.lua:268: attempt to index field 'edgeList' (a nil value)` from `getNodesFromEdgeId` (edge 3230) called by `lua/vehicle/mapmgr.lua:281 findBestRoad`, car at about (-122, 109), the Hospital Road area. Control run on `gridmap_v2` (same car, same `ai.setMode("traffic")`): the car drove away normally, no such error (`beamng_control_gridmap.log`). So the vehicle-side road graph of this level is broken for AI: traffic, random and lane-following will not work. Cause not diagnosed (a MapNG road graph issue, probably: node names `dr_<Road>_<n>_s1_ai<n>` exist).

## What could not be run, and what is unverified

- Lane side of AI traffic (above).
- Tunnel, steep and bridge-with-structure drive kinds: the level has none; only one deck route exists.
- Crash causes and screenshots of the crashes judged by the local model: not done (no `MODEL ALLOWED`).
- `runIngameTests` and `driveTest` were run once each; no repeat runs, so run-to-run variation is unknown.
- Vehicle for the drive test was the Covet `15le_M`; the AI's `aggression 0.6` and fixed speeds are MapNG's, not a learner's.

## For T-106 (gap report) as concrete defects

1. Sidewalk probe failures: 7 positions above, 4 clustered near (265, 950).
2. Four drive crashes at the sites above; two bumps over 7 m/s² at (-388, 8) and (267, 880).
3. AI graph error on the vehicle side (graphpath `edgeList` nil, edge 3230): AI traffic does not work on this level.
4. Missing `vehicles/common/licenseplates/belfast_slice/licensePlate-default.sktemplate.json` (warning at every vehicle spawn).
5. `driveTest.mjs` re-shows the UI over its own screenshots; use `shots\` style captures or hide the UI after each capture.
