# Overrides (T-207)

Our fixes to OpenStreetMap live in `data/overrides/*.yaml`, are reapplied on every build, and are checked against
the pinned cut so a fix is never applied silently to data that has changed under it.

| Thing | Where |
|---|---|
| Validator, apply step, CLI | `pipeline/overrides.py` |
| Fix files (committed) | `data/overrides/*.yaml` (none yet; no fix has evidence beyond a QA finding) |
| Tests | `tests/pipeline/test_overrides.py` |
| Output (not in git) | `H:\BelfastData\osm\cuts_applied\<area>.overpass.json`, plus `.extras.json` and `.report.json` |

Place in the pipeline: pinned cut (`pipeline/osm_pin.py`) -> **overrides** -> `tools/mapng_osm_parse.mjs` -> MapNG.
The applied cut has the same shape as the cut, so MapNG's parse reads it unchanged. With no overrides it is
byte-identical to the cut (checked on the real `slice` cut: SHA-256 `9fe5abe0...c18c8f52`, the manifest's value).

## File format

```yaml
version: 1
overrides:
  - id: hospital-road-example          # unique; a-z 0-9 and '-'
    target: {type: way, id: 1410878962}
    at: [54.5453, -5.9275]             # [lat, lon] of the fix; tells "outside this cut" from "gone"
    against:
      hash: "sha256:..."               # required: `overrides.py hash way 1410878962 --cut slice`
      version: 3                       # optional: the OSM version, for humans (the cut does not carry it)
      tags: {lanes: "3", oneway: "yes"}   # optional: tags as seen, so a conflict can say which tag moved
    change:                            # one or more of the keys below
      set_tags: {lanes: "2"}           # tag values are strings: quote numbers and yes/no
      delete_tags: [lanes:backward]    # each key must exist
      # geometry: [[lat, lon], ...]    # ways: whole new node list; closed stays closed
      # position: [lat, lon]           # nodes
      # delete: true                   # drops the element (and its slot in relations); alone
      # extra: {junction_control: signals}   # for our pipeline only: goes to the sidecar, never into OSM data
    evidence:                          # required, at least one
      - {kind: qa, ref: docs/decisions/001-generator.md, note: "finding 4"}
    author: yordi
    date: 2026-11-13
    note: free text                    # optional
```

- `kind` is one of `osni`, `dfi`, `lidar`, `mapillary`, `qa`, `survey`, `osm`, `open_data`. `ref` says where to look:
  a URL or image key, an issue card (`issues/Q-001.md`), a repo document. A `qa` ref that names a file under `docs/`,
  `issues/`, `feedback/`, `reports/` or `qa/` must exist (`validate` checks).
- Evidence is mandatory (guard rail 8). An entry without it is rejected. Mapillary images are cited by key or link, never committed.
- Google content (Maps, Street View, Earth, Places) is rejected anywhere in the entry, not only in `evidence` (guard rail 7).
  The check is a text match (`google`, `goo.gl`, `gstatic`, `ggpht`, `street view`, `maps.app`), so a note that says
  "not from Google" is rejected too; leave the word out.
- Unknown keys are errors, so a typo cannot silently disable a field. One entry per target; two fixes to one element are one entry.
- The data precedence of task.md section 3 still holds: DfI and OSNI evidence beats OSM, and an override is how a DfI
  or OSNI correction (or a Mapillary-backed one) gets into the OSM layer.

## The element hash

`against.hash` is the SHA-256 of the element as the cut holds it: compact JSON with sorted keys of the whole element
(tags, and for a way its node positions, for a node its position). A relation is hashed on its tags and member list
only; the geometry copied into its members depends on the cut's extent, not on the relation. The cut carries no OSM
version, so the hash is what detects a change; `against.version` is a note for the human reading a conflict.

To write an entry: `overrides.py hash TYPE ID --cut slice`, paste the hash (and the tags, if wanted) into `against`.

## What the apply step does

For each entry, against the cut as given:

| Outcome | When | Effect |
|---|---|---|
| `applied` | target present and its hash is the one recorded | change made |
| `conflict-changed` | target present, hash differs | **not applied**, reported with the tag differences (if `against.tags` is given) or "geometry or members changed" |
| `conflict-missing` | target absent and `at` lies inside the cut's area | not applied: the element was deleted, merged or renumbered |
| `conflict-invalid` | hash matches but the change does not fit (deleting a tag that is not there, opening a closed way, target removed by another entry) | not applied |
| `resolved-upstream` | hash differs, but the element already holds every stated change | not applied, not a conflict; retire the entry |
| `outside-area` | target absent and `at` lies outside the cut's area (a `slice` build with a fix in `full`) | skipped |

Conflicts stop the build: `apply` writes nothing and exits 3. `--keep-going` writes the output without the
conflicting entries and exits 0; the report still lists them. Invalid files (schema, evidence, Google) exit 2 and
nothing is applied. Resolving a conflict is a human step: look at the new element, rewrite or retire the entry, and
paste the new hash.

Deleting a way also removes it from the relations that list it; a relation left with no way members is dropped and
the outcome notes say so. A geometry edit is copied into the relation members that carry that way's geometry.

Determinism: hashes are checked against the cut as given, then changes are applied in (type, id) order; elements go
out sorted by type and id and written with `osm_pin.cut_bytes`. No clock, no network. Same cut and same files, same bytes
(the report holds the cut's and the output's SHA-256).

## Commands

```text
set P=uv run --no-project --python 3.12 --with pyyaml --with osmium --with pyproj --with numpy --with requests python
%P% pipeline/overrides.py validate
%P% pipeline/overrides.py hash way 1410878962 --cut slice
%P% pipeline/overrides.py apply --cut slice [--keep-going]
```

`--cut` is an area name (checked against the SHA-256 in `data/sources/osm.json`) or a cut file.

## Not wired yet

`tools/mapng_export.py pinned-osm` still reads the plain cut. Pointing it at `cuts_applied\<area>.overpass.json`
(and calling `apply` first) is the one change left; it is kept out of T-207 because T-412 is editing that file.
Extras (`.extras.json`) have no consumer yet: the markings and road steps will read them when they need them.

## Real overrides

None are committed. The candidates in `docs/decisions/001-generator.md` are findings, not confirmed corrections:
building 1260690275 stands 1.32 m from a centreline, and Hospital Road way 1410878962 is tagged `oneway=yes` with
`lanes:backward=1`. An entry goes in only once someone has checked OSNI, DfI or Mapillary evidence, which T-505 and
T-506 (fix proposer) are meant to produce.
