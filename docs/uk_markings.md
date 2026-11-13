# UK markings rules (T-401)

`rules/uk_markings.yaml` holds the UK road marking values that T-402 (decal materials) and T-403 (MapNG patch) need. T-106 found that MapNG's marking style is only `americas` or `white` (`exportBeamNGLevel.js:3475-3477`), so these values are the input for a `uk` style.

## What is in it

Centre, lane and warning lines, double white lines, edge lines (1009A, 1010, 1012.1), Stop and Give Way lines, the Give Way triangle, roundabout and mini-roundabout markings, signal Stop lines and crossing studs, zebra crossings and their zig-zag controlled areas, signal-crossing zig-zags, yellow box junctions, single and double yellow lines, the bus lane line, hatching boundaries and the NI transverse yellow bars. `module_selection` maps a speed limit to the small or large line module.

Each rule has `diagram`, `geometry` (width, mark, gap in mm), `applies`, `colour`, `sources` (document plus section, table or schedule item), an `ni` block and `verified`. Fields map to MapNG as: `geometry.width_mm` to line width, `mark_mm` and `gap_mm` to the dash pattern, `colour` to the decal colour, `applies.speed_limit_mph` and `road_types` to the choice of rule.

## Sources

- DfT Traffic Signs Manual chapters 5 (2018), 3 (2019) and 6 (2019). These are joint documents with the Department for Infrastructure (Northern Ireland). Chapter 5 has an Appendix A on NI variations.
- TSRGD 2016 (SI 2016/362), original version as made. Diagram dimensions were read from the rendered pages.
- Traffic Signs Regulations (Northern Ireland) 1997 (SR 1997/386), original version. The PDF is a scan with no text, so every NI diagram was read by eye from page images (printed page numbers are in the citations).

No PDFs or images are committed. Nothing came from Google.

## Northern Ireland findings

- Same values: centre, lane and warning lines (the 40 mph module split is stated for NI in TSM 5 para 2.1.2), double white lines, 1002.1 (400 wide), Stop signal line (200 or 300), 1010, roundabout 1003.1, 1003.3, 1003.4, yellow lines 1017 and 1018.1.
- Partial: NI diagram numbers differ (1003 for GB 1003A, 1023 for 1023A, 1009 for 1009A, 1049 for 1049A). NI allows a half-size Give Way line and triangle. NI bus lane line allows 150, 250 or 300 (default 250). NI box junction states size ranges up to 30 m by 40 m. NI edge-line policy is "still being developed" (TSM 5 Appendix A).
- Differs: NI transverse yellow bars are diagram 1003.6, only on dual carriageway roundabouts (GB diagram 1067).
- Unverified: zebra stripes, zebra and signal-crossing zig-zags, crossing studs. These are governed by the Zebra, Pelican and Puffin Pedestrian Crossing Regulations (NI) 2006, which I could not read. TSM 6 para 1.4.2 says NI markings are "generally similar but can vary". Those rules have `ni.same: unverified` with the reason.

## Rules marked `verified: false`

| Rule | Reason |
|---|---|
| `hatched_1040` | Boundary module only; diagonal and taper tables not transcribed |
| `giveway_mini_roundabout_1003_3` | 700 and 300 labels: which is the mark and which the gap was read from picture proportions |
| `crossing_studs_1055_1` | Square stud side and row spacing not read |
| `zigzag_zebra_1001_4` | Zig-zag line width 150 and terminal line width 200 read from the figure or by arithmetic |

## Not checked

- Amendments to TSRGD 2016 after it was made. The 2016 text was read in its original form.
- The 2006 NI crossing Regulations, as above.
- Amendments to SR 1997/386 (there are several, 1999 onwards). The original scan was read.
- Stud sizes and spacing apart from the figures quoted in the manual text.

Run the check with `uv run pytest tests/rules`.
