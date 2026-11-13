"""Overrides (T-207): our fixes to OSM, validated, applied to the pinned cut, checked for conflicts.

    uv run --no-project --python 3.12 --with pyyaml python pipeline/overrides.py validate
    ... python pipeline/overrides.py hash way 1410878962 --cut slice       # the hash an entry is written against
    ... python pipeline/overrides.py apply --cut slice                     # cut -> cut with our fixes
    ... python pipeline/overrides.py apply --cut slice --keep-going        # write the output even with conflicts

An override file (`data/overrides/*.yaml`, schema in docs/overrides.md) holds fixes, one per entry. Each entry names
its target OSM element, the hash of that element as the entry's author saw it (`against.hash`), one change, and
mandatory evidence. Applying runs on the pinned cut (pipeline/osm_pin.py) and sits between the cut and MapNG's parse.

Rules the code enforces:
- Evidence is mandatory and may not be Google content (Maps, Street View, Earth, Places), anywhere in the entry.
- An entry is applied only if its target still has the hash it was written against. A target that changed or went
  missing is a conflict: reported, not applied, and the build stops unless asked to keep going.
- Deterministic: same cut and same override files give the same bytes. No clock, no network, order-independent.
- Extra attributes for our own pipeline (`change.extra`) go to a sidecar file, never into the OSM data MapNG reads.
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

OVERRIDES_DIR = ROOT / "data" / "overrides"
SCHEMA_VERSION = 1

EVIDENCE_KINDS = ("osni", "dfi", "lidar", "mapillary", "qa", "survey", "osm", "open_data")
ELEMENT_TYPES = ("node", "way", "relation")
CHANGE_KEYS = ("set_tags", "delete_tags", "geometry", "position", "delete", "extra")

ENTRY_KEYS = {"id", "target", "at", "against", "change", "evidence", "author", "date", "note"}
FILE_KEYS = {"version", "overrides"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
EXTRA_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
# Google content is not allowed as evidence, in data, in a prompt or in a fix (CLAUDE.md guard rail 7).
GOOGLE_RE = re.compile(r"google|goo\.gl|gstatic|ggpht|street\s*view|streetview|maps\.app|g\.co/", re.IGNORECASE)

# outcomes
APPLIED = "applied"
OUTSIDE = "outside-area"
RESOLVED = "resolved-upstream"
CONFLICT_CHANGED = "conflict-changed"
CONFLICT_MISSING = "conflict-missing"
CONFLICT_INVALID = "conflict-invalid"


# ---------------------------------------------------------------- schema and validation

@dataclass
class Entry:
    id: str
    file: str
    type: str
    osm_id: int
    at: tuple[float, float]
    hash: str
    version: int | None
    tags_then: dict | None
    change: dict
    evidence: list[dict]
    author: str
    date: str
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.type}/{self.osm_id}"


def _strings(obj):
    """Every string in a nested structure, keys included."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _latlon(v, what: str, errs: list[str]):
    if (not isinstance(v, (list, tuple)) or len(v) != 2 or not all(_is_num(x) for x in v)
            or not (-90 <= v[0] <= 90 and -180 <= v[1] <= 180)):
        errs.append(f"{what} must be [lat, lon] in degrees")
        return None
    return (round(float(v[0]), 7), round(float(v[1]), 7))


def _tag_map(v, what: str, errs: list[str]) -> dict:
    if not isinstance(v, dict) or not v:
        errs.append(f"{what} must be a non-empty mapping of tag to value")
        return {}
    out = {}
    for k, val in v.items():
        if not isinstance(k, str) or not k or not isinstance(val, str) or val == "":
            errs.append(f"{what}: tag {k!r} and its value must be non-empty strings (quote numbers and yes/no)")
        else:
            out[k] = val
    return out


