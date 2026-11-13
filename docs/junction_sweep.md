# Junction camera sweep (T-303)

`qa/junction_sweep.py` takes a driver's-eye photo 30 m and 10 m before every junction on the T-302 routes (`qa/routes/belfast_slice.json`), at a fixed time of day, with a pose file per photo and a manifest that lists them all. T-304 (image judge) and T-305 (eval set) read this output.

## Usage

```powershell
$env:UV_CACHE_DIR='H:\BelfastData\cache\uv'; $env:UV_PYTHON_INSTALL_DIR='H:\BelfastData\uv-python'
uv run --no-project --python 3.12 --with requests --with pillow python qa/junction_sweep.py plan [-v] [--only ID,ID] [--distances 30,10]
uv run --no-project --python 3.12 --with requests --with pillow python qa/junction_sweep.py run  [--only ID,ID] [--distances 30,10] [--out DIR]
uv run --no-project --python 3.12 --with pytest --with requests --with pillow python -m pytest tests/qa -q
```

`plan` needs no game and prints the counts (add `-v` for every shot's position and heading). `run` is unattended and takes about 7 minutes for 108 shots: it starts the game through `tools/gamectl.py` (refuses if any BeamNG process exists or the time is 09:00-18:00; the shot loop also stops itself at 09:00), loads `belfast_slice`, hides the UI, fixes the time of day, spawns one Covet `15le_M`, and always stops the game. Exit 0 when every shot is taken and clean; 22 when a shot failed or is missing (or it reached 09:00); gamectl codes (2 running, 3 daytime, ...); 21 timeout. Only the game's MCP on `127.0.0.1` is contacted.

## Output (not in git)

`H:\BelfastData\qa\sweep\<date_time>\`

| File | Content |
|---|---|
| `<route>/<route>-Jnn-30m.jpg`, `...-10m.jpg` | 1280x720 screenshot, UI hidden |
| `<route>/<same name>.json` | pose file (below) |
| `manifest.json` | index: run header (game, time of day, lane rule, routes file hash), `plan` counts, `shots[]` (one entry per shot: id, route, junction id and index, distance, `image`, `pose_file`, junction x/y/turn/features, camera x/y/z/heading, `ok`, `problems`), `summary` (planned, taken, ok, failed, missing, file counts), `console` (game log errors) and `game_stopped` |

Shot id: `<route id>-J<nn>-<distance>m`; the junction id is `<route id>-J<nn>`, numbered in route order as in the route file's `junctions[]`. `approach_key` (`<junction x,y>@<heading rounded to 30 deg>`) is the same for approaches that appear on more than one route (for example the out and back routes share some), so a consumer can de-duplicate; the sweep takes every route's approach anyway.

Pose file fields: `id`, `route`, `junction_id`, `junction_index`, `distance_m` (asked), `actual_distance_m`, `clamped`, `at_m` (junction distance along the route), `approach_key`, `junction {x, y, from, to, turn, angle, features, branches}`, `intended {x, y, z, heading_deg, lane_offset_m, one_way, one_way_lanes, route_x, route_y}` (where the car was asked to stand), `car {x, y, z, heading_deg, speed_ms, roll_deg, ground_z}` (read back after settling), `camera {x, y, z, heading_deg, pitch_deg, fov_deg, mode, right_hand_drive, offset_right_m, offset_forward_m, height_above_ground_m}` (the real camera, read back), `position_error_m`, `heading_error_deg`, `time_of_day`, `image`, `image_stats`, `image_sha256`, `problems`, `ok`. World metres, x east, y north, heading as compass degrees.

## How a shot is made

- Approach: the route polyline from the route file; the point `distance` metres before the junction (`at_m`); heading = route direction over +-4 m.
- Lane: two-way road, the car stands left of the centre line by half the route's road half-width (1.35 to 1.75 m; T-302 measured the AI at 1.3 to 1.5 m). One-way carriageway (a one-way link of the game graph along the route within 5 m, `H:\BelfastData\qa\graph\belfast_slice.graph.json`) of n lanes: the left-most lane, half-width x (n-1)/n, so a single lane is the centre line. This is a rule of thumb, not a measured lane.
- Placement as `drive_suite.place` (ground ray cast, physics paused while teleporting), with the parking brake on, 2 s settle plus a wait until speed is under 0.15 m/s, 0.8 s for the camera to catch up. A shot is retried once if the car is tilted, still moving, more than 1.5 m or 10 degrees off, or not on the driver camera.
- Camera: the game's own driver camera of the Covet (`onboard.driver`, or the `occupant.*` camera whose permission mode is `driver`; on this car it is `occupant.FR`, and `core_camera.getDriverData` reports `rightHandDrive true`). The seat position is the game's, not ours. The photo shows the cabin (dash, mirror, wipers) as a VR driver sees it.
- Lighting: `set_time_of_day` time 0.5 and play off, read back and stored in every pose file. The date and place are the level's (11 Nov, 54.5428 N), so the sun is low; weather is the level's.
- A screenshot is rejected and retaken if it is black or flat, or the same bytes as the previous shot.

## Known limits

- A junction closer to the route start than the distance (the four junctions at 22 m on routes that start at the provisional test-centre point) is shot at the route start: `clamped` true, `actual_distance_m` 22. The 30 m shot of those looks like a 22 m shot.
- Lane position comes from the rule above; the vehicle origin, not the body centre, is put on it (the driver's eye is about 0.1 m from the origin sideways).
- Static scene: no traffic, no pedestrians; signals and give-way lines are whatever MapNG built (T-104, `docs/spikes/mapng.md`).
- The start point of the routes is provisional (`docs/drive_suite.md`); when the routes are rebuilt, run `plan` and then `run` again.
- The sweep never drives, so it cannot show whether the AI can pass a junction; that is the drive suite's job.

## Result of the run (2026-11-13, 22:04, 391 s wall)

`H:\BelfastData\qa\sweep\2026-11-13_220428\`: 10 routes, 54 junction approaches (48 distinct `approach_key`), 108 shots, 108 JPG and 108 pose files, 108 distinct images, 0 failed, 0 missing. Every shot on `occupant.FR` with `right_hand_drive` true; camera 0.05 to 0.09 m right of the vehicle origin, 1.06 m above the ground; car position error at most 0.14 m, heading error at most 0.7 degrees, speed at most 0.02 m/s; 4 clamped shots (the 22 m junctions). Time of day 0.5, play off. 46 of 108 shots are on one-way carriageways of the game graph (Hospital Road north-east of the test centre and the Newtownbreda Road end). Game log: 1 error (the Steam provider baseline line) and 4 warnings (swap chain, two licence-plate template lines, achievements), all baseline; nothing from our code. An earlier run logged `set new global variable "carRot"` from our placement helper; the helper is now a local in the Lua snippet. The game stopped cleanly (no process, port closed, lock and state files gone).
