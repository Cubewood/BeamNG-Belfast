# Elevation: Belfast City LiDAR, OSNI 10 m, MapNG's height file (T-203)

`pipeline/elevation.py` turns the two open elevation sources into the file MapNG reads instead of its 30 m Terrarium data.

```text
uv run --no-project --python 3.12 --with numpy --with pyproj --with pyyaml python pipeline/elevation.py download
... mosaic                                   # 108 LiDAR DTM tiles -> H:\BelfastData\lidar\mosaic\lidar_1m.npy (1 m)
... osni                                     # OSNI 10 m sheets -> mosaic\osni_10m.npy
... coverage slice city_south full           # numbers and map only
... build slice --install belfast_slice      # + fill, write gpxz.f32, copy it into MapNG's area folder
uv run --no-project --python 3.12 python tools/mapng_export.py export belfast_slice --dem gpxz
```

Outputs under `H:\BelfastData\elevation\<area>\`: `gpxz.f32`, `coverage.png` (map), `coverage_report.json`, `source_mask.npy` (0 LiDAR, 1 OSNI, 2 none). Tests: `tests/pipeline/test_elevation.py`, `tests/mapng/test_mapng_export_dem.py` (small synthetic rasters).

## Sources (`data/sources/elevation.json`)

The committed manifest has URL, retrieval date, size, SHA-256, licence and attribution text for each of the eight files (four LiDAR zone zips, two OSNI 10 m zips, the publisher's notes PDF, the OSNI 10k coverage grid): 1 784 543 569 bytes, fetched 2026-11-13. Raw files are in `H:\BelfastData\lidar`, never in git. T-208 builds the credits from `credits_required`. All are Open Government Licence v3.0.

- **LiDAR Belfast City 2006**: 0.5 m ESRI ASCII DTM, four zones, 108 tiles of 1 km, surveyed 18 March 2006, **Irish Grid (EPSG:29903)**, heights above Mean Sea Level Belfast, nodata -999, quoted accuracy +-0.15 m RMSE. Required attribution (quoted from the publisher's note): "Belfast City LIDAR is provided by the Department for Infrastructure (Rivers) with the permission of the copyright owner, Cyient Europe Limited, and licensed under the Open Government Licence v3.0". The copyright owner is Cyient Europe Ltd.
- **OSNI Open Data 10 m DTM**, sheets 129, 130, 146, 147, 165, 166 (from the 10k coverage grid; they sit in the 101-150 and 151-200 zips): "x y z" text at 10 m cell centres, Irish Grid, interpolated from OSNI's 2014-2016 Enhanced DTM. The dataset page gives no attribution wording; the manifest uses the OGL v3.0 form.
- The API `www.opendatani.gov.uk/api/...` returns 404 and `admin.opendatani.gov.uk/api/...` returns 403 without a browser User-Agent; with one, CKAN `package_show` works. The OSNI links redirect to a signed Cloudflare URL.

Note for `config/aoi.yaml`: its comment says the Belfast LiDAR is in ITM. The publisher's note says Irish Grid, and so do the tile headers (J-square names, xll 334000 for J3474). The pipeline uses Irish Grid; only the Helmert shift (1 to 2 m, see `docs/aoi.md`) separates it from the game frame, for both sources alike.

## Coverage: the LiDAR barely reaches the slice

The southern edge of the Belfast City LiDAR is about northing 369 000 at the slice's eastings (tile list: easting 33 and 34 start at J3369, J3469; J3367, J3368, J3467, J3468 do not exist, and DSM has the same 108 tiles). The Hydebank slice spans about N 367 000 to 369 000, so **the LiDAR covers 2.9 % of the slice**, a strip on its north and west edges (`coverage.png`; the grid is rotated against latitude and longitude, so the edge is slightly slanted).

| Square | Pixels | LiDAR | Gaps before fill | Filled from OSNI | Gaps after fill |
|---|---|---|---|---|---|
| `slice` (2048) | 4 194 304 | 121 573 (2.90 %) | 4 072 731 (97.10 %) | 4 072 731 | **0** |
| `city_south` (4096) | 16 777 216 | 5 094 598 (30.37 %) | 11 682 618 | 11 682 618 | **0** |
| `full` (8192) | 67 108 864 | 52 758 813 (78.62 %) | 14 350 051 | 14 350 051 | **0** |

So the slice terrain is in practice the OSNI 10 m model, bilinear at 1 m, with LiDAR only at two edges. It is far better than 30 m Terrarium (no synthetic relief) but it has 10 m facets; roads will not follow 1 m detail such as kerbs or camber. The LiDAR matters for `city_south` (30 %) and `full` (79 %), the Balmoral side. Maps from the `full` run show the slice outlined in white, entirely outside the green.

## Fill and seam

LiDAR where it has data; otherwise OSNI plus a correction. LiDAR minus OSNI where both exist has median +0.54 m on the slice (+0.63 `city_south`, +0.65 `full`), 10th to 90th percentile -0.57 to +1.39 m on the slice, so the sources differ by more than a constant (datum, 2006 against 2014-16 ground, and 10 m interpolation on slopes with median 7.7 %). The correction is the local difference seen within about 40 m, fading to the area's median where no LiDAR is near. LiDAR within about 20 m of its own edge is blended into the corrected OSNI (`feather_weight`) so there is no cliff. Measured across LiDAR-to-fill neighbour pixels:

| Square | Pairs | Mean step | Max step |
|---|---|---|---|
| `slice` | 3 379 | 0.057 m | 0.43 m |
| `city_south` | 7 328 | 0.060 m | 0.79 m |
| `full` | 26 484 | 0.076 m | 3.24 m |

(The slice, before feathering, had mean 0.475 m, max 2.72 m.) The `full` maximum is at a cut or fill edge a few pixels wide; not investigated.

Slice heights are 8.9 to 102.2 m (MapNG's Terrarium file gives 13.8 to 99.3 m; the difference is -15 to +8 m at the 1st and 99th percentile, median -1.3 m).

## Which grid: the game's

MapNG samples its DEM at `west + col / (n - 1) * span`, but the game draws column `col` at world x `col - n / 2` and row `row` at y `n / 2 - 1 - row` (`docs/aoi.md`; `services/terrainLookup.js terrainDrape`). Up to 1 m apart at the north-east edges. The pipeline samples **where the game draws the terrain** (`--grid game`, default): what the player sees and drives on is then in the right place against the real world. Road heights read the heightmap on MapNG's own `n - 1` mapping (`getTerrainHeightWorld`), so a road's height can come from a pixel up to 1 m away on either choice; the road-to-terrain mismatch is the same (slope times at most 1 m, centimetres on 8 % slopes) whichever is honoured. `--grid mapng` writes the other one.

## MapNG's format (what was checked)

`third_party/mapng/scripts/sweep/exportArea.mjs` (pinned commit) line 33 requires `<area>/<dem>.f32`; line 38 reads `new Float32Array(fs.readFileSync(path.join(dir, `${dem}.f32`)).buffer.slice(0))` and uses it as `heightMap[y * width + x]` with `width = height = meta.size`. `fetchArea.mjs` line 102 writes it as `Buffer.from(gpxz.heightMap.buffer)` from `resampleToMeterGrid`, which fills `heightMap[y * width + x]` with row 0 at the north (`getPixelLatLng`: `lat = north - v * span`), unit metres, no-data -99999 (`terrainResampler.js` lines 414-442). So: little-endian float32, `size * size` values, row-major, row 0 north, metres. `exportArea.mjs` line 43: `fine = dem === 'gpxz' && !meta.gpxzSmooth`; fine skips the coarse path (`coarseElevation: !fine`), which is what adds synthetic relief (`exportBeamNGLevel.js` line 11874, `shapeDetail && terrainData.coarseElevation`), and runs `snapRoadsToTerrain`. Our `meta.json` already has `gpxzSmooth: false` (written by `tools/mapng_fetch_area.mjs`).

Checked by reading the installed file the way MapNG does (Node, `H:\BelfastData\mapng\areas\belfast_slice\gpxz.f32`): 4 194 304 values, equal to `meta.size ** 2`, min 8.880, max 102.167, no value at or below -200; corners NW 11.7 m, NE 59.0, SW 41.5, SE 88.8. SHA-256 `f06005e1b1ecddeaeb5a0ba0bd7ebf2f10103c8e733c7ad4a544beaa561cbe21`. The export itself was not run (not required here).

`tools/mapng_export.py export NAME --dem gpxz` passes `gpxz` to `exportArea.mjs` instead of `t30`, checks that `gpxz.f32` is `meta.size ** 2 * 4` bytes and that `meta.json` has `gpxzSmooth` false (exit 6 otherwise). The default stays `t30`.

## Limits

- Irish Grid to WGS84 uses OSi's 7-parameter Helmert, 1 to 2 m (docs/aoi.md). LiDAR and OSNI share it so they agree with each other; both can sit 1 to 2 m off the OSM roads. On slopes of 8 %, 2 m is 16 cm of height.
- 1 m cells average the 0.5 m cells; a cell counts as LiDAR if any of its four is valid. Nodata cells inside the LiDAR (water, small holes) are filled from OSNI like any other gap.
- Only `slice` was installed into MapNG's area folder (a new file next to `t30.f32`, nothing else touched). `city_south` and `full` have no MapNG area folder yet; run `build` for them after they are fetched.
- The 2006 LiDAR predates 20 years of building and road works; OSNI is 2014-16 data.
