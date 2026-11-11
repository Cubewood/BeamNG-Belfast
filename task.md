# BeamNG Belfast: project plan and task queue

*Last updated 11 November 2026. Planned against BeamNG.drive 0.40.4; the install is now 0.40.5 (found in T-005).*

## 0. Where things stand (read this first in a new session)

**M0 is done except T-011. M1 is done up to the VR drive (11 November 2026).** T-101 to T-104 and T-308 passed and are on `dev` (pushed). Details: `docs/spikes/mapng.md`, `docs/mapng_export.md`, `docs/belfast_slice.md`, `docs/spikes/baseline.md`, `docs/flagspot.md`; M0 details in `docs/spikes/mcp.md`, `docs/spikes/vlm.md`, `docs/spikes/gpu.md`, `docs/gamectl.md`, `docs/loopkit.md`.

**How we work.** On 10 November an orchestrated run of M0 used about 72% of a 5-hour usage limit, so scheduled unattended running is on hold and T-011 is deferred. On 11 November you asked for an orchestrated session again; it ran T-101, T-308, T-102, T-103 and T-104, one agent per task. Default for a new session is still: `Read task.md section 0 and CLAUDE.md, then do T-xxx.` Ask for orchestration when you want it.

**Ready next:** T-105 (you: VR drive of `belfast_slice`, then notes in `feedback/`), T-303 (junction camera sweep), T-307 (digest and issue cards), T-803 (optional). T-106 (gap report) waits for T-105.

**Needs you**

- Confirm the test centre position. OSM has no feature for the Hydebank centre or BT8 8JL, so `belfast_slice` is centred on the middle of Hospital Road (54.5428, -5.9300).
- T-102 was marked done with 806 of 812 MapNG unit tests passing. The six failures are Windows environment problems (path bug, a dependency missing from the lockfile, CRLF checkout), none in export code. Reopen it if that is not acceptable.
- Say when the slice and the flag-this-spot mod should go into your normal `mods` folder (T-105). Both are in the QA profile only.

**What M1 found so far**

- MapNG review: go, with conditions that `tools/mapng_export.py` enforces (no satellite or Google content, hosts blocked on export, UK country rules served). Without the rules file the export comes out as USA, right-hand traffic.
- `belfast_slice` (2048 m, 1 m/px, 72 MB) lints with 0 errors, loads in 17.7 s with no errors of its own, and has white markings and left-hand road rules. Elevation is 30 m Terrarium, so the terrain is coarse until M2.
- Baseline fails: sidewalk probes 7/247 (2.8%, limit 2%); drive test 8 of 12 routes completed, 4 crashes, two bumps over 7 m/s2. Sites are in `docs/spikes/baseline.md`.
- AI traffic works on the slice and keeps left (44 of 44 two-way samples on Hospital Road and Purdysburn Road). The fatal Lua error T-104 saw (`graphpath.lua:268`) came from switching a car to traffic mode after a scripted drive, not from the level (I-001, `docs/spikes/ai-graph.md`). Roundabouts, one-ways and junction turns under AI are still unchecked.
- UK gaps in MapNG: km/h speed signs only, Italian sign meshes, anticlockwise roundabout sign, no mini-roundabouts, box junctions or give-way markings. Biome is still "East Coast USA".
- Route drive suite (T-302, `docs/drive_suite.md`): `qa/drive_suite.py run --gate` drives 10 named routes from a provisional start on Hospital Road in about 8 minutes, unattended. First run: 7 of 10 completed, G3 fails. Crashes at (-211,-93) (Lady Ishbel Avenue right turn, two routes) and (194,284) (Hospital Road / Newtownbreda Road, the same junction T-104 crashed at). The crashes happen only when the AI follows its lane; MapNG-style scripted driving got through both. Causes are not established, and bumps near the 7 m/s2 limit vary between runs.
- Flag-this-spot key works on the QA profile (F9, records in `flagspot\flags.jsonl` in the user folder). Untested in a headset.

**What M0 found**

- BeamNG's built-in MCP works: 102 tools, localhost only, about 6 s from launch to ready. `-windowed` and `-fullscreen` crash this build; the default window is already 1280x720.
- `tools/gamectl.py` starts, loads a level, screenshots and stops the game safely. `tools/vlm.py` drives Qwen. They share `H:\BelfastData\gpu.lock` so the two never overlap.
- The game and Qwen cannot run together on this PC. System RAM (31 GB) is the limit, not video memory. QA stays turn-based, and T-805 would have to be turn-based too.
- Qwen takes about 10 s per frame and its reasoning effort cannot be set. Its accuracy on real game frames is still unmeasured (T-306).

**Loose ends from M0**

- The loop kit's slash commands (`/next-task`, `/status` and the rest) were walked through by hand, never run as real commands. Try `/status` once in a new session.
- The guard-rail hook was tested with sample input, not in a live session. It reads command text, so a script that builds a path or flag at run time is not caught.
- Re-tagging a blocked task and adding Inbox rows are hand edits to this file.
- Gate G1 needs a baseline: a clean stock level already logs two errors of the game's own.
- The 09:00 to 18:00 rule is checked only when the game starts.

