"""QA digest and issue cards (T-307). No game, no model.

Reads QA run outputs that exist (route drive suite metrics, MapNG in-game surface probes and drive test,
generic findings files from lint or the image judge), writes `issues/Q-NNN.md` cards and `reports/DIGEST.md`.

A card is one defect at one place. A finding matches an existing card when it has the same kind and lies within
RADIUS_M of the card's anchor point (or, with no position, has the same kind and the same `where`). A match updates
the card (history row, metric, image); it never creates a second card. Merging is idempotent (union, max), so
running twice on the same inputs changes nothing on the second run.

    uv run --no-project --python 3.12 python qa/digest.py [--qa-root H:\\BelfastData\\qa] [--input PATH ...]
        [--issues DIR] [--reports DIR] [--date YYYY-MM-DD] [--dry-run]

See docs/digest.md for the findings-file format the lint and the image judge write.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QA_ROOT = Path(r"H:\BelfastData\qa")
RADIUS_M = 20.0
DIGEST_MAX_LINES = 59          # task: under 60 lines
HISTORY_KEEP = 15
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
HIGH_KINDS = {"crash", "fell", "stuck", "airborne", "error", "timeout", "no-spawn", "lost", "reversing",
              "off-route", "invalid", "suite-timeout", "not-run", "console-error", "game-not-stopped"}
SOURCES_EXPECTED = ("drive_suite", "mapng_probe", "mapng_drive", "lint", "image_judge")
NOTES_MARK = "## Notes"
NOTES_DEFAULT = "(triage notes go here; the digest keeps this section)"
UNIT = "m/s2"


# ---------------------------------------------------------------- findings

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "unknown"


def clean(text) -> str:
    """One line, no table pipes."""
    return re.sub(r"\s+", " ", str(text)).replace("|", "/").strip()


def finding(source, when, kind, *, x=None, y=None, where="", severity=None, value=None, metric="", detail="",
            image=""):
    kind = slug(kind)
    where = str(where).replace(",", ";")      # "where" is stored comma-separated
    return {"source": source, "when": when, "kind": kind,
            "x": None if x is None else float(x), "y": None if y is None else float(y),
            "where": clean(where), "value": None if value is None else float(value),
            "severity": severity or ("high" if kind in HIGH_KINDS else "medium"),
            "metric": clean(metric), "detail": clean(detail), "image": str(image or "")}


def date_from_path(path: Path) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})(?:[_T-](\d{2})(\d{2})(\d{2}))?", str(path))
    if m:
        return m.group(1) + (f"T{m.group(2)}:{m.group(3)}:{m.group(4)}" if m.group(2) else "")
    return dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def fold_bumps(events: list[dict]) -> tuple[list[dict], dict]:
    """A bump right after a crash is the impact itself: fold it into the crash. Returns (kept, crash->bump text)."""
    crashes = [e for e in events if e.get("type") == "crash"]
    kept, folded = [], {}
    for e in events:
        if e.get("type") == "bump" and crashes:
            for c in crashes:
                near_t = "t" in e and "t" in c and 0 <= e["t"] - c["t"] <= 2.0
                near_d = ("t" not in e or "t" not in c) and math.hypot(e["x"] - c["x"], e["y"] - c["y"]) <= 15
                if near_t or near_d:
                    folded[id(c)] = f"impact bump {abs(e.get('value', 0)):.1f} {UNIT} at ({e['x']:.0f}, {e['y']:.0f})"
                    break
            else:
                kept.append(e)
        else:
            kept.append(e)
    return kept, folded


def event_metric(kind: str, value, thresholds: dict) -> tuple[float | None, str]:
    if value is None:
        return None, ""
    try:
        v = abs(float(value))
    except (TypeError, ValueError):          # MapNG gives some events a text value (impact description)
        return None, f"{kind}: {value}"
    if kind == "crash":
        return v, f"damage rise {v:.0f} (crash over {thresholds.get('DAMAGE_CRASH', 50):g})"
    if kind == "bump":
        return v, f"bump {v:.1f} {UNIT} (limit {thresholds.get('BUMP_ACC', 7):g})"
    return v, f"{kind} {v:g}"


def route_findings(source, when, res, thresholds, route_label, image_for=None) -> list[dict]:
    """Findings of one route result (drive suite and MapNG drive share the event shape)."""
    out = []
    events, folded = fold_bumps(list(res.get("events") or []))
    stopped_by_event = False
    for e in events:
        kind = slug(e.get("type", "event"))
        value = e.get("value")
        if kind == "bump" and value is None:
            value = res.get("maxVertAcc")
        v, metric = event_metric(kind, value, thresholds)
        metric = metric or f"{kind} (no value recorded)"
        detail = f"route {route_label}" + (f"; {folded[id(e)]}" if id(e) in folded else "")
        if "t" in e:
            detail += f"; at t {e['t']:g} s"
        if kind in ("crash", "stuck", "fell", "reversing", "lost"):
            stopped_by_event = True
        out.append(finding(source, when, kind, x=e.get("x"), y=e.get("y"), where=route_label, value=v,
                           metric=metric, detail=detail, image=(image_for(res) if image_for else "")))
    if not res.get("completed") and not stopped_by_event:
        reason = res.get("end_reason") or "incomplete"
        out.append(finding(source, when, reason, where=route_label,
                           metric=f"route not completed: {reason}",
                           detail=f"route {route_label}; progress {res.get('progress_m', res.get('progress', '?'))}"
                                  f" of {res.get('length_m', res.get('length', '?'))} m"))
    return out


def parse_drive_suite(path: Path, data: dict) -> dict:
    when = data.get("started") or date_from_path(path)
    th = data.get("thresholds") or {}
    fs = []
    for r in data.get("results") or []:
        fs += route_findings("drive_suite", when, r, th, r.get("id", "?"))
    summ = data.get("summary") or {}
    for f in (summ.get("g3") or {}).get("failed") or []:
        if "not run" in f.get("why", []):
            fs.append(finding("drive_suite", when, "not-run", where=f["id"], metric="route missing from results"))
    for line in (data.get("console") or {}).get("errors") or []:
        text = line if isinstance(line, str) else json.dumps(line, sort_keys=True)
        fs.append(finding("drive_suite", when, "console-error", where=text[:80], metric="console error line",
                          detail=text[:200]))
    if (data.get("game_stopped") or {}).get("clean") is False:
        fs.append(finding("drive_suite", when, "game-not-stopped", where="game", metric="game left running",
                          detail="end state not clean"))
    g3 = summ.get("g3") or {}
    line = (f"drive suite {path.parent.name}: {summ.get('completed', '?')}/{summ.get('routes', '?')} routes completed,"
            f" G3 {'PASS' if g3.get('pass') else 'FAIL'}"
            + ("" if data.get("status") in (None, "ok") else f", status {data['status']}"))
    return {"source": "drive_suite", "when": when, "run": path.parent.name, "line": line, "findings": fs}


def parse_probe(path: Path, data: dict) -> dict:
    when = date_from_path(path)
    fs = []
    for f in data.get("failures") or []:
        why = str(f.get("why", "failure"))
        obj = why.split(":", 1)[1].strip() if ":" in why else ""
        words = slug(why.split(":")[0]).split("-")
        if words and words[0] == slug(f.get("k", "")) and len(words) > 1:
            words = words[1:]                      # "sidewalk floating over ..." under k "sidewalk"
        kind = f"{f.get('k', 'probe')}-{words[0]}"
        num = f.get("dz", f.get("gap"))
        metric = (f"{'dz' if 'dz' in f else 'gap'} {num:g} m" if num is not None else why)
        fs.append(finding("mapng_probe", when, kind, x=f.get("x"), y=f.get("y"), where=f.get("road", ""),
                          value=num, metric=metric, detail=f"{why}; z {f.get('z', '?')}"
                          + (f"; object {obj}" if obj else "")))
    by = data.get("byKind") or {}
    total = sum(v.get("n", 0) for v in by.values())
    fail = sum(v.get("fail", 0) for v in by.values())
    worst = ", ".join(f"{k} {v['fail']}/{v['n']}" for k, v in by.items() if v.get("fail"))
    line = f"surface probes {path.parent.name}: {fail}/{total} failed" + (f" ({worst})" if worst else "")
    return {"source": "mapng_probe", "when": when, "run": path.parent.name, "line": line, "findings": fs}


def parse_mapng_drive(path: Path, data: dict) -> dict:
    when = date_from_path(path)
    level = data.get("level", "")
    shots_dir = path.parent / "drive" / level

    def image_for(res):
        # MapNG's own shots have the main menu over them (docs/spikes/baseline.md); a hand-made clean shot wins
        clean_shot = path.parent / "shots" / f"route{res.get('i', 0) + 1:02d}_crash.jpg"
        if res.get("incident") and clean_shot.is_file():
            return str(clean_shot)
        shots = (res.get("incident") or {}).get("shots") or {}
        for name in ("front", "behind", "above", "left", "right", "hood"):
            p = shots_dir / shots.get(name, "")
            if shots.get(name) and p.is_file():
                return str(p)
        return ""

    fs, results = [], data.get("results") or []
    for r in results:
        label = f"mapng route {r.get('i', 0) + 1} ({r.get('kind', '?')}/{r.get('road', '?')})"   # no commas
        fs += route_findings("mapng_drive", when, r, {"BUMP_ACC": 7}, label, image_for)
    done = sum(1 for r in results if r.get("completed"))
    line = f"MapNG drive test {path.parent.name}: {done}/{len(results)} routes completed"
    return {"source": "mapng_drive", "when": when, "run": path.parent.name, "line": line, "findings": fs}


def parse_findings(path: Path, data: dict) -> dict:
    source = slug(data["source"]).replace("-", "_")
    when = str(data.get("run") or date_from_path(path))
    fs = []
    for f in data.get("findings") or []:
        fs.append(finding(source, when, f["kind"], x=f.get("x"), y=f.get("y"), where=f.get("where", ""),
                          severity=f.get("severity"), value=f.get("value"), metric=f.get("metric", ""),
                          detail=f.get("detail", ""), image=f.get("image", "")))
    return {"source": source, "when": when, "run": path.parent.name,
            "line": f"{source} {path.parent.name}: {len(fs)} findings", "findings": fs}


def classify(path: Path):
    """Parse one JSON file by its content. Returns a source dict or None when it is not a QA result."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("tool") == "qa/drive_suite.py":
        return parse_drive_suite(path, data)
    if "byKind" in data and "failures" in data:
        return parse_probe(path, data)
    if isinstance(data.get("results"), list) and "level" in data and "tool" not in data:
        return parse_mapng_drive(path, data)
    if isinstance(data.get("findings"), list) and data.get("source"):
        return parse_findings(path, data)
    return None


