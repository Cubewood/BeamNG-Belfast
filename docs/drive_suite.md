# Route drive suite (T-302)

`qa/drive_suite.py` drives named routes around the test centre in the QA game and writes per-frame telemetry and `metrics.json`. It adapts MapNG's `driveTest` (`third_party/mapng/scripts/ingame/driveTest.mjs`, pinned `d605117`, never edited): MapNG picks its own routes, so the route logic here is our own Python and only MapNG's thresholds and spawn handling are reused (cited below).

## Usage

```powershell
$env:UV_CACHE_DIR='H:\BelfastData\cache\uv'; $env:UV_PYTHON_INSTALL_DIR='H:\BelfastData\uv-python'
uv run --no-project --python 3.12 --with requests python qa/drive_suite.py run [--gate] [--only ID,ID] [--mode graph|script]
uv run --no-project --python 3.12 --with requests python qa/drive_suite.py dump-graph     # game must be running
uv run --no-project --python 3.12 python qa/routes/build_routes.py new | rebuild [--tc X,Y | --tc-latlon LAT,LON] | check
uv run --no-project --python 3.12 --with pytest --with requests python -m pytest tests/qa -q
```

`run` needs no prompts: it starts the game with `tools/gamectl.py` (refuses if any BeamNG process exists or the time is 09:00-18:00; no override flag is offered), loads `belfast_slice`, dumps the live navgraph, drives every route, and always stops the game (also on error, `--timeout` default 3600 s, and a watchdog 120 s later). Exit code: 0 even when routes fail; `--gate` gives 20 if G3 fails; gamectl codes (2 running, 3 daytime, ...), 1 other tool error, 21 timeout. Output: `H:\BelfastData\qa\drive\<date_time>\` with `metrics.json`, `telemetry_<route>.csv`, `routes.json` (copy used). Only `127.0.0.1` (the game's MCP) is contacted; the remote-MCP flag is never used.

## Drive mode (decision)

Default `graph`: a fresh Covet `15le_M` per route, then `ai.driveUsingPath{wpTargetList = <graph nodes>, driveInLane = 'on', routeSpeed = 13.4, routeSpeedMode = 'limit', aggression = 0.6}`. Why: the project is about driving on the left, and only the graph AI keeps a lane (offsets in the telemetry show the side); the MapNG script drive uses `driveInLane = 'off'` and follows the centreline, so it cannot show lane side. Per I-001 (`docs/spikes/ai-graph.md`) each route gets a new vehicle (`spawn`), and before the AI call the suite requests the map and checks `mapmgr.mapData.edgeList ~= nil`, refusing otherwise. `--mode script` is the MapNG way (`driveUsingPath{script}` along the route polyline, radius = half width - 1.3 m, speeds with MapNG's turn slowing and 60 m end ease), also on a fresh car. Waypoints are the via points plus every 3+ way junction on the route, so the AI cannot choose another road. Aggression 0.6 and the 30 mph cap are MapNG-like, not a learner's.

## Route file `qa/routes/belfast_slice.json`

Data, not code. Top level: `level`, `origin` (lat/lon of the level centre), `provisional`, `sources`, `routes[]`. Each route:

| Field | Meaning |
|---|---|
| `id`, `name`, `description` | id uses `A-Z a-z 0-9 _ -`; names are plain text |
| `via[]` | hand-editable stops `{name, x, y, lat, lon}`; world metres, x east, y north, origin at the map centre. A stop named `TC` is the test-centre start |
| `path[]` | derived `[x, y, z]` polyline, at most 10 m apart |
| `length_m`, `streets[]`, `half_width_m` | derived |
| `junctions[]` | derived: distance along route, streets in and out, turn (left, right, straight, angle), OSM features near it (signals, give_way, roundabout) |
| `counts` | left and right turns, signalised junctions, roundabouts |

**The start point is provisional.** The test centre (4 Hospital Road, BT8 8JL) is not found in OSM and the user has not confirmed it. `TC` is the Hospital Road node nearest the level origin, (-122, 109). To move it: edit via points, or `build_routes.py rebuild --tc X,Y`, then `check`. When the map area changes: re-export, `dump-graph`, `rebuild`.

How derived: via points are snapped to the game's navgraph (`map.getMap()`, dumped to `H:\BelfastData\qa\graph\belfast_slice.graph.json`, out of git) and joined by shortest legs that respect one-way edges. The game graph is used, not the DecalRoads: the DecalRoad layer alone splits into islands, the game merges it into one network (1863 of 2212 nodes). Street names come from node names (OSM names); signals, give-way and roundabouts from MapNG's OSM file for the slice. `check` re-resolves each route and fails if the path is more than 3 m from it; the suite repeats this against the live graph and records a route that fails as an `error`, never as a skip. Roads with no loop are two routes (out, back): an out-and-back needs a U-turn that the AI is not asked to make.

## Telemetry and metrics

`telemetry_<route>.csv`: a game-side job logs every frame (`job.sleep(0.02)`, about 48 Hz achieved), drained every 0.5 s. Columns: `t` (s), `x y z`, `vx vy vz`, `speed`, `dx dy` (heading vector), `heading_deg` (compass), `vert_acc` (m/s2 over 0.1 s), `dev` (m from route), `off` (signed lane offset, left of travel positive), `progress` (m), `dmg` (MCP `damageSum`, polled 2 Hz and held), `steer throttle brake` (electrics via `core_vehicleBridge`).

`metrics.json`: run header (mode, thresholds, graph size, game timings), `results[]` per route (`completed`, `end_reason`, `events[]` with t and x, y, `max_bump_ms2`, `max_deviation_m`, `duration_s`, `length_m`, `progress_m`, `lane`, `frames`, `frame_rate_hz`, `spawn`), `summary` with `g3 {pass, failed[]}`, `console` (error lines, baseline ignored) and `game_stopped` (end state). A route fails G3 for a crash, stuck, airborne, off route, fell, reversing, bump over 7, timeout, error, bad spawn or not completing; a route missing from the results fails too.

## Thresholds (MapNG defaults, not tuned)

Bump 7 m/s2 `driveAnalysis.mjs:247`; airborne az < -8 for 0.25 s `:277-278`; off route 6 m + half width `:268`; stuck < 0.5 m/s for 5 s `:281-282`; fell 3 m under the road `:269`; one event per kind per 2 s `:253`; 1.5 s settle `driveTest.mjs:494`; crash = damage rise over 50 `:468`; reversing below -1 m/s `:51`; arrival within 10 m + v2/10 of the end `:457`; timeout length/speed x 2.5 + 20 s `:450`; spawn roll limit 8 degrees `:50`. Ours: lost beyond 60 m, arrival only in the last 60 m (loops end where they start). The route stops at the first crash, stuck, fell, reversing or lost.

## Result of the run (2026-11-11, 23:20, graph mode, `--gate`)

`H:\BelfastData\qa\drive\2026-11-11_232005\`, 455 s wall, exit 20 (G3 failed), game stopped, no console errors beyond the baseline. 7 of 10 completed. Crashes: `lady-ishbel-loop` at (-211, -93), `haddo-woods-loop` at (-211, -92) (same spot, Lady Ishbel Avenue / west link, turning right), `newtownbreda-roundabout` at (194, 284) after 399 m (T-104 crashed at (199, 283) too). Bumps over 7: 16.4 (haddo, at the crash) and 9.9 (roundabout, at the crash); highest on a clean route 6.9. Lane side: on two-way roads the car sat left of centre (median offset +1.3 to +1.5 m, 0 right samples on Hospital Road south). A trial in `--mode script` (not in the table above) completed `lady-ishbel-loop` and `newtownbreda-roundabout` with no crash (the roundabout route with a bump of -9.1 at (367, 755)), so those crashes belong to the lane-following line, not the road centreline.

## Known limits

- Run to run differs: one route's bump was 7.3 in a trial and 6.9 in the final run, so G3 near 7 is noisy. Single run only.
- `damageSum` is a polled value (0.5 s) and its size is erratic (17 thousand to 2.3 million); only the over-50 test is used.
- In the graph run the roundabout route crashed before the roundabout (only the script trial reached it); no right turn across a stream of traffic (no other traffic). Signals and give-way are not obeyed or judged.
- The sim clock is the wall clock of the logger; a heavily loaded game slows physics and skews accelerations.
- Crash causes and screenshots are not captured (MapNG's shots show the main menu, `docs/spikes/baseline.md`).
- Start point provisional; graph and OSM inputs live in `H:\BelfastData`, so `rebuild` needs them there.
