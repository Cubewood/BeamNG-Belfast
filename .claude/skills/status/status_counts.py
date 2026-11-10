#!/usr/bin/env python3
"""Compact status summary of task.md for the /status command. Read-only.

Usage: status_counts.py [--file PATH]

Prints one line per section (Inbox, M0..M8) with counts by status, then one
line each for DOING and BLOCKED rows. Uses tools/taskfile.py, the same parser
as next_task.py, so it sees exactly the rows the loops see.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import taskfile as tf  # noqa: E402

_NOTE_RE = re.compile(r"\(note:\s*(.*?)\)\s*$")


def short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def describe(r, with_note: bool) -> str:
    task = r.task
    note = ""
    m = _NOTE_RE.search(task)
    if m:
        note = m.group(1)
        task = task[: m.start()].rstrip()
    out = f"{r.id} {short(task, 40)}"
    if with_note and note:
        out += f" [{short(note, 60)}]"
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", default=str(tf.DEFAULT_FILE))
    a = p.parse_args(argv)
    try:
        rows = tf.load(a.file)
    except OSError as e:
        print(f"error: cannot read {a.file}: {e}", file=sys.stderr)
        return 2
    if not rows:
        print("no task rows found")
        return 3
    order: list = []
    counts: dict = {}
    for r in rows:
        key = r.section.split(":")[0].strip() or "?"
        if key not in counts:
            counts[key] = Counter()
            order.append(key)
        counts[key][r.status] += 1
    for key in order:
        c = counts[key]
        total = sum(c.values())
        print(f"{key}: done {c['done']}/{total}, doing {c['doing']}, todo {c['todo']}, blocked {c['blocked']}")
    doing = [describe(r, False) for r in rows if r.status == "doing"]
    blocked = [describe(r, True) for r in rows if r.status == "blocked"]
    print("DOING: " + ("; ".join(doing) if doing else "none"))
    print("BLOCKED: " + ("; ".join(blocked) if blocked else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
