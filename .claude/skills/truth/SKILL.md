---
name: truth
description: Street-truth check for flagged junctions (Mapillary photos against game frames). Not built until M5; reports that and stops.
disable-model-invocation: true
model: claude-haiku-5-5
effort: low
allowed-tools: Grep
---

The street-truth check is built in M5 (T-503 pairs photos with game frames, T-506 proposes fixes). It has no steps yet.

1. Use Grep on `task.md` for the two lines that start with `| T-503 |` and `| T-506 |`.
2. If either line is missing, or does not have `done` in its second cell, reply exactly `Street truth is not available until T-503/T-506.` and stop.
3. If both are `done`, reply exactly `T-503 and T-506 are done, but .claude/skills/truth/SKILL.md has no steps yet. Ask for it to be written.` and stop.

Do nothing else. Do not invent a check. Google Maps content is never part of this loop.
