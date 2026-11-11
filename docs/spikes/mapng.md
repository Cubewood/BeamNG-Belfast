# Spike T-101: MapNG review at d605117

Question: is MapNG safe and fit to generate the Belfast level, and what must later tasks (T-102 submodule + unit tests, T-103 exportArea wrapper, in-game tests) do about it?

Method: read-only review. Cloned https://github.com/nikkiluzader/mapng into `H:\BelfastData\spikes\mapng-review\mapng`, checked out the pinned commit. Nothing was installed or run (no `npm`, no node, no tests). Paths below are relative to the MapNG tree.

| Item | Value |
|---|---|
| Commit | `d605117d77857e91acb8234335e9f89263a94756` (exists; short hash matches) |
| Date / author | 2026-11-07 19:25:37 -0500, Nikki Luzader |
| Subject | "MapNG 0.2.11: version, update page (bridges built by rules ...)" |
| Upstream HEAD at review | `e15442b` (2026-11-11), 4 days newer; not reviewed |
| Size | 517 tracked files; 117 unit tests in `tests/` |

## 1. Network calls

Headless path = `scripts/sweep/fetchArea.mjs` (download) then `scripts/sweep/exportArea.mjs` (build). Browser UI = Vue app + Cloudflare Worker (`worker.js`); we do not use it.

