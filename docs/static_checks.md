# Static checks, G0 (T-301)

`qa/static_checks/` runs on an exported level zip and the OSM extract it was built from. No game, no model. It writes `findings.json` (source `lint`, the format in `docs/digest.md`), so `qa/digest.py` turns the findings into issue cards.

```powershell
$env:UV_CACHE_DIR='H:\BelfastData\cache\uv'; $env:UV_PYTHON_INSTALL_DIR='H:\BelfastData\uv-python'
uv run --no-project --python 3.12 --with numpy --with shapely python qa/static_checks                    # the Slice, all checks
uv run --no-project --python 3.12 --with numpy --with shapely python qa/static_checks --only steps buildings
uv run --no-project --python 3.12 --with numpy --with shapely --with pytest python -m pytest tests/static_checks -q
```

Options: `--zip` (default `H:\BelfastData\mapng\out\belfast_slice.zip`), `--osm` (default: the file the export read, `H:\BelfastData\mapng\areas\<level>\osm.json`; repoint it at the pinned extract when T-202 lands), `--out-dir` (default `H:\BelfastData\qa\static\<UTC time>`), `--budget` (`config/static_budget.json`), `--game-index`, `--mapng-dir`, `--buildings auto|mesh|osm`, `--no-lint`. Exit code 0 with no `high` finding, 1 otherwise, 2 on bad input. A check that cannot run reports `check-not-run` (medium); it never passes silently.

Needs `node` and a MapNG checkout (`third_party/mapng`, or the main checkout's) only for the linter. Nothing is written under `third_party/`.

## Checks

| Kind | What it checks | How |
|---|---|---|
| `lint-*` | MapNG's own level linter (`scripts/lib/levelLint.mjs`), now with the stock-file check on | `lint_runner.mjs` feeds it our game index; error is `high`, warning `low` |
| `missing-stock-asset`, `missing-stock-material` | Paths and DecalRoad materials that exist neither in the level zip nor in the game (they load as nothing, silently; gap 14) | every asset path in the level's JSON against the game index, with the engine's `.png/.dds/.jpg`, `.dae/.cdae` and `.link` fallbacks. A path the game's own materials reference and the game lacks is only `low` |
| `road-missing` | A public OSM road with no drivable DecalRoad within 6 m for 16 m or more | OSM public ways sampled every 8 m; the last 33 m before the map edge are skipped (MapNG ends roads about 31 m short of it) |
| `road-disconnected` | One OSM network drawn as separate islands | navigation roads (`drivability` above 0) joined where an end is within 3 m of another road; islands over 20 m inside one OSM component are reported. On the Slice this gives 21 islands at 3 m (20 at 12 m); the game's own graph (T-302 dump) has 20 components |
| `oneway-missing`, `oneway-wrong-direction`, `oneway-extra` | OSM one-way ways against the drawn `oneWay` flag and node order (`flipDirection` respected) | samples every 10 m, matched to the nearest parallel navigation road; two-way OSM ways drawn one-way are reported too |
| `road-height` | Road nodes within 0.5 m of the ground (G0) off bridges and tunnels | node height against the terrain in the zip (the road is draped on it). `check_road_height(reference=...)` takes any sampler: pass the LiDAR heights here when T-203 lands. Bridge zones: OSM `bridge` and `tunnel` ways (15 m) and bridge shapes (50 m) |
| `over-budget` | Object counts against `config/static_budget.json` | objects, DecalRoads, statics, building triangles (from the export report), terrain size, zip size. The limits are provisional until T-701 |
| `building-in-lane` | A building standing in the driving lanes, by area (gap 1) | the exported building tiles' collision mesh (`Colmesh`): its walls must stand in the lanes (shrunk by 0.3 m, MapNG's own kerb allowance) with at least 1 m2 and 0.15 m depth; roofs give the area, and a roof that only overhangs is not a hit. This is what the car hits, after MapNG's cut-back, so it works unchanged after T-412. OSM outlines name the building; `--buildings osm` uses the outlines alone |
| `terrain-step` | A step in the ground under the drawn lanes (gap 2) | second difference over 1 m in four directions at every terrain vertex inside the lanes; over 0.15 m (T-413) is reported, 0.25 m and over is `high`. Merged to the worst per 10 m. Bridges skipped |