def discover(qa_root: Path, inputs: list[Path]) -> list[dict]:
    """Latest run of each source. Explicit inputs (files or folders) win over the qa-root scan."""
    paths: list[Path] = []
    for p in inputs:
        paths += sorted(p.rglob("*.json")) if p.is_dir() else [p]
    if not inputs and qa_root.is_dir():
        paths = [p for p in sorted(qa_root.rglob("*.json"))
                 if p.name in ("metrics.json", "tests.json", "drive.json") or p.name.startswith("findings")]
    latest: dict[str, dict] = {}
    for p in paths:
        s = classify(p)
        if s and (s["source"] not in latest or s["when"] >= latest[s["source"]]["when"]):
            latest[s["source"]] = s
    return [latest[k] for k in sorted(latest)]


# ---------------------------------------------------------------- cards

class Card:
    def __init__(self):
        self.id = ""
        self.status = "open"
        self.kind = ""
        self.severity = "medium"
        self.x = self.y = None
        self.image = ""
        self.rows: list[dict] = []          # {when, source, where, value, metric, detail}
        self.notes = NOTES_DEFAULT

    # -- derived
    def where_all(self) -> list[str]:
        w = {s.strip() for r in self.rows for s in r["where"].split(",") if s.strip()}
        return sorted(w)

    def latest(self) -> dict:
        return max(self.rows, key=lambda r: (r["when"], r["source"]))

    def first_seen(self):
        return min(r["when"] for r in self.rows)

    def last_seen(self):
        return max(r["when"] for r in self.rows)

    def sources(self):
        return {r["source"] for r in self.rows}

    def title(self) -> str:
        if self.x is None:
            return f"{self.kind} ({', '.join(self.where_all()) or 'no position'})"
        return f"{self.kind} near ({self.x:.0f}, {self.y:.0f})"

    # -- matching and merging
    def matches(self, f: dict) -> bool:
        if f["kind"] != self.kind:
            return False
        if self.x is None or f["x"] is None:
            return self.x is None and f["x"] is None and f["where"] in self.where_all()
        return math.hypot(f["x"] - self.x, f["y"] - self.y) <= RADIUS_M

    def merge(self, f: dict) -> bool:
        """Add a finding. Returns True when it is the first from that (run, source), i.e. a new history row."""
        row = next((r for r in self.rows if r["when"] == f["when"] and r["source"] == f["source"]), None)
        new_row = row is None
        if new_row:
            row = {"when": f["when"], "source": f["source"], "where": "", "value": None, "metric": "",
                   "detail": ""}
            self.rows.append(row)
        row["where"] = ", ".join(sorted({*(s.strip() for s in row["where"].split(",") if s.strip()),
                                         *(s.strip() for s in f["where"].split(",") if s.strip())}))
        if new_row or (f["value"] is not None and (row["value"] is None or f["value"] > row["value"])):
            row["value"], row["metric"], row["detail"] = f["value"], f["metric"], f["detail"]
        if f["image"] and (not self.image or not Path(self.image).is_file()):
            self.image = f["image"]
        if SEVERITY_RANK[f["severity"]] < SEVERITY_RANK[self.severity]:
            self.severity = f["severity"]
        self.rows.sort(key=lambda r: (r["when"], r["source"]))
        del self.rows[:-HISTORY_KEEP]
        return new_row

    # -- text
    def render(self) -> str:
        last = self.latest()
        loc = (f"x {self.x:.0f}, y {self.y:.0f} (metres from the level centre, x east, y north)"
               if self.x is not None else "no position (route or log level)")
        out = ["---", f"id: {self.id}", f"status: {self.status}", f"kind: {self.kind}",
               f"severity: {self.severity}",
               f"x: {'' if self.x is None else f'{self.x:.1f}'}", f"y: {'' if self.y is None else f'{self.y:.1f}'}",
               f"first_seen: {self.first_seen()}", f"last_seen: {self.last_seen()}",
               f"sources: {', '.join(sorted(self.sources()))}", f"image: {self.image}", "---",
               f"# {self.id} {self.title()}", "",
               f"- Location: {loc}",
               f"- Where: {', '.join(self.where_all()) or '-'}",
               f"- Metric (latest run, {last['when']}): {last['metric'] or '-'}",
               f"- Image: {self.image or 'none'}",
               f"- Detail: {last['detail'] or '-'}", "",
               "## History", "", "| run | source | where | value | metric | detail |", "|---|---|---|---|---|---|"]
        for r in self.rows:
            val = "" if r["value"] is None else f"{r['value']:g}"
            out.append(f"| {r['when']} | {r['source']} | {r['where']} | {val} | {r['metric']} | {r['detail']} |")
        out += ["", NOTES_MARK, "", self.notes.strip() or NOTES_DEFAULT, ""]
        return "\n".join(out)

    @classmethod
    def parse(cls, text: str) -> "Card | None":
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        if not m:
            return None
        meta = {}
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
        c = cls()
        c.id, c.status, c.kind = meta.get("id", ""), meta.get("status", "open"), meta.get("kind", "")
        c.severity = meta.get("severity", "medium")
        c.x = float(meta["x"]) if meta.get("x") else None
        c.y = float(meta["y"]) if meta.get("y") else None
        c.image = meta.get("image", "")
        in_hist = False
        for line in text[m.end():].splitlines():
            if line.startswith("## History"):
                in_hist = True
            elif line.startswith("## "):
                in_hist = False
            elif in_hist and line.startswith("|") and not line.startswith("| run") and not line.startswith("|---"):
                cells = [s.strip() for s in line.strip().strip("|").split("|")]
                if len(cells) == 6:
                    c.rows.append({"when": cells[0], "source": cells[1], "where": cells[2],
                                   "value": float(cells[3]) if cells[3] else None, "metric": cells[4],
                                   "detail": cells[5]})
        if NOTES_MARK in text:
            c.notes = text.split(NOTES_MARK, 1)[1].strip()
        return c if c.id and c.kind and c.rows else None


