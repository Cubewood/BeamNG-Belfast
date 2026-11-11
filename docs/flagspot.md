# Flag this spot (T-308)

Dev-only BeamNG mod. One key or wheel button press appends a JSON line (position, heading, speed, level, vehicle, screenshot path) and takes a screenshot. Meant for VR drives: press it where something looks wrong, review the list later (`/vr-review`).

Source: `mod/flagspot_cubewood/` (mod folder layout, mirrors the game folder). Packer: `mod/pack_flagspot.py`.

| File | Role |
|---|---|
| `lua/ge/extensions/cubewood/flagspot.lua` | GE extension `cubewood_flagspot`, function `flag()` |
| `lua/ge/extensions/core/input/actions/flagspot_cubewood.json` | input action `flagSpotCubewood` (category General, `ctx: tlua`, `onDown`), same format as the game's `general.json` |
| `settings/inputmaps/keyboard_flagspot_cubewood.json` | default keyboard binding `F9` (only F9 use in the game is the editor map) |

## Install

```powershell
uv run --no-project --python 3.12 python mod/pack_flagspot.py "$env:LOCALAPPDATA\BeamNG\BeamNG.drive\current\mods"
```

That writes `flagspot_cubewood.zip` into the user's `mods` folder (drop-in, no other steps). Only on the user's request: nothing in the loops installs it into the normal profile. For development, copy `mod/flagspot_cubewood/` to `<profile>\current\mods\unpacked\flagspot_cubewood\` instead.

## Bind

F9 works out of the box on the keyboard. For a steering-wheel or VR controller button: Options > Controls > Bindings > category General > "Flag this spot (Belfast QA)" > assign the button. (The action title is a plain string, not a translation key; the Options screen was not opened in this test, so the label is unverified.)

## Record format

File: `<user folder>\flagspot\flags.jsonl`, one JSON object per line, appended. The user folder is `%LOCALAPPDATA%\BeamNG\BeamNG.drive\current\` normally, `H:\BeamNG-QA\profile\current\` for QA runs. Screenshots go to `<user folder>\flagspot\shots\flag_<YYYYMMDD_HHMMSS>_<n>.jpg` (1280x720 in the QA window; whatever the game window size is otherwise).

```json
{"schema":1,"time_utc":"2026-11-11T21:54:27Z","time_local":"2026-11-11T21:54:27","level":"gridmap_v2","vehicle":{"model":"pickup","config":"d15_4wd_A"},"pos":{"x":-121.84,"y":-155.92,"z":100.31},"heading_deg":0.5,"speed_ms":14.78,"speed_mph":33.05,"camera_pos":{"x":-122.45,"y":-161.85,"z":102.41},"vr_session":false,"screenshot":"flagspot/shots/flag_20261111_215427_1.jpg","screenshot_abs":"H:/BeamNG-QA/profile/current/flagspot/shots/flag_20261111_215427_1.jpg","note":""}
```

- `pos` is world coordinates of the vehicle (metres), `heading_deg` 0..360, 0 = north (+Y), clockwise.
- `screenshot` is relative to the user folder; `screenshot_abs` is the absolute path. If the capture could not start, both are absent and `screenshot_error` says why.
- `n` in the file name restarts at 1 each game session; the timestamp keeps names unique.
- `note` is empty from the key; `extensions.cubewood_flagspot.flag("text")` from the console sets it.
- Repeat presses within 1 s are ignored (wheel buttons chatter). No vehicle: nothing is written (warning in the log).
- Feedback: an on-screen message ("Flag N saved") and a UI sound. The HTML message does not appear in the headset (not tested, see below); the sound is the VR cue.
- The extension logs one `I` line per flag and a `W` line if the screenshot file is still missing 4 s later.

## What was tested (2026-11-11, QA profile, `gridmap_v2`, stock pickup)

- Zip install in `mods/` and unpacked install: the game mounted both; action, default `F9` binding on `keyboard0` and the extension all loaded.
- Trigger path: a real input event. A virtual device (`core_input_virtualInput.createDevice` / `emit`, the same code the game's remote controller uses) was bound to the action with a QA-only inputmap and sent a button down/up. The event went through the game's binding table and `onDown` into `flag()`. Stationary and moving (14.8 m/s) flags both produced a full record and a non-empty JPEG (about 77 KB, viewed: correct scene). Two presses in a row produced one record.
- Console: no errors or warnings from the mod. Game's own: the two known errors (Steam provider, `track_builder_pieces/items.level.json`) and warnings (swap chain extent, achievements). One extra `E` line, `Hook discarded: FilteredInputChanged`, showed up in 2 of 4 mod runs and in none of 3 later clean runs; the game fires it itself on physical input while the UI is closing. Not attributable to the mod, but not proven either.
- Game stopped, no BeamNG process left.

## Not tested

- No headset. Not tested: that `createScreenshot2` captures a useful image while an OpenXR session runs (it may capture the desktop mirror, not the eye view; `vr_session` in each record shows what the game reported), that the HTML message is seen in VR, that a wheel or VR controller button can be bound and fires.
- A physical F9 key press (no OS key injection was used), and the Options screen label.
- The UI sound is audible: the QA profile is muted; the call returned without error.
- Any level other than `gridmap_v2` (no Belfast build exists yet).
