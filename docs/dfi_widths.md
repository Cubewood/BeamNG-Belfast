# DfI road widths (T-205)

DfI Roads publishes the width of almost every adopted road in Northern Ireland. This step reads that data, matches it
to the pinned OSM ways of the three map squares, and writes one width per OSM way with a confidence score. T-406
(road widths, below) reads the output.

| Thing | Where |
|---|---|
| Step | `pipeline/dfi.py` |
| Tests | `tests/pipeline/test_dfi.py` (synthetic data only, no network) |
| Manifest (committed) | `data/sources/dfi.json`: URL, dataset page, download time, size, SHA-256, licence, attribution |
| Raw files (not in git) | `H:\BelfastData\dfi\HIGHWAY_NETWORK.CSV`, `carriageway-details-all-export-5-july-2021.xlsx` |
| Parsed (not in git) | `H:\BelfastData\dfi\items.csv` (587,138 rows), `sections.csv` (72,842 rows) |
| Output (not in git) | `H:\BelfastData\dfi\widths_slice.json`, `widths_city_south.json`, `widths_full.json` |

Data: Department for Infrastructure (DfI Roads) via OpenDataNI, UK Open Government Licence v3.0. "Contains public
sector information licensed under the Open Government Licence v3.0." The raw files stay out of git; the manifest
and code are in it. No Google content is used anywhere.

## What the two datasets are

- **Highway Network** (CSV, 11 MB, a live link rebuilt every evening). One row per road section: code
  (`7035A0055_17`), name, division, type (single or dual carriageway, roundabout, footway), adoption status, length,
  class (M, A, B, C, U, F). **No coordinates.**
- **Carriageway Details** (xlsx, 55 MB, an export of 5 July 2021, four sheets by division). One row per stretch of a
  section: width in metres, start and end chainage along the section, surface, and **one point** (Easting,
  Northing, whole metres). Every row is type `CARRIAGEWAY`; footways are not in it.

So geometry comes from Carriageway Details and names, classes and types from the Highway Network, joined on the
section code. All 199 sections inside the slice box, and 3,936 of 3,941 in the full box, are found in the Highway Network.

Two things I checked in the data, because the dataset description does not say:

- The point of a row is where the stretch **starts**. Consecutive rows of a section are as far apart as their
  chainage difference (for example 158 m of chainage, 159 m between the points). The step uses this to draw each
  stretch as a line from its point to the next row's point.
- The coordinates are **Irish Grid (EPSG:29903), not ITM**: Bangor items read about 350,000 east where ITM would read
  about 750,000. The datum shift in `config/aoi.yaml` (7-parameter Helmert, 1 to 2 m by OSi's own figure) is therefore
  in the path, and OSi's grid-based shift is not used. Measured result: DfI's own points sit a **median 0.76 m** from the
  OSM way they were matched to (0.75 m on the full box, systematic part under 0.3 m), so the shift is good enough here
  and no correction is applied. The whole-metre rounding of the points is part of that 0.76 m.

## Getting the files

```text
set P=uv run --no-project --python 3.12 --with pyproj --with numpy --with pyyaml --with requests --with shapely python
%P% pipeline/dfi.py download     # once; refuses to replace existing files without --force
%P% pipeline/dfi.py verify       # size and SHA-256 against data/sources/dfi.json
%P% pipeline/dfi.py parse        # xlsx and csv -> items.csv, sections.csv (stdlib parser, about 30 s)
%P% pipeline/dfi.py match        # all three squares (about 15 s); --area slice for one
%P% pipeline/dfi.py sample -n 30 # the 30-way spread used for the hand check below
```

How the files were found: the CKAN API of OpenDataNI (`admin.opendatani.gov.uk/api/3/action/package_show`) answered
403 to every request I made, and `www.opendatani.gov.uk` is a front end without that API. The two resource URLs were
read from the dataset pages themselves (`www.opendatani.gov.uk/dataset/highway-network` and `.../carriageway-details`,
embedded page data). The Carriageway Details link answers 403 to a bare `curl` and 302 (to signed object storage) to a
client that names itself; the step sends `BeamNGBelfast-dfi/1.0 (...)`. The Highway Network is a live file, so a
later download will differ from the pinned SHA-256 and will need a new manifest (and the match re-run).

Because the xlsx is from July 2021, roads built or re-surfaced since have no row, and a road re-marked since may have
a different width. The Highway Network is 2026 and shows 199 sections in the slice box, all with widths in the xlsx.

## How a width gets onto an OSM way

1. Items inside the square's Irish Grid box (plus 250 m) become metres in ITM. OSM ways come from the pinned cut
   (`H:\BelfastData\osm\cuts\<area>.overpass.json`, SHA-256 checked against `data/sources/osm.json`), drivable
   classes only (motorway to service, living street, road; not footways or area ways).
2. Each item becomes a stretch (a line to the next row's point, or a lone point if the chainage does not run on or
   the distance disagrees with it by more than 4 m) and is sampled every 8 m. A sample counts for its share of the
   item's length; a lone point counts for at most 10 m.
3. Items narrower than 2 m or wider than 20 m are left out of the width (junction mouths, turning heads, car park
   edges; 118 of 2,101 items in the slice box).
4. A sample goes to the cheapest OSM way within reach, cost = distance + a penalty for the name:

   | Name relation (DfI street v OSM `name`) | Penalty | Reach |
   |---|---|---|
   | exact, or same road number (A55 v `A0055` in the section code) | 0 m | 30 m |
   | partial ('Ormeau Road' v 'Upper Ormeau Road', or Road v Street) | 4 m | 18 m |
   | unknown (the way has no name) | 10 m | 18 m |
   | conflict (both have names and they differ) | never matched | |

   Names are compared after spelling out DfI's abbreviations (RD, AV, GDS, CRE, GVE, ST becoming SAINT when leading and
   STREET when trailing and so on) and dropping the section digit that DfI glues on ('RD1').
