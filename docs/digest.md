# Digest and issue cards (T-307)

`qa/digest.py` turns QA run results into `issues/Q-NNN.md` cards (committed, text only) and `reports/DIGEST.md` (under 60 lines, runtime output, ignored by git). No game, no model. Triage (Haiku, 06:30) reads only the digest; nightly QA (T-309) runs this last.

```powershell
$env:UV_CACHE_DIR='H:\BelfastData\cache\uv'; $env:UV_PYTHON_INSTALL_DIR='H:\BelfastData\uv-python'
uv run --no-project --python 3.12 python qa/digest.py                 # latest run of each source under H:\BelfastData\qa
uv run --no-project --python 3.12 python qa/digest.py --input H:\BelfastData\qa\drive\2026-11-11_232005 --dry-run
uv run --no-project --python 3.12 --with pytest --with requests python -m pytest tests/qa/test_digest.py -q
```

Options: `--qa-root`, `--input` (file or folder, repeatable, replaces the scan), `--issues`, `--reports`, `--date`, `--dry-run`. It prints one JSON line (cards new, updated, unchanged, reopened, not seen).

## Inputs (found by content, not by name)

| Source | File | Kinds of finding |
|---|---|---|
| `drive_suite` | `metrics.json` of `qa/drive_suite.py` | crash, bump, stuck, airborne, off-route, fell, reversing, lost (each with x, y); route-level timeout, error, no-spawn, not-run; `console-error`; `game-not-stopped` |
| `mapng_probe` | `tests.json` of MapNG `runIngameTests` | one per probe failure, e.g. `sidewalk-object`, `sidewalk-building`, `sidewalk-floating` (x, y, dz or gap) |
| `mapng_drive` | `drive.json` of MapNG `driveTest` | crash and bump events, with the incident shot as image when the file exists (a clean `shots/routeNN_crash.jpg` next to it wins, because MapNG's own shots carry the main menu) |
| any other | `findings*.json`, see below | whatever the writer lists: T-301 lint, T-304 image judge |

Only the latest run of each source is read (by its start time, else the date in the path). A bump within 2 s (or 15 m when there is no time) after a crash on the same route is the impact itself and is folded into the crash card.

### Findings file (for the lint T-301 and the image judge T-304)

```json
{"source": "image_judge", "run": "2026-11-13T02:00:00",
 "findings": [{"kind": "missing-markings", "x": 5, "y": 6, "where": "junction J12", "severity": "medium",
               "value": 0.8, "metric": "confidence 0.80", "detail": "no give way line",
               "image": "H:\\BelfastData\\qa\\frames\\j12_30m.jpg"}]}
```

`kind` and `source` are required; `x`, `y` (world metres, x east, y north), `where`, `severity` (`high`, `medium`, `low`), `value` (number used to pick the worst), `metric` (text), `detail`, `image` (path outside the repo) are optional. Without `x`, `y` the finding is matched by `kind` and `where`.

## De-duplication

One card is one defect at one place. A finding matches an existing card when the kind is equal and it lies within 20 m of the card's anchor point (the position of the first finding; it never moves), or, with no position, the kind and `where` are equal. Within a run, findings of the same source are merged first, so two routes crashing at the same corner give one card. A match adds a history row `(run, source)`; merging is idempotent (union of routes, worst value), so rerunning the same inputs rewrites nothing: the run reports `new 0, updated 0, unchanged N`. Different sources (for example MapNG's drive test and our drive suite) hitting the same place share a card. New ids continue after the highest `Q-NNN` present. The card status is `open` or `closed`; triage sets `closed` and may write under `## Notes`, which the script keeps. A closed card reopens only when a new run (not a rerun) finds the defect again. A card whose source ran again without finding it is listed in the digest as "not seen" (maybe fixed); it is never closed automatically.

## Card layout

Front matter (`id`, `status`, `kind`, `severity`, `x`, `y`, `first_seen`, `last_seen`, `sources`, `image`), then location, where (routes or roads), latest metric, image path, detail, a history table (last 15 runs) and `## Notes`.

## Digest layout

Runs read (one line per source, G3 result), cards this run (new, updated, unchanged, reopened), open issues worst first as a table (cut to fit 59 lines, with a count of the rest), and the not-seen list. Missing sources are named, so a nightly run with no lint output shows it.

## Limits

- Card ids are allocated from the folder in front of it; two branches creating cards at once would collide. Nightly QA runs on `dev` only.
- The 20 m radius is a guess; nearby but different defects of one kind merge.
- Real data used so far: the 2026-11-11 drive suite, MapNG probes and MapNG drive baseline (`docs/spikes/baseline.md`, `docs/drive_suite.md`). No lint or image-judge output exists yet.
