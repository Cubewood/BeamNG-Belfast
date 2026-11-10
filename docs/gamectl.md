# gamectl: start, drive and stop BeamNG for QA

`tools/gamectl.py` (CLI and module) plus `tools/beamng_mcp.py` (MCP client). Windows, Python 3.12 from the project venv. Built for T-006 on top of the T-005 findings in `docs/spikes/mcp.md`.

```powershell
$env:UV_CACHE_DIR='H:\BelfastData\cache\uv'; $env:UV_PYTHON_INSTALL_DIR='H:\BelfastData\uv-python'
uv run python tools/gamectl.py <command>
```

## Commands

| Command | What it does |
|---|---|
| `start [--allow-daytime] [--lock-timeout S] [--ready-timeout S]` | Refuse if any BeamNG process exists or it is 09:00-18:00. Wait for the GPU lock, take it, seed QA settings, launch with `-userpath H:\BeamNG-QA\profile -enablemcp` (cwd = install folder, no `-windowed`, `-fullscreen`, `-headless`; never the remote-MCP flag). Wait for `initialize` + `get_status` (default 120 s). Check the port bind. Verify the QA settings through MCP. |
| `status` | JSON: our game running, PID, MCP reachable, level, vehicle ready, lock line and whether it is ours, other BeamNG processes. Exit 0 if our game runs, else 9. |
| `load-level NAME [--timeout S] [--settle S] [--keep-ui]` | `list_levels` check, `load_level {name}`, then wait until `get_status` reports that level and a vehicle and `core_gamestate.loading()` is false (default 300 s). Waits `--settle` (3 s), then hides the UI (`toggle_ui show=false`, plus 1.5 s) so screenshots show the 3D scene only. |
| `screenshot OUT.jpg [--scale 1.0] [--min-mean 5]` | Triggers `screenshot_image`, polls until the image arrives (first call only answers "capturing"), saves it, prints size, mean/stddev brightness. Exit 7 if black or flat. Output must be outside the repo, or inside it and git-ignored. Scale 1.0 gives 1280x720. |
| `stop` | `run_lua shutdown(0)`, wait up to 30 s, else terminate only the recorded PID after re-checking it is still a BeamNG process with the recorded start time. Waits for the port and the CEF helper processes to go. Removes the state file and the lock (only a `game` lock naming our PID). |
| `clear-lock [--also-vlm]` | Removes a stale lock (owner PID dead) and a stale state file. Refuses if the owner is alive, the content is not understood, or the lock is `vlm`'s (its model may still be loaded: use `vlm.py unload`, or `--also-vlm` if you know it is unloaded). |
| `log-errors [--strict] [--ignore REGEX]... [--no-warnings]` | `E` and `W` lines of `H:\BeamNG-QA\profile\current\beamng.log` (the last run) as JSON. `--strict` exits 8 if any error is left. Module: `gamectl.log_errors()`. |
| `selftest [--cycles 3] [--level gridmap_v2] [--keep-going] [--allow-daytime]` | start, (cycle 1: second-start refusal proof), load-level, screenshot with brightness check, stop; then checks the end state. Report in `H:\BelfastData\selftest\selftest-<stamp>.json`, images next to it. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | other error (also: profile outside `H:\BeamNG-QA`, output path would be committed) |
| 2 | a BeamNG process already exists (any, whoever started it); nothing was touched |
| 3 | daytime (09:00-18:00) without `--allow-daytime` |
| 4 | GPU lock still held after `--lock-timeout` (default 20 min) |
| 5 | game did not become ready, or died on start; port already taken. Our game is stopped. |
| 6 | MCP port not bound to 127.0.0.1 by the game. Game stopped. |
| 7 | screenshot failed or black/flat |
| 8 | `log-errors --strict` found errors |
| 9 | our game is not running (`status`, `load-level`, `screenshot`) |
| 10 | `clear-lock` refused |
| 11 | level unknown or not ready in time |
| 12 | process still alive after stop |
| 13 | selftest failed |

## Lock and state rules

- GPU lock `H:\BelfastData\gpu.lock`, same file and format as `tools/vlm.py`: `<kind> pid=<pid> t=<epoch>`, created with exclusive create. `start` polls every 15 s while it exists, then creates `game pid=<our pid> t=<epoch>` and rewrites it to `game pid=<game pid> t=<launch epoch>` once the game is launched. Removed only if its content is still exactly what we wrote (or, in `stop`, a `game` line with the recorded PID).
- A `game` lock whose PID is dead is removed automatically by `start`. A `vlm` lock is never auto-cleared, even with a dead PID, because `vlm.py load` deliberately leaves the file behind while the model stays loaded. No disagreement with vlm.py found otherwise.
- State `H:\BelfastData\game.state.json`: pid, process creation time (from the OS), launch time, profile, port, lock line, bind evidence, settings report. Written after launch, removed by `stop`.
- Identity check before any kill: the PID must be alive, its image `BeamNG.drive.x64.exe`, and its creation time within 2 s of the recorded one; the check is repeated on the process handle that gets terminated. Never kills by image name. Helper processes (CEF) share the image name; they exit by themselves after the main process (a few seconds after a forced kill) and `stop` waits for them but never kills them.
- `start` aborts and stops only its own recorded PID if anything fails after launch.

