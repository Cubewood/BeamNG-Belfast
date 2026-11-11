// Preloaded into every MapNG node process started by tools/mapng_export.py
// (node --import <this file> ...). It is loaded before MapNG code runs, so the
// fetch it installs is the one MapNG's headless polyfill (scripts/sweep/polyfill.mjs)
// captures as "the real fetch". MapNG itself is never edited.
//
// What it does:
//   1. serves /country_rules.json from MAPNG_COUNTRY_RULES. MapNG's
//      countryRules.js fetches that relative URL, which fails in Node, and the
//      export then falls back to right-hand traffic and US markings (T-101 risk 3);
//   2. blocks every absolute http(s) request whose host is not listed in
//      MAPNG_ALLOW_HOSTS (comma list, default none), so no satellite tiles, Overture
//      tiles, Google or any other host can be reached by accident;
//   3. appends one line per absolute request to MAPNG_NETLOG ("ALLOW host" or
//      "BLOCK host"), so the hosts really contacted are on record.
import fs from 'node:fs';

const allow = new Set((process.env.MAPNG_ALLOW_HOSTS || '').split(',').map((h) => h.trim().toLowerCase()).filter(Boolean));
const netlog = process.env.MAPNG_NETLOG || '';
const rulesFile = process.env.MAPNG_COUNTRY_RULES || '';
const upstream = globalThis.fetch;

const note = (verdict, host) => {
  if (!netlog) return;
  try { fs.appendFileSync(netlog, `${verdict} ${host}\n`); } catch { /* logging must never break a run */ }
};

export function hostOf(url) {
  try { return new URL(url).hostname.toLowerCase(); } catch { return ''; }
}

globalThis.fetch = async (input, init) => {
  const url = typeof input === 'string' ? input : String(input?.url || input || '');
  if (url === '/country_rules.json') {
    if (!rulesFile) throw new Error('mapng_preload: MAPNG_COUNTRY_RULES is not set');
    const text = fs.readFileSync(rulesFile, 'utf8');
    return { ok: true, status: 200, json: async () => JSON.parse(text) };
  }
  if (/^https?:\/\//i.test(url)) {
    const host = hostOf(url);
    if (!allow.has(host)) {
      note('BLOCK', host);
      throw new Error(`mapng_preload: request to ${host} blocked (not in MAPNG_ALLOW_HOSTS)`);
    }
    note('ALLOW', host);
    return upstream(input, init);
  }
  // other relative URLs (/mapng_flag_static.zip, cubemap, ...) are answered by MapNG's polyfill
  // before they get here; anything else has no network to go to
  throw new Error(`mapng_preload: unexpected relative fetch ${url}`);
};
