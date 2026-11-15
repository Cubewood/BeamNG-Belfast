# Left-hand traffic and AI rules (T-407)

Does the AI keep left, circulate roundabouts clockwise, respect one-ways and stop at red, and are the signals the UK sequence? Measured on the I-007 build (`H:\BelfastData\mapng\i007\out\belfast_slice.zip`, MapNG patches 0001 to 0007), fixed in the T-407 build (`H:\BelfastData\mapng\t407\out\belfast_slice.zip`, I-007 plus `pipeline/signals_fix.py`). Game 0.40.5.0, QA profile, one launch each, 15 November 2026. Raw runs (not in git): `H:\BelfastData\qa\ai_rules\t407_i007\`, `t407_fixed\`, drive suite `H:\BelfastData\qa\drive\`.

## Inventory (what the slice has)

| Item | In OSM (pinned slice cut / MapNG `osm.json`) | In the level |
|---|---|---|
| Traffic signals | 26 `highway=traffic_signals` nodes (25 `signal`, 1 `crossing`) | `signals.json`: 28 heads, 14 controllers (all `lightsBasicEU`), 5 sequences (clusters with two axes). 6 heads in 4 clusters had no sequence |
| One-way roads | 88 highway ways `oneway=yes` in the pinned cut, 81 of them vehicle roads in MapNG's `osm.json` (plus the 2 roundabouts, one-way by definition: 83) | 81 of 530 navigation DecalRoads carry `oneWay`, none `flipDirection`; game graph: 236 one-way edges |
| Roundabouts | 2 ways: C302 at Newtownbreda (centre (462, 871), radius 11 m, 2 lanes) and a 3 m residential ring at (-12, 683) | both drawn one-way, clockwise. The big ring is 11 one-way edges in the game graph |
| Turn restrictions | none: 0 `type=restriction` relations (the cut holds 15 relations: 7 multipolygon, 6 boundary, 1 waterway, 1 route). `turn:lanes` is on 14 ways, that is lane arrows, not a restriction | nothing to build. MapNG's `osm.json` does not carry relations at all, so a restriction in a bigger area would need a new step |
| Mini roundabouts | none (`highway=mini_roundabout` 0) | none |

## Measurements

| Item | Result | How |
|---|---|---|
| `rightHandDrive` | the level says `roadRules {rightHandDrive: true, turnOnRed: false}`; the vehicle's `mapmgr.rules` says the same | `info.json`; `ai_rules.py` reads `mapmgr.rules` in vehicle Lua |
| AI keeps left | lane-keeping graph AI: 0 wrong-side samples on 8 of 10 suite routes; the other two have 9 and 27 samples 0.3 to 1.0 m right of the route line at four junction corners at 6 m/s (corner cutting on right turns, the longest run 22 samples at (-311, -97)). AI traffic mode (its own choice of lane, 14 cars): 920 to 1174 samples left of the road centre, 0 to 4 right, 34 to 41 within 0.3 m | `qa/drive_suite.py` (T-311 lane layout), `ai_rules_lib.lane_side` |
| Roundabout circulation | clockwise on all three circuits round C302: swept -146, -239, -229 degrees (the graph's own shortest paths give -123, -238, -224) | `roundabout-*` routes in `qa/routes/belfast_rules.json`, `ai_rules_lib.swept_angle_deg`. The 3 m ring was not driven |
| One-ways | 0 of 1922 (I-007 run) and 0 of 1923 (T-407 run) moving samples on one-way edges ran against them (10 suite routes, 3 circuits, 14 traffic runs each). Statically: 0 findings after the check was corrected (below) | `ai_rules_lib.wrong_way`; `qa/static_checks` `oneway` |
| Light order | all 28 heads follow red, red and amber, green, amber, red in 817 s of recording; 0 bad changes; durations red and amber 1.5 s, green 12 s, amber 4 s, red 1.5 s own plus 19 s while the other axis runs (20.5 to 21.1 s together); a one-phase head is red 3 s of every 19 | game-side logger of every head's state, `ai_rules_lib.check_order`. The game's `lightsBasicEU` type lists exactly this order (`settings/trafficSignals.json:151`) |
| Two axes never open together | 0 conflicts in the 5 two-axis sequences, 4 more one-phase | `ai_rules_lib.sequence_conflicts` |
| AI at a red light | the I-007 build: 5 of 5 heads reached on a red stopped 0.7 to 3 m before the head and moved off 1.4 to 2.2 s after green. The T-407 build: 6 of 6 stopped (0.7 to 4.1 m); 5 moved off 1.3 to 2.4 s after green, the sixth was still waiting when its 45 s ended; no car crossed a red | one fresh Covet per head, started 18 to 94 m up the road (a stretch with no junction, so the AI cannot turn off), released while the head is red and stays red for 9 s more, AI traffic mode, up to 45 s |

Drive suite on the T-407 build (`H:\BelfastData\qa\drive\2026-11-15_180836\`): 10 of 10 routes completed, G3 pass, no console error; 0 of 1639 one-way samples against the road; wrong-side samples as above (10 and 27, junction corners). Console of the `ai_rules.py` runs: one `Hook discarded: FilteredInputChanged` line (a UI hook, not our Lua).

## Found and fixed

1. **Six lights stayed red for ever (high).** MapNG gives a cluster a sequence only when it has two axes or a protected turn (`services/beamngSignals.js:875`). A one-axis cluster has none, and the game gives a light with no sequence its controller's default state, which for `lightsBasicEU` is red (`core/trafficSignals.lua`, `SignalInstance:setActive`). Heads 27, 28, 42, 43, 45, 47 at (-199, 438), (-182, 422), (-425, 499), (-468, 504), (-221, 457), (-438, 501). In game: the I-007 build's cars stopped at heads 27 and 42 and waited the whole run (32 s and 35 s) with no change in 596 s of recording. Fix: `pipeline/signals_fix.py` gives each such controller a one-phase sequence (same structure as the two-phase ones, the controller's own 19 s cycle), run by `tools/mapng_export.py` after MapNG writes the zip. The T-407 build: all 28 heads cycle, a car crossed head 27 and 42 on green at 17.5 m/s. This is a level-side repair of what is really a MapNG gap; if it is patched upstream the step finds nothing to do. The new timing of those lights is MapNG's (3 s red of every 19 s), not a measured pedestrian-crossing cycle.
2. **`oneway-missing` false positive.** OSM way 1410878962 (19 m, one-way) is drawn one-way in the right direction, but the check took the two-way road that ends at the same node. The check now looks at every road that fits a sample and is satisfied by a one-way one running the right way (`LineIndex.parallel_all`). The I-007 build gives 0 findings, the test with only the two-way road still gives one.
3. **No signals were loaded by the QA level start.** `core_trafficSignals` is a preset extension that freeroam loads (`main.lua` `presetExtensions`, `freeroam.lua:38`); the MCP `load_level` does not. `ai_rules.py` loads it and calls `loadSignals(nil, true)` (the player's freeroam state). Drives that must see lights need the same.

## Limits found (not fixed)

- **Two signals close together.** In the I-007 run a car passed head 33 on green and ran head 39 (red for 9.7 s, 13.6 m/s) 25 m further on: the AI only looks for the next controlled junction once it is 20 m past the last (`ai.lua:4830`, `distSq > 400`), so it cannot brake for a second light within about 20 m plus its braking distance. In the T-407 run the car stopped at head 33 first and then stopped at head 39. This is the game's AI; the level has several such pairs (the Milltown Road dual carriageway). Not repairable in the level data.
- **Traffic mode picks its own turns**, so some approaches never reached the head they were built for: the I-007 run reached 6 of 14 heads, the T-407 run 9 of 14. Heads reached are judged, the others only counted.
- Amber is 4 s and all-red 1.5 s (MapNG's); UK amber is 3 s at 30 mph, 4 s above. Left alone.

## What stays unchecked, and why

- Turn restrictions: none exist in the slice; a larger area must be checked again, and MapNG does not read restriction relations.
- The 3 m ring at (-12, 683): the static check finds it drawn clockwise and one-way; no car was sent round it (a ring that small is a mini roundabout to a driver).
- Give way and stop signs under AI: 37 give-way nodes exist and the game's AI handles `giveWay` itself; the suite records no give-way judgement.
- Pedestrian crossings, turn signals, right turns across traffic: no other cars were present in any run.
- A second run of each measurement: every figure is from one launch, so a number near a threshold could move.

## How it stays measured

- `qa/static_checks` (`--only signals roundabouts oneway`): `signal-never-changes`, `signal-not-uk-order`, `signal-short-phase`, `signal-missing`, `roundabout-osm-anticlockwise`, `roundabout-not-drawn`, `roundabout-two-way`, `roundabout-wrong-way` (`docs/static_checks.md`). On the I-007 zip: 6 `signal-never-changes`; on the T-407 zip: none.
- `qa/drive_suite.py`: every route result now has `one_way` (moving samples on one-way edges and how many ran against them); not part of G3.
- `qa/ai_rules.py run`: roundabout circuits, signal timeline, AI at lights, traffic lane side, one-way; `analyse DIR` re-judges a recorded folder without the game. About 14 minutes.
- Tests: `tests/qa/test_ai_rules.py`, `tests/static_checks/test_signals_roundabouts.py`, `tests/mapng/test_signals_export.py`.
