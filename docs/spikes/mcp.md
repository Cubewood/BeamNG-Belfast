# Spike: BeamNG built-in MCP server (T-005)

Run on 2026-11-10 (21:05 to 21:15 local, outside the 09:00-18:00 block). The installed game reports **0.40.5.0.302427** (the task text said 0.40.4). Everything below was observed live unless marked "from code".

## Result

Works. The game starts with the throwaway profile, the MCP server answers on `127.0.0.1:29292/mcp`, the full handshake succeeds, `tools/list` returns **102 tools**, `get_status` and `screenshot_image` both work. The one trap is `-windowed`, which crashes this build (see Gotchas).

## Launch command

Use the direct x64 binary (no launcher UI). Working directory = install folder.

```powershell
Start-Process 'H:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe' `
  -ArgumentList '-userpath','H:\BeamNG-QA\profile','-enablemcp' `
  -WorkingDirectory 'H:\SteamLibrary\steamapps\common\BeamNG.drive' -PassThru
```

- Never add `-mcpremote` (from code: it binds the server to `any` instead of `127.0.0.1`). Never add `-headless` (code comment says it misbehaves).
- `-mcpport <n>` (from code) changes the base port. If the bind fails, a watchdog moves to the next port, cycling 20 ports from the base. So "port 29292" is the first choice, not a guarantee; the log line `MCP listening on http://127.0.0.1:<port>/mcp` is authoritative.
- The profile is created on first start: `H:\BeamNG-QA\profile\current\` holds `beamng.log`, `settings\`, `temp\`, etc. The game log is `H:\BeamNG-QA\profile\current\beamng.log`.
- `-userpath` selects the user folder only. `startup.ini` has an empty `UserPath`, so the normal profile is `%LOCALAPPDATA%\BeamNG\BeamNG.drive\current\`.

## Protocol details

From `lua/ge/extensions/mcp/server.lua`, confirmed live:

- Transport: Streamable HTTP, single route `POST /mcp`. Any other method gets `405`. There is **no session header** (`Mcp-Session-Id` is never issued or required) and no auth. No SSE: every reply is plain `application/json`, even though clients send `Accept: application/json, text/event-stream`.
- Response headers seen: `content-type: application/json`, `access-control-allow-origin: *`.
- Protocol version: server supports `2025-03-26`, `2025-06-18`, `2025-11-25`. It echoes the client's version if supported, else answers `2025-11-25`.
- `initialize` reply: `{"serverInfo":{"name":"beamng-game","version":"1.0.0"},"protocolVersion":"2025-11-25","capabilities":{"tools":{"listChanged":false}}}`.
- Notifications (no `id`), e.g. `notifications/initialized`, get `202` with an empty body.
- JSON-RPC batches are supported. Methods implemented: `initialize`, `tools/list`, `tools/call`, `ping`. Anything else returns `-32601`. No resources, prompts or sampling.
- `tools/call` result: `{"content":[{"type":"text","text":...}],"isError":bool}`. The text is a JSON string for most tools. A tool that returns `{data, mimeType}` produces an `image` content item.
- Requests are handled in the game's Lua `onUpdate`, so every call waits for a frame (about 16 ms at 60 fps, about 33 ms when background-limited). Typical measured latency: 7 to 40 ms.
- Tools are discovered, not registered by hand: every function on the `mcp_tools` extension that does not match `^on%u` is a tool. The tool files are `lua/ge/extensions/mcp/tools/*.lua`, merged by `mcp/tools.lua`.
- Several tools are **async by design**: the first call triggers the work and returns a "dispatched/capturing, call again" text; a later call returns the result (`screenshot_image`, `ui_eval`, `dump_vue_state`, `get_electrics`, `get_ai`, `run_lua_vehicle`, `test_hydro`). A client must poll.
- Python snippet used: `requests.post(URL, json=body, headers={"Accept":"application/json, text/event-stream"})`; see `tools/spikes/mcp/mcp_client.py`.

## Calls made

| Call | Result |
|---|---|
| `initialize` | 200, reply above |
| `notifications/initialized` | 202, empty body |
| `tools/list` | 200, 102 tools |
| `get_status` | OK, 7 ms (below) |
| `screenshot_image` | OK on the second call (below) |

`get_status` at the main menu (no level):

```json
{"level":"","camera":{"pos":{"x":0,"y":0,"z":0}},"sim":{"timeScale":1,"physicsRunning":true},"vehicleCount":0}
```

`screenshot_image` (arg `scale`, default 0.4): the first call returns text `capturing JPG (scale 0.4); call screenshot_image again in a moment to receive the image`. The second call (a few seconds later) returns `content[0] = {"type":"image","mimeType":"image/jpeg","data":"<base64>"}`. At the main menu the image was **512x288 JPEG, 2.9 KB, entirely black**: it captures the 3D render target only, not the CEF UI overlay, and there is no 3D scene at the menu. Expect a real image only after a level is loaded. The separate `screenshot` tool saves a file under `screenshots/mcp/` in the user folder instead (the first call's log line: `Saved screenshot 'screenshots/mcp/wire_...jpg'`).

Other calls that worked at the menu: `get_fps_limit_reason`, `get_vram_usage`, `get_performance_metrics`, `get_render_info` (`Direct3D12`, RTX 4090, driver 32.0.16.2114, Vulkan off), `get_ui_state`, `list_levels`, `ui_eval`, `ui_click`, `run_lua`, `get_var`. `get_var` for `$pref::Video::*` names returned empty (those are not the keys in use; use `settings.getValue` through `run_lua`).

## Timings

- Launch to MCP answering: the game log shows `MCP server binding` at **8.35 s** and `MCP listening` at **8.38 s** after process start (`Started up in 6.84 seconds` at the engine level). A client poll started about 6 s after launch answered on its first poll. So budget about 10 s; wait-ready should poll `ping` for up to 120 s.
- Each tool call: 7 to 40 ms once up.
- Shutdown: `run_lua` with `shutdown(0)` returned HTTP 200 (`nil`), the process was gone within about 1.5 s, and nothing was left listening on 29292.

## First-run dialogs

Fresh profile, in order:

1. A native Windows window titled **"BeamNG.drive - Send Error Reports"** appeared in the foreground about 2 s after launch, then went away by itself within 4 s. It is the crash-reporter offering to send the crash from my earlier `-windowed` attempt (that crash left a report in the QA profile's `temp\crashReports`). It did not need input here. I did not see whether it sent anything. A clean first launch should not show it.
2. In-game the UI first reported route `legal.onlineFeatures` (online-features consent screen), then a "Welcome to BeamNG.drive!" onboarding modal (buttons Back / Not now / Start driving!), then route `menu`. I did not click the consent screen: the game moved on within seconds and wrote `onlineFeatures: "disable"`, `telemetry: "disable"`, `onboardingPopupShown: true` to `settings\cloud\settings.json`. Whether that happened by itself or via my later `ui_click "button:nth-of-type(2)"` on the Welcome modal is not certain; the consent route was gone before I clicked. For a repeatable fresh start, pre-seed these keys instead of clicking (see recommendations).
3. The log says `Telemetry disabled via settings - operating in no-op mode`.

## Focus, window and frame limit

- Window: normal desktop window, client area 1280x720 by default (outer rect 1296x759 at 632,317). The saved `WindowPlacement` in `settings.json` is `0 1 -1 -1 -1 -1 632 317 1076 1928`. No resolution flag was needed; `defaults.json` has `GraphicDisplayResolutions = "1280 720"`.
- **Focus steal: yes.** Foreground before launch was Steam. The game window took the foreground at about 6 s after launch (the title showed `... - background` for a moment, then the normal title). Sampled every 2 s: Steam, then the error-report dialog, then the game window, which then stayed foreground. A QA run on a PC in use will interrupt the user's typing, which is another reason for the night-only rule.
- **Background frame limit:** `get_fps_limit_reason` focused: `{"backgroundCap":30,"backgroundActive":false,"fpsCap":59.99,"windowFocused":true,"foregroundCap":60,"foregroundActive":false,"vsync":false}`. After I moved focus to the desktop shell: `backgroundActive:true,"fpsCap":30,"windowFocused":false`. The `FPSLimiter` sleep in `get_performance_metrics` went from about 15 ms (focused, about 60 fps) to about 32 ms (unfocused, 30 fps). I did not measure a true frame counter, only the limiter's caps and sleep times.
- Setting that controls it: `fpsLimitBackgroundEnabled` (default true) and `fpsLimitBackground` (default 30). Running `settings.setValue('fpsLimitBackgroundEnabled', false)` through `run_lua` took effect immediately: unfocused, `backgroundActive:false`, `fpsCap` back to 60, limiter sleep about 15.8 ms. It is persisted as `"fpsLimitBackgroundEnabled":false` in `<profile>\current\settings\settings.json`. Foreground limit 60 applied even though `fpsLimitEnabled` defaults to false (cap may come from the menu/refresh rate; not investigated).

## Network and VRAM

- Bind: from code, `127.0.0.1` unless `-mcpremote`. Live `netstat` while the game ran was not captured before shutdown (I only captured it after: no LISTENING socket on 29292, only TIME_WAIT leftovers from my client). **The loopback-only bind is therefore from code, not observed with netstat.** Do that check in T-006's smoke test.
- VRAM at the main menu: `get_vram_usage` returned `709463244` (assumed bytes, about 677 MiB; the tool does not state units). `nvidia-smi` showed 2578 MiB used machine-wide (other apps included) and reports per-process memory as N/A on this driver (WDDM), so there is no per-process figure from nvidia-smi. Game process working set was about 1.94 GB RAM.

## Normal profile unchanged

Before and after snapshots of `%LOCALAPPDATA%\BeamNG` and `%LOCALAPPDATA%\BeamNG.drive` (5365 files, path|size|mtime) are in `H:\BelfastData\spikes\mcp\profile_before.txt` and `profile_after.txt`; script `tools/spikes/mcp/snapshot.ps1`.

- Diff: exactly one entry differs, `%LOCALAPPDATA%\BeamNG\BeamNG.drive.ini` (85 bytes, same size): its mtime moved from 2026-11-05 22:16 to the launch instant, 2026-11-10 21:06:40. Its content is `version = 0.40.5.0` and `installPath = H:\SteamLibrary\steamapps\common\BeamNG.drive\`, so the content is the same as it would have been. The game re-writes this locator file on every start regardless of `-userpath`. No settings, mods, logs or other file in the normal profile changed.
- No MCP setting in the normal profile: a search for `enableMcp` in `current\settings\*.json` found 0 hits. (One path, `settings\cloud\crawlTrails`, is access-denied to read and was not searched.)
- **`-enablemcp` persists in the profile it is used with.** `main.lua` runs `settings.setState({ enableMcp = true })`; the QA profile's `settings\settings.json` now holds `"enableMcp": true` (the setting is declared `local`, default false, in the game's `settings/defaults.json`). This is harmless for the throwaway profile but means that profile keeps MCP on even if the flag is dropped later. Do not point `-enablemcp` at the normal profile.

## Gotchas

- **`-windowed` crashes this build.** `parseArgs.lua` line 58 calls `setFullScreen(false)`, which is a nil global: `FATAL LUA ERROR ... attempt to call global 'setFullScreen'`, followed by a native crash a moment later (crash report written to `<profile>\current\temp\crashReports\`). The same applies to `-fullscreen` (line 56) by reading the code; I did not run it. Do not pass either. The default is already a window.
- The task's "about 80 tools" is 102 in 0.40.5.
- Disruptive/dangerous tools exist: `run_lua` (arbitrary Lua in the game; also `load_file`, `write_file`, `delete_file`, `copy_file`, `rename_file`, `make_dir` take real paths, not only the user folder), `reload_lua`, `load_level`, `editor_save_level`. With no auth, anything on the PC can call these while the game runs. Keep the game up only for the QA window.
- `shutdown(0)` through `run_lua` is the clean quit. It returned `nil` in the response, so a client must expect the connection to close right after.
- Async tools need a second call (see Protocol). `screenshot_image` black at menu, see above.
- Old `.ini`/crash artefacts: after a crash, the next launch shows the crash-reporter dialog; delete `<profile>\current\temp\crashReports` (QA profile only) before the next run if a crash happened.
- A leftover `Send Error Reports` window could steal focus; a headless QA run cannot answer it.

## Recommendations for `tools/gamectl.py` (T-006)

- Order: check clock is outside 09:00-18:00; refuse if any `BeamNG.drive.x64` process exists (do not kill it); wait for `H:\BelfastData\gpu.lock` to vanish, then create it with the task id; launch as above and keep the PID; delete the lock in a `finally` once the PID has exited.
- Wait-ready: poll `POST ping` (or `initialize`) until 200, timeout 120 s; then read `beamng.log` for `MCP listening on http://127.0.0.1:<port>` to learn the real port (handle the port-cycling watchdog).
- Stop: `run_lua {"code":"shutdown(0)"}`, wait up to 30 s for the PID to exit, then `Stop-Process -Id <pid>` only for that PID; verify the port is closed.
- Load a level: `list_levels` then `load_level {name}`; poll `get_status` until `level` is non-empty. (Not tested in this spike: no level was loaded.)
- QA settings, applied by writing the QA profile only (keys from the game's `settings/defaults.json`, stored in `<profile>\current\settings\settings.json` unless noted), or live via `run_lua` `settings.setValue(key, value)`:
  - Resolution 1280x720: already the default (`GraphicDisplayResolutions`). Do not use `-windowed`.
  - Audio off: `AudioMasterVol = 0` (default 1.0; setting it was not tested here).
  - Background frame limit off: `fpsLimitBackgroundEnabled = false` (tested, takes effect live).
  - Telemetry off and consent: `settings\cloud\settings.json` with `{"onboardingPopupShown":true,"onlineFeatures":"disable","telemetry":"disable"}` (this is what the game wrote; pre-seeding it before the first launch was not tested).
  - Graphics quality for stable frames: `GraphicLightingQuality`, `GraphicShadowsQuality`, `GraphicMeshQuality`, `GraphicTextureQuality` etc. are present in the profile settings (not tuned).
- Take screenshots only after a level is loaded and at least a few frames have rendered. Poll `screenshot_image` until the content type is `image`.
- Never leave `-enablemcp` on a profile that is not the QA profile.

## Open questions

- Loopback-only binding not confirmed with `netstat` while running.
- Whether pre-seeding `settings\cloud\settings.json` skips the consent and welcome screens on a clean profile, and whether the "Send Error Reports" dialog appears on a clean first launch (it did only after a crash).
- Whether `get_vram_usage` is bytes (assumed) and what it reports in a loaded level.
- Behaviour of `load_level` for a mod level from `H:\BeamNG-QA\profile\current\mods`.
- Whether `-fullscreen` has the same nil-global crash as `-windowed`.

## Full tool list (from `tools/list`, 102 tools)

Grouped by the source file under `lua/ge/extensions/mcp/tools/` that defines each tool. The "Args" column lists the input-schema property names (see `H:\BelfastData\spikes\mcp\tools_list.json` for full schemas).

Total: 102


### ai (4)

| Tool | Args | Description |
|---|---|---|
| `drive_to` | aggression, avoidCars, driveInLane, id, pos, routeSpeed, routeSpeedMode, targetId, waypoint | Send a vehicle's AI to drive to a location: 'pos' {x,y,z} (snapped to the nearest navgraph node), a named 'waypoint' node, or another vehicle's position via 'targetId'. Defaults to the player vehicle (id). Options: routeSpeed (m/s) with routeSpeedMode ('limit'/'set'), aggression (0.3 calm..1), avoidCars ('on'/'off', default 'on'), driveInLane ('on'/'off'). The AI drives until it arrives; use set_ai mode 'disabled' to hand control back. |
| `get_ai` | id | Get a vehicle's AI controls (mode, speedMode, routeSpeed, aggression, targetObjectID, avoidCars) as JSON. Async: call again in a moment. Pair with set_ai. |
| `get_navgraph` | all, maxNodes, near, radius | Get the AI navgraph (road network used by AI & traffic): always returns summary stats (nodeCount, linkCount, bbox). Pass 'near' {x,y,z} (default: player vehicle) with 'radius' to also get nearby nodes with their links (to, drivability, oneWay, speedLimit, len) and the closest road; or all=true to dump the whole graph (capped by maxNodes). |
| `set_ai` | aggression, avoidCars, id, mode, routeSpeed, speedMode, targetObjectID | Set a vehicle's AI controls. Any of: mode ('disabled'/'random'/'span'/'manual'/'chase'/'flee'/'stop'/'follow'/'traffic'/'script'), speedMode ('set'/'limit'/'legal'/'off'), routeSpeed (m/s), aggression (~0.3 calm .. 1 aggressive), targetObjectID, avoidCars ('on'/'off'). Accepts the keys from get_ai. |

### camera (4)

| Tool | Args | Description |
|---|---|---|
| `get_camera_state` |  | Current camera as JSON: mode, position, rotation (quat), fov. |
| `orbit_camera` | distance, id, pitch, spin, yaw | Control the orbit camera around a vehicle (switches to orbit). yaw/pitch in degrees, distance in m. spin (deg/s) continuously rotates around the car; spin=0 stops. |
| `set_camera` | name | Switch the player camera mode: orbit, driver, chase, external, hood, top, dash, etc. |
| `set_free_camera` | fov, pos, rot | Switch to the free camera and optionally place it. pos {x,y,z}, rot {x,y,z,w} (quat), fov degrees. The free cam then flies from that pose on input. |

### debug (2)

| Tool | Args | Description |
|---|---|---|
| `debug_clear` | id | Clear debug shapes: pass an id to clear one, or omit to clear all. |
| `debug_draw` | color, from, id, kind, label, pos, radius, to, ttl | Draw a persistent debug shape in the world (redrawn every frame until cleared) to highlight a position/error. kind: 'sphere' (pos+radius), 'line' (from+to), 'text' (pos+label). Optional label, color {r,g,b,a} 0..1 (default red), ttl seconds (0=persistent), id (reuse to move/replace). Returns the shape id. |

### editor (16)

| Tool | Args | Description |
|---|---|---|
| `clear_editor_selection` |  | Clear the World Editor object selection. |
| `create_editor_object` | class, classname, collisionType, decalType, field, fields, name, parent, parentId, pos, scale, shape, shapeName, value, yaw | Create a scene object in the World Editor (undoable, selected, dirty). class defaults to TSStatic. For TSStatic pass shape/shapeName (mesh .dae path); otherwise uses the editor no-mesh placeholder. Optional pos {x,y,z} (default: 10m in front of camera), scale, yaw (deg), name, parent/parentId, fields map, collisionType. |
| `delete_editor_selection` |  | Delete currently selected objects (editor history / undoable). Editor must be active. |
| `editor_load_level` | name, path | Open a level in the World Editor via editor.openLevel (discards unsaved editor changes; async). Provide name (from list_levels) or path (level folder / main file). Editor must be active. |
| `editor_redo` | steps | Redo World Editor history steps (default 1). |
| `editor_save_level` |  | Save the current level (MissionGroup serialize). Editor must be active and a level must be open. Disruptive if misused. |
| `editor_status` |  | World Editor status JSON: active, initialized, dirty, levelName/path, editMode, selectionCount, undoCount, redoCount, headless. |
| `editor_undo` | steps | Undo World Editor history steps (default 1). |
| `fit_view_to_selection` |  | Fit/frame the editor camera on the current object selection. |
| `get_editor_selection` |  | List currently selected scene objects as JSON: {count, objects:[{id,name,class}]}. Editor must be active. |
| `list_edit_modes` |  | List registered World Editor edit mode names and the current mode. |
| `list_editor_classes` | pattern | List SimObject-derived class names creatable in the World Editor (optional 'pattern' substring filter). Use with create_editor_object. |
| `select_editor_objects` | id, ids, mode, name | Select scene object(s) in the World Editor by id, ids[], or name. mode: 'new' (default), 'add', 'remove', 'toggle'. |
| `set_edit_mode` | name | Switch World Editor edit mode by name (from list_edit_modes), e.g. objectSelect, createObject, terrainEditor. |
| `set_editor_active` | active, safeMode | Activate or deactivate the World Editor (loads editor_main if needed). Activation is async over a couple frames; poll editor_status. |
| `set_editor_transform` | euler, id, ids, items, name, pos, rot, scale | Set position/rotation/scale on scene object(s) with undo (one history transaction; sets dirty). Target per-object with 'items' [{id/name, pos?, euler?/rot?, scale?}], or apply a shared pos/euler/rot/scale to 'ids'/'id'/'name' or the current selection. Rotation is euler {x,y,z} degrees or a quaternion rot {x,y,z,w}. scale is a number or {x,y,z}. Only provided components change. |

### env (2)

| Tool | Args | Description |
|---|---|---|
| `get_time_of_day` |  | Get time of day as JSON: time (0..1), play, dayLength, startTime, latitude, longitude, date, utcOffset, dstRule, celestialProfile. |
| `set_time_of_day` | celestialProfile, day, dayLength, dstRule, latitude, longitude, month, play, startTime, time, utcOffset, year | Set time of day and astronomical sky fields. time is 0..1 (0/1 = midnight, 0.5 = noon). |

### fs (8)

| Tool | Args | Description |
|---|---|---|
| `copy_file` | from, to | Copy a file. |
| `delete_file` | path | Delete a file. |
| `find_files` | depth, path, pattern | Find files by glob pattern (VFS-aware). Returns {count, files}. |
| `list_dir` | dirs, files, path | List a directory (VFS-aware) as JSON. files/dirs default both true. |
| `make_dir` | path | Create a directory (recursive). |
| `read_file` | maxBytes, path | Read a text file's content (loose-on-disk paths, e.g. jbeam/json/lua). Truncated to maxBytes. |
| `rename_file` | from, to | Rename/move a file. |
| `write_file` | content, path | Write a text file (creates parent dirs). Overwrites if it exists. |

### input (3)

| Tool | Args | Description |
|---|---|---|
| `inject_input` | event, filter, id, value | Inject a vehicle input event (control) into a vehicle's Lua VM, e.g. steering/throttle/brake or a vehicle-specific event. |
| `run_lua_vehicle` | all, code, id | Run a Lua string in a vehicle's Lua VM (default: player vehicle; set all=true for every spawned vehicle). Async: returns the PREVIOUS run's results as a JSON map of vehicleId -> result; call again to get the latest. |
| `test_hydro` | event, id | Drive an input up -> down -> neutral over ~1.5s and report any errors logged during that window. Async: call again after ~2s to get the result. |

### perf (5)

| Tool | Args | Description |
|---|---|---|
| `get_fps_limit_reason` |  | Current FPS-limiter sources and caps (vsync, foreground/background limits, focus) as JSON. |
| `get_performance_metrics` |  | Last frame's performance metrics (frame timings, memory, counts) as JSON. |
| `get_vram_usage` |  | GPU VRAM usage as JSON. |
| `profiler_run` | delay, duration, path | Trigger an Optick profiler capture (BNG_PROFILER=OPTICK builds only). Captures for 'duration' seconds after an optional 'delay', saves a .opt file and returns its path. Non-blocking - the game keeps running, so reproduce the slow case during the window; the file is ready after delay+duration seconds, then analyze it with DevTools/optick/optick.py or open it via the optick-ui MCP. |
| `run_optick_ui` |  | Launch the external Optick profiler GUI - same as the CTRL+ALT+O keybind (non-shipping Optick builds only). |

### scene (7)

| Tool | Args | Description |
|---|---|---|
| `delete_object` | id, name | Delete a scene object (by id or name). Permanent for this session. |
| `find_objects` | class, name, pattern | Find scene objects in the scenetree. Pass 'class' to list all objects of a class (e.g. TSStatic, PointLight, BeamNGVehicle, SimGroup, Forest), optionally filtered by 'pattern' (name substring), or 'name' for an exact lookup. Returns [{id, name, class}]. |
| `get_object` | id, name | Inspect a scene object (like the editor inspector): class, name, static fields with values, dynamic fields, and position. Identify it by id or name. |
| `raycast` | dir, from, includeTerrain, length, renderGeometry, to | Cast a ray and return the first hit (pt, norm, distance, hit object, material if any). Default: from the camera along its view direction (what you're looking at). Or pass from + to, or from + dir + length. |
| `serialize_object` | id, name, pretty | Serialize a scene object to JSON (full state, as written to a level/prefab file). Identify by id or name. |
| `set_object_field` | field, fields, id, index, name, value | Set field(s) on a scene object (write counterpart of get_object); calls postApply so changes take effect. Values are strings (e.g. position '1 2 3'). Identify by id or name. |
| `view_objects` | class, mask, maxDist | List objects in the camera view via the native frustum query (fast: C++ broad-phase by type mask + exact OOBB cull), sorted by distance. Filter with class: a mask keyword ('vehicles' (default), 'static', 'item', 'player', 'terrain', 'water') or any exact scene class name (e.g. 'TSStatic', 'PointLight'); or pass a raw 'mask'. Returns [{id, name, class, dist, pos, jbeam?}]. |

### sim (5)

| Tool | Args | Description |
|---|---|---|
| `get_simulation_state` |  | Physics/simulation state as JSON: physicsRunning, timeScale, speedFactor, deterministic, enabled. |
| `pause_physics` |  | Freeze the physics core (vehicles stop simulating; rendering/camera keep running). Pair with step_physics / resume_physics. |
| `resume_physics` |  | Resume the physics core after pause_physics. |
| `set_simulation_speed` | scale | Set the simulation time scale: 1 = normal, <1 = slow motion (e.g. 0.25), >1 = fast forward. |
| `step_physics` | steps | Advance the physics core by N ticks then leave it paused (frame-by-frame debugging). |

### spawn (3)

| Tool | Args | Description |
|---|---|---|
| `get_configs` | model | List the config keys (.pc) available for a model (default: current player model). Use the keys with spawn. |
| `spawn` | add, config, model | Spawn a vehicle. Default replaces the current vehicle with the given model+config; add=true spawns an additional one. model defaults to the current model; config defaults to the model default. |
| `test_configs` | delay, model | Spawn-test every config of a model (default current): replaces the vehicle with each config, waits, and records error-log lines + physics instability. Long-running/async: call to start, poll for progress, then receive the final per-config report (config, ok, errors, unstable). |

### system (13)

| Tool | Args | Description |
|---|---|---|
| `file_info` | path | Info for a path: exists, isDir, realPath (VFS->real), virtualPath (real->VFS), expanded, and stat (filesize, modtime, filetype). |
| `get_logs` | lines | Game log (beamng.log): the FIRST call returns the last N lines; subsequent calls return only lines logged since the previous call. |
| `get_render_info` |  | Render backend info as JSON: graphics adapter type, Vulkan enabled, availability, and GPU info. |
| `get_var` | default, name | Read an engine/preference variable (VariableRegistry), e.g. '$pref::Video::gpu'. |
| `load_extension` | name | Load a GE extension by name (e.g. 'core_camera', 'util_richPresence'). |
| `load_file` | path | Load and execute a Lua file in the GE VM by path (VFS or real). Returns its result or the error. |
| `reload_lua` |  | Reload the game-engine (GE) Lua VM (equivalent to CTRL+L). Picks up GE Lua edits including these MCP tools. The MCP may briefly drop; reconnect if so. |
| `reload_ui` |  | Hard-reload the UI (CEF), ignoring cache. Use after editing UI files. |
| `run_lua` | code | Run a Lua string in the game engine (GE) Lua VM and return tostring() of its result. |
| `screenshot` | jpg | Take a screenshot and return its absolute file path (so it can be opened/viewed). Saved under screenshots/mcp/. |
| `screenshot_image` | scale | Take a small JPG and return it inline as a base64 image over the wire (no file read needed) for quick visual assessment. Async: call once to trigger, call again in a moment to receive the image. |
| `set_var` | name, value | Set an engine/preference variable (VariableRegistry). Returns the read-back value. |
| `unload_extension` | name | Unload a GE extension by name (mcp_server/mcp_tools are protected). |

### telemetry (3)

| Tool | Args | Description |
|---|---|---|
| `get_electrics` | id, keys | Vehicle electrics telemetry (rpm, wheelspeed, gear, fuel, throttle/brake/steering, lights, temps, ...) as JSON. Async: call again in a moment. Pass keys=[...] to fetch only specific values. |
| `get_status` |  | One-call situational snapshot: player vehicle (id/jbeam/config/pos/speed/damage), camera (mode/pos), level, sim state, vehicle count. (rpm/gear via get_electrics, AI via get_ai.) |
| `get_vehicle_damage` | id | Vehicle damage summary (total section damage) as JSON. For detailed per-part damage use run_lua_vehicle with beamstate/damageTracker. |

### ui (11)

| Tool | Args | Description |
|---|---|---|
| `dump_vue_state` |  | Dump the UI Vue state (current route + Pinia stores) as JSON. Async: call again in a moment to receive the snapshot. |
| `get_ui_state` |  | Current UI state as JSON: router route/scope (ui_router) plus gameplay mode (core_gamestate). |
| `set_ui_state` | params, route | Navigate the UI to a route (ui_router.navigate). |
| `show_message` | category, icon, text, ttl | Show a message in the in-game message app. Reuse the same category for progress updates (replaces the previous message). |
| `toggle_ui` | show | Show/hide the game UI (like Alt+U). Pass show=true/false to set explicitly, or omit to toggle. |
| `ui_click` | button, doubleClick, nth, selector | Click a UI element by CSS selector (full pointerdown/mousedown/up/click sequence at its center). Fire-and-forget; assert the effect with ui_eval or dump_vue_state. |
| `ui_eval` | js | Run JavaScript in the UI (CEF) and return its value as JSON (the main UI-test/assert tool: query the DOM, read component state). Provide a JS body; use 'return X' (a bare expression is auto-returned). Async: call again in a moment for the result. Examples: "document.querySelectorAll('.bng-button').length", "return [...document.querySelectorAll('button')].map(b=>b.textContent.trim())". |
| `ui_hover` | nth, selector | Hover an element (pointer/mouse over + move), e.g. to reveal tooltips or hover menus. |
| `ui_key` | event, key, nth, selector | Dispatch a keyboard event to a selector (or the active element if none). Default keydown+keyup. |
| `ui_scroll` | deltaX, deltaY, nth, selector | Dispatch a mouse wheel event to an element. deltaY>0 scrolls down (default 120 if no delta given). |
| `ui_type` | clear, nth, selector, text | Type text into an input/textarea/contenteditable: sets the value and fires input+change (drives Vue v-model). clear=false appends instead of replacing. |

### vehicle (10)

| Tool | Args | Description |
|---|---|---|
| `get_instability` | clear | Physics instability events (a vehicle going unstable/exploding) since the last check. Returns count + events and clears the buffer. |
| `get_part_tree` | id | Vehicle part config as a JSON map of slot path -> chosen part name. |
| `get_player_vehicle_id` |  | Return the id of the current player vehicle (use with the id arg of other vehicle tools). |
| `get_position` | id | Get a vehicle's pose (position + rotation) as JSON. Save it, then pass it back to set_position to return the vehicle to that pose. |
| `get_vehicle` | id | Vehicle details (id, jbeam, name, config, pc file, position, speed) as JSON. |
| `get_vehicle_files` | id | Files used by a vehicle: jbeam files of active parts, loaded directories, and .pc config path, as JSON. |
| `get_vehicles` |  | List currently spawned vehicles (id, jbeam, display name) as JSON. |
| `reload_vehicle` |  | Reload the player vehicle from disk, re-reading its jbeam (picks up file edits). |
| `reset_vehicle` |  | Reset/repair the player vehicle in place (physics reset). |
| `set_position` | id, pos, rot | Set a vehicle's pose (teleports + repairs). Accepts the JSON from get_position; rot is optional (keeps current heading if omitted). |

### world (6)

| Tool | Args | Description |
|---|---|---|
| `get_ground_at_point` | pos, radius | Surface height below a point and terrain drivability (0..1) around it. Default point: under the player vehicle. |
| `list_levels` |  | List installed levels as JSON: [{name, title, path}]. Use path/name with load_level. |
| `load_level` | name, path | Load a level (reloads the world; disruptive). Provide path (misFilePath from list_levels) or name (levelName). |
| `recover_vehicle` | id | Recover a vehicle to the last road (teleport + repair). Default: player vehicle. |
| `remove_vehicle` | id | Delete (despawn) a vehicle. Default: player vehicle. |
| `trigger_action` | action, state | Fire a bound game action by name (e.g. 'recover_vehicle', 'reset_physics', 'toggleCamera', 'switch_next_vehicle'). Tap by default; pass state='down'/'up' for hold actions. |