def validate_entry(raw, file: str = "") -> tuple[Entry | None, list[str]]:
    """Check one raw entry (a mapping). Returns (Entry or None, error messages)."""
    errs: list[str] = []
    if not isinstance(raw, dict):
        return None, ["entry must be a mapping"]
    eid = raw.get("id") if isinstance(raw.get("id"), str) else "?"
    unknown = set(raw) - ENTRY_KEYS
    if unknown:
        errs.append(f"unknown keys {sorted(map(str, unknown))}")
    if not isinstance(raw.get("id"), str) or not ID_RE.match(raw["id"]):
        errs.append("id must be 3 to 64 characters of a-z 0-9 and '-', starting with a letter or digit")

    # Google check first and over the whole entry: the rule is not limited to the evidence field
    for s in _strings(raw):
        m = GOOGLE_RE.search(s)
        if m:
            errs.append(f"Google content is not allowed (found {m.group(0)!r}); cite OSNI, DfI, Mapillary, our own QA or a survey")
            break

    target = raw.get("target")
    ttype, tid = None, None
    if not isinstance(target, dict) or set(target) != {"type", "id"}:
        errs.append("target must be {type: node|way|relation, id: <OSM id>}")
    else:
        ttype, tid = target["type"], target["id"]
        if ttype not in ELEMENT_TYPES:
            errs.append(f"target.type must be one of {ELEMENT_TYPES}")
        if not isinstance(tid, int) or isinstance(tid, bool) or tid <= 0:
            errs.append("target.id must be a positive integer")

    at = _latlon(raw.get("at"), "at", errs) if "at" in raw else None
    if "at" not in raw:
        errs.append("at is required: [lat, lon] of the fix, so the build can tell 'outside this area' from 'gone'")

    against = raw.get("against")
    ahash, aversion, atags = None, None, None
    if not isinstance(against, dict) or "hash" not in against:
        errs.append("against.hash is required: the element hash the fix was written against (overrides.py hash ...)")
    else:
        extra = set(against) - {"hash", "version", "tags"}
        if extra:
            errs.append(f"against: unknown keys {sorted(extra)}")
        ahash = against["hash"]
        if not isinstance(ahash, str) or not HASH_RE.match(ahash):
            errs.append("against.hash must look like 'sha256:' and 64 hex digits")
            ahash = None
        aversion = against.get("version")
        if aversion is not None and (not isinstance(aversion, int) or isinstance(aversion, bool) or aversion < 1):
            errs.append("against.version must be a positive integer (the OSM version, for humans)")
            aversion = None
        if "tags" in against:
            atags = _tag_map(against["tags"], "against.tags", errs) or None

    change = raw.get("change")
    clean: dict = {}
    if not isinstance(change, dict) or not change:
        errs.append(f"change is required and must use one or more of {CHANGE_KEYS}")
    else:
        bad = set(change) - set(CHANGE_KEYS)
        if bad:
            errs.append(f"change: unknown keys {sorted(map(str, bad))}")
        if "delete" in change:
            if change["delete"] is not True:
                errs.append("change.delete must be true")
            elif set(change) != {"delete"}:
                errs.append("change.delete cannot be combined with other changes")
            else:
                clean["delete"] = True
        if "set_tags" in change:
            clean["set_tags"] = _tag_map(change["set_tags"], "change.set_tags", errs)
        if "delete_tags" in change:
            dt = change["delete_tags"]
            if not isinstance(dt, list) or not dt or not all(isinstance(k, str) and k for k in dt):
                errs.append("change.delete_tags must be a non-empty list of tag keys")
            else:
                clean["delete_tags"] = sorted(set(dt))
        if "set_tags" in clean and "delete_tags" in clean and set(clean["set_tags"]) & set(clean["delete_tags"]):
            errs.append("change: a tag is both set and deleted")
        if "geometry" in change:
            if ttype != "way":
                errs.append("change.geometry is for ways only")
            g = change["geometry"]
            if not isinstance(g, list) or len(g) < 2:
                errs.append("change.geometry must be a list of at least 2 [lat, lon] points")
            else:
                pts = [_latlon(p, f"change.geometry[{i}]", errs) for i, p in enumerate(g)]
                if all(pts):
                    clean["geometry"] = pts
        if "position" in change:
            if ttype != "node":
                errs.append("change.position is for nodes only")
            p = _latlon(change["position"], "change.position", errs)
            if p:
                clean["position"] = p
        if "extra" in change:
            ex = change["extra"]
            if not isinstance(ex, dict) or not ex:
                errs.append("change.extra must be a non-empty mapping")
            else:
                for k, v in ex.items():
                    if not isinstance(k, str) or not EXTRA_KEY_RE.match(k):
                        errs.append(f"change.extra: key {k!r} must be lower_snake_case")
                    elif not (isinstance(v, (str, bool)) or _is_num(v)):
                        errs.append(f"change.extra.{k} must be a string, number or boolean")
                clean["extra"] = dict(ex)
        if ttype == "relation" and ({"geometry", "position"} & set(change)):
            errs.append("relations take tag, delete or extra changes only")

    evidence = raw.get("evidence")
    ev_clean: list[dict] = []
    if not isinstance(evidence, list) or not evidence:
        errs.append("evidence is required: a list of {kind, ref} (a fix without evidence is rejected)")
    else:
        for i, e in enumerate(evidence):
            if not isinstance(e, dict) or set(e) - {"kind", "ref", "note"}:
                errs.append(f"evidence[{i}] must be {{kind, ref, note?}}")
                continue
            if e.get("kind") not in EVIDENCE_KINDS:
                errs.append(f"evidence[{i}].kind must be one of {EVIDENCE_KINDS}")
            if not isinstance(e.get("ref"), str) or len(e["ref"].strip()) < 3:
                errs.append(f"evidence[{i}].ref must say where to look (URL, image key, issue card, document)")
            if "note" in e and not isinstance(e["note"], str):
                errs.append(f"evidence[{i}].note must be a string")
            ev_clean.append({k: e[k] for k in ("kind", "ref", "note") if k in e})

    author = raw.get("author")
    if not isinstance(author, str) or not author.strip():
        errs.append("author is required")
    date = raw.get("date")
    if isinstance(date, _dt.datetime):
        errs.append("date must be a plain date, YYYY-MM-DD")
    elif isinstance(date, _dt.date):
        date = date.isoformat()
    elif isinstance(date, str):
        try:
            _dt.date.fromisoformat(date)
        except ValueError:
            errs.append("date must be YYYY-MM-DD")
    else:
        errs.append("date is required, YYYY-MM-DD")
    note = raw.get("note", "")
    if not isinstance(note, str):
        errs.append("note must be a string")
        note = ""

    if errs:
        return None, [f"{eid}: {e}" for e in errs]
    return Entry(id=raw["id"], file=file, type=ttype, osm_id=tid, at=at, hash=ahash, version=aversion,
                 tags_then=atags, change=clean, evidence=ev_clean, author=author.strip(), date=date, note=note), []


