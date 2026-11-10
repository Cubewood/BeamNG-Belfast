---
name: status
description: Short status of the BeamNG Belfast task queue, 15 lines at most. Counts by status per milestone, what is doing or blocked, the next ready tasks, and what needs the user.
disable-model-invocation: true
allowed-tools: Bash(uv run *) PowerShell(uv run *)
---

Give a status of the task queue in at most 15 lines. Read-only: change no file.

Run these three commands from the repository root, one after the other:

1. `uv run --no-project --python 3.12 python .claude/skills/status/status_counts.py`
2. `uv run --no-project --python 3.12 python tools/next_task.py --list-ready night`
3. `uv run --no-project --python 3.12 python tools/next_task.py --you`

Exit code 3 with no output from 2 or 3 means "none". Any other non-zero exit code: show the error line and stop.

Then print, with no other text:

- The lines from command 1 as they are (one per milestone, then `DOING:` and `BLOCKED:`).
- `Next ready:` and up to 3 rows from command 2 as `ID model/effort where`, or `none`.
- `Needs you:` and up to 3 rows from command 3 as `ID short-task`, or `none`.

If that is more than 15 lines, drop milestone lines where everything is `todo` and nothing is done. Do not add commentary.
