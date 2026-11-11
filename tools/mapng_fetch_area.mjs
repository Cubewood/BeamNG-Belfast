#!/usr/bin/env node
// Our own area fetch for MapNG's headless export, without a GPXZ key.
//
// MapNG's scripts/sweep/fetchArea.mjs throws without a GPXZ_API_KEY even with
// T30_ONLY=1 (T-101 risk 5). This script is its 30 m path alone: the free
// Terrarium heightmap (resampled the same way) plus OSM through MapNG's own
// fetchOSMData, written in the folder layout exportArea.mjs reads:
//   <MAPNG_AREAS>/<name>/{t30.f32, osm.json, meta.json}
// The elevation and bounds code below follows fetchArea.mjs at the pinned commit
// (MapNG, MIT, (c) 2026 Nikki Luzader); the GPXZ branch and the Overture merge
// are left out. Overture buildings are switched off (mapng_overtureBuildings=false).
//
//   node --import tools/mapng_preload.mjs tools/mapng_fetch_area.mjs <name> <lat> <lng> [size=512]
//   env: MAPNG_ROOT (submodule folder), MAPNG_AREAS, MPP (default 1)
//
// Data: (c) OpenStreetMap contributors (ODbL); elevation: AWS Terrain Tiles (Terrarium).
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const ROOT = process.env.MAPNG_ROOT;
const AREAS_DIR = process.env.MAPNG_AREAS;
if (!ROOT || !AREAS_DIR) { console.error('MAPNG_ROOT and MAPNG_AREAS must be set (use tools/mapng_export.py fetch)'); process.exit(2); }
const [name, lat, lng, size] = process.argv.slice(2);
if (!name || !Number.isFinite(+lat) || !Number.isFinite(+lng)) { console.error('usage: mapng_fetch_area.mjs <name> <lat> <lng> [size=512]'); process.exit(2); }

const imp = (rel) => import(pathToFileURL(path.join(ROOT, rel)).href);
const { decode } = createRequire(path.join(ROOT, 'package.json'))('fast-png');
const { createLocalToWGS84 } = await imp('services/geoUtils.js');
const { resampleToMeterGrid } = await imp('services/terrainResampler.js');
const { decodeTerrariumPixels, blendTerrariumTileSeams, sampleTerrariumHeights, wrapMosaicX } = await imp('services/terrariumSampling.js');

// Overture off; a Node localStorage stand-in (osm.js reads and writes mirror health there)
const store = new Map([['mapng_overtureBuildings', 'false']]);
globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };

const realFetchBase = globalThis.fetch;
globalThis.fetch = (url, init = {}) => {
  const headers = new Headers(init.headers || {});
  if (!headers.has('User-Agent')) headers.set('User-Agent', 'BeamNGBelfast-sample-fetch/1.0 (local diagnostics)');
  return realFetchBase(url, { ...init, headers });
};

const MIN_OSM_FEATURES = 50;
const center = { lat: +lat, lng: +lng };
const SIZE = +(size || 512);
if (Math.log2(SIZE) % 1) throw new Error(`size ${SIZE} is no power of two`);
const MPP = +(process.env.MPP || 1);
const NO_DATA = -99999;
const OUT = path.join(AREAS_DIR, name);
fs.mkdirSync(OUT, { recursive: true });

// computeMetricFetchBounds (terrain.js), as fetchArea.mjs
const toWGS84 = createLocalToWGS84(center.lat, center.lng);
const hw = (SIZE * MPP) / 2 + 4;
const corners = [[-hw, hw], [hw, hw], [-hw, -hw], [hw, -hw]].map((c) => toWGS84.forward(c));
const bounds = {
  north: Math.max(...corners.map((c) => c[1])), south: Math.min(...corners.map((c) => c[1])),
  east: Math.max(...corners.map((c) => c[0])), west: Math.min(...corners.map((c) => c[0])),
};

// global 30 m (Terrarium z15), as fetchArea.mjs
const Z = 15; const TS = 256;
const proj = (la, lo) => { const s = Math.sin((la * Math.PI) / 180); const z = TS * 2 ** Z; return { x: ((lo + 180) / 360) * z, y: (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * z }; };
const nw = proj(bounds.north, bounds.west); const se = proj(bounds.south, bounds.east);
const minTx = Math.floor(nw.x / TS); const maxTx = Math.floor(se.x / TS); const minTy = Math.floor(nw.y / TS); const maxTy = Math.floor(se.y / TS);
const MW = (maxTx - minTx + 1) * TS; const MH = (maxTy - minTy + 1) * TS;
const rgba = new Uint8Array(MW * MH * 4);
for (let ty = minTy; ty <= maxTy; ty++) {
  for (let tx = minTx; tx <= maxTx; tx++) {
    const res = await fetch(`https://s3.amazonaws.com/elevation-tiles-prod/terrarium/${Z}/${((tx % 2 ** Z) + 2 ** Z) % 2 ** Z}/${ty}.png`);
    if (!res.ok) throw new Error(`terrarium tile ${Z}/${tx}/${ty}: HTTP ${res.status}`);
    const png = decode(new Uint8Array(await res.arrayBuffer()));
    const ch = png.channels ?? png.data.length / (png.width * png.height);
    for (let y = 0; y < TS; y++) {
      for (let x = 0; x < TS; x++) {
        const p = (y * TS + x) * ch; const q = (((ty - minTy) * TS + y) * MW + (tx - minTx) * TS + x) * 4;
        rgba[q] = png.data[p]; rgba[q + 1] = png.data[p + 1]; rgba[q + 2] = png.data[p + 2]; rgba[q + 3] = 255;
      }
    }
  }
}
const th = decodeTerrariumPixels(rgba, MW, MH, NO_DATA);
blendTerrariumTileSeams(th, MW, MH, NO_DATA);
const sampler = (la, lo) => { const p = proj(la, lo); return sampleTerrariumHeights(th, MW, MH, wrapMosaicX(p.x - minTx * TS, Z, MW, TS), p.y - minTy * TS, NO_DATA); };
const sigmaPx = 5 / MPP; const smoothR = Math.round((Math.sqrt(6 * sigmaPx * sigmaPx + 1) - 1) / 2);
const t30 = await resampleToMeterGrid({ type: 'sampler', sampler }, center, SIZE, SIZE, 'bilinear', smoothR >= 1 ? smoothR : false, true, bounds, true);
fs.writeFileSync(path.join(OUT, 't30.f32'), Buffer.from(t30.heightMap.buffer));
const meta = { name, center, size: SIZE, mpp: MPP, fetchBounds: bounds, bounds: t30.bounds, gpxzSmooth: false, resolutions: [], rasterRes: null, rasterSize: null };
fs.writeFileSync(path.join(OUT, 'meta.json'), JSON.stringify(meta, null, 1));
console.log(name, 'terrarium z15 tiles', (maxTx - minTx + 1) * (maxTy - minTy + 1));

// OSM, as fetchArea.mjs (no `location` stand-in: that would switch on MapNG's own edge-cache endpoint)
const { fetchOSMData } = await imp('services/osm.js');
const features = await fetchOSMData(meta.bounds);
if (features.length < MIN_OSM_FEATURES) {
  fs.rmSync(path.join(OUT, 'meta.json'));
  console.error(`${name}: only ${features.length} OSM features (an outage?): not saved, try again`);
  process.exit(1);
}
fs.writeFileSync(path.join(OUT, 'osm.json'), JSON.stringify(features));
console.log(name, 'osm features', features.length, 'roads', features.filter((x) => x.tags?.highway).length);