This file is the plan and the work queue. The loops described in [GUIDE.md](GUIDE.md) read the tables in section 7 and change the Status column, so please keep their layout.

## 1. Goal

Build a BeamNG.drive map of Belfast that you can drive in VR to practise for the Northern Ireland car test. The roads need to be right. Buildings only need to make it look like a city.

The first target is the area around your test centre. The map then grows outwards as far as VR performance allows.

Road accuracy is measured rather than judged by eye alone:

| What | Target | How it is checked |
|---|---|---|
| Connections | Every public road in the map area exists, joins up, and has the right one-way direction | Automatic comparison with OpenStreetMap (OSM) |
| Position and width | Road follows its mapped line. Width within 0.75 m of the Department for Infrastructure (DfI) figure where one exists | Automatic |
| Lanes and markings | Correct lane count, UK line patterns, give-way and stop lines in the right place | Automatic rules, then image checks |
| Junction control | Traffic lights, roundabouts, mini-roundabouts and crossings where they really are | OSM and DfI data, then street photos |
| Height | Road within 0.5 m of LiDAR ground height, except on bridges | Automatic |
| Speed limits | Correct per road, shown in mph | Automatic |
| Drivability | BeamNG's AI drives every route with no crash, no getting stuck and no falling through | In-game drive tests |
| VR comfort | Holds your headset's frame rate on the routes | You, in the headset |

A simulator helps with knowing the roads and reading junctions. It does not replace lessons, and the DVA does not publish its test routes, so the practice routes here are informed guesses.

## 2. What the research found

Several of these change the idea you started with.

| # | Finding | What it changes |
|---|---|---|
| 1 | **BeamNG 0.40.4 ships its own MCP server** (`lua/ge/extensions/mcp/` in your install). The `-enablemcp` launch option starts it at `http://127.0.0.1:29292/mcp`. It has about 80 tools, including `run_lua`, `drive_to`, `get_navgraph`, `raycast`, `get_electrics`, `get_vehicle_damage`, `screenshot_image` and `load_level`. MapNG's test scripts use this same address, and your saved settings do not turn it on. | This becomes the main QA interface. It needs no DLL injection and gives exact measurements rather than pixels. |
| 2 | `llm-game-automation-mcp` injects a DLL into the game. It is six days old, BeamNG is not among its tested games, its prebuilt binaries are unsigned, and its own road-driving results are modest (GTA V car: 6 of 11 arrivals). BeamNG's EULA bars reverse engineering and circumvention (section 4.2) and does not mention injection either way. | Kept as an optional late extra (T-803, T-804), built only from reviewed source. Nothing depends on it. |
| 3 | **Google's Maps MCP has no Street View.** "Maps Experience agent tools" has five tools: `search_places`, `compute_routes`, `lookup_weather`, `resolve_names` and `resolve_maps_urls`. Street View is a separate API, and Google's terms (section 3.2.3) forbid scraping and creating content from Google Maps content, with tracing roads and building terrain models as named examples. | Google is left out of the loop. Street-level checks use Mapillary photos (CC BY-SA) and official NI data instead. See decision 4. |
| 4 | **MapNG** (MIT licence, version 0.2.11 released 8 November 2026) already exports complete static levels for BeamNG 0.40: terrain, roads with markings, signals, signs, pavements, bridges and AI traffic. It accepts a custom `.osm` file and custom elevation, has a headless export script, and has in-game probe and drive tests. | MapNG becomes the generator, so we do not write one. Our work is Belfast data, UK rules, road fixes and the QA loop. |
| 5 | BeamNG.Earth and EarthNG generate the world as you drive. EarthNG's own rules exclude static export. | Streaming is the wrong shape for one fixed city that has to hold VR frame rates. Both are MIT and stay as references: BeamNG.Earth for engine measurements and UK marking dimensions, EarthNG for road-audit ideas. |
| 6 | **Northern Ireland publishes very good open data** under the Open Government Licence: a 0.5 m LiDAR ground model of Belfast City (2006), York Street LiDAR (2012), the OSNI 10 m terrain model, DfI Highway Network, DfI Carriageway Details (widths and surfaces) and Controlled Pedestrian Crossings. | Heights and widths can come from official data. OSM has a width tag on only 0.4% of road length. |
| 7 | Size check on an 8.2 km square over south and central Belfast: 841 km of public road plus 455 km of service roads, 1,132 traffic-signal nodes, 80 roundabout ways, 35 mini-roundabouts and 110,493 buildings. West Coast USA has about 148 km of drivable road and 22,000 static objects. | The full square has several times West Coast USA's road length. The map grows in steps: 2 km, 4 km, then 8 km if performance allows. |
| 8 | OSM tag coverage on those roads, by length: `maxspeed` 65%, `lanes` 38%, `turn:lanes` 0.8%, `width` 0.4%. | Lane arrows are the biggest gap. Corrections start with roads near your test centre. |
| 9 | Only one terrain block has collision (BeamNG.Earth, measured on 0.39.4). Terrain import accepts 128 to 8192 px. 4096 px at 1 m is proven by official maps. 8192 px was not tested by BeamNG.Earth. | T-701 measures this on your install before we commit to 8 km. |
| 10 | Your PC has an RTX 4090 (24 GB), 31 GB RAM and 563 GB free on H:. Your local model is **Qwen3.8 27B**, which LM Studio reports as vision and tool capable (17.7 GB file). Game and model together will be tight on video memory. | QA captures frames first, closes the game, then judges them. LM Studio is enough, so Ollama is not needed. |
| 11 | BeamNGpy needs BeamNG.tech, and this install does not contain its `tech` Lua module. The game's OSM and SUMO import window only appears with a BeamNG.tech licence, and its importer does not build real junctions. A code comment in the game says `-headless` should not be used. | None of these are used. QA needs a real game window, so full runs happen at night. |
| 12 | The NI test is about 40 minutes with independent driving, one reversing exercise (around a corner, turn in the road, or reverse park), an emergency stop, and up to 15 driving faults allowed. | M6 is built around these. |
| 13 | The Ibishu Covet config `15le_M` in your install is a right-hand-drive 5-speed manual. | It is the default learner car. |

