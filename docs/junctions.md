# Junctions: Give Way and Stop lines, roundabouts, kerb radii, signal layout (T-404)

Build `H:\BelfastData\mapng\t404\out\belfast_slice.zip`: area `t413_slice` (pinned OSM, `gpxz`), MapNG patches 0001 to 0009 (`tools/mapng_export.py export t413_slice --dem gpxz --level belfast_slice`, MapNG commit `ef986b0`). New here: patch 0008 (UK junctions, `services/ukJunctions.js`) and patch 0009 (road snapping keeps shared nodes). Sources: `rules/uk_markings.yaml` (TSM 3, TSM 5, TSM 6, TSRGD 2016, SR 1997/386) and, for kerb radii, Creating Places (DOE NI / DRD, 2000), Table 4 (printed p. 123) and paras 16.25 to 16.28 (p. 124). Nothing from Google.

## What is placed, and where

**Which arm gives way** (`impliedGiveWays`, `priorityAtJunction`). At every junction node with three or more public arms (service roads, driveways, parking aisles and tracks neither get a line nor count), with no signals: the priority road is the pair of arms traffic can run through with the highest class, the same road (name or ref) before two different ones, then the straightest pair. Every other inbound arm gives way, except the other carriageway where a dual carriageway starts (same road, one-way, the pair holds its two-way end) and a one-way arm merging within 35 degrees. On a roundabout the ring has priority and every entry gives way. An arm OSM already controls (give-way or stop node) keeps OSM's approach; where OSM's control sits on an arm this rule gives priority, the junction is left as OSM has it (3 junctions in the Slice). "Not normally used on minor residential roads with low speed and good visibility" (TSM 3, `giveway_1003a` notes) is not applied: the learner meets lines at every T, as on most Belfast estates.

**Where the line goes** (`junctionMouth`, `lineSpan`). Walking out along the minor arm, the first spot where the line's near edge, across its whole span, is 0.1 m clear of every carriageway crossing the arm (TSM 3 2.3.1 to 2.3.4: "the line edge nearest the major road continues the major road edge"); 1003A and 1003.1 are sited the same way. Square to the minor road. Kerb to centre line on a two-way road (the centre line moved by uneven `lanes:forward` / `lanes:backward`), full width on a one-way road or where kerb to centre is under 2.75 m (TSM 3 2.3.2). The arm's own carriageway (a bend in it) never counts as the road it meets. The 1009A edge line (600/300, 100 mm) runs across the rest of a two-way mouth, flush with the line's near edge; the triangle 1023A stays 2.4 m behind the line in each approaching lane, dropped where it would reach the road at the arm's far end. No line where none fits off the carriageways (a link inside a junction), in a dual carriageway's gap with less than 5 m behind the line (our figure: a car's length and a margin), or on service roads (drawn as paving, so there is no edge to site a line at or width to size it by: T-403 painted 7 such OSM lines at a guessed width).

**Roundabouts**: 1003.1 on every entry, `uk_dash_1000_1000` (1000/1000), 200 mm, 300 mm over 40 mph (TSM 5 6.2.1), no triangle. Both Slice rings get it: C302 at Newtownbreda (3 entries) and the 3 m residential ring at (-12, 683) (3 entries). That ring's island is under 4 m, where TSM 5 6.2.2 would use 1003.3; its dash pattern is `verified: false` in the YAML, so 1003.1 is used. **Mini-roundabouts and box junctions**: none in the Slice (`docs/left_hand_traffic.md`), out of scope; a give-way at a `highway=mini_roundabout` node stays unpainted.

**STOP signs**: none in the Slice. 1002.1 (400 mm) goes to the mouth like a Give Way line; the AI's stop instance stays at MapNG's stop point (behind it).

**Signals**: 1001 stays at the signal's stop point, where the AI stops (`signals.json` instance `pos`), kerb to the (lane-split) centre line, 200 mm, 300 mm from 40 mph.

**Patch 0009.** `snapRoadsToTerrain` faded its offset to zero only at its 2 m samples, so a junction node in the middle of a moved way moved a few centimetres (Bests Hill at Bests Hill Lane: 0.11 m) off the way joining it, and every graph keyed by node lost the junction: 19 of 909 junction nodes on the Slice, so no line at Bests Hill Lane, Greer Park Drive, Haddo Woods, Woodland Manor and others. Shared nodes now stay exactly.

