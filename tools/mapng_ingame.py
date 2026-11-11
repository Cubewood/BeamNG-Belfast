#!/usr/bin/env python3
"""Run MapNG's in-game scripts against the QA game's built-in MCP (T-104). Thin wrapper.

    uv run --no-project --python 3.12 python tools/mapng_ingame.py tests LEVEL OUTDIR [--max-fail 0.02]
    uv run --no-project --python 3.12 python tools/mapng_ingame.py drive LEVEL OUTDIR [--routes 12] [--vary N]

It does NOT start or stop the game: use tools/gamectl.py (start, load-level, stop) first. The level
must already be loaded (the scripts' own --load is not used: it calls freeroam directly).

Conditions (same as tools/mapng_export.py, docs/spikes/mapng.md section 6):
  * submodule at the pinned commit, MapNG files unmodified (check_pin);
  * GOOGLE*/MAPILLARY*/GPXZ*/TRACESTRACK*/BEAMNG_MCP* and MapNG switches stripped from the child env,
    HOME/USERPROFILE redirected to an empty folder (no ~/.config/mapng/mapng.env);
  * tools/mapng_preload.mjs wraps fetch: only 127.0.0.1 may be reached (the game's MCP, default URL
    http://127.0.0.1:29292/mcp); every request is logged;
  * driveTest always gets --no-street. Street View is never used.
  * OUTDIR must be under H:\\BelfastData.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mapng_export as mx  # noqa: E402

TESTS_SCRIPT = mx.MAPNG_ROOT / "scripts" / "ingame" / "runIngameTests.mjs"
DRIVE_SCRIPT = mx.MAPNG_ROOT / "scripts" / "ingame" / "driveTest.mjs"
MCP_HOST = "127.0.0.1"


def build_ingame_env(base: dict[str, str], home: Path, netlog: Path) -> dict[str, str]:
    env = mx.strip_env(base)
    env.update({
        "MAPNG_ROOT": str(mx.MAPNG_ROOT),
        "MAPNG_COUNTRY_RULES": str(mx.COUNTRY_RULES),
        "MAPNG_ALLOW_HOSTS": MCP_HOST,
        "MAPNG_NETLOG": str(netlog),
        "HOME": str(home),
        "USERPROFILE": str(home),
    })
    return env


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["tests", "drive"])
    ap.add_argument("level")
    ap.add_argument("outdir")
    ap.add_argument("--max-fail", default="0.02")
    ap.add_argument("--routes", default="12")
    ap.add_argument("--vary", default=None)
    args = ap.parse_args(argv)
    if not mx.LEVEL_RE.match(args.level):
        print("bad level name", file=sys.stderr)
        return mx.EXIT_USAGE
    try:
        out = mx.require_under_data(Path(args.outdir), "outdir")
        mx.check_pin()
    except mx.GuardError as e:
        print(f"refused: {e}", file=sys.stderr)
        return e.code
    out.mkdir(parents=True, exist_ok=True)
    home = mx.require_under_data(mx.HOME_DIR, "home")
    home.mkdir(parents=True, exist_ok=True)
    netlog = out / f"netlog_{args.kind}.txt"
    netlog.unlink(missing_ok=True)
    env = build_ingame_env(dict(os.environ), home, netlog)
    json_out = out / f"{args.kind}.json"
    if args.kind == "tests":
        script_args = [str(TESTS_SCRIPT), args.level, "--json", str(json_out), "--max-fail", args.max_fail]
    else:
        script_args = [str(DRIVE_SCRIPT), args.level, "--no-street", "--routes", args.routes,
                       "--out", str(out / "drive"), "--json", str(json_out)]
        if args.vary is not None:
            script_args += ["--vary", args.vary]
    cmd = [mx.find_node(), "--import", mx.PRELOAD.as_uri(), *script_args]
    print("+", " ".join(cmd), flush=True)
    rc = subprocess.run(cmd, env=env, cwd=str(mx.WORK_DIR)).returncode
    print(f"node exit {rc}; netlog {netlog}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