def load_file(path: Path) -> tuple[list[Entry], list[str]]:
    """Parse and validate one override file. Errors name the file."""
    import yaml
    errs: list[str] = []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [], [f"{path.name}: cannot read: {exc}"]
    if not isinstance(doc, dict) or set(doc) - FILE_KEYS or doc.get("version") != SCHEMA_VERSION \
            or not isinstance(doc.get("overrides"), list):
        return [], [f"{path.name}: must be a mapping {{version: {SCHEMA_VERSION}, overrides: [...]}}"]
    entries = []
    for raw in doc["overrides"]:
        e, e_errs = validate_entry(raw, path.name)
        errs += [f"{path.name}: {m}" for m in e_errs]
        if e:
            entries.append(e)
    return entries, errs


def load_dir(directory: Path = OVERRIDES_DIR) -> tuple[list[Entry], list[str]]:
    """Every `*.yaml` in the directory, in name order. Adds cross-entry checks (unique id, one entry per target)."""
    entries: list[Entry] = []
    errs: list[str] = []
    for path in sorted(directory.glob("*.yaml")):
        e, m = load_file(path)
        entries += e
        errs += m
    seen_id: dict[str, Entry] = {}
    seen_key: dict[str, Entry] = {}
    for e in entries:
        if e.id in seen_id:
            errs.append(f"{e.file}: {e.id}: id already used in {seen_id[e.id].file}")
        seen_id.setdefault(e.id, e)
        if e.key in seen_key and seen_key[e.key].id != e.id:
            errs.append(f"{e.file}: {e.id}: {e.key} is already the target of {seen_key[e.key].id}; merge them into one entry")
        seen_key.setdefault(e.key, e)
    return entries, errs