## 3. How it will be built

```text
OSM snapshot (pinned) ---+
Our fixes (overrides) ---+--> Belfast inputs --> MapNG (pinned) --> UK rules --> Level zip
DfI widths and crossings +                                                         |
NI LiDAR ground model ---+                                                         v
                                                                     Throwaway QA game profile
                                                                                   |
   Scripts --> AI drive tests --> Local Qwen image checks --> Street photo checks --> You in VR
```

**Design decisions**

- **A static, baked level.** One terrain block with its origin at the centre of the square, so nothing is far from the origin.
- **Roads built the way official maps build them.** DecalRoads (strips the engine drapes over the terrain) for surfaces and markings, and static meshes for bridges. MapNG already works this way.
- **Grow in three steps.** Slice (2 km square around your test centre), City South (4 km), Full (8 km, covering both Belfast test centres and the city centre, if T-701 allows). Balmoral and Hydebank are about 4.1 km apart.
- **Data precedence.** Official DfI and OSNI data first, then OSM plus our fixes, then Mapillary photos as evidence for a fix, then rule-based defaults. Google content is never a source.
- **Fixes survive rebuilds.** Corrections live in `data/overrides/`, keyed by OSM way ID and version, each naming its evidence. They are reapplied on every build. A fix whose road has since changed in OSM is flagged, not silently applied.
- **Cheapest check first.** Scripts, then the local model, then Claude, then you.
- **One game at a time.** A lock file serialises everything that launches BeamNG. Tests use a throwaway profile and never touch your normal one.

**Repository layout**

```text
config/          map areas and settings
data/overrides/  our road fixes (committed)
third_party/     MapNG at a pinned commit
rules/           UK markings and signs
pipeline/        data steps
qa/              checks, drive tests, image judge, nightly run
tools/           game control, task queue, local model helper
issues/          QA issue cards, text only
reports/         daily digest
docs/            spikes and decisions
H:\BelfastData\  downloads and builds, not in git
H:\BeamNG-QA\    throwaway game profile for tests
```

## 4. Decisions for you

**Answered 10 November 2026 (T-001):** decision 1 = **Hydebank**. Decision 3 = **yes**, loops may push to `dev` and open pull requests (`main` is still never pushed by a loop). Decisions 2, 4, 5, 6 and 7 = the defaults below.

| # | Decision | Default | Why it matters |
|---|---|---|---|
| 1 | Which test centre? | **Chosen: Belfast (Hydebank), 4 Hospital Road, BT8 8JL.** The other is Belfast (Balmoral), 66 Balmoral Road, BT12 6QL | Sets the first map area |
| 2 | Does this PC also run your day job? | Yes. Anything that launches the game stays outside 09:00 to 18:00 | The game uses the GPU and opens a window |
| 3 | May the loops push to `dev` on GitHub and open pull requests? | **Yes (answered).** `main` is never pushed by a loop | Unattended work needs somewhere to land |
| 4 | Leave Google Maps out? | Yes | See finding 3. Say so if you want it added as a lookup-only tool for things like place names |
| 5 | Private use or public release? | Private. The repo is public, so it holds code and fix data only, and the level zip stays on your PC | Public release adds licence and polish work (T-802) |
| 6 | Learner car | Manual right-hand-drive Covet | Affects T-601 |
| 7 | `llm-game-automation-mcp`: still wanted? | Optional, at the end, after a code review | See finding 2 |

## 5. Who does what

Model choice follows Anthropic's [choosing a model](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model) guide. It names Opus 5.5 as the starting point for most workloads, Sonnet 5.5 for everyday coding and agent work, Haiku 5.5 for the lowest cost and latency, and Fable 5.1 for when Opus at higher effort still falls short. It also says effort is often a better lever than changing model, and that a good evaluation set is the most important step.

Most tasks here are tagged Sonnet rather than Opus because they are tightly scoped and you asked to keep token use down. A tag gets raised if the task keeps failing (see Escalation below).

### 5.1 Tiers

