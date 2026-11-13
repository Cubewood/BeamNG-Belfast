# Speed limits in mph (T-408)

Every road way that cars can use gets a speed limit in mph before MapNG reads the OSM data. MapNG turns it into
`speedLimit` (m/s) on the DecalRoad of each carriageway; the game's AI and any limit warning read that.

| Thing | Where |
|---|---|
| Step | `pipeline/speed_limits.py` |
| Called by | `tools/mapng_export.py`: after `fetch`, after `pinned-osm`, and again at the start of `export` (safe to repeat) |
| Static check | `qa/static_checks` kind `road-no-speed-limit` (high) and `road-speed-not-mph-step` (low), `--only speed` |
| Tests | `tests/pipeline/test_speed_limits.py`, `tests/static_checks/test_static_checks.py` |
| Report of a run | `<areas>/<name>/speed_limits.json` (counts by class; not in git) |

```text
uv run --no-project --python 3.12 python pipeline/speed_limits.py report H:\BelfastData\mapng\areas\belfast_slice_pinned\osm.json
uv run --no-project --python 3.12 python pipeline/speed_limits.py apply  H:\BelfastData\mapng\areas\NAME\osm.json --report out.json
```

## What MapNG does with it (pinned commit)

- `services/beamngMapNavigation.js` `parseMaxspeedMps` reads only `tags.maxspeed` and gives m/s. It accepts
  `"30 mph"`, bare numbers (as km/h) and `XX:zone30` (as km/h, a mistake for a UK zone). Anything it cannot parse
  gives no limit, and BeamNG then works one out itself.
- `services/exportBeamNGLevel.js:2693-2695` (`getDecalRoadNavMetadata`) writes `speedLimit` on the drivable base layer
  only: the invisible `_ai` road of each carriageway, and body roads with no twin. These are the navigation roads
  (`drivability` above 0), 537 on the Slice. That set is what "road edge" means in this task.
- So the step writes `maxspeed = "N mph"` in the one form MapNG reads correctly. The OSM text moves to
  `maxspeed:osm` when it differed. Nothing in MapNG is edited.

Tags the step adds to each car road: `belfast:speed_source` (`tagged`, `tagged_type`, `tagged_directional`,
`inherited`, `default`) and `belfast:speed_rule`. Ways cars cannot use (footway, path, steps, cycleway, pedestrian,
bridleway) get `belfast:speed_source = "n/a"` and are left alone.

## Rules

Order for each car road way: its own tags, then a tagged neighbour, then the national default.

1. **Tagged.** `maxspeed` in mph or km/h (km/h is converted and rounded to the nearest 5 mph); a bare 20, 30, 40, 50,
   60 or 70 is read as mph (a unit left off in Northern Ireland; OSM's own reading would be km/h, which no limit
   there uses); `walk` is 5 mph; a list takes its lowest entry. Then `maxspeed:forward` and `:backward` (the lower),
   then `maxspeed:type`, `source:maxspeed`, `zone:maxspeed`, `zone:traffic` when they name a number
   (`GB:nsl_restricted` 30, `GB:nsl_single` 60, `GB:nsl_dual` 70, `GB:motorway` 70, `GB:20`, `zone30`, `20 mph zone`).
   `none`, `signals`, conditional text and anything else do not count and fall through.
2. **Inherited.** An untagged way with a name takes the limit of tagged ways of the same name and class that meet
   it at an end, when those agree. A limit changes at a junction, not where OSM split a road in two.
3. **Default**, by class:

| Class | Default | Why |
|---|---|---|
| motorway, motorway_link | 70 | national limit on a motorway |
| trunk, primary, secondary, tertiary, unclassified, road (and their links) | 30 if `lit=yes` or built up; else 70 on a dual carriageway; else 60 | restricted road, then dual carriageway, then single carriageway |
| residential | 30 | residential streets are built up |
| living_street | 20 | our choice (home zone); no national limit |
| service | 20 | our choice for the AI: private land with no national limit |
| service=driveway, parking_aisle, drive-through | 10 | our choice |
| track | 20 | our choice; not a public road |

"Built up": at least 4 building footprints within 50 m of at least half the points sampled every 30 m along the way
(buildings come from the same osm.json). "Dual carriageway": `dual_carriageway=yes`, or a one-way motorway, trunk or
primary way that is not a roundabout or a link.

The national figures are the ones in the speed-limit table of the Highway Code for Northern Ireland (nidirect.gov.uk):
30 mph on a restricted road (lamp-lit, built up), 60 mph on a single carriageway, 70 mph on a dual carriageway or
motorway, for cars; the law is the Road Traffic Regulation (Northern Ireland) Order 1997. I cite the table and the
Order by name only. No network was used in this task, so the rule number and article are not looked up and not
quoted; a person should check the table against the live Highway Code before a driving-test rule leans on it.
Northern Ireland has no national 20 mph or 40 mph limit: those exist only where signed, which is why they come
from OSM tags or not at all.