5. Per way: the width is the length-weighted median of the matched items' widths; quartiles are kept.

## Confidence

`confidence` is a product of five parts, each in `parts` of the output:

| Part | 1.0 when | Falls to |
|---|---|---|
| geometry | DfI's own points are within 4 m of the way (median) | 0.3 at 18 m |
| name | the street name is equal | 0.85 near match or same road number, 0.55 when the way has no name |
| coverage | DfI covers the whole way | 0.4 when it covers none (0.4 + 0.6 x fraction) |
| spread | all widths on the way agree | 0.5 when the inter-quartile range is 2 m or more |
| class | DfI class and OSM class are within one rank | 0.8 when two ranks apart |

Tiers: **high** 0.70 and over, **medium** 0.40 to 0.70, **low** below 0.40. A way with low confidence still has a
width; T-406 should decide what to do with it. The numbers are chosen by me, not fitted: with 30 hand-checked ways
there is nothing to fit them to (see below).

## Output

`widths_<area>.json`: `inputs` (SHA-256 of both DfI files, the OSM cut and the extract), `settings`, `coverage`, `ways`
and `unmatched_in_area`. Each way, keyed by OSM way id, has: `width_m`, `width_q1_m`, `width_q3_m`, `confidence`,
`tier`, `parts`, `offset_median_m`, `covered_m`, `way_length_m`, `coverage`, `n_items`, `samples`, `sections`
(DfI section code to metres matched), `section_main`, `name_relation`, plus the OSM `highway`, `name`, `ref`, `width_tag`,
`lanes`, `oneway`, `length_m`, `in_area` (any vertex inside the square without the 100 m margin).

## Coverage report

Counts are OSM drivable ways with a vertex inside the square (the 100 m margin of the cut is not counted). DfI
adoption: every section in all three boxes is `Adopted` except 5 in the full box that are not in the Highway Network.

| Square | OSM ways | km | Ways with a width | km with a width | high / medium / low (km) |
|---|---|---|---|---|---|
| slice | 495 | 61.8 | 196 | 39.2 (63 %) | 18.7 / 16.5 / 4.0 |
| city_south | 1,410 | 166.8 | 535 | 101.6 (61 %) | 65.1 / 23.2 / 13.3 |
| full | 13,167 | 1,178.4 | 5,360 | 709.9 (60 %) | 430.1 / 195.5 / 84.3 |

The share is low because of `service` ways: 230 of the slice's 495 ways and 19.2 of 61.8 km are OSM service roads
(car parks, driveways, hospital grounds, estate lanes), and DfI adopts almost none of them (7 per cent of service km
get a width). **Without service ways the slice has 37.9 of 42.6 km, 89 per cent, with a width.**

Slice by OSM class:

| Class | Ways | km | With a width | km with a width | Share | high / medium / low (km) | Median width (p10 to p90) m |
|---|---|---|---|---|---|---|---|
| trunk | 38 | 5.07 | 27 | 4.55 | 90 % | 1.84 / 1.99 / 0.71 | 7.6 (6.2 to 17.5) |
| secondary | 27 | 4.42 | 15 | 3.99 | 90 % | 2.99 / 1.00 / 0 | 6.6 (5.4 to 10.0) |
| tertiary | 17 | 5.36 | 8 | 5.05 | 94 % | 1.00 / 3.24 / 0.81 | 9.0 (5.0 to 14.3) |
| unclassified | 23 | 8.37 | 12 | 7.85 | 94 % | 3.13 / 4.71 / 0.01 | 6.2 (5.0 to 7.6) |
| residential | 160 | 19.39 | 116 | 16.44 | 85 % | 9.76 / 5.35 / 1.33 | 5.6 (4.8 to 10.3) |
| service | 230 | 19.21 | 18 | 1.34 | 7 % | 0 / 0.16 / 1.19 | 6.8 (5.0 to 12.7) |

