# Pinned OSM (T-202)

The build reads OpenStreetMap from one dated Geofabrik file, not from Overpass. The same file gives the same
`osm.json`, byte for byte, so a changed level can only come from a changed rule or fix.

| Thing | Where |
|---|---|
| Step | `pipeline/osm_pin.py` |
| Manifest (committed) | `data/sources/osm.json`: URL, dated URL, download time, size, SHA-256, MD5 checked against Geofabrik, PBF header (data timestamp), and each cut's box, counts and SHA-256 |
| Extract (not in git) | `H:\BelfastData\osm\ireland-and-northern-ireland-latest.osm.pbf` (about 420 MB) |
| Cuts (not in git) | `H:\BelfastData\osm\cuts\<area>.overpass.json` for `slice`, `city_south`, `full` |
| Parser | `tools/mapng_osm_parse.mjs`: MapNG's own `parseOverpassResponse`, no network |
| Wiring | `tools/mapng_export.py fetch ... --pinned-osm AREA` and `tools/mapng_export.py pinned-osm NAME` |

Data: (c) OpenStreetMap contributors, ODbL 1.0; extract by Geofabrik GmbH. Only the manifest and code are in the
repo; the extract and the cuts stay on the PC.

## Commands

```text
set P=uv run --no-project --python 3.12 --with osmium --with pyproj --with numpy --with pyyaml --with requests python
%P% pipeline/osm_pin.py download          # once; refuses to replace an existing file without --force
%P% pipeline/osm_pin.py verify            # size and SHA-256 of the file on disk against the manifest
%P% pipeline/osm_pin.py cut               # all three squares in one read of the file (about 6 minutes, one core); records the hashes
%P% pipeline/osm_pin.py cut --check       # recut into --out-dir and compare with the manifest; changes nothing in it
%P% tools/mapng_export.py fetch belfast_slice 54.5428 -5.9300 --size 2048 --pinned-osm slice
%P% tools/mapng_export.py pinned-osm belfast_slice --cut slice      # rewrite osm.json of an existing area, offline
```

Updating the pin is a deliberate act: `download --force`, then `cut`, then commit the manifest. Geofabrik
keeps dated files (`...-YYMMDD.osm.pbf`) for a while; `dated_url` in the manifest is the one that matches the
download, and the SHA-256 is what decides whether a file is the pinned one.

## What a cut is

For each square in `config/aoi.yaml`: the square's bounds plus 100 m on every side, and from the extract the
elements MapNG's Overpass query would have returned, in the shape `out body geom` gives (ways and relation members
carry their own node positions). The filter is a copy of MapNG's `OSM_FILTER` at the pinned commit
(`tests/pipeline/test_osm_pin.py` compares the copy with `services/overpassQuery.js` and fails if they differ):

- nodes with one of the listed tags (traffic signals, stop and give-way signs, street lamps, bollards, gates,
  traffic calming, trees, fuel, pubs, hotels and so on) inside the box;
- ways with one of the listed keys (`highway`, `building`, `landuse`, `natural` and so on) whose extent overlaps the box,
  whole, including a way that crosses the box with no node in it;
- relations with one of the listed keys that use one of those ways, with the geometry of every member way (a third
  read of the file fetches members that lie outside the box). A relation whose only link to the box is a node
  member is not taken; MapNG ignores node members.

The margin means a building or park that sticks out of the square arrives whole, as it does from Overpass; MapNG
clips everything to the square's bounds afterwards.

Byte-identical output: elements are sorted by type then id; keys are in a fixed order; coordinates are rounded to
7 decimals (OSM's own precision); the file is compact JSON, one element a line, UTF-8, LF. Nothing in it depends on
the clock, the machine or the order the file is read in.

## How the build avoids Overpass

- `fetch --pinned-osm AREA` sets `MAPNG_OSM_CUT`; `tools/mapng_fetch_area.mjs` then calls only `parseOverpassResponse`
  and never `fetchOSMData`. The five Overpass mirrors are also taken off the preload's allow list (elevation tiles
  from `s3.amazonaws.com` stay), so a stray Overpass request would be blocked and logged.
- `pinned-osm` runs the parse with an empty allow list. It fails (exit 5) if the preload's log shows any request at all.
- The cut's SHA-256 is checked against `data/sources/osm.json` before every use (exit 6 on a mismatch), so an
  edited or stale cut is refused.
- The export step already runs with no allowed hosts.

## What differs from the Overpass data

- The extract is a daily snapshot (the header says which); Overpass answers with the state of the minute. Edits made
  since the snapshot are not in it.
- Overpass uses the box exactly; the cut uses the box plus the margin. After MapNG's clip the two agree except at the edge.
- The `osm.json` produced earlier by Overpass (`H:\BelfastData\mapng\areas\belfast_slice`) is not the pinned one. The
  build moves to the pinned one on the next fetch or `pinned-osm`.

## First pin (2026-11-13)

Extract: 417,951,856 bytes, SHA-256 `7e52f404...17e9fe1`, OSM data of 2026-11-12 21:23 UTC. Cuts (elements):
slice 206 nodes, 4,594 ways, 15 relations; city_south 1,281 / 14,168 / 27; full 21,738 / 134,497 / 71.

Checks run:

- The slice cut made on its own and again inside the three-square run has the same SHA-256 (`9fe5abe0...a18c8f52`).
- `pinned-osm` run twice gives an `osm.json` with the same SHA-256 both times (`A4582E3B...B3C524`), and a third run
  under a hook that throws on any socket, DNS or fetch use reports 0 network attempts.
- MapNG's export accepted the cut: `export belfast_slice_pinned` finished, zero hosts contacted, driving-side check passed.
- Against the Overpass `osm.json` of 2026-11-11 for the same square: the same 824 road ways (none only on one side);
  buildings 2,613 against 2,610, landuse 399 against 400, vegetation 2,160 against 2,001 (the snapshot is a day newer).

## Checking

```text
uv run --no-project --python 3.12 --with pytest --with osmium --with pyproj --with numpy --with pyyaml --with requests python -m pytest tests/pipeline/test_osm_pin.py tests/mapng -q
```

From a git worktree, which has no submodule files, set `BELFAST_MAPNG_ROOT` to a checkout at the pinned commit
(the tests that need MapNG skip without one; `tools/mapng_export.py` reads the same variable and still requires the
pinned commit and a clean tree).
