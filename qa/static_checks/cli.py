"""G0 static checks on an exported level. No game, no model.

    uv run --no-project --python 3.12 --with numpy --with shapely python qa/static_checks
        [--zip H:\\BelfastData\\mapng\\out\\belfast_slice.zip] [--osm PATH] [--out-dir DIR] [--no-lint]

Writes `findings.json` (source `lint`, the format of docs/digest.md) into a timestamped folder under
H:\\BelfastData\\qa\\static and prints one summary line. Exit code 0 when no finding is `high`, 1 otherwise,
2 on bad input. See docs/static_checks.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import checks as ck
from .game_index import DEFAULT_GAME, DEFAULT_INDEX, GameIndex, ensure_index
from .geometry import finding
from .level import load_level

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ZIP = Path(r"H:\BelfastData\mapng\out\belfast_slice.zip")
DEFAULT_OUT = Path(r"H:\BelfastData\qa\static")
DEFAULT_BUDGET = ROOT / "config" / "static_budget.json"
DEFAULT_MAPNG = ROOT / "third_party" / "mapng"
FALLBACK_MAPNG = Path(r"H:\Claude\BeamNGBelfast\third_party\mapng")
ALL_CHECKS = ("lint", "roads", "connection", "oneway", "height", "budget", "buildings", "steps", "assets")


def run_mapng_lint(zip_path: Path, index_path: Path | None, mapng_dir: Path | None, out_json: Path) -> list[dict]:
    node = shutil.which("node")
    mapng = next((d for d in (mapng_dir, DEFAULT_MAPNG, FALLBACK_MAPNG)
                  if d and (Path(d) / "scripts" / "lib" / "levelLint.mjs").exists()), None)
    if not node or not mapng:
        return [finding("check-not-run", where="lint", severity="medium",
                        detail="MapNG linter not run: node or third_party/mapng (scripts/lib/levelLint.mjs) not found")]
    runner = Path(__file__).with_name("lint_runner.mjs")
    cmd = [node, str(runner), str(mapng), str(zip_path), str(index_path) if index_path else "-", str(out_json)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if p.returncode != 0 or not out_json.exists():
        return [finding("check-not-run", where="lint", severity="medium",
                        detail=f"MapNG linter failed (exit {p.returncode}): {(p.stderr or p.stdout).strip()[:300]}")]
    return lint_findings(json.loads(out_json.read_text(encoding="utf-8")))


def lint_findings(result: dict) -> list[dict]:
    out = []
    for f in result.get("findings", []):
        sev = "high" if f.get("level") == "error" else "low"
        out.append(finding(f"lint-{f.get('check', 'misc')}", where=f.get("where", ""), severity=sev,
                           detail=f.get("msg", ""), metric=f.get("level", "")))
    return out


def cap(findings: list[dict], n: int) -> list[dict]:
    """Keep the n worst findings of each kind (by severity, then value)."""
    rank = {"high": 0, "medium": 1, "low": 2}
    by: dict[str, list[dict]] = {}
    for f in findings:
        by.setdefault(f["kind"], []).append(f)
    out = []
    for kind, lst in by.items():
        lst.sort(key=lambda f: (rank.get(f.get("severity"), 1), -(f.get("value") or 0)))
        out += lst[:n]
    return out


def run_checks(zip_path: Path, osm_path: Path | None, *, only=ALL_CHECKS, overrides=None, budget_path=DEFAULT_BUDGET,
               index_path: Path | None = None, mapng_dir: Path | None = None, out_dir: Path | None = None,
               buildings_mode: str = "auto", log=print) -> dict:
    c = ck.cfg(overrides)
    t0 = time.time()
    level = load_level(zip_path, osm_path)
    if osm_path is None:
        auto = Path(r"H:\BelfastData\mapng\areas") / level.name / "osm.json"
        if auto.exists():
            from .level import read_osm
            level.osm = read_osm(auto, level.project)
            osm_path = auto
    findings: list[dict] = []
    info: dict = {"zip": str(zip_path), "osm": str(osm_path) if osm_path else None, "level": level.name,
                  "osm_features": len(level.osm), "decal_roads": len(level.roads)}
    if not level.osm and any(k in only for k in ("roads", "connection", "oneway", "buildings")):
        findings.append(finding("check-not-run", where="osm", severity="medium",
                                detail="no OSM file: road presence, connection, one-way and OSM building checks need it"))

    def step(name, fn):
        if name in only:
            t = time.time()
            r = fn()
            log(f"  {name}: {len(r)} finding(s) in {time.time() - t:.1f}s")
            return r
        return []

    if index_path is None and any(k in only for k in ("lint", "assets")):
        try:
            index_path = ensure_index(DEFAULT_GAME, DEFAULT_INDEX)
        except Exception as e:                      # no game install: stock checks cannot run
            findings.append(finding("check-not-run", where="game-index", severity="medium", detail=str(e)))
    out_dir = out_dir or DEFAULT_OUT / dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    findings += step("lint", lambda: run_mapng_lint(zip_path, index_path, mapng_dir, out_dir / "mapng_lint.json"))
    findings += step("roads", lambda: ck.check_roads_present(level, c))
    findings += step("connection", lambda: ck.check_connection(level, c))
    findings += step("oneway", lambda: ck.check_oneway(level, c))
    findings += step("height", lambda: ck.check_road_height(level, c))

    def budget():
        f, counts = ck.check_budget(level, ck.load_budget(budget_path), zip_path.stat().st_size)
        info["counts"] = counts
        return f
    findings += step("budget", budget)

    def buildings():
        f, binfo = ck.check_buildings(level, c, buildings_mode)
        info["buildings"] = binfo
        return f
    findings += step("buildings", buildings)
    findings += step("steps", lambda: ck.check_terrain_steps(level, c))

    def assets():
        if index_path is None:
            return [finding("check-not-run", where="assets", severity="medium", detail="no game file index")]
        f, ainfo = ck.check_stock_assets(level, GameIndex.load(index_path))
        info["assets"] = ainfo
        return f
    findings += step("assets", assets)

    findings = cap(findings, c["max_per_kind"])
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    doc = {"source": "lint", "run": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
           "tool": "qa/static_checks", "info": info, "counts": counts, "findings": findings}
    (out_dir / "findings.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    info["seconds"] = round(time.time() - t0, 1)
    return {"doc": doc, "out_dir": out_dir}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    ap.add_argument("--osm", type=Path, default=None, help="OSM extract (default: the one the export read)")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--budget", type=Path, default=DEFAULT_BUDGET)
    ap.add_argument("--game-index", type=Path, default=None, help="game file index (default: built under H:\\BelfastData)")
    ap.add_argument("--mapng-dir", type=Path, default=None)
    ap.add_argument("--only", nargs="+", choices=ALL_CHECKS, default=list(ALL_CHECKS))
    ap.add_argument("--buildings", choices=("auto", "mesh", "osm"), default="auto")
    ap.add_argument("--no-lint", action="store_true", help="skip MapNG's linter (needs node)")
    a = ap.parse_args(argv)
    if not a.zip.exists():
        print(f"no such zip: {a.zip}", file=sys.stderr)
        return 2
    only = tuple(k for k in a.only if not (a.no_lint and k == "lint"))
    r = run_checks(a.zip, a.osm, only=only, budget_path=a.budget, index_path=a.game_index, mapng_dir=a.mapng_dir,
                   out_dir=a.out_dir, buildings_mode=a.buildings)
    doc = r["doc"]
    high = sum(1 for f in doc["findings"] if f.get("severity") == "high")
    print(json.dumps({"findings": len(doc["findings"]), "high": high, "by_kind": doc["counts"],
                      "out": str(r["out_dir"] / "findings.json")}))
    return 1 if high else 0


if __name__ == "__main__":
    sys.exit(main())
