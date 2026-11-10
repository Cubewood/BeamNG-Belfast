"""Shared parser for the task tables in task.md (section 7). Standard library only."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

FIELDS = ("id", "status", "task", "model", "effort", "where", "needs", "done_when")
STATUSES = ("todo", "doing", "done", "blocked")
WHERES = ("any", "game", "gpu", "you")
DEFAULT_FILE = Path(__file__).resolve().parent.parent / "task.md"

_SECTION_RE = re.compile(r"^##\s+7\.")
_H2_RE = re.compile(r"^##\s")
_H3_RE = re.compile(r"^###\s+(.*?)\s*$")
_SEP_RE = re.compile(r"^\|[\s:|-]+\|\s*$")
_PIPE_RE = re.compile(r"(?<!\\)\|")


@dataclass
class Row:
    cells: list  # stripped cell text, 8 entries
    line_index: int  # index into the list of lines (keepends)
    section: str  # e.g. "Inbox", "M0: Foundations"

    def __getattr__(self, name):
        if name in FIELDS:
            return self.cells[FIELDS.index(name)]
        raise AttributeError(name)

    @property
    def needs_list(self) -> list:
        n = self.needs.strip()
        if n in ("", "-"):
            return []
        return [p.strip() for p in re.split(r"[,\s]+", n) if p.strip()]

    def as_dict(self) -> dict:
        d = dict(zip(FIELDS, self.cells))
        d["needs"] = self.needs_list
        return d


def read_lines(path) -> list:
    """Read as UTF-8 without newline translation; lines keep their endings."""
    text = Path(path).read_bytes().decode("utf-8")
    return text.splitlines(keepends=True)


def split_cells(line: str):
    """Return raw cell strings (unstripped) for a table row, or None."""
    s = line.rstrip("\r\n")
    if not s.startswith("|"):
        return None
    parts = _PIPE_RE.split(s)
    if parts[-1].strip() == "":
        parts = parts[:-1]
    return parts[1:]


def parse(lines: list) -> list:
    rows = []
    in7 = False
    section = ""
    for i, line in enumerate(lines):
        bare = line.rstrip("\r\n")
        if _SECTION_RE.match(bare):
            in7 = True
            continue
        if in7 and _H2_RE.match(bare):
            break
        if not in7:
            continue
        m = _H3_RE.match(bare)
        if m:
            section = m.group(1)
            continue
        if _SEP_RE.match(bare):
            continue
        cells = split_cells(line)
        if cells is None:
            continue
        stripped = [c.strip() for c in cells]
        if stripped and stripped[0] == "ID":
            continue
        rows.append(Row(stripped, i, section))
    return rows


def load(path=None) -> list:
    return parse(read_lines(Path(path) if path else DEFAULT_FILE))


def is_ready(row: Row, by_id: dict) -> bool:
    if row.status != "todo":
        return False
    return all(n in by_id and by_id[n].status == "done" for n in row.needs_list)


def ready_rows(rows: list, mode: str) -> list:
    """Ready rows for 'day' (Where=any) or 'night' (any, game, gpu).

    Inbox rows come first, then the rest in file order."""
    allowed = {"day": {"any"}, "night": {"any", "game", "gpu"}}[mode]
    by_id = {r.id: r for r in rows}
    out = [
        r for r in rows
        if r.where in allowed and r.model != "you" and is_ready(r, by_id)
    ]
    out.sort(key=lambda r: 0 if r.section.lower() == "inbox" else 1)  # stable
    return out


def you_rows(rows: list) -> list:
    by_id = {r.id: r for r in rows}
    return [r for r in rows if (r.where == "you" or r.model == "you") and is_ready(r, by_id)]


def set_status_line(line: str, new_status: str, note=None) -> str:
    """Return the line with the Status cell (and optionally the Task cell) edited.

    Only the content of those cells changes; whitespace padding, line ending
    and every other byte are preserved."""
    body = line.rstrip("\r\n")
    ending = line[len(body):]
    pipes = [m.start() for m in _PIPE_RE.finditer(body)]

    def replace_cell(text, n, fn):
        a, b = pipes[n] + 1, pipes[n + 1]
        raw = text[a:b]
        lead = raw[: len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()):]
        return text[:a] + lead + fn(raw.strip()) + trail + text[b:]

    # Edit the later cell first so earlier offsets stay valid.
    if note is not None:
        safe = note.replace("|", "/").replace("\r", " ").replace("\n", " ").strip()
        body = replace_cell(body, 2, lambda t: f"{t} (note: {safe})")
    body = replace_cell(body, 1, lambda t: new_status)
    return body + ending
