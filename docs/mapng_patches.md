# MapNG patches: the `belfast` branch (T-412)

Decision: **patch files in this repo, applied to the pinned commit by a script.** This confirms the recommendation in `docs/decisions/001-generator.md`. No fork. `third_party/mapng` stays a submodule at the upstream pin `d605117d77857e91acb8234335e9f89263a94756`, so the repository never holds MapNG's code and the submodule can always be fetched from upstream.

## What is where

| Path | Content |
|---|---|
| `patches/mapng/*.patch` | One `git format-patch` file per patch (author, message, diff). Stored byte for byte (`.gitattributes`: `-text`) |
| `patches/mapng/series` | Patch names, in apply order |
| `patches/mapng/BASE` | The upstream commit the patches were made against. Must equal `PINNED_COMMIT` in `tools/mapng_export.py` |
| `tools/mapng_patches.py` | `status`, `apply [--reset]`, `save` |
| `tests/mapng/test_mapng_patches.py` | Tests on throwaway git repos: round trip, changed pin, extra or edited commit, a patch that no longer applies, dirty tree |

In the submodule, `apply` creates the local branch `belfast`: the pin, then one commit per patch (`git am`). This is the `belfast` branch of T-403, but it exists only in the local submodule; what is committed here is the patch files. The superproject's `git status` shows the submodule as "new commits" while the patches are applied; that is expected. Never `git add third_party/mapng`.

## How the wrapper enforces it

`tools/mapng_export.py check_pin` (used by `fetch`, `export` and `tools/mapng_ingame.py`) now requires "pin plus exactly these patches", and exits 3 (`EXIT_PIN`) with the reason otherwise:

- `BASE` differs from `PINNED_COMMIT`: the pin changed and the patches were not rebased;
- the submodule is at the pin with none of the patches applied (run `apply`);
- the number of commits above the pin differs from the series;
- a commit's `git patch-id --stable` differs from its patch file (a commit was edited or reordered);
- any tracked MapNG file has uncommitted changes.

With no `series` file the rule is the old one (HEAD is the pin, tree clean). `export.json` now records `mapng_pin` and `mapng_patches`.

## Workflow

```text
git submodule update --init third_party/mapng      # the pin (use --reference <other checkout>/.git/modules/third_party/mapng to skip the network)
cd third_party/mapng; npm ci --ignore-scripts      # see docs/mapng_export.md
python tools/mapng_patches.py apply                # branch belfast = pin + series (idempotent; fails if the state is unknown)

# add or change a patch (T-403, T-413, ...)
cd third_party/mapng; edit; git commit             # on branch belfast; add the test next to the change
python tools/mapng_patches.py save                 # rewrites patches/mapng/*.patch, series, BASE
git add patches/mapng                              # in the repo root, by name
```

`apply --reset` rebuilds `belfast` from the pin (it drops commits that were not `save`d). `git submodule update` moves the submodule back to the pin and drops the patches; the next `export` then exits 3 until `apply` is run again.

### Re-pinning

1. Move the submodule to the new upstream commit and set `PINNED_COMMIT` in `tools/mapng_export.py`.
2. `export` now exits 3 ("the pin changed"). `apply --reset` stops at the first patch that no longer applies and leaves the submodule clean at the new pin.
3. Rebase by hand: `git checkout -B belfast <new pin>`, `git am --3way patches/mapng/<name>` for each, fix conflicts, then `save` (this writes the new `BASE`).
4. Run MapNG's tests (806 of 812 pass on Windows, `docs/mapng_export.md`; the six failures are environment) and the new tests of each patch.

Offering generic patches upstream (the building rule, the junction step, mph signs) needs the user's GitHub account and stays a follow-up.

## Patch 0001: buildings cut back by overlap area and depth (gap 1)

File `services/buildingRoadConflicts.js` (and one report line in `services/exportBeamNGLevel.js`).

Before: an OSM building was cut back along the lanes' edge only when 3% or more of its footprint was on the driving lanes (and under 30%). Below 3% it was kept and not listed, so a large footprint with a small share stayed in the lane. OSM building 1260690275 (678 m2, 1.8 to 2.2% in the lanes) stood in the lane at (-211, -93), where the drive suite crashed.

After: the cut also applies when the overlap is at least **1 m2** and **0.15 m deep** past the cut line (the lanes' edge plus the existing 0.3 m kerb allowance). These are the same limits as the `building-in-lane` check of T-301, so the check and the generator agree. An overlap of that size that cannot be cut (holes, or a straight cut along the road line that would remove more than the overlap plus 10% of the footprint) is listed in `export_report.txt` with its area and depth, whatever its share. Hairline overlaps and walls flush with the kerb still stay and are not listed. Unchanged: ML outlines, the 3% to 30% rule, passages and tunnels.

Tests (`third_party/mapng/tests/buildingRoadConflicts.test.mjs`): a 40 x 20 m footprint with 2.5% in the lanes is cut back; a hairline overlap is left alone and not listed; a courtyard footprint is kept and listed.

### Result on the Slice (re-export of `belfast_slice`, same OSM, in `H:\BelfastData\mapng\t412\`)

| | Before (`mapng\out\belfast_slice.zip`) | After (`mapng\t412\belfast_slice.zip`) |
|---|---|---|
| MapNG report: OSM buildings cut back | 11 | 19 |
| MapNG report: OSM buildings kept and listed | 0 | 3 |
| T-301 `building-in-lane` findings | 8 (1 high, 5 medium, 2 low) | 5 (0 high, 5 medium) |
| 1260690275 at (-209.5, -81.9), on a driving road | high, 11.8 m2, 1.68 m deep | gone |

### Buildings that still stay in lanes

All five remaining findings are on `highway=service` ways (not in the navigation graph). Nothing is left on a residential, tertiary or other navigation road.

| Building | Where | What the check finds | Why it stays |
|---|---|---|---|
| 1259387657 (`building=construction`, about 76 x 59 m) | (76, -116) | 309 m2, up to 0.88 m deep | Plain service ways 404213828 and 860893189 run 28 m through the footprint (15% of it, 534 m2 by MapNG's lanes). Not tagged as a passage. MapNG keeps it as mapped and lists it. This is an OSM data question (a service road mapped through a construction site), not a cut-back case. Override only with evidence (OSNI or Mapillary, never Google) |
| 148546058 (Tesco Extra, `building=retail`) | (580, 787), (643, 861), (600, 769) | 3 sites of 5.4 m2, up to 1.20 m deep | Plain service ways 148546062, 148546066 and 148546070 run along its edge (tunnelled service ways under it are skipped). The share is 0.25%, but a straight cut along a road line would slice the very large footprint, so MapNG keeps it and lists it (20.6 m2, 1.59 m deep). A local cut (subtract the lane strip from the footprint with `polygon-clipping`, already a MapNG dependency) would remove it: follow-up |
| 1260691543 (`building=house`) | (-717, -797) | 6.4 m2, up to 0.37 m deep | MapNG cuts it back (4 m2, 1.38 m deep, in a standalone run of the rule). What the check still sees is 0.37 m deeper than the cut line on service way 609214743: the drawn lane is a little wider than MapNG's estimate. Not investigated further |

MapNG also lists 1258799627 (1.7 m2, 0.74 m deep, 5%) as kept; the check finds no wall of it in the lanes.

## Not verified

- No game run. The crash at (-211, -93) is expected to be gone because the walls are cut back, but only the drive suite rerun (T-413 Done-when) confirms it.
- The `building-in-lane` check reads the collision mesh of the tiles, as the car would; it was run on the re-exported zip only, with the OSM extract of the original export (`areas/belfast_slice/osm.json`).