The slice has no motorway, primary or living-street ways; the full box has 66 motorway ways (median 7.3 m), 515
primary (median 11.3 m).

DfI side, slice: 2,101 items, 5,443 samples; 87.9 per cent of the DfI length found an OSM way. Of the rest, 436
samples had no way within reach and 162 had only ways with a different name. Matched length by name relation
(slice): exact 36.8 km, partial 0.4, road number 0.1, unknown 2.0 (the unknown ones are mostly unnamed residential
and service ways). Median of each way's offset: 0.8 m, 90th percentile 3.8 m, 99th 9.7 m.

Against OSM's own `width` tags: almost none to compare with (the slice has 0 tagged ways, the full box 30, 20 of them
matched). On those 20 the median difference is 1.0 m, 9 agree within 0.75 m, and OSM is the narrower in 9 of the rest.
That fits DfI measuring the whole carriageway and mappers tagging the lane.

### What stays unmatched

- Service roads, as above.
- Unnamed residential ways whose DfI neighbour is a longer section (several OSM ways to one section; the point that
  ends up nearest another named way takes the item). Castledillon Road (156202503) is an example: its DfI items lie
  7 to 13 m from it but a neighbouring way of the same name is nearer.
- Roads built since July 2021, and roads DfI does not adopt.
- Ways where a different street name is on the OSM way: for example the A55 'Belvoir Road' where the DfI section is
  called 'Milltown Rd'. The A55 road number rescues it when the section code carries it.

## Hand-checked sample of 30 ways (slice)

**Who checked, and how.** I (the builder agent, Sonnet 5.5) checked this from the data, on 13 November 2026. The user
has not checked it. It was **not** checked against imagery or a site visit, and no Google content was used.
For each way I looked at: do the street names agree; is the OSM way's length similar to the DfI section's; do the
OSM ways that meet at its ends carry the cross streets the DfI section description names ('X to Y'); is the DfI point
close to the way. "Right" means all of that agrees. It says the **match** is right, not that the **width** is true:
I have no independent measurement of any width, only plausibility (a one-way 2-lane slip at 5.2 m is narrow, a
rural C road at a constant 10.0 m is wide).

How the 30 were picked: `pipeline/dfi.py sample`, a fixed spread (2 motorway, 2 trunk, 4 primary, 4 secondary, 3
tertiary, 3 unclassified, 8 residential, 1 living street, 3 service as far as the class exists in the slice, then the
longest remaining ways), and within each class evenly spaced through the list ordered by confidence, so high, medium and
low all appear. No cherry-picking of ways. The matcher was tuned after I read a first 30 (nearest-way cost with a name
penalty, a shorter reach for partial names, more DfI abbreviations, a road-number rule, using DfI's own points for the
offset); the 30 below are from the final run, so this is **not an independent test** of the final rules, only a check
that nothing obviously wrong is left in a spread of ways.

Result: **25 right, 1 likely right, 3 plausible but not provable (the OSM way has no name), 1 doubtful, 0 wrong.**