## QA settings

Seeded into the QA profile only (`require_qa_profile` refuses any path outside `H:\BeamNG-QA`) before launch, merged into the existing JSON so other keys stay. Each key is read back through MCP `run_lua` after start; if one were wrong, `settings.setValue` is called live. Verified on the existing QA profile and on a brand-new profile (`H:\BeamNG-QA\profile-fresh`, since deleted): every key took effect from the file alone.

| Key | File (`<profile>\current\settings\`) | Value | Method that works |
|---|---|---|---|
| `GraphicDisplayModes` | `settings.json` | `Window` | pre-seed (already default) |
| `GraphicDisplayResolutions` | `settings.json` | `1280 720` | pre-seed (already default). Screenshot at scale 1.0 is 1280x720; the log notes the client area is 1264x681 |
| `AudioMasterVol` | `settings.json` | `0.0` | pre-seed; live read `AudioMasterVol` and `$pref::SFX::AudioChannelMaster` both 0 |
| `fpsLimitBackgroundEnabled` | `settings.json` | `false` | pre-seed (spike: also works live) |
| `enableMcp` | `settings.json` | `true` | pre-seed (the flag also writes it; only ever use the flag with the QA profile) |
| `telemetry` | `cloud\settings.json` | `disable` | pre-seed |
| `onlineFeatures` | `cloud\settings.json` | `disable` | pre-seed (no consent screen) |
| `onboardingPopupShown` | `cloud\settings.json` | `true` | pre-seed (no Welcome modal) |
| `showedInputLayoutPopupV37` | `cloud\settings.json` | `true` | pre-seed. Found on the fresh-profile run: without it the first screen is "Input Changes" |

A leftover `temp\crashReports` folder (it triggers the "Send Error Reports" dialog) is moved to `H:\BeamNG-QA\crash-archive\<stamp>` before launch.

## Observed timings (selftest, 3 cycles, 2026-11-10 evening)

| | Cycle 1 | Cycle 2 | Cycle 3 |
|---|---|---|---|
| launch to MCP ready | 6.11 s | 6.09 s | 6.08 s |
| `gridmap_v2` load (warm shader cache) | 3.86 s | 3.97 s | 3.94 s |
| stop | 2.05 s | 2.05 s | 2.01 s |

Whole cycle about 25 s. First-ever `gridmap_v2` load in a fresh process (cold): 11.2 s. Screenshot call 0.6 to 1.0 s. Screenshots were real 1280x720 images of the level (mean brightness 140 to 147, stddev 44 to 48).

## Port bind

`start` samples the kernel TCP table (about 1 ms per sample) for up to 10 s, then keeps watching for 1.5 s, and fails with exit 6 (stopping the game) if any socket on the port is not `127.0.0.1` or not owned by the game. Live evidence from `netstat -ano` while the game ran: `TCP 127.0.0.1:29292  0.0.0.0:0  FIN_WAIT_1 <game pid>` and `... ESTABLISHED <game pid>`, no `0.0.0.0` or `[::]` row for the port, and the game log says `MCP listening on http://127.0.0.1:29292/mcp`. On this Windows build (26300) the state column of listening sockets is unreliable (the same socket shows as LISTENING, CLOSED, FIN_WAIT_1 or state 0; a plain Python listener shows state 0), so the check ignores state and judges by address and owner.

## Known limits

- `screenshot_image` captures the UI as well as the 3D view (the spike thought otherwise): after a level loads, the main-menu route is on top. `load-level` hides it; a screenshot taken straight after hiding still shows it (hence the 1.5 s wait).
- Re-loading the level that is already loaded can be reported ready too early (the old level matches the name). Use a fresh `start` or load a different level first.
- `load-level` default vehicle is the stock pickup, not the Covet.
- The 09:00-18:00 check happens at `start` only. A running game is not stopped at 09:00.
- `log-errors` has no built-in baseline. A clean `gridmap_v2` run logs 2 errors (`Could not initialize online service provider: Steam`, `Unable to deserialize ... track_builder_pieces/items.level.json`) and 2 warnings (swap chain extent, no online platform for achievements). Pass `--ignore` patterns for known ones.
- `-enablemcp` stays recorded in the QA profile. No auth on MCP while the game runs; keep runs short.
- While the local model holds the GPU lock, `start` waits (up to 20 min). Lock waiting is covered by unit tests only, not observed live.
- Tests: `uv run pytest tests/gamectl -q` (no game needed).