def load_cards(issues: Path) -> dict[str, Card]:
    cards = {}
    for p in sorted(issues.glob("Q-*.md")):
        c = Card.parse(p.read_text(encoding="utf-8").replace("\r\n", "\n"))     # git on Windows may give CRLF
        if c:
            cards[c.id] = c
    return cards


def next_id(cards: dict[str, Card], issues: Path) -> str:
    nums = [int(m.group(1)) for p in issues.glob("Q-*.md") if (m := re.match(r"Q-(\d+)", p.name))]
    nums += [int(i[2:]) for i in cards if i[2:].isdigit()]
    return f"Q-{max(nums, default=0) + 1:03d}"


def cluster(findings: list[dict]) -> list[dict]:
    """Merge findings of one run that are the same defect (same kind, within RADIUS_M, or same where)."""
    out: list[Card] = []
    for f in sorted(findings, key=lambda f: (f["source"], f["kind"], f["x"] is None, f["x"] or 0, f["y"] or 0,
                                              f["where"])):
        for c in out:
            if c.kind == f["kind"] and c.rows[0]["source"] == f["source"] and c.matches(f):
                c.merge(f)
                break
        else:
            c = Card()
            c.kind, c.x, c.y = f["kind"], f["x"], f["y"]
            c.severity = f["severity"]
            c.merge(f)
            out.append(c)
    return out