| # | OSM way | Class | OSM name (ref) | DfI section | DfI name | Offset m | Width m (quartiles) | Confidence | My check | Why |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 863855829 | trunk_link | (none) | 7035C0302_09 | NEWTOWNBREDA RD1 | 0.35 | 17.7 (17.7-18.8) | 0.21 low | Right | OSM way 76.9 m, DfI section 75.3 m, ends meet Belvoir Rd and the C302 roundabout. Width 17.7 m looks flared for a 2-lane link; confidence correctly low. |
| 2 | 1422555647 | trunk | Belvoir Road (A55) | 7035A0055_18 | BELVOIR RD1 | 0.96 | 10.7 (10.7-10.7) | 1.0 high | Right | Name equal, 0.96 m off; the section is the south carriageway. |
| 3 | 26345421 | secondary | Milltown Road (B205) | 7035B0023_08 | MILLTOWN RD2 | 2.49 | 7.3 (7.3-8.0) | 0.436 medium | Right | Name equal, ends at Belvoir Rd as the section says. Only 21 per cent of the way covered, so medium. |
| 4 | 932109005 | secondary | Milltown Road (B205) | 7035B0023_11 | MILLTOWN RD2 | 0.17 | 6.3 (5.2-6.3) | 0.718 high | Right | Name equal, one end meets Hospital Rd as the section 'Hospital Rd to Belvoir Rd' says. |
| 5 | 860893153 | secondary | Milltown Road (B205) | 7035B0023_11 | MILLTOWN RD2 | 0.48 | 5.2 (5.2-6.0) | 0.8 high | Right | Same DfI section as the previous way; 5.2 m for a one-way 2-lane slip is narrow but plausible. |
| 6 | 404102951 | secondary | Hospital Road (B205) | 7035B0023_10 | HOSPITAL RD1 | 1.98 | 6.5 (6.5-6.5) | 1.0 high | Right | Name equal. OSM ref is B205 but DfI files Hospital Rd under B0023: possibly an OSM ref error, not a matching error. |
| 7 | 932108000 | tertiary | Hospital Road (C302) | 7035C0302_06 | HOSPITAL RD1 | 0.59 | 14.3 (14.3-15.8) | 0.366 low | Right | Name and ref (C302) agree. 14.3 m is a junction approach (several lanes), not a running width; low confidence fits. |
| 8 | 135069684 | tertiary | Newtownbreda Road (C302) | 7035C0302_03 | NEWTOWNBREDA RD1 | 1.56 | 7.9 (7.2-9.8) | 0.499 medium | Right | Name and ref agree, ends at Purdysburn Rd and Hospital Rd. Widths vary 7.2 to 9.8 m along it. |
| 9 | 386981923 | tertiary | Purdysburn Road (C302) | 7035C0302_02 | PURDYSBURN RD1 | 0.55 | 10.0 (10.0-10.0) | 0.887 high | Right (width unverified) | Name and ref agree. A constant 10.0 m is wide for a rural C road; I cannot say if it includes verges or lane markings. |
| 10 | 366053151 | unclassified | Milltown Hill | 7035U0510_07 | MILLTOWN HILL1 | 1.31 | 7.5 (7.5-10.2) | 0.366 low | Right | 7 m stub at the Ballylesson Rd junction; name equal. Tiny way, so only 55 per cent covered. |
| 11 | 370927381 | unclassified | Old Milltown Road | 7035U0500_28 | OLD MILLTOWN RD2 | 2.5 | 5.7 (5.7-5.7) | 0.926 high | Right | Name equal, 2.5 m off, its ends meet Old Milltown Rd and Belvoir Drive. |
| 12 | 1083622670 | unclassified | Milltown Hill | 7035U0510_07 | MILLTOWN HILL1 | 0.84 | 5.0 (5.0-5.0) | 1.0 high | Right | Name equal; length 46 m of a 380 m DfI section that OSM has cut into several ways. |
| 13 | 371345029 | residential | (none) | 7035U0500_62 | BELVOIR DR1 | 0.99 | 5.0 (5.0-14.0) | 0.154 low | Plausible, not provable | OSM way has no name. DfI 'Belvoir Dr ... No.228 to 240' is 43 m, the way 75 m; it hangs off Belvoir Drive. Width 5.0 with a 14 m upper quartile: unreliable. |
| 14 | 371345000 | residential | (none) | 7035U0500_65 | DRUMART DR1 | 0.2 | 4.0 (4.0-4.0) | 0.412 medium | Plausible, not provable | No OSM name; it joins Drumart Drive and the section is 'Drumart Dr ... (access rd)'. 0.2 m off. |
| 15 | 41418203 | residential | Cedarhurst Rise | 7035U0511_24 | CEDARHURST RISE1 | 0.72 | 5.4 (5.4-8.9) | 0.5 medium | Right | Name equal, 70 m against 74 m in DfI, joins Cedarhurst Rd as the section says. |
| 16 | 306231738 | residential | Gray's Park Avenue | 7035U0500_20 | GRAY'S PK AV1 | 0.16 | 6.0 (6.0-6.0) | 0.516 medium | Right | Name equal; the DfI section is only 'No.7 to No.23' of a 295 m way, so 19 per cent covered. |
| 17 | 370200858 | residential | Greer Park Heights | 7035U0511_91 | GREER PK HTS1 | 0.8 | 6.0 (6.0-7.0) | 0.75 high | Right | Name equal, whole way covered. |
| 18 | 370707397 | residential | Royal Lodge Avenue | 7035U0527_13 | ROYAL LODGE AV1 | 1.02 | 5.5 (5.0-5.6) | 0.85 high | Right | Name equal, whole way covered, 1.0 m off. |
| 19 | 370202290 | residential | Bests Hill Glen | 7035U0527_04 | BESTS HILL GLEN1 | 0.59 | 5.5 (5.5-5.5) | 0.975 high | Right | Name equal; joins Bests Hill as the section 'Bests Hill to end' says. |
| 20 | 372846477 | residential | Beechwood Grove | 7035U0511_57 | BEECHWOOD GVE2 | 0.31 | 5.3 (5.3-5.3) | 1.0 high | Right | Name equal, 66.8 m against 71.1 m in DfI. |
| 21 | 847368563 | service | (none) | 7065B0023_05 | BALLYLESSON RD1 | 1.84 | 5.4 (5.4-5.4) | 0.198 low | Doubtful | Unnamed service way 96 m, matched on one item of a 2.6 km Ballylesson Rd section 1.8 m away. Probably a lay-by or old alignment; the width may not apply. |
| 22 | 694938314 | service | (none) | 7035U0500_15 | DRUMART DR1 | 3.63 | 5.0 (5.0-5.0) | 0.336 low | Plausible, not provable | Unnamed 24 m service way next to Drumart Drive; DfI section runs 'Dunseverick Av at church to Dunseverick Av'. 3.6 m off, 35 per cent covered. |
| 23 | 370927383 | service | (none) | 7035U0500_21 | GRAYS PK2 | 5.39 | 7.0 (7.0-7.0) | 0.492 medium | Likely right | Unnamed; joins Gray's Park, which DfI calls 'Grays Pk: Belvoir Dr to Milltown Rd'. 36.6 m way, 93 per cent covered, 5.4 m off. |
| 24 | 185555365 | unclassified | Ballycoan Road | 7065U0146_22 | BALLYCOAN RD1 | 1.33 | 5.3 (4.9-6.0) | 0.435 medium | Right | Name equal; DfI section is 2.0 km of this 3.0 km way, ends at Purdysburn Hill (B205) as the section says. |
| 25 | 374712136 | unclassified | Belvoir Drive | 7035U0500_01 | BELVOIR DR1 | 1.72 | 6.7 (6.5-6.8) | 0.778 high | Right | Name equal and 1,877 m against 1,864 m in DfI: the same road. |
| 26 | 127284812 | tertiary | Fort Road (C299) | 7065C0299_03 | FORT RD2 | 0.33 | 5.0 (4.5-5.0) | 0.453 medium | Right | Name and ref (C299) agree, 1,774 m against 1,777 m. Only 5 items, so 20 per cent covered. |
| 27 | 362852746 | unclassified | Mill Road West | 7035U0144_01 | MILL RD W1 | 0.82 | 4.6 (4.5-5.3) | 0.539 medium | Right | Name equal ('Mill Rd W' is Mill Road West), 1,379 m against 1,395 m. |
| 28 | 283605943 | secondary | Purdysburn Hill (B205) | 7065B0205_02 | PURDYSBURN HILL1 | 0.72 | 6.6 (6.6-6.6) | 0.787 high | Right | Name and ref (B205) agree; ends at Fort Rd and Hospital Rd as the section says. |
| 29 | 374712135 | trunk | Belvoir Road (A55) | 7035A0055_17 | BELVOIR RD1 | 0.59 | 10.2 (10.1-10.5) | 0.535 medium | Right | Name and ref agree; 0.6 m off fits the north carriageway the section names (the south one is a different section). |
| 30 | 194775124 | residential | Royal Lodge Road | 7035U0511_50 | ROYAL LODGE RD1 | 0.63 | 6.0 (6.0-6.3) | 0.909 high | Right | Name equal, 818 m against 688 m plus side sections, 68 items, 0.6 m off. |