| Tag | Model | Price per million tokens (in / out) | Used for | Default effort |
|---|---|---|---|---|
| `script` | No LLM | Free | Anything deterministic: builds, checks, metrics, digests | n/a |
| `local` | Qwen3.8 27B in LM Studio | Free | Judging screenshots, comparing photos, sorting logs | low or medium |
| `haiku` | Claude Haiku 5.5 (`claude-haiku-5-5`) | from $0.10 / $0.50 | Dispatching tasks, triage, tidy-ups, simple scripts | medium |
| `sonnet` | Claude Sonnet 5.5 (`claude-sonnet-5-5`) | $2 / $10 | Most implementation | high |
| `opus` | Claude Opus 5.5 (`claude-opus-5-5`) | $4 / $20 | Design, junction geometry, working inside MapNG's very large export code, reviewing third-party code | medium, or xhigh for the hardest tasks |
| `fable` | Claude Fable 5.1 (`claude-fable-5-1`) | $10 / $50 | Escalation only. No task starts here | high |
| `you` | You | Your time | Decisions, accounts, VR drives | n/a |

**How tasks reach the right model.** One dispatcher runs on Haiku 5.5. It asks `tools/next_task.py` for the next ready task, then hands it to a Claude Code agent pinned to that task's model and effort (T-010).

**Escalation.** If a task fails its Done-when check twice, raise the effort first, then move it up one tier, and note why in the task. Fable is allowed only after Opus at xhigh has failed.

### 5.2 Recurring jobs

| Job | When | Tier |
|---|---|---|
| Lint and static checks | Every build | `script` |
| In-game surface probes and AI drive tests | Nightly | `script` |
| Junction screenshot judging | Nightly | `local` |
| Sorting game log messages | Nightly | `local` |
| Street photo comparison | Nightly on changed junctions, weekly on all route junctions | `local` |
| Exploratory play-test, if T-008 shows it fits in memory | Weekly | `local` |
| Writing the digest and issue cards | Nightly | `script` |
| Reading the digest and updating the queue | Each morning | `haiku` |
| Frames Qwen cannot decide (capped at 20 a night) | When they occur | `sonnet` |
| Architecture and plan review | Weekly | `opus` |

## 6. Quality gates

