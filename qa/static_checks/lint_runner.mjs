#!/usr/bin/env node
// Run MapNG's own level linter (scripts/lib/levelLint.mjs) on a level zip, with the stock-file check
// switched on from OUR game index (qa/static_checks/game_index.py), so nothing is written under third_party/.
//
//   node lint_runner.mjs <mapng-dir> <level.zip> <game_index.txt|-> <out.json>
//
// MapNG's own CLI (scripts/lintLevel.mjs) builds its game index from refs/beamng/content inside the MapNG
// checkout; this wrapper reads the index file the Python side writes under H:\BelfastData instead.
// Output: the linter's result object ({ level, findings, counts }) as JSON.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [mapngDir, zipPath, indexPath, outPath] = process.argv.slice(2);
if (!mapngDir || !zipPath || !indexPath || !outPath) {
  console.error('usage: lint_runner.mjs <mapng-dir> <level.zip> <game_index.txt|-> <out.json>');
  process.exit(2);
}
const { lintLevelZip } = await import(pathToFileURL(path.join(mapngDir, 'scripts/lib/levelLint.mjs')).href);

let opts = {};
if (indexPath !== '-') {
  const lines = fs.readFileSync(indexPath, 'utf8').split('\n').slice(1);
  const set = new Set(); const mats = new Set(); const refs = new Set();
  for (const l of lines) {
    if (!l) continue;
    if (l.startsWith('M ')) mats.add(l.slice(2));
    else if (l.startsWith('T ')) refs.add(l.slice(2).replace(/^\//, ''));
    else set.add(l);
  }
  const has = (rel) => {
    const r = rel.toLowerCase();
    const alts = [r, r.replace(/\.(png|dds|jpg)$/, '.dds'), r.replace(/\.(png|dds|jpg)$/, '.png'), r.replace(/\.(png|dds|jpg)$/, '.jpg'), `${r}.link`];
    return alts.some((a) => set.has(a));
  };
  opts = { gameHas: has, gameMaterials: mats, gameDangling: (rel) => refs.has(rel.toLowerCase()) };
}
const r = await lintLevelZip(fs.readFileSync(zipPath), opts);
fs.writeFileSync(outPath, JSON.stringify(r, null, 1));
const errors = r.findings.filter((f) => f.level === 'error').length;
const warns = r.findings.filter((f) => f.level === 'warn').length;
console.log(`${errors} error(s), ${warns} warning(s)`);