Lanes: the drawn invisible `_ai` DecalRoad of each carriageway, at its drawn width (half width per side), plus any body road with no twin, less 2 m. Service roads, driveways and parking aisles (not in the navigation graph) are reported one severity lower. Roads that may pass a building (driveway, parking aisle, tunnel, covered, bridge, other layer) are not counted for the building check, as in MapNG's `roadCountsAgainstBuildings`.

World coordinates: MapNG maps latitude and longitude linearly over the bounds in `mapng.json`; the checks use the same mapping, so OSM lines up with the DecalRoads to centimetres.

## Game file index

`qa/static_checks/game_index.py` lists the files and material names of the game's `content` zips into `H:\BelfastData\qa\static\game_index.txt` (about 4.5 MB, paths only, not in git, rebuilt when the game's content changes; 0.4 s). The format is the one MapNG's `gameIndex.mjs` reads.

## On the Slice (2026-11-13 run, `belfast_slice.zip`, 3 s of node plus about 25 s of Python)

| Finding | Count | Notes |
|---|---|---|
| MapNG linter | 0 errors, 5 warnings | the two known `Colmesh` warnings, and three textures the game's own materials reference and the game lacks. Stock paths are now checked (T-103 ran `--no-game`) |
| `missing-stock-asset` | 8 | 3 `low` (also dangling in the game); 5 `high`: four `art/sky_gradients/default/gradient_*.png` and `art/skies/clouds/clouds_normal_displacement.png`, referenced from the `sky_and_sun` objects and not shipped by the game. T-104 loaded the level, so these are likely engine defaults, but nothing has shown it |
| `building-in-lane` | 8 | 1 on a driving road: OSM 1260690275 at (-209.5, -81.9), 11.8 m2, 1.68 m deep (gap 1, the known site). The other 7 are on service roads and driveways |
| `terrain-step` | 431 over 15 cm, about 200 of them 25 cm or more | includes the known site: 29 cm at (199, 285) on `dr_Hospital_Road_25_s1_ai` (gap 2). Belvoir Road has steps over 1.1 m at about (256, 702) and (262, 716) |
| `road-missing` | 2 | two service ways at (611, 824) and (611, 780) |
| `road-disconnected` | 4 | islands of 86 to 344 m at the map edge (Mill Road West, Ballylesson Road, Castledillon Road, Woodland Manor) |
| `oneway-missing` | 1 | Hospital Road way 1410878962 at (169, 283), 1 of 2 samples |
| `road-height` | 8 | service roads and driveways, 0.50 to 0.85 m; none on the navigation graph |
| `over-budget` | 0 | 7571 objects, 4052 DecalRoads, 3185 statics, 71.6 MB |

## Limits

- The building check reads the collision mesh of the tiles. Two tiles (`bld_y1_-2`, `bld_y-2_3`) have no Colmesh and are not checked; MapNG's linter warns about them too.
- The terrain step check uses the exported heightfield, not the road surface the car drives on (decals follow the terrain; sidewalk meshes are not included).
- Lane width is the drawn `_ai` width. If T-406 changes road widths the lanes follow, which is what is wanted.
- `road-disconnected` compares with OSM, not with the game: a gap the game closes by itself (it merges nodes within a radius we do not know) would be reported. At 3 m the Slice has about the same number of islands as the game graph from T-302 (21 against 20), and a dump of that graph is the better check once a build has run in game.
- One finding per kind and place is not enforced here: `qa/digest.py` merges findings of one kind within 20 m. `terrain-step` can produce many cards on a rough map (cap 150 per kind in the file).
