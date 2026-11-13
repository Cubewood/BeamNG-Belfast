# Credits (T-208)

`pipeline/credits.py` builds two things from `data/sources/*.json` (one manifest per data source, no code change needed for a new one) plus a short static list in the script (MapNG, BeamNG assets, reference-only projects, Mapillary):

- `CREDITS.md`: the block between the `credits:begin` and `credits:end` markers is generated. Hand-written text outside the markers is kept.
- In-level credits: `credits.txt` for the level folder, and a one-line string for `info.json`.

A manifest with no `attribution_text`, `credits_required`, `attribution` or `licence` field stops the run (exit 2) and writes nothing.

```text
uv run --no-project --python 3.12 python pipeline/credits.py          # list + CREDITS.md + H:\BelfastData\credits
uv run --no-project --python 3.12 python -m pytest tests/pipeline/test_credits.py -q
```

## The one call for the export step

`tools/mapng_export.py` should make this call once, after the level folder is staged and before the zip is written (not yet added; `tools/mapng_export.py` is being edited by other tasks):

```python
import sys; sys.path.insert(0, "pipeline"); from credits import write_level_credits
credit = write_level_credits(staging / "levels" / level_name)   # copies credits.txt into the level folder
info["description"] = f"{info['description']} {credit}"         # then write info.json
```

`write_level_credits` returns the one-line string (`Credits: ...`) to append to the `description` of `levels/<level>/info.json`. BeamNG levels carry `authors`, `description` and `title`; `description` is the field the game shows, so the credits go there. The text file is `levels/<level>/credits.txt`.

## Reading

- The OSM line is taken from the manifest's `licence` (ODbL 1.0, OpenStreetMap contributors).
- The Belfast City LiDAR line is the publisher's required wording, including the Cyient Europe Limited note. The Cyient wording is in `data/sources/elevation.json`, not here.
- Mapillary, BeamNG.Earth and EarthNG are listed in `CREDITS.md` only (`in_level` false), because the level does not use them.