Result on the Slice (`uk_*` DecalRoads in the zip): 154 Give Way lines 1003A (T-403 build: 35), 143 triangles, 151 edge lines 1009A, 6 roundabout lines 1003.1 (0 before), 28 signal Stop lines.

## Kerb radii

`UK_CORNER_RADII` (Creating Places Table 4, 16.27): 6 m where residential and unclassified streets meet, 4 m on living streets, 10 m with tertiary roads and up (Creating Places names nothing larger; trunk roads take 10 m too), by the larger road's class. Where the full radius does not fit, a metre less at a time down to 4 m, then MapNG's half and quarter. Before: MapNG's table (3 m residential, 4.5 m tertiary, 6 m secondary, 8 m trunk), median 3.0 m, 244 of 336 corners under 4 m.

After (226 square-ish corners judged): median 6.0 m, 1.5 to 10.0 m, **20 under 4 m** (11 at 3 m, 1 at 2.5 m, 8 at about 1.5 m). Traced at Drumart Drive (20, 823.8): the corner takes 6 m with the streets alone; with the buildings, MapNG splits the sidewalk where a house stands close into a slab run and a short curb-only piece, pairs each into its own return, and the short pieces' return falls to half the radius, standing 1.1 m nearer the junction than the 6 m one. Left for a follow-up (sidewalk splitting, MapNG's `joinCorners`), not done here.

## Signalled junctions (measured, not changed)

28 signal Stop lines at 9 junction clusters and crossings. Gap from the line's near edge to the crossing carriageway: median 5.9 m; 3 inside it (Milltown Road / Belvoir Road at (-96, 382), -0.15 m; Old Milltown Road / the unclassified link at (-782, 572) and (-778, 573), -0.26 and -1.49 m), 2 more than 15 m back (behind crosswalks), 2 with no crossing carriageway within 20 m (midblock heads on Milltown Road and Belvoir Road). Primary head: MapNG stands it at the stop line, so 23 of 24 measured heads are less than 1.5 m past the line (median 0.35 m; TSM 6 4.2.1 asks for at least 1.5 m, 2.5 m preferred). Every line has its head in `signals.json` and every head its line; `pipeline/signals_fix.py` still gives the 6 one-axis lights a sequence. Changing these means moving the AI's stop points and the heads, which T-407's red-light measurement would need to repeat: left.

## Static geometry tests (definition)

"Static geometry tests pass" for T-404 means all three:

