# belfast_slice (T-103)

First full-size level: a 2048 m square at 1 m/px around the Hospital Road area, built with `tools/mapng_export.py` (see `docs/mapng_export.md`) from MapNG `d605117d77857e91acb8234335e9f89263a94756`. No code changed in the wrapper; it already took `--size` and `--level`. Level zips, areas and caches stay in `H:\BelfastData` and out of git.

## Centre point

Centre used: **54.5428 N, -5.9300 E (approximate)**. The user should confirm it.

The test centre is "4 Hospital Road, BT8 8JL". Looked for in OSM only (Overpass on 2026-11-11, Nominatim):

- Nominatim `q=BT8 8JL` (country GB): empty result.
- Overpass, `addr:postcode=BT8 8JL`: no element. `amenity=driver_training`, `office=government` or a name matching DVA, Driving Test, Hydebank within the Belfast box: no feature on Hospital Road. The DVA centres OSM does have are elsewhere: Dill Road (ways 1263292532 and 1182761977, BT6 9HT, operator DVA) and Balmoral Road (way 1262986364, BT12 6QL, operator DVA).
- Nearest related OSM element: way 272784033, "TransportNI / Rivers Agency Hydebank", `addr:street=Hospital Road`, tagged `demolished:building=public`, centre 54.54221, -5.92878. It is a demolished building, not a test centre, so it is not used as the centre.
- So the centre is the middle of the bounding box of all 22 OSM ways named "Hospital Road" with a `highway` tag (ids 404102952, 860893161, 1419115908, 864619375 and others): lat 54.54017 to 54.54538, lon -5.93318 to -5.92683, middle 54.54278, -5.93001. Rounded to 54.5428, -5.9300.

The slice (about 2.05 km each side) holds all of Hospital Road, whichever end of it the real centre is on. Nothing outside OSM was used: no Google content.

## Bounds

From `meta.json`: north 54.55203, south 54.53356, west -5.94589, east -5.91411. Size 2048 m, 1 m per pixel, heightmap 2048 x 2048 float32 (16,777,216 bytes).

## Command lines

```text
uv run --no-project --python 3.12 python tools/mapng_export.py fetch belfast_slice 54.5428 -5.9300 --size 2048
uv run --no-project --python 3.12 python tools/mapng_export.py export belfast_slice --level belfast_slice
uv run --no-project --python 3.12 python tools/mapng_export.py check H:/BelfastData/mapng/out/belfast_slice.zip
node third_party/mapng/scripts/lintLevel.mjs H:/BelfastData/mapng/out/belfast_slice.zip --no-game --json H:/BelfastData/mapng/out/belfast_slice.lint.json
```

The linter was run with `HOME` pointed at `H:\BelfastData\mapng\home`, cwd `H:\BelfastData\mapng`. (On bash, use forward slashes in the zip path; backslashes are eaten.) Setup: `npm ci --ignore-scripts` in `third_party/mapng` (7 s, cached).

## Inputs

| Input | Source | Date |
|---|---|---|
| Elevation | AWS Terrain Tiles (Terrarium), zoom 15, 16 tiles, 30 m, resampled to 1 m | fetched 2026-11-11 |
| OSM | Overpass `overpass-api.de` (the winner), 4069 elements, 6197 parsed features, 898 roads; OSM data timestamp 2026-11-11 | fetched 2026-11-11 |
| Trees | off (no WorldCover) | |
| Base texture | `BASE=none` (no satellite) | |

Hosts contacted. Fetch: `s3.amazonaws.com` (16 requests), `overpass-api.de` (1), `overpass.kumi.systems` (1). MapNG tried `tiles.overturemaps.org` 4 times after its 45 s Overpass wait; the preload blocked all four (Overture buildings carry Google ML footprints) and OSM data then arrived from `overpass-api.de`. Export: no host contacted, none blocked.

## Result

| Item | Value |
|---|---|
| Zip | `H:\BelfastData\mapng\out\belfast_slice.zip`, 71,594,032 bytes (181 MB unpacked, 2072 entries), sha256 `1528102d...ce8c8` |
| Provenance | `H:\BelfastData\mapng\out\belfast_slice.export.json` |
| Driving side | `mapng_export.py check`: PASSED, `rightHandDrive true`, `region europe`, `country United Kingdom` |
| Linter (`--no-game`) | **0 errors, 2 warnings**; 8467 objects, 4052 roads, 3185 statics, 1039 texture refs |
| Time | fetch about 54 s (45 s of it waiting on the first Overpass mirror); export 13.6 s in node, about 21 s with stub fixing and checks |
| Memory | node peak working set about 2.3 GB, sampled every 3 s (so a floor). Node heap limit was 12 GB |
| Installed | copy at `H:\BeamNG-QA\profile\current\mods\belfast_slice.zip` (identical sha256). Not in the user's profile, not in the Steam folder. Game not started |

Linter warnings (both `shapes`): `buildings/bld_y1_-2.dae` and `buildings/bld_y-2_3.dae` have `collisionType Collision Mesh` but no Colmesh. Two buildings out of the generated set; MapNG's own output, not an input or option we control.

## Road-marking colour

Inside the zip, `art/decals/main.materials.json` has one marking material, `mapng_roadmarkings` (a stock `roadmarkings1` atlas). The 4052 DecalRoad objects use these materials: `m_line_white` 659, `m_line_white_discontinue` 121, `italy_road_markings_zebra` 14, plus asphalt, gutter, edge, dirt and invisible roads. There is no yellow marking material at all. Centre lines are white (continuous or dashed) as for the europe style, not the yellow of the americas style. This agrees with `region europe`. What is not checked: the texture atlas itself (`t_decal_roadmarkings_b.color.png` is a stock game file, not in the zip), and the UK gaps listed in `docs/spikes/mapng.md` section 4 (no give-way lines, hatching, box junctions, yellow kerb lines, mph signs).

## Known problems

- Centre is approximate (see above); user to confirm.
- Terrain is 30 m Terrarium resampled to 1 m: smooth and coarse. LiDAR comes in M2.
- `--no-game` linter run: stock paths (`/assets/...`) are not checked against the game's files, because MapNG's `refs/beamng/content` does not exist here. T-104's in-game load is the real check.
- `info.json` still has `biome "East Coast USA"` (look of the terrain materials); the road rules are UK.
- Trees off; road surface material for `mapng_road_pavers` and signs use stock Italian assets.
- Not loaded in the game; no console check. That is T-104.

## For T-104 (in-game tests)

- The zip is in `H:\BeamNG-QA\profile\current\mods\`. The game's mod manager finds it at start; the level name is `belfast_slice`.
- `levels/belfast_slice/mapng_probes.json` is in the zip, as `runIngameTests.mjs` needs.
- Start with `tools/gamectl.py start` then `load-level belfast_slice`; the MCP server comes up with `-enablemcp` (never `-mcpremote`). `driveTest` must be run with `--no-street`, and `~/.config/mapng/mapng.env` must not exist (HOME redirected).
- The `db.json` in the QA mods folder does not list the zip yet; the game rewrites it on start.
- Level load will be heavy: 8467 objects, 4052 roads. Allow a long `--timeout` for `load-level`.
