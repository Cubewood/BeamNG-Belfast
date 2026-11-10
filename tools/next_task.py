#!/usr/bin/env python3
"""Print the next ready task from task.md.

Usage:
  next_task.py <day|night> [--format json|md] [--file PATH]
  next_task.py --list-ready <day|night> [--file PATH]
  next_task.py --you [--file PATH]

day   = only Where=any.   night = Where any, game or gpu.
Rows with Model or Where = you are never returned (see --you).
Inbox rows come first, then milestones in file order.

Exit codes: 0 task printed, 2 usage/file error, 3 nothing ready.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import taskfile as tf  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Print the next ready task.", epilog=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", nargs="?", choices=("day", "night"))
    p.add_argument("--format", choices=("json", "md"), default="json")
    p.add_argument("--file", default=str(tf.DEFAULT_FILE))
    p.add_argument("--list-ready", choices=("day", "night"), metavar="day|night",
                   help="one line per ready task: ID, model, effort, where")
    p.add_argument("--you", action="store_true", help="list ready rows that need the human")
    a = p.parse_args(argv)
    try:
        lines = tf.read_lines(Path(a.file))
    except OSError as e:
        print(f"error: cannot read {a.file}: {e}", file=sys.stderr)
        return 2
    rows = tf.parse(lines)

    if a.you:
        found = tf.you_rows(rows)
        for r in found:
            print(f"{r.id}\t{r.model}\t{r.effort}\t{r.where}\t{r.task[:80]}")
        return 0 if found else 3
    if a.list_ready:
        found = tf.ready_rows(rows, a.list_ready)
        for r in found:
            print(f"{r.id}\t{r.model}\t{r.effort}\t{r.where}")
        return 0 if found else 3
    if not a.mode:
        p.print_usage(sys.stderr)
        print("error: mode day|night required", file=sys.stderr)
        return 2
    found = tf.ready_rows(rows, a.mode)
    if not found:
        return 3
    r = found[0]
    if a.format == "md":
        print(lines[r.line_index].rstrip("\r\n"))
    else:
        print(json.dumps(r.as_dict(), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