def update_cards(issues: Path, sources: list[dict], dry_run: bool = False):
    """Apply findings to the cards in `issues`. Returns (cards, stats)."""
    issues.mkdir(parents=True, exist_ok=True)
    cards = load_cards(issues)
    before = {i: c.render() for i, c in cards.items()}
    stats = {"new": [], "updated": [], "unchanged": [], "reopened": [], "not_seen": []}
    seen: set[str] = set()
    for src in sources:
        by_cluster = cluster(src["findings"])
        for cl in by_cluster:
            f_rows = cl.rows[0]
            f = {"source": f_rows["source"], "when": f_rows["when"], "kind": cl.kind, "x": cl.x, "y": cl.y,
                 "where": f_rows["where"], "value": f_rows["value"], "severity": cl.severity,
                 "metric": f_rows["metric"], "detail": f_rows["detail"], "image": cl.image}
            card = next((c for c in sorted(cards.values(), key=lambda c: c.id) if c.matches(f)), None)
            if card is None:
                card = Card()
                card.id = next_id(cards, issues)
                card.kind, card.x, card.y = cl.kind, cl.x, cl.y
                card.severity = cl.severity
                cards[card.id] = card
                before.setdefault(card.id, "")
                card.merge(f)
            elif card.merge(f) and card.status == "closed":
                card.status = "open"
                stats["reopened"].append(card.id)
            seen.add(card.id)
        # cards of this source that did not recur in its latest run
        for c in cards.values():
            if (c.id not in seen and c.status != "closed" and src["source"] in c.sources()
                    and not any(r["when"] == src["when"] and r["source"] == src["source"] for r in c.rows)):
                stats["not_seen"].append(c.id)
    for cid in sorted(seen):
        text = cards[cid].render()
        if not before.get(cid):
            stats["new"].append(cid)
        elif text != before[cid]:
            stats["updated"].append(cid)
        else:
            stats["unchanged"].append(cid)
        if not dry_run and text != before.get(cid):
            (issues / f"{cid}.md").write_text(text, encoding="utf-8", newline="\n")
    stats["not_seen"] = sorted(set(stats["not_seen"]) - seen)
    return cards, stats


