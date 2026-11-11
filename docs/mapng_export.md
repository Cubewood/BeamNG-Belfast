# MapNG export wrapper (T-102)

`tools/mapng_export.py` runs MapNG's headless export under the conditions of the T-101 review
(`docs/spikes/mapng.md`). MapNG is the submodule `third_party/mapng`, pinned to
`d605117d77857e91acb8234335e9f89263a94756` and never edited.

## Setup

```text
git submodule update --init third_party/mapng      # checks out the pinned commit
cd third_party/mapng; npm ci --ignore-scripts      # Node 22, npm cache on H: (tools/bootstrap.ps1)
```

`--ignore-scripts` is enough: `sharp` (imported by `polyfill.mjs`) loads from its prebuilt package, and the export ran.
`npm ci` reports 24 audit findings in dev dependencies; nothing here serves or exposes them.

## Usage

```text
uv run --no-project --python 3.12 python tools/mapng_export.py fetch hydebank_sample 54.553 -5.925 --size 512
uv run --no-project --python 3.12 python tools/mapng_export.py export hydebank_sample
uv run --no-project --python 3.12 python tools/mapng_export.py check H:\BelfastData\mapng\out\belfast_hydebank_sample.zip
```

| Path | Content |
|---|---|
| `H:\BelfastData\mapng\areas\<name>\` | `t30.f32`, `osm.json`, `meta.json` (fetch output) |
| `H:\BelfastData\mapng\out\` | `<level>.zip`, `<level>.checks.json`, `<level>.export.json` (provenance: commit, hosts, driving side) |
| `H:\BelfastData\mapng\netlog\` | one line per absolute request: `ALLOW host` or `BLOCK host` |
| `H:\BelfastData\mapng\home\` | empty folder used as HOME, so `~/.config/mapng/mapng.env` is never read |

Exit codes: 0 ok, 1 error, 2 usage or path outside `H:\BelfastData`, 3 submodule not at the pin or modified, 4 driving-side assertion failed, 5 node failed or no zip, 6 area inputs missing.
The zip is never installed anywhere and the game is never started.

## What it enforces

| Review item | How |
|---|---|
| Pin (risk 10) | `check_pin`: HEAD must equal the pinned commit and tracked files must be unchanged |
| Satellite imagery (risk 1) | `BASE=none` always; gametexnode/satellite never run; the preload blocks `server.arcgisonline.com` anyway |
| Google (risk 2) | all `GOOGLE*` variables stripped from the child (also `GPXZ*`, `TRACESTRACK*`, `MAPILLARY*`, `NODE_OPTIONS`, MapNG's own switches); HOME redirected |
| Overture (risk 2) | `mapng_overtureBuildings=false` set by the fetch script; Overture host not on the allow list |
| iNaturalist, WorldCover | `TREEMIX=climate`; `includeTrees:false` unless `--trees` (then only the WorldCover host is allowed) |
| Folders (risk 6) | `MAPNG_AREAS`, `MAPNG_SWEEP_OUT` set explicitly and required to be under `H:\BelfastData` |
| Driving side (risk 3) | preload serves `/country_rules.json`; after the export `roadRules.rightHandDrive == true` and `region == "europe"` in `levels/<name>/info.json`, else exit 4 |
| Network | `tools/mapng_preload.mjs` (loaded with `node --import`) wraps `fetch`: only hosts in `MAPNG_ALLOW_HOSTS` pass. Fetch allows Terrarium (`s3.amazonaws.com`) and the five Overpass mirrors; export allows none |

`info.json` naming: BeamNG's `roadRules.rightHandDrive: true` means left-hand traffic. MapNG sets it from `country_rules.json` (`l: 1` for GB).

## What actually happened on Windows

- `new URL(import.meta.url).pathname` gives `/H:/Claude/...`; `path.resolve` turns it into `H:\H:\Claude\...`. Confirmed:
  - `scripts/sweep/env.mjs` `REPO` is wrong. `AREAS_DIR` and `OUT_DIR` are fine when `MAPNG_AREAS` / `MAPNG_SWEEP_OUT` are set. `REPO` matters only for LIDAR and the satellite texture, both off.
  - `scripts/sweep/fixStubs.mjs` fails with `ENOENT ... H:\H:\...\public\cubemap\skybox0.hdr.dds`. The wrapper does the same job in Python (`fix_stubs`), replacing 9 stub images (6 cubemap faces, `terrain.png`, `preview.png`, plus the facade emissive map), so the zip is not left with 4-byte images. `--no-fix-stubs` turns it off. Two tiny `items.level.json` files stay as they are (empty lists, not stubs).
  - `exportArea.mjs` itself ran without `--fix-stubs`. Its other path use (`REPO`) was not reached.
- Without the preload the headless export fails to read `/country_rules.json` ("Failed to parse URL from /country_rules.json") and falls back to the US profile. Control run on the same inputs: `country levels.common.country.usa`, `region northAmerica`, `rightHandDrive false`, `turnOnRed true`. With the preload: `United Kingdom`, `europe`, `true`, `false`. So the review's inference was right and the preload is needed.
- MapNG's `fetchArea.mjs` throws without a `GPXZ_API_KEY`. The wrapper uses `tools/mapng_fetch_area.mjs` instead: MapNG's 30 m Terrarium path and `fetchOSMData`, written in the layout `exportArea.mjs` reads. No key, no satellite.

## Unit tests of MapNG (`npm test`, Node 22.23.4, pinned commit)

812 tests: 806 pass, 6 fail, 0 skipped. The six failures are all environment, none in export logic:

| Failing test | Cause |
|---|---|
| `every desktop module the app imports is packaged`, `every worker module ... staged`, `the desktop main process and runners compile` | the `H:\H:\` path bug above (they read `desktop/...` through `.pathname`) |
| `tests/noPlaceSpecificCode.test.mjs` (whole file) | same path bug (`scandir H:\H:\...\services\`) |
| `tests/sourceRecorder.test.mjs` (whole file) | `@napi-rs/canvas` is imported by `desktop/runner/browserShims.mjs` but is not in the lockfile |
| `the data page's example is exactly the settings the app sends` | `public/data.html` is checked out with CRLF (Git for Windows `core.autocrlf=true`); the regex expects LF. With LF the regex matches |

