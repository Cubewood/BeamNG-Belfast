# Spike T-008: BeamNG and the local vision model on one GPU

Measured 2026-11-10, 21:43 to 22:05, RTX 4090 24 GB, 31.9 GB RAM, Windows 11. Scripts: `tools/spikes/gpu/` (`lib.py`, `run.py`; scratch code, not a product tool). Raw results and the frames live in `H:\BelfastData\spikes\gpu\` (not in git).

## Verdict

**Not workable on this PC as it is, and the limit is system RAM, not VRAM or GPU time.**

- The model process (`llama-server`) holds about 20 GB of host RAM even with `--gpu max` (working set 20.35 GB at the end of the loop run). The game adds 2.5 to 6 GB. Windows plus the session's other programs used about 8 GB. Total 31 to 32 GB of 31.9 GB.
- Every configuration that ran both at once pushed available RAM to 0.65 to 2 GB. My abort rule (stop a configuration when available RAM stays under 2 GB) ended all of them, so only partial data exists for the overlap. Nothing crashed, nothing stalled, no driver reset.
- What did work, in the best case (model loaded first, `gridmap_v2`, 640x360 frames): the game stayed at its full frame rate while the model answered, VRAM peaked at 19.5 GB of 24.5 GB, and a model-driven loop ran 5 steps at about 12 s each before the RAM floor tripped.
- It would only be "workable with constraints" if free RAM is freed up or added: for example 64 GB RAM, or the user's other programs closed. Not measured: what happens if the floor is ignored (it might just swap and work, it might stall the whole PC; I did not push on).

## Method notes

- The spike script holds `H:\BelfastData\gpu.lock` itself (owner `spike-T008 pid=...`) for the whole run and removes it at the end. `gamectl.cmd_start` takes that same lock, so inside the spike process only, `gamectl.GameLock` is replaced by a no-op stand-in (`lib.py`). `tools/gamectl.py` and `tools/vlm.py` are unchanged. `vlm.py load` refuses while BeamNG runs, so the model is loaded with `lms load` directly (same arguments as `vlm.py`, plus `--context-length` and `--gpu` varied); inference reuses `vlm.judge_image`.
- Graphics: the documented preset key is `GraphicOverallQuality` = `Lowest`. Setting it live through `settings.setValue` left the sub-keys unchanged (read back: lighting `High`, shadows `Normal`, textures `Normal`), so the spike also set every key of the `Lowest` block in `settingsPresets.json` explicitly and read them all back (all matched). The QA profile's `settings.json` was backed up first and restored afterwards, so the QA profile is back to what it was. (`gamectl` does not set graphics quality; if QA should always run at Lowest this needs a decision in T-006's settings table.)
- Frame rate: the `get_performance_metrics` tool returns per-component times, not an fps figure. The spike loads a tiny GE extension through `run_lua` that counts `onPreRender` calls and reads the count over wall time. Frame rate is frames per wall second over 10 to 12 s windows; max frame time is the longest `dtReal` seen.
- GPU spill: Windows perf counters `\GPU Adapter Memory(*)\Shared Usage` and `Dedicated Usage` (4090 adapter), sampled about every 4 s, plus `nvidia-smi memory.used` every second.
- Window focus: `windowFocused` came from `get_fps_limit_reason`. The background limit was off (`fpsLimitBackgroundEnabled=false`, QA setting).
- Test frames: 5 camera positions via `orbit_camera` (yaw 15 to 295), each captured at scale 0.5 (640x360) and 1.0 (1280x720). The model schema was `{"sees_road": bool, "action": forward|left|right|stop, "reason": string}`, reasoning label `low` (which has no real effect, see `vlm.md`).

## Baseline: game alone, `Lowest`, 1280x720 window

| | `gridmap_v2` | `west_coast_usa` |
|---|---|---|
| Level load (warm shader cache) | 3.9 s | 28.7 s (13.9 s and 17.0 s on later runs) |
| Frame rate, focused or unfocused | 224.9 / not reliably measured (see below) | 224.7 (window not focused, background limit off) |
| Mean frame time / max | 4.5 ms / 4.8 ms | 4.5 ms / 4.9 ms |
| nvidia-smi VRAM, machine-wide | 3115 MiB | 3070 MiB |
| Game's own counter (`get_vram_usage`, bytes) | 1180 MiB | 1596 MiB |
| VRAM with no game running | 1300 MiB | 1300 MiB |
| Shared GPU memory (perf counter) | 407 MiB | 626 MiB |
| RAM used (machine, with game) | 12.2 GB | 14.3 GB |
| RAM used before the game (about) | 8.0 GB | 8.0 GB |
| Game process working set | not recorded | 2.5 GB main process, plus helpers |

- Frame rate is the same 224.7 to 224.9 in every game-alone condition, including the empty `gridmap_v2` and the larger `west_coast_usa`, with zero frames over 50 ms. A number that constant means a limiter, not the GPU. `get_fps_limit_reason` shows `fpsCap` 0 and the 60 fps foreground cap inactive; the source of the roughly 225 fps ceiling (three times the 75 Hz display refresh?) is not identified. Real uncapped GPU headroom is **not measured**.
- Focus: on `west_coast_usa` the game was unfocused in both measurements (run from the agent shell), and ran the same 224.7 fps with the background limit off. My attempt to defocus the game on `gridmap_v2` did not work (it stayed focused), so no focused vs unfocused comparison exists on the same level.
- Game log: `gridmap_v2` has the 2 known errors. `west_coast_usa` adds about 40 stock-level `DecalRoad ... invalid distance fade parameters` errors. No GPU or crash lines.
- West-coast is a stock outdoor map. It is a rough stand-in for a city map, not a measurement of a dense Belfast build.

## Both together

Configurations, `--gpu max` unless stated. "Avail min" is the lowest available system RAM seen.

| # | Order | Level | Context | Result |
|---|---|---|---|---|
| a | game first, then model | `west_coast_usa` | 16384 | Load aborted at 26 s: RAM avail min 1066 MiB. VRAM at abort 20.3 GB, shared GPU 804 MiB. |
| b | game first | `west_coast_usa` | 8192 | Aborted at 12 s: avail min 1353 MiB. |
| c | game first | `west_coast_usa` | 4096 | Aborted at 13 s: avail min 1284 MiB. |
| d | partial offload (`--gpu 0.8` / `0.6`) | any | any | **Not run.** Weights live in host RAM already; offloading less puts more in RAM, so it cannot cure a RAM problem. |
| e1 | model first, then game | `west_coast_usa` | 16384 | Model loaded in 9 s (RAM peak 26.1 GB, avail min 5.8 GB, VRAM 18.3 GB). Game start plus level load tripped the floor: avail min 652 MiB, RAM used 31.2 of 31.9 GB. Game stopped, nothing inferred. |
| e2 | model first | `gridmap_v2` | 16384 | Load 10 s. Game and level up, avail min 3.8 GB. Game 224.9 fps with the model idle. 360p inference, 5 frames: see below. Floor tripped during the 720p set (no result). |
| e3 | model first | `gridmap_v2` | 4096 | Load 10 s. Same pattern; 360p 5 frames, and 1 of 5 720p frames before the floor tripped. |

Numbers for e2 and e3 (the only configurations that produced inference data with the game running):

| | e2: ctx 16384 | e3: ctx 4096 |
|---|---|---|
| Game fps, model loaded and idle | 224.9 | 224.8 |
| Game fps during inference (360p set) | 224.9, max frame 4.9 ms, 0 frames over 50 ms | 224.9, max frame 5.0 ms, 0 frames over 50 ms |
| Seconds per frame, 640x360 input (prompt 304 tokens) | mean 10.8 (min 9.9, max 11.6), n=5 | mean 10.4 (min 9.9, max 11.8), n=5 |
| Seconds per frame, 1280x720 input (prompt 964 tokens) | not measured (floor tripped) | 11.7, n=1 |
| Tokens per second (completion tokens over request time, so includes prompt) | 21.9 | 23.4 |
| Schema-valid first try | 5/5 | 6/6 |
| nvidia-smi VRAM peak, machine-wide | 19,550 MiB of 24,564 | 19,472 MiB |
| Shared GPU memory peak | 595 MiB | 570 MiB |
| RAM avail min during run | 1.65 GB | 1.6 to 2.07 GB |
| Model load VRAM | about 17.2 GB above the 1.3 GB baseline | about 16.9 GB above baseline |

- GPU time is not the problem. The game kept its ceiling frame rate while the model ran, with no hitches over 50 ms (the T-007 run alone showed about 10 s per frame as well, so the model is not slowed by the game either).
- No spill into shared GPU memory: shared usage stayed at 520 to 600 MiB, about the same as the game alone (407 to 626 MiB). VRAM stayed 5 GB under the limit.
- Context length 16384 vs 4096 changed machine-wide VRAM by well under 0.2 GB in these runs, so context is not the lever for either VRAM or RAM here. The 23.96 GB peak recorded in T-007 (model alone) was **not reproduced**; the highest value seen in this spike was 20.3 GB, during an aborted load. Reason for the difference is not known (other programs' VRAM then, larger test frames, or a transient).
- No errors from LM Studio loads, no Windows display driver events (`nvlddmkm` / `Display` / 4101) in the System log for the runs, no game crash.
- Order matters for RAM: game first then model failed at load in all three contexts (the load needs about 18 GB free on top of the game's 6 GB). Model first then game gets through a light level (`gridmap_v2`) but still ends at the floor.

## Closed loop (10 steps planned, 5 done)

Best configuration: model first, `gridmap_v2`, context 4096, `--gpu max`, 640x360 frames. Per step: `screenshot_image` (scale 0.5) -> model reply as schema JSON -> `orbit_camera` (yaw +/- 25 for left/right, distance -1 for forward; tool name checked in `tools/list`).

| Step | Screenshot | Model | Action call | Step total | Completion tokens | JSON valid |
|---|---|---|---|---|---|---|
| 0 | 0.52 s | 10.97 s | 0.01 s | 11.5 s | 238 | yes |
| 1 | 0.52 | 11.24 | 0.02 | 11.8 | 248 | yes |
| 2 | 0.52 | 10.56 | 0.03 | 11.1 | 233 | yes |
| 3 | 0.51 | 12.83 | 0.01 | 13.4 | 267 | yes |
| 4 | 0.51 | 12.30 | 0.02 | 12.8 | 269 | yes |
| 5 | aborted: available RAM under 2 GB (1.6 GB min) | | | | | |

- Mean step time 12.1 s, nearly all of it the model. Screenshot and the MCP call are about 0.5 s together.
- 5 of 5 replies were schema-valid on the first attempt, 0 retries. The answers were plausible for the scene (`gridmap_v2` is flat ground with no road: `sees_road` false each time, actions `forward` or `stop`).
- Game fps during the loop: 224.7, max frame 6.2 ms, 0 frames over 50 ms. VRAM peak 19.4 GB, shared GPU 565 MiB.
- The loop was cut off by my RAM rule at step 5, not by a failure. Steps 6 to 10 were not run. This is a feasibility probe, not a driving test.
- RAM at the end of the loop (top working sets): `llama-server` 20.35 GB, BeamNG main 2.5 GB, then the rest of the system programs each under 0.5 GB.

## What this means

**T-805 (weekly exploratory play-test by Qwen).** The simultaneous loop, as asked, does not fit on this PC at the 2 GB RAM floor. Options, in order of cost:

1. Turn-based, game closed during model work: capture a batch of frames on a route (game running, model unloaded), close the game, load the model, judge the batch, and decide the next batch from the answers. This is the pattern `CLAUDE.md` already prescribes and needs no new hardware. A step costs the game start (about 6 s) plus a level load (4 to 30 s), so a play-test is a series of short sessions, not one continuous drive.
2. More RAM (64 GB would leave about 25 GB free with both running); then the loop as measured here (about 12 s per step with 640x360 input) works. Not measured with that much RAM.
3. If the loop must be live on 32 GB: only the lightest levels, model loaded first, nothing else open, and accept running at the edge of memory. Not recommended and not proven stable: it reached the floor within about 60 s of loop time.

With about 12 s per step, any live loop must pause the game between decisions (`pause_physics` / `resume_physics` exist as tools), or the car will have moved 12 s of game time before the next frame. Not tested here.

**Nightly QA.** Not affected: it already runs the game and the model in turn under the shared lock. Measured here: model load 9 to 10 s (warm), game start 6 s, game stop 2 s.

**Recommended context for the image judge (T-304).** Use 8192. A 1280x720 frame is about 964 prompt tokens plus about 300 reply tokens, so even 4096 fitted (measured, 1 frame at 720p); 8192 leaves room for a longer prompt and a retry. Context made under 0.2 GB of difference in VRAM in this spike, so the choice is about margin, not about fitting. For the model alone the T-007 peak was 23.96 GB of 24.56 GB, so keep the desktop light and do not run it while the game is open. 8192 was not itself loaded in this spike (only 16384 and 4096 got past load; the 8192 attempt was a game-first one that aborted on RAM), so this is an interpolation between two measured sizes.

## Not measured / open

- Fps with the 60 fps foreground cap or the 225 fps ceiling removed; focused vs unfocused on one level.
- Context 8192 and partial offload (`--gpu 0.8`, `0.6`) with the game.
- Inference at 1280x720 for more than 1 frame while the game runs; 640x360 only has n=5 per configuration.
- Game first then model on `gridmap_v2` (game first was only tried on `west_coast_usa`).
- Behaviour with the RAM floor ignored (swapping, stalls).
- Whether LM Studio has a setting that avoids the 20 GB host copy (there is no `lms load` flag for it; a GUI-side "keep model in memory" or mmap option was not looked at).
- A dense city map. The Belfast build will use more VRAM and RAM than `gridmap_v2`.

## Final state

Game closed, model unloaded (`lms unload --all`), LM Studio server stopped (started by the spike), `gpu.lock` and `game.state.json` removed, VRAM back to 1.3 GB (baseline), QA `settings.json` restored from backup. Spike scripts are in `tools/spikes/gpu/`; nothing committed.