| Host | Purpose | Code | Headless? | Can switch off / replace |
|---|---|---|---|---|
| api.gpxz.io | Elevation (paid key) | fetchArea.mjs:69,89-90 | fetchArea | `T30_ONLY=1` skips the calls (fetchArea.mjs:64), but fetchArea.mjs:33-34 still throws without a non-empty `GPXZ_API_KEY` |
| s3.amazonaws.com/elevation-tiles-prod (Terrarium, ~30 m) | Elevation | fetchArea.mjs:113; terrain.js:46 | fetchArea | Replace by writing `t30.f32` + `meta.json` ourselves from a local DEM |
| Overpass mirrors: lz4.overpass-api.de, overpass-api.de, overpass.private.coffee, overpass.kumi.systems, overpass.openstreetmap.ru | OSM | overpassQuery.js:12-17; osm.js:823 | fetchArea | Local `.osm` file via `loadOsmFileText` (osmFile.js:287) wins over every server (osm.js:1034-1041); or own Overpass via `TRACESTRACK_API_URL` (fetchArea.mjs:133-137) |
| tiles.overturemaps.org (release 2026-10-21.0) | Extra building footprints/heights; OSM fallback in outages | overtureTiles.js:30-31; osm.js:969-1010, 1150 | fetchArea (default ON) | `localStorage mapng_overtureBuildings="false"` (osm.js:23-30); fallback runs only if Overpass fails (local file avoids it) |
| esa-worldcover.s3.eu-central-1.amazonaws.com | Land cover for tree placement | worldCover.js:14,77; exportBeamNGLevel.js:10632 | exportArea (default ON, includeTrees) | `EXPORT_OPTS='{"includeTrees":false}'`, or a wrapper setting `terrainData.landCover = null` |
| api.inaturalist.org | Tree species mix | treeSpecies.js:90; exportArea.mjs:75-81 | exportArea (once, cached to `tree_mix.json`) | `TREEMIX=climate` |
| server.arcgisonline.com (Esri World Imagery) | Satellite base texture | satellite.mjs:34 via gametexnode.mjs:25 via exportArea.mjs:64-67 | exportArea (default `BASE=game`) | `BASE=none` (mandatory for us) |
| s3-us-west-2.amazonaws.com/usgs-lidar-public | US bridge lidar | exportArea.mjs:93-108; lidarDecks.js:21 | only `LIDAR=1` | off by default |
| maps.googleapis.com (Street View) | Photo comparison | scripts/lib/streetView.mjs:47,84; driveTest.mjs:178; streetCompare.mjs | driveTest/streetCompare | Off when no `GOOGLE_STREETVIEW_KEY` (streetView.mjs:37) and with `--no-street` |
| graph.mapillary.com | Photo comparison | streetCompare.mjs:193 | streetCompare only | never run streetCompare |
| 127.0.0.1:29292/mcp | BeamNG MCP | runIngameTests.mjs:20; driveTest.mjs:37 | in-game tests | local only |
| UI only: services.arcgisonline.com, nominatim (2 hosts), tiles.mapterhorn.com, s3.opengeohub.org (GEDTM), tnmaccess.nationalmap.gov, epsg.io, api.openstreetmap.org, mapy/opendata.geoportal.gov.pl, mapng.com/api/* | Map, search, DEM sources, telemetry | MapSelector.vue:34-43,494; vite.config.js:127-154; worker.js:41-45 | no | not used |

Note: `polyfill.mjs:80-104` stubs three relative URLs and passes everything else to the real `fetch`, so the headless export can reach any host the services call.

## 2. Telemetry, side effects, dependencies

- Telemetry: export "hotspots" and settings usage POST to `/api/hotspots` and `/api/usage` (composables/useExportHotspots.js:33, useUsageStats.js:22), opt-in, browser UI only. Crash reports and auto-update (`desktop/crashReports.js`, `desktop/updater.js` via electron-updater) are Electron desktop only. None is imported by `scripts/` or `services/exportBeamNGLevel.js`.
- Writes outside its folder: `~/.config/mapng/mapng.env` is read for keys (sweep/env.mjs:14-19); linter caches `~/.cache/mapng/game-files.txt` (gameIndex.mjs:13,55-56); driveTest writes to `%TEMP%/mapng-drives` (driveTest.mjs:53); gametexnode writes `%TEMP%/gametex_<area>_sat.png` (gametexnode.mjs:24). Data/outputs default to `scratch/` inside the repo unless `MAPNG_AREAS` / `MAPNG_SWEEP_OUT` are set (env.mjs:11-12).
- Processes: exportArea spawns `node` for gametexnode and fixStubs (exportArea.mjs:67,119); runSweep spawns node and git; gameIndex spawns `python3` over `refs/beamng/content` (gameIndex.mjs:54); a few tests spawn node (polyClip, zipEntryDate, desktopPackaging).
- BeamNG install/user folder: only the Electron app finds `.../BeamNG.drive/current/mods` (desktop/modsFolder.js:10-24). The linter reads game content only if `refs/beamng/content` exists inside the MapNG tree (read-only); `--no-game` skips it. In-game scripts call `core_modmanager.initDB()` and `freeroam_freeroam.startFreeroam` (runIngameTests.mjs:38; driveTest.mjs:66). Nothing launches the game.
- package.json: no install/postinstall scripts of its own. `npm test` = `node --test tests/*.test.mjs`. Runtime deps are mainstream (three, jszip, geotiff, proj4, pmtiles, laz-perf, fast-png, vue, leaflet). Dev dep `wrangler` pulls `workerd`, `esbuild` and `sharp` (all `hasInstallScript`, package-lock.json:2538,3778,4336). `sharp` is reached only through wrangler (lock:3085) yet `scripts/sweep/polyfill.mjs:4` imports it, so the headless export needs dev deps. `@napi-rs/canvas` (gametexnode.mjs:8) is not in the lockfile at all; unneeded with `BASE=none`. CI uses Node 24 (.github/workflows/desktop-release.yml:33).

## 3. Licences

| Item | Licence | Notes |
|---|---|---|
| MapNG code | MIT (LICENSE, 2026 Nikki Luzader) | Keep the notice in the submodule |
| Fonts in public/fonts | SIL OFL (README.md:296, OFL-*.txt) | UI only |
| public/cubemap/*.dds, public/hdr/kloofendal_48d_partly_cloudy_puresky_4k.hdr | Not stated in repo | Name matches a Poly Haven CC0 sky; cubemap source unknown. fixStubs copies the cubemap into the level |
| public/mapng_flag_static.zip, beamng_facade_e.png | MapNG's own, MIT presumably | Flag mesh is placed in every level (exportBeamNGLevel.js:13705-13717, 14253) |
| Stock BeamNG assets | Referenced by path, not copied (Italian signs `/art/shapes/garage_and_dealership/...`, ECA signs; roadSigns.js:32-33) | Fine for a mod |
| OSM data | ODbL: "© OpenStreetMap contributors" | Applies also to our `data/overrides/` if derived from OSM and published |
| Overture buildings | ODbL/CDLA mix; includes "Microsoft / Google ML" footprints (overtureTiles.js:588) | Turn off (see risks) |
| ESA WorldCover | CC BY 4.0, attribution string worldCover.js:16 | Written into level notes |
| Terrarium / AWS Terrain Tiles | Mixed open sources (SRTM etc.), attribution required | |
| iNaturalist | Species counts only, no images | |
| Esri World Imagery | Proprietary, not redistributable | Must stay off (`BASE=none`) |
| GPXZ | Commercial API terms | Not needed |

Since the level zip stays on the user's PC, redistribution limits mostly do not bite; what we commit (code, overrides) must carry OSM attribution.

## 4. UK rules today

| Topic | What MapNG does | Gap for Belfast |
|---|---|---|
| Driving side | Country from `public/country_rules.json` (GB: l=1, Europe); sets info.json `roadRules.rightHandDrive`, lane sides, spawn lane, kerb-side signals and parking (countryRules.js:110-131; exportBeamNGLevel.js:11836-11847, 486, 6379-6390, 10351; beamngSignals.js:1001-1024). Unit test covers London (tests/beamngExportGuideConformance.test.mjs:757-795) | **Headless bug**: `loadCountries` fetches relative `/country_rules.json` (countryRules.js:6,45) and polyfill.mjs does not stub it, so in Node it fails, returns null, and falls back to the biome profile `rightHandDrive:false` (beamngBiomeCatalog.js:1303) and `americas` markings (exportBeamNGLevel.js:3475-3477). Inference from code; T-103 must confirm |
| One-way / lanes | oneway, oneway=-1, lanes:forward/backward, junction=roundabout as one-way (signalApproaches.js:53; exportBeamNGLevel.js:2688-2695, 2965-2985) | Asymmetric-lane mirroring caveat (exportBeamNGLevel.js:2983-2985) |
| Markings | `white` style for non-Americas: white dashed centre, solid where overtaking=no, white edges; zebra crossings (exportBeamNGLevel.js:2976-2979, 6315) | No UK give-way double-dashed lines, hatching, box junctions, bus lanes, yellow kerb lines. `mini_roundabout` and `box_junction` appear nowhere in services/ |
| Signs | Vienna set from stock Italian meshes in km/h (30-100), stop, give way, priority, one way, no entry, roundabout (roadSigns.js:55-64); US set in mph (roadSigns.js:40-53) | UK mph limits are skipped (unit mismatch, roadSigns.js:238-241): no speed signs. Italian roundabout sign is anticlockwise. No UK sign meshes |
| Traffic lights | `lightsBasicEU` red+amber before green for Europe (beamngSignals.js:438-445, 832) | Matches UK sequence; needs in-game check of head placement for LHT |
| Roundabouts | Ring follows OSM way direction (clockwise in UK) as one-way; derived give-way/roundabout signs (roadSigns.js:92,179) | No mini-roundabouts; no roundabout entry give-way markings |
| Speed limits | OSM `maxspeed` incl. "30 mph" to m/s for AI (beamngMapNavigation.js:99-118) | Untagged roads get BeamNG's own default |
| Junction geometry | Corner rounding, street widths from buildings, twin carriageways, structure solver (exportArea.mjs:48-51) | Quality for Belfast unknown until built |

## 5. What the later tasks need

- Download step: `node scripts/sweep/fetchArea.mjs <name> <lat> <lng> [size]` writes `<MAPNG_AREAS>/<name>/{gpxz.f32|t30.f32, osm.json, meta.json}` (fetchArea.mjs:1-11,102,126,128,145). Size must be a power of two (fetchArea.mjs:43); 2048 with `MPP=1` is our 2048 m square.
- Build step: `node scripts/sweep/exportArea.mjs <area> [gpxz|t30] [levelName] [--out dir] [--fix-stubs]` reads that folder, writes `<out>/<levelName>.zip` and `<levelName>.checks.json` (exportArea.mjs:5-13,115-120). Env: `BASE`, `TREEMIX`, `LIDAR`, `RAW`, `NOWIDTH`, `STRUCT`, `EXPORT_OPTS` (JSON merged into export options, exportArea.mjs:110). Explicit `rightHandDrive`/`country` options win over location (exportBeamNGLevel.js:11837-11842). runSweep uses `--max-old-space-size=12288` (runSweep.mjs:55).
- Unit tests: `npm test` (node --test). Needs `npm ci` with dev deps.
- Level linter: `node scripts/lintLevel.mjs <zip>... [--json out] [--no-game]`, exit 1 on any error (lintLevel.mjs:1-44).
- In-game: `node scripts/ingame/runIngameTests.mjs <level> [--load] [--json out] [--max-fail 0.02]` and `node scripts/ingame/driveTest.mjs <level> [--load] [--routes N] [--json out] --no-street`. Both need the level zip installed in the profile's mods, `levels/<level>/mapng_probes.json` (written by the export, exportBeamNGLevel.js:13630), and an MCP server at `BEAMNG_MCP_URL` (default 127.0.0.1:29292/mcp) with tools `run_lua`, `run_lua_vehicle`, `screenshot_image`, `get_vehicle_damage`. All four exist in the game's built-in MCP (`lua/ge/extensions/mcp/tools/system.lua:14,159,174`, `input.lua:66,101`, `telemetry.lua:78`), so `-enablemcp` should suffice; the comment's "beamng-mcp mod" is not required. driveTest drives the player vehicle with the AI.

## 6. Risks and required mitigations

1. Satellite imagery by default (Esri via `BASE=game`). Always run exportArea with `BASE=none`; never run satellite.mjs or gametexnode.mjs.
2. Google content: Street View in driveTest/streetCompare. Always pass `--no-street`, never create `GOOGLE_STREETVIEW_KEY`/`~/.config/mapng/mapng.env`, never run streetCompare.mjs. Overture building footprints include Google ML data: disable Overture buildings (`mapng_overtureBuildings=false`) and prevent the Overture fallback by feeding a local OSM file.
3. Wrong driving side headlessly: the T-103 wrapper must serve `public/country_rules.json` to `fetch('/country_rules.json')` (as the conformance test does) and assert info.json `roadRules.rightHandDrive == true`, `region == "europe"`, and white markings. Passing `EXPORT_OPTS={"rightHandDrive":true}` alone still leaves US markings.
4. Unpinned live data: Overpass, Terrarium, WorldCover, iNaturalist change over time. Pin inputs under `H:\BelfastData`: local OSM extract (dated), DEM file, `tree_mix.json`; set `TREEMIX=climate` or cache; decide on WorldCover (`includeTrees:false` or cache).
5. fetchArea needs a GPXZ key even with `T30_ONLY=1`; the 30 m Terrarium DEM is coarse. Prefer our own step that writes `t30.f32`-format heightmap + `meta.json` + `osm.json` (via MapNG's `fetchOSMData` over a loaded OSM file).
6. Windows paths: env.mjs:10, fixStubs.mjs:11,33, gameIndex.mjs:9 and exportArea.mjs:119 use `new URL(import.meta.url).pathname`, which gives `/H:/...` on Windows. Expect `--fix-stubs` and default dirs to break on native Windows. Set `MAPNG_AREAS` and `MAPNG_SWEEP_OUT`; verify in T-102/T-103 or run fixStubs logic in our wrapper. gameIndex needs `python3`; use `--no-game` or set it up.
7. Install scripts: `npm ci --ignore-scripts` inside the submodule, then confirm sharp/esbuild still load (prebuilt optional packages). Lockfile is v3; use `npm ci`, never `npm install`.
8. UK gaps (signs in mph, give-way markings, mini-roundabouts, UK sign meshes): ours to add via `rules/` and `data/overrides/`, as planned.
9. MapNG flag mesh in every level: harmless; remove later if wanted.
10. Pin: use exactly `d605117d77857e91acb8234335e9f89263a94756`; re-review any bump (upstream moves daily).

Conditions for go: mitigations 1, 2, 3, 4, 6 and 7 are applied in T-102/T-103 before the first export is accepted; T-103 asserts left-hand traffic in the built zip.

Not verified: anything at runtime (no install, no tests, no export run); whether the 117 tests are fully offline; the headless right-hand-traffic fallback and the Windows path breakage are inferences from code; cubemap/HDR licences.

Verdict: go