What the check says about the confidence score: the doubtful way (#21) got 0.20 and the three unprovable ones 0.15,
0.34 and 0.41, but three ways I judge right also scored low (#1 0.21, #7 0.37, #10 0.37: a flared link road, a junction
approach and a 7 m stub). So the score separates well-covered, named, steady-width ways (high) from the rest, but "low"
means "do not trust this number", not "this is probably the wrong road". Thirty ways is too few to calibrate the
thresholds, and I did not try.

## Known limits (for T-406)

- **Widths near junctions are inflated.** DfI measures the carriageway including flares, bell-mouths and turning
  lanes. The step drops items above 20 m and takes a length-weighted median, but a short way at a junction (7 m
  stub, junction approach) can still carry a 14 to 18 m width. Use the confidence.
- **Dual carriageways**: each carriageway is its own DfI section. If OSM draws one carriageway per way (oneway) the width is
  right for the way. If a dual carriageway were drawn as one two-way way, the width would be one carriageway's.
- **Width is the whole carriageway** (kerb to kerb or edge to edge, DfI does not say which), not the lane. Compare
  the 5 to 6 m of residential streets with the 3.3 to 3.7 m lane the game needs.
- **One width per way.** A way that DfI measures 6 m in one place and 9 m in another gets the median; `width_q1_m`
  and `width_q3_m` show the range, and a wide range lowers `spread`.
- **Unnamed OSM ways** match on position alone (name part 0.55). The three unprovable ways above are of this kind.
- The xlsx is from July 2021; the OSM extract is from 12 November 2026.
- Items dropped for width (118 of 2,101 in the slice box) are counted in `coverage.dfi` of each output file.

## How the level uses it (T-406)

`pipeline/road_widths.py` writes a `width` tag on every road way of the area's `osm.json` before MapNG reads it (the
same route as the speed limits; `tools/mapng_export.py` calls it in `fetch`, `pinned-osm` and `export`, `--dfi-widths`
names the file, default `widths_full.json`, `--no-road-widths` turns it off). MapNG draws a road as wide as its
`width` tag, so nothing in MapNG changed. Details and the order are in the module docstring:

1. DfI width when the match is high or medium confidence; a low confidence one only inside the p10 to p90 of its class
   and never on a service road (12 of 42 low ways in the Slice are kept).