Network: the tests are not fully offline. A run with a `--require` hook logging `net` and `dns` showed attempted connections to `api.inaturalist.org` and `esa-worldcover.s3.eu-central-1.amazonaws.com` (443). Overpass and Overture calls in tests are mocked ("Failed to fetch", "unexpected request"). Nothing else was attempted. Run the tests with outbound traffic blocked, or accept those two calls.

## Sample export

Area `hydebank_sample`: centre 54.553 N, -5.925 E (from the task brief), 512 m square at 1 m/px, 30 m Terrarium elevation, OSM from Overpass (fetched 2026-11-11, 964 features, 134 roads). The point is not confirmed from OSM: Nominatim does not find BT8 8JL, and OSM's "Hospital Road" ways lie near 54.545 N, -5.930 E, about 1 km away. T-103 should place the centre properly.

- Hosts contacted: fetch: `s3.amazonaws.com` (4 tiles), `overpass-api.de`, `overpass.kumi.systems`. Export: none.
- Output: `H:\BelfastData\mapng\out\belfast_hydebank_sample.zip`, 34,007,660 bytes after stub fixing (4.1 MB before, when the stubs were 4 bytes).
- `info.json`: `country "United Kingdom"`, `region "europe"`, `roadRules {"rightHandDrive": true, "turnOnRed": false}`. Assertion passed.
- `info.json` still says `biome "East Coast USA"` (regional look), and trees were off. The road-marking style (`white` against `americas`) was not inspected in the zip.
- Not loaded in the game; no console check.