## The Slice (belfast_slice_pinned, pinned OSM of 2026-11-12)

524 car road ways (OSM ways cut into segments; 51.4 km). Counts are road segments as MapNG receives them; `n/a` ways
(footway 135, path 154, steps 13, cycleway 1) are not listed.

| Class | Ways | km | tagged | via type or zone | via direction | inherited | defaulted | limits (mph: ways) |
|---|---|---|---|---|---|---|---|---|
| residential | 160 | 17.12 | 121 | 0 | 0 | 0 | 39 | 30: 160 |
| secondary | 23 | 3.57 | 23 | 0 | 0 | 0 | 0 | 40: 13, 50: 5, 60: 5 |
| secondary_link | 4 | 0.21 | 4 | 0 | 0 | 0 | 0 | 40: 4 |
| service | 231 | 16.3 | 3 | 0 | 0 | 0 | 228 | 10: 103, 20: 125, 30: 3 |
| tertiary | 16 | 2.46 | 16 | 0 | 0 | 0 | 0 | 30: 6, 40: 8, 50: 1, 60: 1 |
| tertiary_link | 1 | 0.03 | 1 | 0 | 0 | 0 | 0 | 40: 1 |
| track | 28 | 3.2 | 0 | 0 | 0 | 0 | 28 | 20: 28 |
| trunk | 30 | 3.27 | 30 | 0 | 0 | 0 | 0 | 40: 12, 50: 18 |
| trunk_link | 8 | 0.45 | 8 | 0 | 0 | 0 | 0 | 30: 2, 40: 2, 50: 4 |
| unclassified | 23 | 4.79 | 23 | 0 | 0 | 0 | 0 | 30: 9, 40: 11, 60: 3 |
| **all** | 524 | 51.4 | 229 | 0 | 0 | 0 | 295 | |

By length 29.2 km of 51.4 km (57%) is tagged, 43% defaulted. Every untagged way is a residential street, service
road or track; the public network (trunk down to unclassified) is fully tagged, so the rural, dual-carriageway and
built-up rules did not run on real data on the Slice (unit tests only). Before the step 229 of 524 had a limit; after,
524 of 524.

## Verified

- osm.json: `pipeline/speed_limits.py report` on the pinned `osm.json`: 524 car road ways, 0 without a limit.
- **Exported level**: a full MapNG export from that osm.json (`tools/mapng_export.py export`, area copy in
  `H:\BelfastData\mapng\t408`, level `belfast_t408`, zero hosts contacted, driving-side check passed). In the zip's
  `Decal_Roads` files, every DecalRoad with `drivability` above 0 (537) has `speedLimit`: 537 of 537, 0 missing, as
  mph 20: 35, 30: 377, 40: 60, 50: 40, 60: 25. The same check on the earlier export of the same area
  (`belfast_slice_pinned.zip`, no step) found 100 of 537 navigation roads without a limit. `qa/static_checks --only speed`
  gives 0 findings on the new zip and 100 `road-no-speed-limit` on the old one.
- Not verified: nothing was loaded in the game, and no AI drive ran. That the game's AI actually holds the
  limit is not shown.

## Side effects and limits

- **Signs (for T-409).** MapNG's sign code reads the same `maxspeed` (`services/roadSigns.js:217` and `:230`,
  and `services/curveWarningSigns.js:169`), so a defaulted limit that differs from a mapped neighbour counts as a
  limit change. Today these are all skipped as an unit mismatch (the export log says `speed_30mph 22` not placed,
  was 9 before the step), so nothing wrong is drawn. When T-409 adds a UK sign set that places mph signs, it needs a
  small MapNG patch so that only mapped limits make signs: in `roadSigns.js` at line 217 (`const speed =
  parseSignedSpeed(road.tags?.maxspeed)`) and line 230 (`const other = parseSignedSpeed(e.road.tags?.maxspeed)`),
  and in `curveWarningSigns.js` line 169, ignore a road whose `tags['belfast:speed_source']` is `default` or
  `inherited`. That patch is not made here (MapNG is pinned and unedited); the tags are in the osm.json for it.
- A road drawn as several segments with different limits is possible (OSM can tag each way differently); the limit
  follows the way, as MapNG draws it.
- The built-up test is rough. Checked against the tagged public roads of the Slice (trunk to unclassified, own
  `maxspeed` hidden), it calls 15 of the 59 roads signed 40 mph or less built up, and 1 of the 9 signed 60 mph. Roads
  signed 40 to 50 mph often have no 4 houses within 50 m of half their length, so a defaulted unlit trunk or
  unclassified road may get 60 where a sign would say 40. It does not run on the Slice. Tune
  `BUILT_UP_*` in `pipeline/speed_limits.py` against a larger tagged area (`city_south`) when the build grows.
- Variable and conditional limits (school times, `maxspeed:conditional`) are ignored; the base limit is used.
- Roads at the edge of the square lose buildings to MapNG's clip, so the built-up test leans rural there.