# ---------------------------------------------------------------- digest

def build_digest(date: str, sources: list[dict], cards: dict[str, Card], stats: dict) -> str:
    head = [f"# Belfast QA digest {date}", ""]
    head.append("## Runs read")
    head += [f"- {s['line']}" for s in sources] or ["- none found: nothing to report"]
    missing = [k for k in SOURCES_EXPECTED if k not in {s["source"] for s in sources}]
    if missing:
        head.append(f"- not present: {', '.join(missing)}")
    n_open = sum(1 for c in cards.values() if c.status != "closed")
    head += ["", "## Cards this run",
             f"- new {len(stats['new'])}, updated {len(stats['updated'])}, unchanged {len(stats['unchanged'])},"
             f" reopened {len(stats['reopened'])}; open in total {n_open}"]
    if stats["new"]:
        head.append("- new: " + ", ".join(stats["new"][:12]) + (" ..." if len(stats["new"]) > 12 else ""))
    if stats["reopened"]:
        head.append("- reopened (came back after being closed): " + ", ".join(stats["reopened"]))
    tail = []
    if stats["not_seen"]:
        shown = stats["not_seen"][:12]
        tail += ["", "## Not seen in the latest run of their source (maybe fixed; triage to confirm)",
                 ", ".join(shown) + (f" ... (+{len(stats['not_seen']) - 12})" if len(stats["not_seen"]) > 12 else "")]
    tail += ["", "Cards: `issues/Q-*.md` (image paths point outside the repo)."]
    op = sorted((c for c in cards.values() if c.status != "closed"),
                key=lambda c: (SEVERITY_RANK[c.severity], -len(c.rows), c.id))
    room = DIGEST_MAX_LINES - len(head) - len(tail) - 4      # blank, heading, table header x2
    shown = op[:max(room - (1 if len(op) > room else 0), 0)]
    mid = ["", f"## Open issues ({len(op)}), worst first", "", ]
    if op:
        mid = ["", f"## Open issues ({len(op)}), worst first", "| id | sev | kind | where | metric | runs |",
               "|---|---|---|---|---|---|"]
        for c in shown:
            loc = f"({c.x:.0f}, {c.y:.0f})" if c.x is not None else ", ".join(c.where_all())[:30]
            mid.append(f"| {c.id} | {c.severity} | {c.kind} | {loc} | {c.latest()['metric'][:46] or '-'} |"
                       f" {len(c.rows)} |")
        if len(op) > len(shown):
            mid.append(f"... and {len(op) - len(shown)} more open cards in `issues/`")
    lines = head + mid + tail
    return "\n".join(lines[:DIGEST_MAX_LINES]) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--qa-root", type=Path, default=DEFAULT_QA_ROOT, help="scanned for the latest run of each source")
    ap.add_argument("--input", type=Path, action="append", default=[], help="result file or folder (repeatable)")
    ap.add_argument("--issues", type=Path, default=ROOT / "issues")
    ap.add_argument("--reports", type=Path, default=ROOT / "reports")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true", help="write nothing")
    a = ap.parse_args(argv)
    sources = discover(a.qa_root, a.input)
    cards, stats = update_cards(a.issues, sources, a.dry_run)
    text = build_digest(a.date, sources, cards, stats)
    if not a.dry_run:
        a.reports.mkdir(parents=True, exist_ok=True)
        (a.reports / "DIGEST.md").write_text(text, encoding="utf-8", newline="\n")
    print(json.dumps({"sources": [s["line"] for s in sources], **{k: len(v) for k, v in stats.items()},
                      "new_ids": stats["new"], "cards_total": len(cards), "digest_lines": text.count("\n")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
