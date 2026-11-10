# BeamNG Belfast: agent instructions

A BeamNG.drive map of Belfast for practising the Northern Ireland driving test in VR. MapNG generates the level; this repo holds Belfast data steps, UK rules, road fixes and the QA loop. Roads must be accurate; buildings only need to look like a city.

Detail lives in `task.md` (plan, task queue) and `GUIDE.md` (how the loops run). Read only what you need.

## Recorded decisions

- Test centre: Belfast (Hydebank), 4 Hospital Road, BT8 8JL. First map area is around it.
- This PC also runs the user's day job. Nothing may launch the game between 09:00 and 18:00.
- Agents may push to `dev` and open pull requests. Never push `main`.
- No Google Maps content (Maps, Street View, Earth, Places) in any data, prompt or fix.
- Private use, public repo: the repo holds code and fix data only. The level zip stays on the user's PC.
- Learner car: Ibishu Covet, config `15le_M` (right-hand-drive 5-speed manual).

## Repository layout

```text
config/          map areas and settings
data/overrides/  our road fixes (committed)
third_party/     MapNG at a pinned commit (not created yet)
rules/           UK markings and signs
pipeline/        data steps
qa/              checks, drive tests, image judge, nightly run
tools/           game control, task queue, local model helper
issues/          QA issue cards, text only
reports/         daily digest (runtime output, ignored except .gitkeep)
docs/            spikes and decisions
feedback/        VR review notes
H:\BelfastData\  downloads and builds, not in git
H:\BeamNG-QA\    throwaway game profile for tests
```

## Guard rails (task.md section 8)

1. Never write inside `H:\SteamLibrary\steamapps\common\BeamNG.drive`. It is reference material only.
2. Tests never touch the user's normal BeamNG profile. A build reaches the real `mods` folder only when the user asks.
3. One game process at a time. Exit at once if BeamNG is already running, because the user may be playing.
4. Never pass `-mcpremote`. BeamNG's MCP server has no login, so anything on this PC can call it while it runs. Keep it on only during QA.
5. Never push to `main`. Pushing to `dev` is allowed (decision 3).
6. The repository is public. Do not commit secrets, game files, Mapillary images or raw datasets.
7. No Google Maps content in any data, prompt or fix.
8. Every entry in `data/overrides/` names its evidence.
9. Only use Lua functions that exist in the game's `lua/` folder.
10. Nothing is done until it has run in the game with a clean console. Report failures as failures.
11. Read third-party code before running it, and pin it to a commit.

## BeamNG modder rules

From the game's own `NOTE_FOR_MODDERS.md` (read-only reference; official rules at beamng.com/game/support/policies/modding-guidelines/ and documentation.beamng.com/modding/):

- Never edit the game folder. Develop in `mods/unpacked/<modname>/` in the user folder, then pack to a `.zip`. The mod must work by dropping the zip into `mods/`, with no extra steps.
- Mod folders mirror the game folder: `levels/<level>/`, `vehicles/<model>/`, `lua/ge/extensions/mymod/foo.lua` (loads as extension `mymod_foo`), `lua/vehicle/extensions/`, `scripts/mymod/modScript.lua`, `ui/modules/apps/<AppName>/`.
- Give everything a unique name (zips, folders, files, jbeam parts, meshes, materials, extensions); `name_username` works. Never overwrite default game files, parts, meshes or materials; add to existing content instead.
- Zip names use only `A-Z a-z 0-9 - _`, with no version in the name, and stay the same across updates.
- Before writing any file, find an existing game file of the same type, read it and follow its structure.
- Only use functions found in the game's `lua/` folder. Reuse its helpers (`lua/common/utils.lua`, `lua/common/mathlib.lua`, `lua/ge/ge_utils.lua`).
- Lua is LuaJIT (Lua 5.1), not 5.3 or 5.4. Log with `log('I', 'mymod', 'message')`.
- Test in game: fix every console error and warning (`~` console; full log is `beamng.log` in the user folder). `Ctrl+L` reloads game Lua, `Ctrl+R` reloads the vehicle. Test with only your mod enabled.
- Before upload: only content you made or have permission to use; remove unused files, notes, logs and readmes; keep size small.
- Textures: name PNGs `*.color.png`, `*.normal.png` or `*.data.png`; the game cooks them to DDS. Ship the DDS next to where the PNGs were but keep referencing the `.png` in `materials.json`.

## Working conventions

- One task per run. Read that task's row in `task.md`, not the whole plan.
- Set the task status in `task.md` through the task tools once they exist (T-009: `tools/next_task.py`, `tools/task_state.py`). Until then, edit only that row's Status column and keep the table layout, because the loops parse it.
- Run the task's Done-when check. If it fails twice, set `blocked` with a note and escalate as `task.md` section 5.1 describes (raise effort, then one tier up; Fable only after Opus at xhigh has failed).
- Report failures as failures. Do not mark a task done on a partial pass.
- Keep token use low. Cheapest check first: script, then local model, then Claude, then the user. Read the digest, not raw logs or images.
- Work on a `loop/T-xxx` branch; merge to `dev` when the check passes.
- Day runs never launch the game. Night runs may, outside 09:00 to 18:00, with the lock file held.
- Keep this file under 200 lines. Move detail to `docs/`.

## Key paths

- Game install (read-only): `H:\SteamLibrary\steamapps\common\BeamNG.drive`
- Downloads and builds: `H:\BelfastData`
- Throwaway QA profile: `H:\BeamNG-QA\profile`
- BeamNG built-in MCP: `http://127.0.0.1:29292/mcp`, enabled only with `-enablemcp` during QA, never `-mcpremote`.
- Local model: Qwen3.8 27B in LM Studio (`lms`, port 1234). Capture frames, close the game, then load the model.

## Model tiers

| Tag | Model | Use |
|---|---|---|
| script | none | Deterministic: builds, checks, metrics, digests |
| local | Qwen3.8 27B (LM Studio) | Judge screenshots, compare photos, sort logs |
| haiku | Haiku 5.5 | Dispatch, triage, tidy-ups, simple scripts |
| sonnet | Sonnet 5.5, high effort | Most implementation |
| opus | Opus 5.5, medium to xhigh | Design, junction geometry, MapNG internals, third-party code review |
| fable | Fable 5.1 | Escalation only; no task starts here |
| you | the user | Decisions, accounts, VR drives |

Raise effort before changing model.
