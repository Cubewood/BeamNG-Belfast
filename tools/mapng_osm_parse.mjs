#!/usr/bin/env node
// Turn a pinned OSM cut (pipeline/osm_pin.py) into the osm.json MapNG's export reads, with no network.
//
// MapNG's fetchOSMData asks Overpass for `out body geom` and hands the answer to parseOverpassResponse.
// A cut has the same shape, so this script calls the same parser, from the pinned MapNG commit, and
// writes what fetchArea writes: <area>/osm.json (a JSON array of features).
//
//   node --import tools/mapng_preload.mjs tools/mapng_osm_parse.mjs <cut.overpass.json> <area-dir>
//   env: MAPNG_ROOT (submodule folder)
//
// <area-dir>/meta.json supplies `bounds` (what the export clips to). Only services/osm.js's parser is
// called; no fetch function of it runs. Run under the preload with an empty MAPNG_ALLOW_HOSTS, any
// request at all is blocked and logged (tools/mapng_export.py pinned-osm does exactly that).
//
// Data: (c) OpenStreetMap contributors (ODbL).
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const ROOT = process.env.MAPNG_ROOT;
if (!ROOT) { console.error('MAPNG_ROOT must be set (use tools/mapng_export.py pinned-osm)'); process.exit(2); }
const [cutFile, areaDir] = process.argv.slice(2);
if (!cutFile || !areaDir) { console.error('usage: mapng_osm_parse.mjs <cut.overpass.json> <area-dir>'); process.exit(2); }

// osm.js reads and writes mirror health in localStorage when it is imported or used; give it a stand-in
// that never reaches the network.
const store = new Map([['mapng_overtureBuildings', 'false']]);
globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };

const { parseOverpassResponse } = await import(pathToFileURL(path.join(ROOT, 'services/osm.js')).href);
const meta = JSON.parse(fs.readFileSync(path.join(areaDir, 'meta.json'), 'utf8'));
const data = JSON.parse(fs.readFileSync(cutFile, 'utf8'));
const features = parseOverpassResponse(data, meta.bounds);
fs.writeFileSync(path.join(areaDir, 'osm.json'), JSON.stringify(features));
console.log(path.basename(areaDir), 'pinned osm: cut elements', data.elements.length, 'features', features.length,
  'roads', features.filter((x) => x.tags?.highway).length);