2. An OSM `width` tag; a `lanes` tag picks the table row for that lane count when there is one.
3. The Northern Ireland class default: `config/road_width_defaults.json`, the **DfI median** of the high and
   medium ways of that class, direction and lanes tag in the full box (5,544 ways; made by `road_widths.py derive`).
   Residential two-way 5.6 m, unclassified 6.6, tertiary 6.8 (no lanes tag), secondary 10.1. **Two-way only** (since
   18 November): the one-way rows mix dual-road carriageways matched to DfI sections measured across both and junction
   flares (tertiary, one lane, one-way: median 12.1 m), and the two one-way arms of the Newtownbreda Road roundabout entry
   drawn at 11.0 and 12.1 m overlapped and lost the entry's Give Way line; a one-way way with no DfI or OSM width keeps
   MapNG's lane-based width (34 pieces on the Slice).
   Service roads and tracks have no default (width depends on what they serve) and stay with MapNG's.

Before, an untagged Belfast street came out 10.6 m wide (two 3 m lanes plus a 2.3 m parking lane each side) where DfI
says 5.6 m. On the Slice build (`H:\BelfastData\mapng\t406\`) of the 524 road pieces: DfI high 95, medium 66, low 12,
OSM lanes 33, class default 57, left to MapNG 261 (228 service, 28 track, 5 links).

Limits found:

- MapNG clamps a `width` to its class range (carriageway 4.4 to 14.6 m for residential and tertiary, 5.6 to 16.6
  primary and secondary, 7.0 to 22.6 trunk, 3.6 to 11.6 service). A DfI width under the minimum is reached with
  MapNG's `mapng:carriageway_max` tag (written with `belfast:width_cap=yes`; 13 ways on the Slice). One over the maximum
  cannot be drawn without a MapNG patch: 1 way on the Slice (Castlehume Gardens 17.8 m, a junction mouth).
- **MapNG merges consecutive ways of one name, class, ref, one-way and surface into one DecalRoad and draws it with
  the first way's width.** Where the merged ways have different DfI widths (the A55 Belvoir Road: 7.5 m and 10.2 m)
  the road gets one of them, and the other way is off by the difference. 5 high or medium ways on the Slice were off
  for this reason alone. Fixed by MapNG patch 0014 (`width` and `mapng:carriageway_max` in `getDecalMergeStyleKey`,
  18 November): widths check 145 of 158, all 134 high and medium ways within 0.75 m.
- DfI's wide medium confidence figures at cul-de-sac heads (Moyle Walk 12.5 and 11.1 m, inter-quartile range 5.9 to
  12.7) are drawn as DfI gives them and put one building 2.4 m into the lanes.


## Drive gate on the T-406 build (15 November 2026, not passed)

First t406 run `2026-11-15_183606`: 4 of 10. Causes, each checked against level data and telemetry:

- **Six routes refused, 244 m on the roundabout route: real.** MapNG takes `width` as the whole carriageway and, with no parking or
  shoulder tag, makes all of it the AI road. Hospital Road 24 (OSM one-way, lanes=1, DfI low confidence 14.3 m) became a 14.3 m AI lane;
  the game merges graph nodes within the road radius and joined the two carriageways 6 m apart, so the eastbound link at (150, 282) was lost
  (the graph had no path from the test centre to the Newtownbreda junction). Fix: `pipeline/road_widths.py` keeps MapNG's own driving
  width (lanes x class lane width) and writes the rest as `shoulder:both:width` (only over 1.6 m, else the kerb face lands within 0.5 m
  of the lane edge: 6 kerb findings at 0.4 m strips); the `_ai` roads are back as in i007. Routes then validate to 3 to 6 m (artefact:
  the `_ai` lines were shifted by `laneShift`, the body is not), so `build_routes.py rebuild` was run on the new graph. **The routes file
  now matches this build only; `dev` (i007, t407) needs its own copy** (`H:\BelfastData\mapng\t406b\routes_dev_i007.json`).
- **Wrong-side 86, 28, 106 and `milltown-road-out` median -0.62: artefact.** The route line came from the old graph; the AI line sat 1 to 2 m east
  of it (checked at (-190, 85): car 1.1 m left of the new graph line, 1.9 m east of the old route line).
- **Crash at (127, 275) on `hospital-road-ne-back`: real.** A 14.3 m body pushed MapNG's street props (2.4 m beyond the carriageway
  edge) onto the slip road: `utility_pole_36` at (123.4, 273.8), 0.5 m from the slip centre. Fix: a low-confidence DfI width is cut to the
  driving width plus two parking lanes (2 x 2.3 m); the DfI figure stays in `belfast:width_dfi` (widths check 141 to 140 of 158).
- **Kerb strike at (-295, -163) on `haddo-woods-loop`: real.** Without the parking lanes the kerb stands at 2.8 m from the centre and the
  AI's line through the bend reaches it. Fix: a two-way carriageway up to 0.75 m under its lanes' width is drawn at it (5.6 to 6.0 m).
- **`lady-ishbel-loop-reverse`, (-130, 129): real, fixed (MapNG patch 0011).** Not the road at (-130, 129): the damage is polled every 0.5 s and
  stamped on the batch's first sample, so the hit lies up to 0.5 s on. Front spindle damage, FL zone: the car's left front wheel on the inside
  kerb at (-136, 131.5), 2.9 m from the centre, with the car's reference node 2.7 m left of it. The West link road bends 53 degrees at the
  vertex where service road 579543545 ends; MapNG pinned the road through that vertex, an S (right 7 degrees, then the whole turn) with a
  7.8 m core radius, and the AI cut it by 1.2 m. With the 10.6 m body the kerb had stood at 5.3 m. Patch 0011 rounds such a vertex (20 m arc,
  the vertex moved 2.4 m, the service road's end with it). No hit there in any of the three t406e runs.
- **The drop at (-192, -145): improved, not fixed.** The path (y -146 to -144) runs inside both cores; the cores had shrunk to 4.0 m (Lady
  Ishbel) and 4.55 m (Hospital Road) from 6.3 and 5.5, so the junction-mouth blend of patch 0005 took the car from Lady Ishbel's 15 % to
  Hospital Road's level within about 2 m. Patch 0010 keeps cores at 6.5 m within a junction mouth; game bump 7.2 and 8.3 (t406d: 8.6 to 11.7,
  i007 6.1). Widening further is not the answer: Lady Ishbel's own solved profile changes from 15 % to 8.5 % at one node 4 m from the
  junction, which only easing that profile (a vertical curve on the minor road's approach, `services/structures/profileSolve.js`) can spread.
- **The known scrape (-196, -146) is partly the kerb end: fixed (patch 0012).** Lady Ishbel's north kerb ran on to 0.5 m from Hospital Road
  (no sidewalk there, so no curb return) and its ramp lay under the turning car's front left wheel (v1 run: `crash` 2410, front spindle).
  Patch 0012 stops such a loose end the run's corner radius back (here 3 m; 6 m since 18 November, when the patch was rebased onto T-404 and reads `UK_CORNER_RADII`). Run `2026-11-15_234431` still logged a `scrape` there.
- **New with 0010, fixed in it: bump 7.0 to 7.2 at (-96, 144)** on `hospital-road-ne-back` and `milltown-road-back`: a level service road's
  widened core pulled Hospital Road's climbing lanes off their grade. Service roads and tracks keep their own core (3.3 and 3.6 after).

Build `H:\BelfastData\mapng\t406e\out\belfast_slice.zip` (patches 0001 to 0007 and today's 0010 to 0012, then numbered 0008 to 0010; `out_run1` is the first t406e build, without 0012): widths
140 of 158, `terrain-step` 85 (navigation 18; t406d 90 and 24), kerbs 0. Routes rebuilt on its graph (`build_routes.py rebuild`, `check`: 10
routes, 0 problems); they match t406e builds only. Suite `2026-11-15_232145` (first t406e build): 10 of 10 completed, G3 failed (bumps 7.2,
7.2, 7.0). Suite `2026-11-15_234431` (final build): 10 of 10 completed, G3 failed on `lady-ishbel-loop-reverse` only (bump 8.3 at (-193,
-145), scrape at (-197, -146)); other events: `haddo-woods-loop` scrapes at (-244, -144), (-298, -158), (-271, -111), `newtownbreda-roundabout`
scrape at (401, 808) (t406d had scrapes at those Haddo Woods and roundabout places too). Console: one error, `Hook discarded:
FilteredInputChanged` (game UI), not seen before. The QA profile holds the i007 zip again. The suite now reads the osm.json the routes file
names (`sources.osm_file`) unless `--osm` is given.

## Drive gate on the t406f build (18 November 2026, not passed)

Build `H:\BelfastData\mapng\t406f\out\belfast_slice.zip`: area `t413_slice` (pinned OSM, `gpxz`), MapNG patches 0001 to
0014 (T-404's 0008 and 0009; T-406's 0010 to 0012, then 0013 a side road's approach to a junction and 0014 the decal
merge key, `docs/mapng_patches.md`). Static checks against t404 (dev): widths 145 of 158 (high 77 of 77, medium 57 of
57, low 11 of 24; t406e 140), `terrain-step` 78 (1 high, (457, 923); t404 98 with 15 high, t406e 85), `kerb-height` 1 (22 cm at
(133, 735), as on the build without 0013 and 0014), signals check 0, junctions: `kerb-radius-small` 4 (t404 20,
accepted), but 4 high `junction-line-on-priority-road`, 1 `roundabout-entry-unmarked` (450, 872), 1
`junction-mouth-unmarked`, 1 `junction-line-no-mouth`, all already on the build with 0001 to 0012 alone (the T-406 widths
under T-404's lines; on t404 the junction gate fails only on its 20 accepted short corners). Routes rebuilt on its graph (`build_routes.py rebuild`, `check`: 10 routes,
0 problems).

- **The Lady Ishbel drop: fixed (patch 0013).** Lady Ishbel Avenue leaves Hospital Road at 2.3 % and turns about 4 % per
  station to 15 % at 16 m, instead of 9.4 % then 15 % from 4 m. `lady-ishbel-loop-reverse` bump 6.1 and 6.2 (t406e 8.3,
  t404 6.0); no scrape there (t404: (-194, -146)).
- **Suite `2026-11-18_183125`: 9 of 10, G3 failed.** `haddo-woods-loop` crashed at (-297, -159): front spindle, FL. The AI
  comes out of the 12 m radius right-hand bend 1.95 m left of the centreline (0.45 m outside its 3.0 m lane's middle),
  front left wheel at about 2.75 m, and meets the 13 cm kerb at 3.0 m. Haddo Woods has no DfI match: 5.6 m class
  default, drawn at the lanes' 6.0 m. Road, kerb and terrain under the path are as on t406e, where the same contact
  was a scrape at (-298, -158) in its runs; not caused by 0013 or 0014. Tried: a 0.3 m channel inside two-way
  carriageways at the lanes' width (`shoulder:both:width`, AI lanes 5.4 m): `kerb-height` 1 to 91 and `terrain-step` 78 to 150
  (43 high) on the static checks (build `t406g`), so reverted and not driven.
- **Suite `2026-11-18_184913` (same build, rerun): 10 of 10, G3 passed.** Worst bump 6.8 at (-54, 340) on
  `milltown-road-out`, new with 0014 (6.4 in the first run; t406e and t404 under 5 there); scrapes 2: Haddo Woods
  (-298, -158) and the roundabout (401, 808) (t404: 2, both on `lady-ishbel-loop-reverse`).
- Console: no errors in either run (3 warnings, the known baseline lines); `Hook discarded: FilteredInputChanged` did not
  come back.

One failing and one passing run of the same build: the Haddo Woods bend is a coin toss between scrape and crash for
this AI on a 6.0 m street. The QA profile holds the t404 zip again. The routes file matches t406f's graph only.

## Drive gate on the t406i build (18 November 2026, passed twice)

Build `H:\BelfastData\mapng\t406i\out\belfast_slice.zip` (area `t413_slice`, `gpxz`, MapNG patches 0001 to 0017, MapNG
commit `e918084`; `docs/mapng_patches.md`), with one-way ways left to MapNG's widths (above). In the QA profile.

- **Haddo Woods kerb strike (t406f: crash, then scrape at (-297, -159)): no contact in four runs** (t406h twice, t406i
  twice). Patch 0015 gives 348 narrow curbed two-way streets an invisible AI path 0.5 m inside each kerb (Haddo Woods:
  5.0 m in a 6.0 m street); nothing drawn moved. At the bend exit the car's centre came 1.89 and 1.88 m left of the
  centreline (front left corner 2.63 m, kerb at 3.0 m), against 1.95 and 1.96 m (2.70 m) on t406f: the AI still swings
  wide out of the bend, 7 cm less.
- Widths 145 of 158 (high 77 of 77, medium 57 of 57, low 11 of 24). Junction gate fails on `kerb-radius-small` 4 and one
  `junction-mouth-unmarked` (Old Milltown Road, a check tolerance; `docs/mapng_patches.md`); roundabout entries 6 of 6
  marked, minor arms 150 of 155 (4 with no room). Signals check 0. `terrain-step` 150 (41 high) and `kerb-height` 82:
  the checks now measure 0.5 m nearer the kerbs of the 348 streets with an `_ai` twin (the ground is the same as
  t406f's); one step and three kerb findings within 4 m of a route, none high.
- Suites `2026-11-18_211606` and `2026-11-18_212543`: 10 of 10 each, G3 passed both. Worst bump 6.1 at (-218, -146) on
  `lady-ishbel-loop-reverse` (Lady Ishbel Avenue's crest 30 m from Hospital Road; the drop at the junction is gone).
  No scrapes (t404: 2). Console: no errors, 3 known warnings. `milltown-road-out` 5.5: the dip 0014 left between
  Milltown Road's carriageways at (-42 to -53, 332 to 340) is still in the ground (not fixed).
- t406h (0016 before it spared driveways) also passed twice (`2026-11-18_205320`, `2026-11-18_210307`) but kept the West
  link road's T vertex where mapped (its service road goes straight on from one arm) and scraped the inside kerb at
  (-134, 132) once: t406i rounds it again, as on t406f.

Routes rebuilt on t406i's graph (`build_routes.py rebuild`, `check`: 10 routes, 0 problems); they match t406i only.
