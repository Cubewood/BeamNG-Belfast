#!/usr/bin/env python3
"""Set the Status of a task row in task.md, touching nothing else.

Usage: task_state.py <ID> <todo|doing|done|blocked> [--note "text"] [--file PATH]

--note appends " (note: text)" to the row's Task cell ("|" becomes "/").
Exit codes: 0 ok, 1 unknown ID or invalid status (file untouched), 2 file error.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import taskfile as tf  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("id")
    p.add_argument("status")
    p.add_argument("--note")
    p.add_argument("--file", default=str(tf.DEFAULT_FILE))
    a = p.parse_args(argv)
    if a.status not in tf.STATUSES:
        print(f"error: invalid status {a.status!r}; use one of {', '.join(tf.STATUSES)}", file=sys.stderr)
        return 1
    path = Path(a.file)
    try:
        lines = tf.read_lines(path)
    except OSError as e:
        print(f"error: cannot read {path}: {e}", file=sys.stderr)
        return 2
    matches = [r for r in tf.parse(lines) if r.id == a.id]
    if not matches:
        print(f"error: unknown task ID {a.id!r}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"error: task ID {a.id!r} appears {len(matches)} times", file=sys.stderr)
        return 1
    r = matches[0]
    lines[r.line_index] = tf.set_status_line(lines[r.line_index], a.status, a.note)
    data = "".join(lines).encode("utf-8")
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        tmp = None
    except OSError as e:
        print(f"error: cannot write {path}: {e}", file=sys.stderr)
        return 2
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    print(f"{a.id} -> {a.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
