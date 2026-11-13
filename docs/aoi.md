# Map squares and coordinate transforms (T-201)

`config/aoi.yaml` defines the three squares and the coordinate systems; `pipeline/aoi.py` converts between WGS84 lat/lon, Irish Grid (EPSG:29903), ITM (EPSG:2157) and the game world; `pipeline/aoi_check.py` shows the conversion reproduces MapNG's own placement of the slice.

```text
uv run --no-project --python 3.12 --with pytest --with pyproj --with numpy --with pyyaml python -m pytest tests/pipeline -q
uv run --no-project --python 3.12 --with pyproj --with numpy --with pyyaml python pipeline/aoi_check.py
```

```python
import aoi                              # pipeline/ on sys.path
s = aoi.load().area("slice")
x, y = s.latlon_to_game(54.5428, -5.93)
e, n = s.game_to_crs("itm", x, y)        # also crs_to_game, game_to_latlon, cfg.latlon_to_crs, cfg.convert
```

## The squares

| Area | Centre | Side | Pixels at 1 m | Notes |
|---|---|---|---|---|
| `slice` | 54.5428, -5.9300 | 2048 m | 2048 | Built and driven (`belfast_slice`). Bounds frozen from MapNG's `meta.json`. |
| `city_south` | 54.5428, -5.9300 | 4096 m | 4096 | Concentric with the slice, which sits in the middle. |
| `full` | 54.5700, -5.9500 | 8192 m | 8192 | Provisional centre: holds the slice, both test centres and the city centre. Needs T-701. |

Sides are powers of two because MapNG needs that. `city_south` holds the slice. `full` holds the slice too but not all of `city_south` (its south edge, 54.5332, is a few hundred metres north of `city_south`'s 54.5244), because centring it far enough south to hold `city_south` would push the city centre out of the north edge. Each is a separate level, so nesting is not needed. Moving a centre is one edit in the yaml.

Reference points are approximate and marked `confirmed: false`: the Hydebank centre (the middle of Hospital Road, from `docs/belfast_slice.md`), Balmoral (54.5700, -5.9760, my estimate; OSM way 1262986364 is the DVA site, read its position from the T-202 extract and replace this; an Overpass lookup was tried twice and failed) and Belfast City Hall. Tests require each area to hold its listed points with 500 m to spare.

## The game-world frame (what MapNG really does)

World x east, y north, metres, origin at the middle of the square. MapNG does not project to metres: `services/terrainLookup.js geoToWorldPoint` maps latitude and longitude **linearly** across the square's `bounds`:

```text
x = ((lon - west) / (east - west) - 0.5) * size
y = (0.5 - (north - lat) / (north - south)) * size
```

The bounds come from `tools/mapng_fetch_area.mjs`, which copies MapNG's `fetchArea.mjs`: a local transverse Mercator centred on the area centre, corners at +-(size/2 + 4) m, then min and max lat/lon. Consequences:

- The 4 m margin means 2048 game metres cover 2056 m of ground: one game metre is 1.0041 m east-west and 1.0039 m north-south on the slice (1.0024 / 1.0020 on `city_south`, 1.0019 / 1.0010 on `full`). Distances in the game are 0.4 % short. Do not "correct" it in the data steps; the frame is MapNG's, and the data must land where MapNG puts the roads.
- The area centre lands about 0.1 m from the origin (the bounds are symmetric in metres, not in degrees).
- Outside the square `aoi.py` extrapolates; MapNG pins the point to the edge.
- The existing `qa/route_lib.py` `latlon_to_xy` is a flat-earth approximation of this and is 1.5 m out in median (see the check below). The lat/lon in `qa/routes/belfast_slice.json` are therefore only good to a few metres. Left alone (not this task); switch it to `aoi.py` when routes are next rebuilt.

## Heightmap grid (for T-203)

MapNG samples its DEM for pixel (col, row) at `lon = west + col/(n-1) * span`, `lat = north - row/(n-1) * span` (`Area.pixel_to_latlon_mapng`), but the game terrain draws column `col` at world x = `col - n/2` and row `row` at y = `n/2 - 1 - row` (`terrainLookup.js drapeAt`; `Area.pixel_to_game`). The two disagree by up to 1 game metre at the north and east edges (zero at the south-west corner); `Area.terrain_shift` gives it per pixel. If the LiDAR grid is resampled on MapNG's sampling positions, the terrain is up to 1 m off from the roads at the north-east edges. T-203 should decide which grid to honour; this module supplies both.

## CRS definitions and their accuracy

The yaml pins a PROJ string for each CRS so a PROJ upgrade or an installed grid file cannot move the data.

- ITM (2157) is ETRS89-based and treated as equal to WGS84, as PROJ does. No shift.
- Irish Grid (29903) uses OSi's 7-parameter Helmert (EPSG "TM75 to WGS 84 (2)"), good to about 1 to 2 m. OSi's grid shift (`ie_osi_*` grid file) is better and is not installed, so data in Irish Grid is as far off as that shift. Anything from DfI Open Data or the LiDAR (ITM) is unaffected.
- The pinned strings agree with pyproj's EPSG operations to under 2 mm over Belfast (tested). Round trips lat/lon <-> Irish Grid reach 0.6 mm worst case (the Helmert inverse), ITM 0.000003 mm.
- Hospital Road centre: ITM 733936.2198 E, 868019.4872 N; Irish Grid 334013.4684 E, 368022.0816 N.

## Checks and results

Round trips (20 000 random points per square; also in `tests/pipeline`, 1 cm limit): game <-> lat/lon 4e-7 mm; game <-> ITM 3e-6 mm; game <-> Irish Grid 0.63 mm. Computed bounds equal MapNG's frozen slice bounds to under 1 cm.

MapNG placement (`aoi_check.py`, slice zip `H:\BelfastData\mapng\out\belfast_slice.zip` and its `osm.json`, run on 2026-11-13). The 5741 OSM road vertices are projected and compared with the 69 013 DecalRoad nodes in the zip (written by MapNG from the same vertices, rounded to 0.001 m):

| Model | Vertices with a node within 1 mm | Median distance to nearest node |
|---|---|---|
| `aoi.py` (linear in lat/lon over MapNG's bounds) | 26.1 % | 0.49 m (resampled nodes) |
| Local transverse Mercator at the centre | 0.0 % | 1.70 m |
| Flat earth, as `qa/route_lib.py` | 0.0 % | 1.52 m |

Among vertices within 1 cm, the mean offset is 0.013 mm in x and y, and the worst is 7.8 mm in x. The rest of the vertices have no node on them (MapNG resamples and smooths roads), so only the matching quarter is evidence; it is enough to rule out any systematic error above about 1 mm. 300 of the matching pairs are in `tests/pipeline/fixtures/belfast_slice_mapng_pairs.json` (OSM-derived lat/lon plus MapNG's x/y), so the test runs without `H:\BelfastData`; the full check runs too when the zip exists.

Drive-suite crash sites: (-211, -93) is 54.54196, -5.93327, 4.5 m from the nearest OSM road vertex; (194, 284) is 54.54536, -5.92699, 7.4 m from one (great-circle distance, independent of the transform). Both are on roads.

## Known limits

- Only the slice has been exported, so the `city_south` and `full` bounds are computed by the same rule, not read from a MapNG export. When they are exported, add `bounds:` from `meta.json` and the test will compare.
- The check compares the transform with MapNG's output for the slice only, and OSM vertices only, not buildings or signals.
- If T-203 passes its own DEM, MapNG's bounds still come from the same fetch rule, so the frame holds.