1. **MapNG's own tests**: `node --test tests/*.test.mjs` in `third_party/mapng` passes except the six Windows environment failures of T-102 (`docs/mapng_export.md`): `every desktop module the app imports is packaged`, `every worker module ... staged`, `the desktop main process and runners compile`, `tests/noPlaceSpecificCode.test.mjs`, `tests/sourceRecorder.test.mjs`, `the data page's example is exactly the settings the app sends`.
2. **Unit tests of the new behaviour**: `tests/ukJunctions.test.mjs` (priority rule, implied give-ways, line span, mouth placement, bend, self-meeting way, dual carriageway gap, roundabout 1003.1, UK kerb radii and their tries), `tests/roadSnap.test.mjs` (a shared node mid-way stays), and `tests/static_checks/test_junctions.py` (the check itself).
3. **The level check**: `uv run --no-project --python 3.12 --with numpy --with shapely python -m qa.static_checks --zip <zip> --only junctions` reports none of the placement kinds below, and no `kerb-radius-small` / `kerb-radius-large` (low `junction-line-on-priority-road` findings, OSM's own controls, do not count; `signal-*` findings are measurements for item 5).

| Assertion | Finding | Tolerance | Source |
|---|---|---|---|
| near edge at the mouth | `junction-line-on-road` (high) under -0.05 m, `junction-line-off-mouth` over 1.0 m | -0.05 to 1.0 m from the edge of the carriageway it meets (roads within 26 degrees count as its own) | TSM 3 2.3.1 to 2.3.4, Figure 2-5 (`stop_1002_1`, `giveway_1003a`, `giveway_roundabout_1003_1`); the 1.0 m is ours |
| square | `junction-line-not-square` | within 10 degrees of square to its road | TSM 6 4.2.1 (signal line at right angles); 10 degrees ours |
| width | `junction-line-span` | kerb end within 0.6 m of the kerb and not past it; two-way: inner end at the centre line (up to 2.5 m off the axis for uneven lanes), full width under 2.75 m or one-way | TSM 3 2.3.2, TSM 6 4.2.1; 0.6 m = MapNG's 0.3 m inset plus 0.3 m |
| none on the priority road | `junction-line-on-priority-road` (high) | across an arm the rule gives priority, within 3 m of it | the rule above, recomputed from OSM |
| every minor arm and roundabout entry has one | `junction-mouth-unmarked`, `roundabout-entry-unmarked` (high) | a line across the arm within 20 m of its node; arms with no room (full width clear by 0.45 m, or 5.7 m in a dual carriageway gap) are counted, not failed | TSM 3 3.1, TSM 5 6.2.1 |
| kerb radius | `kerb-radius-small` under 4 m, `kerb-radius-large` over 10.5 m | 4 to 10.5 m; corners turning 60 to 120 degrees whose roads run 25 m or more to the next junction | Creating Places Table 4 (4 m minimum, 6 m, 10 m; 90 degree entry; 25 m spacing), 16.26 |

The check also lists signal Stop lines (`signal-line-in-junction` under 0.5 m from the crossing carriageway, `signal-line-far` over 15 m, `signal-line-no-head`, `signal-head-no-line`, `signal-head-close` under TSM 6's 1.5 m).

### Result (18 November 2026)

1. MapNG tests with patches 0001 to 0009: 855, 849 pass, 6 fail, exactly the six above (`H:\BelfastData\mapng\t404\upstream_tests.log`).
2. `tests/ukJunctions.test.mjs` 11 of 11, `tests/roadSnap.test.mjs` 7 of 7 (the new one fails on the unpatched file), `tests/static_checks/test_junctions.py` 6 of 6; `tests/static_checks` and `tests/mapng` patch and marking tests 84 of 84.
3. `--only junctions` on the build (`H:\BelfastData\mapng\t404\static_final\findings.json`): Give Way lines 154, near edge 0.10 to 0.20 m from the road met (median 0.14); roundabout lines 6, 0.11 to 0.18 m; no line off square, short, on a road or on a priority arm (3 `low`: OSM's own give-ways at Old Milltown Road and Belvoir Drive, kept); minor arms 150 of 154 marked, 4 with no room; roundabout entries 6 of 6. Kerb radii: 20 of 226 judged corners under 4 m. **Gate: fail**, `{"kerb-radius-small": 20}`. So item 3, and with it the static geometry tests, **fail on the kerb radii only**.

The full static check on the build against the I-007 build: `terrain-step` 98 and 98 (high 16 to 15), `kerb-height` 2 to 1, `road-disconnected` 5 to 4, `oneway-missing` 1 to 0, `road-height` 9 to 8; nothing new outside the junction kinds.

**G3** (`qa/drive_suite.py run --gate --allow-daytime`, the build installed in the QA profile, run `H:\BelfastData\qa\drive\2026-11-18_133155`): 10 of 10 routes completed, G3 pass, 595 s, game stopped. Routes matched the live graph (no rebuild needed). Events: 2 `scrape` on `lady-ishbel-loop-reverse`: the known one at (-194, -146) and a new, tiny one at (-249, -138) (damage reading 240, `beamstate` damage 0.1, no part, no body zone, 4.8 m/s in a left turn). The kerb faces within 3 m of that path are the same in both builds, so it is not a kerb this task moved; not explained. Console: no error or warning from the level; two engine errors (Steam online provider, the throwaway profile; display enumeration, `engine::Platform::Win32`), warnings only slow-extension, swap-chain, licence-plate and the suite's own globals. No frames were captured, so no model judgement.

## Left

- **Kerb radii under 4 m (20 corners)**: the sidewalk split by a building near a corner (above). Needs a change in MapNG's sidewalk runs (`joinCorners` pairing a short curb-only piece); it touches T-406's parked patch 0010 (kerb ends a corner radius back), which reads the same `CORNER_RADII` table and should read `UK_CORNER_RADII` once both land.
- **Signal layout** (above): 3 stop points inside the crossing carriageway, heads less than 1.5 m past the line. Needs the AI stop points and heads moved and T-407's red-light run repeated.
- Not measured: where each minor road's warning line 1004 ends against its Give Way line (MapNG's own trim, unchanged); 1010 at lay-bys and accesses; NI's half-size Give Way variant (not used).
- Not seen in game: the new lines and returns were driven over (G3) but not looked at; a VR look or judged frames at a few mouths, the C302 entries and a 10 m corner would confirm the paint reads right.