def check_refs(entries: list[Entry], root: Path = ROOT) -> list[str]:
    """`qa` evidence that names a repo file (docs/..., issues/...) must point at one that exists."""
    errs = []
    for e in entries:
        for ev in e.evidence:
            ref = ev["ref"].split("#")[0].strip()
            if ev["kind"] == "qa" and re.match(r"^(docs|issues|feedback|reports|qa)/", ref) and not (root / ref).is_file():
                errs.append(f"{e.file}: {e.id}: qa evidence {ev['ref']!r} is not a file in the repo")
    return errs


# ---------------------------------------------------------------- element hash

def element_hash(el: dict) -> str:
    """`sha256:<hex>` of an element as the cut holds it. Relations hash their tags and member list only: the
    geometry copied into members depends on the cut's extent, not on the relation."""
    if el["type"] == "relation":
        el = {"type": "relation", "id": el["id"], "tags": el.get("tags", {}),
              "members": [{"type": m["type"], "ref": m["ref"], "role": m.get("role", "")} for m in el.get("members", [])]}
    data = json.dumps(el, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- apply

@dataclass
class Outcome:
    id: str
    target: str
    status: str
    detail: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_conflict(self) -> bool:
        return self.status.startswith("conflict")

    def as_dict(self) -> dict:
        return {"id": self.id, "target": self.target, "status": self.status, "detail": self.detail,
                "notes": self.notes}


@dataclass
class Result:
    elements: list[dict]
    extras: dict
    outcomes: list[Outcome]

    @property
    def conflicts(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.is_conflict]


def _in_bounds(at: tuple[float, float], b: dict | None) -> bool:
    return b is None or (b["south"] <= at[0] <= b["north"] and b["west"] <= at[1] <= b["east"])


def _diff_tags(then: dict, now: dict) -> list[str]:
    out = []
    for k in sorted(set(then) | set(now)):
        if then.get(k) != now.get(k):
            out.append(f"{k}: {then.get(k)!r} -> {now.get(k)!r}")
    return out


def _explain_change(e: Entry, cur: dict) -> str:
    base = f"{e.key} differs from what {e.id} was written against ({e.hash[:19]}..., now {element_hash(cur)[:19]}...)"
    if e.version:
        base += f"; written against OSM version {e.version}"
    if e.tags_then is not None:
        d = _diff_tags(e.tags_then, cur.get("tags", {}))
        base += "; tags " + ("changed: " + "; ".join(d) if d else "unchanged, so geometry or members changed")
    return base + ". Review the fix against the new data, then update against.hash."


def _already_true(el: dict, change: dict) -> bool:
    """True if every stated change already holds on `el` (OSM adopted the fix). Extra-only entries never are."""
    if "extra" in change and len(change) == 1:
        return False
    if change.get("delete"):
        return False
    tags = el.get("tags", {})
    if any(tags.get(k) != v for k, v in change.get("set_tags", {}).items()):
        return False
    if any(k in tags for k in change.get("delete_tags", [])):
        return False
    if "geometry" in change and [(g["lat"], g["lon"]) for g in el.get("geometry", [])] != [tuple(p) for p in change["geometry"]]:
        return False
    if "position" in change and (el.get("lat"), el.get("lon")) != tuple(change["position"]):
        return False
    return True


def _sorted_tags(tags: dict) -> dict:
    return {k: tags[k] for k in sorted(tags)}


def _apply_change(el: dict, change: dict) -> dict:
    """The element after the change. Raises ValueError for a change that does not fit the element."""
    out = copy.deepcopy(el)
    tags = dict(out.get("tags", {}))
    for k, v in change.get("set_tags", {}).items():
        tags[k] = v
    for k in change.get("delete_tags", []):
        if k not in tags:
            raise ValueError(f"delete_tags: {k!r} is not on {el['type']}/{el['id']}")
        del tags[k]
    if "set_tags" in change or "delete_tags" in change:
        out["tags"] = _sorted_tags(tags)
    if "geometry" in change:
        old = el.get("geometry", [])
        new = [{"lat": p[0], "lon": p[1]} for p in change["geometry"]]
        if (len(old) > 1 and old[0] == old[-1]) != (new[0] == new[-1]):
            raise ValueError("geometry: a closed way must stay closed and an open way open")
        out["geometry"] = new
    if "position" in change:
        out["lat"], out["lon"] = change["position"]
    return out


def apply_overrides(elements: list[dict], entries: list[Entry], bounds: dict | None = None) -> Result:
    """Apply entries to a cut. Never mutates `elements`. Conflicting entries are reported and left out.

    Hashes are checked against the cut as given, then the changes are applied in (type, id) order, so the result
    does not depend on the order of files or entries."""
    index = {(e["type"], e["id"]): e for e in elements}
    work = {k: copy.deepcopy(v) for k, v in index.items()}
    extras: dict[str, dict] = {}
    outcomes: list[Outcome] = []
    todo: list[Entry] = []

    for e in sorted(entries, key=lambda x: (x.type, x.osm_id, x.id)):
        cur = index.get((e.type, e.osm_id))
        if cur is None:
            if not _in_bounds(e.at, bounds):
                outcomes.append(Outcome(e.id, e.key, OUTSIDE, "the fix lies outside this cut"))
            else:
                outcomes.append(Outcome(e.id, e.key, CONFLICT_MISSING,
                                        f"{e.key} is not in the cut, but the fix lies inside its area: the element was deleted, "
                                        f"merged or renumbered in OSM. Find it again and rewrite {e.id}."))
            continue
        if element_hash(cur) != e.hash:
            if _already_true(cur, e.change):
                outcomes.append(Outcome(e.id, e.key, RESOLVED, f"{e.key} changed, and now already holds this fix. Retire {e.id}."))
            else:
                outcomes.append(Outcome(e.id, e.key, CONFLICT_CHANGED, _explain_change(e, cur)))
            continue
        todo.append(e)

    for e in todo:
        key = (e.type, e.osm_id)
        if key not in work:
            outcomes.append(Outcome(e.id, e.key, CONFLICT_INVALID, "target already removed by another override"))
            continue
        notes: list[str] = []
        try:
            ch = e.change
            if ch.get("delete"):
                del work[key]
                if e.type == "way":
                    for rk in sorted(k for k in work if k[0] == "relation"):
                        rel = work[rk]
                        kept = [m for m in rel["members"] if not (m["type"] == "way" and m["ref"] == e.osm_id)]
                        if len(kept) != len(rel["members"]):
                            rel["members"] = kept
                            if not any(m["type"] == "way" for m in kept):
                                del work[rk]
                                notes.append(f"relation/{rk[1]} had no way members left and was dropped")
                            else:
                                notes.append(f"relation/{rk[1]} lost its member way/{e.osm_id}")
            else:
                new = _apply_change(work[key], ch)
                work[key] = new
                if e.type == "way" and "geometry" in ch:
                    for rel in work.values():
                        if rel["type"] == "relation":
                            for m in rel["members"]:
                                if m["type"] == "way" and m["ref"] == e.osm_id:
                                    m["geometry"] = copy.deepcopy(new["geometry"])
                if "extra" in ch:
                    extras[e.key] = {"override": e.id, **{k: ch["extra"][k] for k in sorted(ch["extra"])}}
        except ValueError as exc:
            outcomes.append(Outcome(e.id, e.key, CONFLICT_INVALID, str(exc)))
            continue
        outcomes.append(Outcome(e.id, e.key, APPLIED, "", notes))

    out = [work[k] for k in sorted(work, key=lambda k: ({"node": 0, "way": 1, "relation": 2}[k[0]], k[1]))]
    outcomes.sort(key=lambda o: o.id)
    return Result(out, {k: extras[k] for k in sorted(extras)}, outcomes)


# ---------------------------------------------------------------- files and CLI

def read_cut(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["elements"]


def _resolve_cut(arg: str, manifest_path: Path | None = None):
    """(cut path, bounds or None, sha256 recorded in the manifest or None) from an area name or a file path."""
    import osm_pin
    if Path(arg).suffix:
        return Path(arg), None, None
    info = osm_pin.load_manifest(manifest_path or osm_pin.MANIFEST_PATH).get("cuts", {}).get(arg)
    if not info:
        raise SystemExit(f"overrides: no cut named {arg!r} in the manifest")
    return osm_pin.cut_path(arg), info["bbox"], info["sha256"]


def report_text(result: Result) -> str:
    lines = []
    for o in result.outcomes:
        mark = "CONFLICT" if o.is_conflict else o.status.upper()
        lines.append(f"{mark:<18} {o.id} ({o.target})" + (f": {o.detail}" if o.detail else ""))
        lines += [f"{'':<18}   note: {n}" for n in o.notes]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("validate", help="check the override files (schema, evidence, no Google, unique targets)")
    s.add_argument("--dir", type=Path, default=OVERRIDES_DIR)
    s = sub.add_parser("hash", help="print the hash of an element in a cut, to write into an entry")
    s.add_argument("type", choices=ELEMENT_TYPES)
    s.add_argument("id", type=int)
    s.add_argument("--cut", required=True, help="area name (slice, city_south, full) or a cut file")
    s = sub.add_parser("apply", help="apply the overrides to a cut and write the result")
    s.add_argument("--cut", required=True, help="area name (slice, city_south, full) or a cut file")
    s.add_argument("--dir", type=Path, default=OVERRIDES_DIR)
    s.add_argument("--out", type=Path, help="default H:\\BelfastData\\osm\\cuts_applied\\<name>.overpass.json")
    s.add_argument("--keep-going", action="store_true", help="write the output without the conflicting entries, exit 0")
    args = p.parse_args(argv)

    if args.cmd == "validate":
        entries, errs = load_dir(args.dir)
        errs += check_refs(entries)
        for m in errs:
            print(f"ERROR {m}", file=sys.stderr)
        print(f"{len(entries)} valid entries, {len(errs)} errors")
        return 2 if errs else 0

    cut, bounds, want = _resolve_cut(args.cut)
    if not cut.is_file():
        print(f"overrides: {cut} is missing; run pipeline/osm_pin.py cut", file=sys.stderr)
        return 1
    raw = cut.read_bytes()
    if want and hashlib.sha256(raw).hexdigest() != want:
        print(f"overrides: {cut} does not match the manifest's sha256; recut with pipeline/osm_pin.py", file=sys.stderr)
        return 1
    elements = json.loads(raw.decode("utf-8"))["elements"]

    if args.cmd == "hash":
        el = next((e for e in elements if e["type"] == args.type and e["id"] == args.id), None)
        if el is None:
            print(f"{args.type}/{args.id} is not in {cut.name}", file=sys.stderr)
            return 1
        print(element_hash(el))
        if el.get("tags"):
            print("tags: " + json.dumps(el["tags"], ensure_ascii=False), file=sys.stderr)
        return 0

    import osm_pin
    entries, errs = load_dir(args.dir)
    errs += check_refs(entries)
    if errs:
        for m in errs:
            print(f"ERROR {m}", file=sys.stderr)
        print("overrides: invalid override files; nothing applied", file=sys.stderr)
        return 2
    result = apply_overrides(elements, entries, bounds)
    print(report_text(result))
    out = args.out or osm_pin.CUT_DIR.parent / "cuts_applied" / cut.name
    data = osm_pin.cut_bytes(result.elements)
    report = {"cut": cut.name, "cut_sha256": hashlib.sha256(raw).hexdigest(),
              "entries": len(entries), "applied": sum(o.status == APPLIED for o in result.outcomes),
              "conflicts": len(result.conflicts), "outcomes": [o.as_dict() for o in result.outcomes]}
    if result.conflicts and not args.keep_going:
        print(f"overrides: {len(result.conflicts)} conflict(s); nothing written. Fix the entries, or use --keep-going.",
              file=sys.stderr)
        return 3
    osm_pin.write_atomic(out, data)
    report["output_sha256"] = hashlib.sha256(data).hexdigest()
    osm_pin.write_atomic(out.with_suffix(".extras.json"),
                         (json.dumps(result.extras, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    osm_pin.write_atomic(out.with_suffix(".report.json"),
                         (json.dumps(report, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"wrote {out} ({report['applied']} applied, {report['conflicts']} conflicts left out), sha256 {report['output_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