| Gate | Runs | Passes when |
|---|---|---|
| G0 Static | Every build, no game | MapNG's level linter reports 0 errors. All public roads present, one-way directions match, road heights within 0.5 m of LiDAR off bridges, object counts within budget |
| G1 Load | In game | Level loads within 5 minutes, no errors from our mod in `beamng.log`, no physics instabilities |
| G2 Surface probes | In game | Downward rays land on the expected surface. Failures at most 2% per kind (MapNG's default), at most 0.5% on practice routes |
| G3 Drive tests | In game | Every route completes with no crash, stuck, airborne or off-route event, and vertical bumps under 7 m/s² (MapNG's defaults) |
| G4 Image check | Local Qwen | No open high-confidence defects on route junctions, using the thresholds set in T-306 |
| G5 Street truth | Local Qwen and Mapillary | Lane count and junction control match on at least 95% of route junctions that have usable photos |
| G6 VR | You | A 20-minute drive feels right, and your flagged issues are closed or accepted |

A build that passes G0 to G5 on `dev` is a release candidate. It merges to `main` only after you pass G6.

## 7. Milestones and tasks

Status values are `todo`, `doing`, `done` and `blocked`. For Where, `any` needs no game, `game` needs BeamNG running, `gpu` needs the local model and `you` needs you.

A task is ready when its status is `todo` and everything in Needs is `done`.

### Inbox

New requests land here. The dispatcher takes them before the milestones.

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|
| I-001 | done | Diagnose why AI traffic does not move on `belfast_slice`: fatal Lua error `graphpath.lua:268` edgeList nil (edge 3230) from `mapmgr.lua:281`, near (-122,109). See `docs/spikes/baseline.md` | opus | high | game | T-104 | Cause and proposed fix in `docs/spikes/ai-graph.md` |

### M0: Foundations

Outcome: Claude can run the game and the local model, safely and unattended.

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|
| T-001 | done | Answer the decisions in section 4, or say "use the defaults" | you | - | you | - | Section 4 updated |
| T-002 | done | Connect this folder to `Cubewood/BeamNG-Belfast`. Create the `dev` branch, `.gitignore` (data, builds, images, secrets), `CLAUDE.md` under 200 lines (project rules, the rules in the game's `NOTE_FOR_MODDERS.md`, section 8 below) and `CREDITS.md` | sonnet | medium | any | T-001 | `dev` exists, `git status` is clean, nothing over 5 MB is tracked |
| T-003 | done | Write `tools/bootstrap.ps1`: Node 22, GitHub CLI, Python 3.12 through uv, geospatial libraries, with uv and npm caches on H: | haiku | medium | any | T-002 | Runs twice cleanly and prints all versions |
| T-004 | done | Guard rails: deny rules and `PreToolUse` hooks that block writes to the Steam game folder, pushes to `main`, committing secrets and any use of `-mcpremote` | sonnet | high | any | T-002 | A test shows each rule blocking |
| T-005 | done | Prove BeamNG's built-in MCP live: launch windowed with `-userpath H:\BeamNG-QA\profile -enablemcp`, then call `initialize`, `tools/list`, `get_status` and `screenshot_image`. Record whether the window steals focus and how background frame limiting behaves. Confirm MCP stays off in your normal profile | sonnet | medium | game | T-003 | `docs/spikes/mcp.md` holds the real tool list and results. Your normal profile is unchanged |
| T-006 | done | `tools/gamectl.py`: lock file, launch, wait until ready, load level, stop, QA settings (1280x720, audio off, background frame limit off, telemetry off). It may only kill the process it started, and must exit at once if BeamNG is already running | sonnet | high | game | T-005 | Three unattended start and stop cycles pass |
| T-007 | done | `tools/vlm.py`: start the LM Studio server, load Qwen, send an image with a JSON schema, unload. Record `lms load --estimate-only`, video memory and seconds per frame at low and medium reasoning | sonnet | medium | gpu | T-003 | 20 frames return valid JSON. Video memory returns to baseline after unload |
| T-008 | done | Load the game and Qwen together at 1280x720 on low settings. Decide whether a closed-loop local play-test is workable | sonnet | medium | game | T-006, T-007 | Verdict with numbers in `docs/spikes/gpu.md` |
| T-009 | done | `tools/next_task.py <day or night>` prints only the next ready row. `tools/task_state.py` sets a status | sonnet | medium | any | T-002 | Unit tests pass against this file |
| T-010 | done | Loop kit: `.claude/agents/` pinned per model and effort (`builder`, `builder-light`, `architect`, `architect-deep`, `steward`) and project commands `/next-task`, `/qa`, `/triage`, `/truth`, `/vr-review`, `/status` (note: dry runs were manual walk-throughs in a throwaway repo; first real slash-command run still to be confirmed in a new session) | sonnet | high | any | T-004, T-009 | Each command completes a dry run on a dummy task |
| T-011 | todo | Approve the schedules in GUIDE.md section 5 and let Claude create them (note: deferred by you on 10 Nov 2026; unattended running is on hold because of usage limits) | you | - | you | T-010 | Schedules appear in the app |

### M1: First drivable Belfast

Outcome: you have driven a real slice of Belfast and we know what is wrong with it.

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|
| T-101 | done | Review MapNG commit `d605117` before running any of it: network calls, telemetry, bundled data licences, and how it handles UK rules today | opus | medium | any | T-002 | `docs/spikes/mapng.md` ends in go or no-go |
| T-102 | done | Add MapNG as a pinned submodule in `third_party/mapng`, run its unit tests, and wrap `scripts/sweep/exportArea.mjs` in `tools/mapng_export.py` | sonnet | medium | any | T-101, T-003 | Unit tests pass and a sample area exports |
| T-103 | done | Generate `belfast_slice`: a 2048 m square at 1 m per pixel around your test centre, using stock OSM and free elevation, without satellite imagery | sonnet | medium | any | T-102 | Level linter reports 0 errors. Zip installed in the QA profile only |
| T-104 | done | Run MapNG's `runIngameTests` and `driveTest --no-street` on the slice | sonnet | medium | game | T-103, T-006 | Results saved, summary in `docs/spikes/baseline.md` |
| T-105 | todo | Ask Claude to deploy the slice to your normal mods folder, drive it in VR for 15 minutes, and write notes using the template in GUIDE.md section 4 | you | - | you | T-103 | Notes in `feedback/` |
| T-106 | todo | Write the gap report. Decide whether MapNG stays as the generator with patches (expected) or parts need replacing. Re-rank M3 and M4 to match | opus | high | any | T-104, T-105 | `docs/decisions/001-generator.md` written, this file updated |

### M2: Belfast data

Outcome: official heights and widths, and a pinned, repeatable set of inputs.

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|
| T-201 | todo | `config/aoi.yaml`: Slice, City South and Full squares, coordinate systems, and lat/lon to game-world transforms | sonnet | high | any | T-106 | Round-trip tests accurate to under 1 cm |
| T-202 | todo | Pinned OSM: download the Geofabrik extract for Ireland and Northern Ireland, cut the map area, record hash and date | sonnet | medium | any | T-201 | Rebuild is byte-identical. No Overpass calls in the build |
| T-203 | todo | Elevation: download the four Belfast City LiDAR zones (about 1.4 GB), mosaic them, check coverage, fill gaps from the OSNI 10 m model, resample to the level grid and write MapNG's height file | sonnet | high | any | T-201 | Coverage map shows no gaps inside the map area |
| T-204 | todo | Elevation check: compare 2006 LiDAR with newer sources along roads. Flag where the city has changed, using the 2012 York Street LiDAR where useful | sonnet | high | any | T-203 | Changed areas listed, each with a chosen handling |
| T-205 | todo | Ingest DfI Highway Network and Carriageway Details, match them to OSM ways, and output width per way with a confidence score | sonnet | high | any | T-202 | Coverage report for the map area, with a hand-checked sample of 30 ways |
| T-206 | todo | Ingest DfI Controlled Pedestrian Crossings and compare with OSM signals and crossings | haiku | medium | any | T-202 | Mismatch list written |
| T-207 | todo | Overrides system: file schema, apply step, conflict detection when OSM changes, tests | sonnet | high | any | T-202 | Tests pass, including a changed-way conflict |
| T-208 | todo | Generate `CREDITS.md` and in-level credits from a source manifest: OSM (ODbL), Open Government Licence statements, the Cyient Europe note the LiDAR requires, MapNG | haiku | medium | any | T-203 | Every data source has an attribution line |

### M3: Automatic QA

Outcome: every night, a build is tested without you or Claude, and you get a short digest.

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|
| T-301 | todo | Static checks: MapNG's linter plus connection, one-way, height and budget checks against the pinned data | sonnet | high | any | T-103, T-202 | Planted faults are all caught |
| T-302 | done | Route drive suite: adapt MapNG's `driveTest` to named routes around the test centre, with per-frame telemetry | sonnet | high | game | T-104 | Runs unattended and writes metrics JSON |
| T-303 | todo | Junction camera sweep: driver's-eye view from the right-hand seat at 30 m and 10 m before every route junction, at fixed lighting | sonnet | medium | game | T-302 | JPG and pose file for each junction approach |
| T-304 | todo | Image judge: close the game, load Qwen, judge frames against a JSON schema, unload | sonnet | high | gpu | T-303, T-007 | Each frame gets defect type, confidence and reason |
| T-305 | todo | Build a labelled evaluation set of at least 150 frames, half with planted faults. Label with Claude vision, and you spot-check the disagreements | sonnet | high | any | T-303 | Set saved in `qa/eval/` |
| T-306 | todo | Measure Qwen on the evaluation set. Write a trust table per defect type. Fault types under 90% precision go to Sonnet or are dropped | sonnet | medium | gpu | T-304, T-305 | `docs/spikes/vlm-eval.md` with thresholds |
| T-307 | todo | Digest and issue cards: `DIGEST.md` under 60 lines, plus `issues/Q-*.md` with location, metric and image path, de-duplicated between runs | sonnet | medium | any | T-302 | A rerun creates no duplicate cards |
| T-308 | done | Flag-this-spot key: a dev-only extension that logs position, heading, speed and a screenshot when you press a bound key or wheel button in VR | sonnet | medium | game | T-006 | A test flag produces a complete record |
| T-309 | todo | `qa/nightly.ps1`: build, G0 to G4, digest, clean shutdown. Register it in Windows Task Scheduler for 01:00 | sonnet | high | game | T-301, T-304, T-307 | Three unattended nights with nothing left running |
| T-310 | todo | Regression gate: compare each night with the last accepted metrics and block release candidates that get worse | sonnet | medium | any | T-309 | A planted regression is blocked |

### M4: UK road correctness

Outcome: roads read like Belfast roads, and the Slice passes G0 to G4.

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|
| T-401 | todo | `rules/uk_markings.yaml` from the Traffic Signs Manual. Check Northern Ireland's own regulations against the GB values, and add zebra and zig-zag rules | sonnet | high | any | T-106 | Every rule cites its diagram and source |
| T-402 | todo | UK decal materials made by us: centre, lane and warning line patterns, give-way, double yellow, zig-zag and box junction | sonnet | high | any | T-401 | Materials load with no missing textures |
| T-403 | todo | Apply UK markings inside MapNG with the smallest possible patch, kept on a `belfast` branch | opus | xhigh | any | T-102, T-402 | Upstream tests pass, and new tests cover each UK pattern |
| T-404 | todo | Junctions: give-way and stop line placement, kerb radii, roundabouts, mini-roundabouts and signalled junction layout | opus | xhigh | any | T-403 | Static geometry tests pass, and G3 passes on the Slice |
| T-405 | todo | Spike: do osm2streets (Apache-2.0) lane and junction polygons improve complex junctions enough to justify adding them? | opus | medium | any | T-404 | Written verdict with five before-and-after junctions |
| T-406 | todo | Road widths: DfI where matched, then OSM tags, then class defaults for NI | sonnet | high | any | T-205, T-403 | G0 width target is met where DfI data exists |
| T-407 | todo | Left-hand traffic and AI: `rightHandDrive: true`, UK light sequence (red, red and amber, green, amber), one-ways, turn restrictions | sonnet | high | game | T-302, T-403 | AI keeps left and obeys signals on all routes |
| T-408 | todo | Speed limits: mph throughout, with NI defaults where `maxspeed` is missing | sonnet | medium | any | T-202 | Every road edge has a limit |
| T-409 | todo | UK road signs from the Department for Transport sign images (Crown copyright, reproducible if accurate and not misleading), placed from OSM rules | sonnet | high | any | T-403 | Signs face the driver and are readable at 20 m |
| T-410 | todo | Bus lanes, cycle lanes, yellow boxes and kerbside lines from OSM tags | sonnet | medium | any | T-403 | Static checks match the tagged lengths |
| T-411 | todo | Fix the Slice until it passes G0 to G4 | sonnet | high | game | T-404, T-406, T-407, T-408, T-409, T-309 | Three consecutive nightly passes |

### M5: Street truth

Outcome: differences from the real street get found and fixed with evidence.

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|
| T-501 | todo | Create a Mapillary account and developer token. Save it in `%USERPROFILE%\.config\belfast\secrets.env` | you | - | you | - | Token saved outside the repo |
| T-502 | todo | Mapillary coverage report for the map area and routes | haiku | medium | any | T-501, T-201 | Percentage of route junctions with photos from 2020 or later |
| T-503 | todo | Truth fetch: run MapNG's `streetCompare --source mapillary`, keeping images out of git, and pair each with a game frame at the same place and heading | sonnet | medium | game | T-502, T-303 | Image pairs produced for route junctions |
| T-504 | todo | Comparison prompt and schema for lanes, control type and markings, measured on 30 hand-checked pairs | sonnet | high | gpu | T-503, T-306 | Accuracy recorded per field |
| T-505 | todo | Correction policy: what can be auto-applied, what needs Claude and what needs you | opus | medium | any | T-504 | `docs/decisions/002-corrections.md` written |
| T-506 | todo | Fix proposer: turn confirmed differences into override entries with evidence links | sonnet | high | any | T-207, T-505 | A proposed fix applies, and the rebuilt junction scores better |
| T-507 | todo | Re-check only the changed junctions after each fix | sonnet | medium | game | T-506 | Single-junction loop completes in under 10 minutes |
| T-508 | todo | Optional: with your instructor's agreement, record a mounted dashcam on lessons. Your own footage is the best evidence for your routes | you | - | you | - | Footage saved in `H:\BelfastData\dashcam` |

### M6: Test practice

Outcome: the map works as a practice tool.

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|
| T-601 | todo | Learner car: Covet `15le_M` as the level's default vehicle, plus our own L-plate part | sonnet | medium | game | T-411 | Spawns at the driver's seat on the right |
| T-602 | todo | Spawn points at the test centre and around the map | haiku | medium | any | T-411 | Each spawn starts on the road, facing the right way |
| T-603 | todo | Ask your instructor which areas tests tend to use, and note them | you | - | you | - | Notes in `feedback/` |
| T-604 | todo | Six to ten practice routes around the test centre | sonnet | high | game | T-603, T-411 | All routes pass G3 |
| T-605 | todo | Manoeuvre spots: reverse around a corner, turn in the road, reverse park on road and in a bay, emergency stop. Confirm kerbs exist and collide at each | sonnet | high | game | T-604 | Each spot has a spawn and a probe check |
| T-606 | todo | Design practice mode: independent driving following signs or a series of directions, plus faults the game can actually measure (speeding, stop-line overrun, wrong way, kerb strike, stalls) | opus | medium | any | T-604 | `docs/decisions/003-practice-mode.md` written |
| T-607 | todo | Build practice mode as a UI app in mph, with route prompts and a fault log | sonnet | high | game | T-606 | A full route runs and the log is correct |
| T-608 | todo | Traffic and parked cars, tuned for left-hand driving on residential streets | sonnet | medium | game | T-407 | Ten-minute soak with no crashes |
| T-609 | todo | VR pass: comfort, seat position and UI placement | you | - | you | T-607 | G6 passed on the Slice |

### M7: Scale and performance

Outcome: as much of Belfast as VR can carry.

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|
| T-701 | todo | Limits test on 0.40.4: 4096 and 8192 px terrain, DecalRoad count, static object count, AI road graph size, load time | opus | high | game | T-411 | `docs/spikes/limits.md` with safe budgets |
| T-702 | todo | Building strategy: more detail along routes, merged and simple elsewhere | sonnet | high | any | T-701 | Counts within the budget |
| T-703 | todo | Frame-time proxy for VR: fixed route at headset-like resolution, logging CPU and GPU time and draw calls | sonnet | medium | game | T-701 | Repeatable within 5% across two runs |
| T-704 | todo | Grow to City South (4 km) | sonnet | high | game | T-702, T-703 | G0 to G5 pass, G6 by you |
| T-705 | todo | Grow to Full (8 km), if T-701 allows | opus | high | game | T-704 | G0 to G5 pass, G6 by you |
| T-706 | todo | After each BeamNG update, rerun the smoke suite and report what changed | haiku | medium | game | T-309 | Report in `reports/` |

### M8: Release and extras

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|
| T-801 | todo | Private release packager producing `belfast_cubewood.zip` under BeamNG's naming rules, and a deploy script | haiku | medium | any | T-411 | The zip loads with only this mod enabled |
| T-802 | todo | Public release checklist, only if decision 5 changes: no third-party imagery, full credits, ODbL obligations, BeamNG repository rules | sonnet | medium | any | T-801 | Checklist complete and approved by you |
| T-803 | todo | Optional: review `llm-game-automation-mcp` at a pinned commit and build it from source | opus | high | any | T-001 | Written security review ends in go or no-go |
| T-804 | todo | Optional: test it on a QA profile for real gamepad driving, if the review passes | sonnet | medium | game | T-803, T-006 | Report says what it adds beyond the built-in MCP |
| T-805 | todo | Optional: weekly exploratory play-test by Qwen through LM Studio's MCP support, limited to safe tools (note: T-008 found game and Qwen cannot run together; would have to be turn-based) | sonnet | medium | game | T-008, T-306 | One useful report that scripts had missed |

## 8. Guard rails

Every agent follows these. T-002 copies them into `CLAUDE.md`, and T-004 enforces the ones that can be enforced.

1. Never write inside `H:\SteamLibrary\steamapps\common\BeamNG.drive`. It is reference material only.
2. Tests never touch your normal BeamNG profile. A build reaches your real `mods` folder only when you ask.
3. One game process at a time. Exit at once if BeamNG is already running, because you may be playing.
4. Never pass `-mcpremote`. BeamNG's MCP server has no login, so anything on this PC can call it while it is running. Keep it on only during QA.
5. Never push to `main`. Pushing to `dev` needs decision 3.
6. The repository is public. Do not commit secrets, game files, Mapillary images or raw datasets.
7. No Google Maps content in any data, prompt or fix.
8. Every entry in `data/overrides/` names its evidence.
9. Only use Lua functions that exist in the game's `lua/` folder.
10. Nothing is done until it has run in the game with a clean console. Report failures as failures.
11. Read third-party code before running it, and pin it to a commit.

## 9. Risks

| Risk | Effect | Response |
|---|---|---|
| The built-in MCP is not in BeamNG's public docs and could change | QA stops after a game update | T-706 checks each update. EarthNG's file-based bridge is the fallback design |
| MapNG changes quickly and its export code is very large | Patches conflict | Pin a commit, keep patches small, offer general fixes upstream |
| 2006 LiDAR is out of date in places | Wrong heights on newer roads | T-204 finds these, and overrides handle them |
| 4 km or 8 km is too heavy for VR | Smaller map than hoped | Grow in steps with measured budgets |
| Qwen judges images badly | Noise in the digest | T-306 measures it first. Scripts remain the main gate |
| Mapillary coverage is thin | Fewer truth checks | T-502 measures it. Your own dashcam footage fills gaps |
| An unattended agent does something unwanted | Lost work or a broken branch | Guard rails, `dev` only, one task per run |
| Limited RAM (31 GB) and space on C: (67 GB free) | Crashes or a full disk | Run game and model in turn, keep data and caches on H: |

## 10. What has not been verified yet

- BeamNG's built-in MCP has now been run live (T-005, T-006). `load_level` and screenshots are proven on a stock level only.
- MapNG has not been run. Its headless export and tests were read as code only.
- Mapillary coverage of Belfast is unknown until you have a token.
- 8192 px terrain and all VR performance on 0.40.4 are unmeasured.
- Qwen's accuracy on BeamNG screenshots is unmeasured.
- The GB marking values have not been checked against Northern Ireland's own regulations (T-401).
- The Carriageway Details spreadsheet was not opened, and its download link redirects. Its description says it has widths and coordinates.
- Engine figures quoted from BeamNG.Earth were measured on 0.39.4.

## 11. Sources

- BeamNG, read directly from your install: `lua/ge/extensions/mcp/`, `lua/ge/client/parseArgs.lua`, `NOTE_FOR_MODDERS.md`
- [BeamNG level formats](https://documentation.beamng.com/modding/levels/level_formats/), [EULA](https://www.beamng.com/game/support/policies/eula/), [modding guidelines](https://beamng.com/game/support/policies/modding-guidelines/)
- [MapNG](https://github.com/nikkiluzader/mapng), [BeamNG.Earth](https://github.com/TecnaGamer/BeamNG.Earth), [EarthNG](https://github.com/Alezx311/earthng), [llm-game-automation-mcp](https://github.com/CorruptedAng3l/llm-game-automation-mcp), [osm2streets](https://github.com/a-b-street/osm2streets)
- [Maps Experience agent tools](https://developers.google.com/maps/ai/maps-experience-agent-tools), [Google Maps Platform Terms](https://cloud.google.com/maps-platform/terms)
- [Claude: choosing a model](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model), [models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- OpenDataNI: [LIDAR Belfast City 2006](https://www.opendatani.gov.uk/dataset/lidar-belfast-city-2006), [Highway Network](https://www.opendatani.gov.uk/dataset/highway-network), [Carriageway Details](https://www.opendatani.gov.uk/dataset/carriageway-details), [Controlled Pedestrian Crossings](https://www.opendatani.gov.uk/dataset/pedestrain-crossing)
- [nidirect test centres](https://www.nidirect.gov.uk/articles/driving-and-vehicle-test-centres), [about the practical test](https://www.nidirect.gov.uk/articles/about-practical-test-cars)
- [Mapillary terms](https://www.mapillary.com/terms), [OSM copyright](https://www.openstreetmap.org/copyright), [UK traffic sign images](https://www.gov.uk/guidance/traffic-sign-images)
- Road counts come from Overpass API queries on 8 November 2026, for the box 54.5432, -5.9935 to 54.6168, -5.8665
