#!/usr/bin/env python3
"""Run MapNG's headless level export under the T-101 review conditions. Importable module and CLI.

    uv run --no-project --python 3.12 python tools/mapng_export.py fetch NAME LAT LNG [--size 512]
    uv run --no-project --python 3.12 python tools/mapng_export.py export NAME [--level LEVEL]
    uv run --no-project --python 3.12 python tools/mapng_export.py check ZIP

`fetch` writes <areas>/NAME/{t30.f32, osm.json, meta.json} with tools/mapng_fetch_area.mjs (MapNG's
own fetchArea.mjs needs a GPXZ key). `export` wraps third_party/mapng/scripts/sweep/exportArea.mjs
and then asserts the zip's info.json is left-hand traffic in Europe. `check` runs only the assertion.

What the wrapper enforces (docs/spikes/mapng.md section 6; details in docs/mapng_export.md):
  * the submodule must sit at exactly PINNED_COMMIT, with no local changes to MapNG files;
  * BASE=none (no Esri satellite tiles), TREEMIX=climate (no iNaturalist), trees off unless asked
    (no WorldCover), LIDAR off, Overture buildings off at fetch;
  * GOOGLE_*, GPXZ_*, TRACESTRACK_*, MAPILLARY_* and MapNG's own switches are stripped from the child
    environment, and HOME/USERPROFILE point at an empty folder so ~/.config/mapng/mapng.env is not read;
  * MAPNG_AREAS and MAPNG_SWEEP_OUT are set explicitly, and must be under H:\\BelfastData;
  * tools/mapng_preload.mjs serves /country_rules.json (so a UK area comes out left-hand traffic),
    blocks every host not on the allow list, and logs each request;
  * a finished zip must say roadRules.rightHandDrive == true and region == "europe", else exit 4.

Nothing here installs the zip anywhere or starts the game.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAPNG_ROOT = REPO_ROOT / "third_party" / "mapng"
PINNED_COMMIT = "d605117d77857e91acb8234335e9f89263a94756"
DATA_DIR = Path(r"H:\BelfastData")
WORK_DIR = DATA_DIR / "mapng"
AREAS_DIR = WORK_DIR / "areas"
OUT_DIR = WORK_DIR / "out"
HOME_DIR = WORK_DIR / "home"
NETLOG_DIR = WORK_DIR / "netlog"
PRELOAD = REPO_ROOT / "tools" / "mapng_preload.mjs"
FETCH_SCRIPT = REPO_ROOT / "tools" / "mapng_fetch_area.mjs"
EXPORT_SCRIPT = MAPNG_ROOT / "scripts" / "sweep" / "exportArea.mjs"
COUNTRY_RULES = MAPNG_ROOT / "public" / "country_rules.json"

# Hosts the fetch step may contact (free OSM and elevation). The export step may contact none.
FETCH_ALLOW_HOSTS = (
    "s3.amazonaws.com",
    "lz4.overpass-api.de",
    "overpass-api.de",
    "overpass.private.coffee",
    "overpass.kumi.systems",
    "overpass.openstreetmap.ru",
)
TREES_ALLOW_HOSTS = ("esa-worldcover.s3.eu-central-1.amazonaws.com",)

# Child environment variables removed before anything runs. Prefix match, case-insensitive.
STRIP_PREFIXES = ("GOOGLE", "GPXZ", "TRACESTRACK", "MAPILLARY", "MAPNG_", "BEAMNG_MCP", "NODE_OPTIONS")
# MapNG's exportArea.mjs switches; we set the ones we want and drop the rest.
STRIP_EXACT = ("BASE", "TREEMIX", "LIDAR", "RAW", "NOWIDTH", "STRUCT", "EXPORT_OPTS", "OSM_ONLY", "T30_ONLY", "MPP")

LEVEL_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Exit codes
EXIT_OK = 0
EXIT_ERROR = 1          # unexpected failure
EXIT_USAGE = 2          # bad arguments, path outside H:\BelfastData, bad level name
EXIT_PIN = 3            # submodule not at the pinned commit, or MapNG files modified
EXIT_DRIVING_SIDE = 4   # info.json is not left-hand traffic in Europe (or missing)
EXIT_NODE = 5           # node process failed, or no zip was produced
EXIT_INPUTS = 6         # area inputs missing


class GuardError(Exception):
    """A rule of the review was broken; carries the exit code."""

    def __init__(self, message: str, code: int = EXIT_USAGE):
        super().__init__(message)
        self.code = code


class DrivingSideError(GuardError):
    def __init__(self, message: str):
        super().__init__(message, EXIT_DRIVING_SIDE)


# ---------------------------------------------------------------- guards

def require_under_data(path: Path, label: str = "path") -> Path:
    """Resolve `path` and require it to sit under H:\\BelfastData (case-insensitive on Windows)."""
    resolved = Path(os.path.abspath(path))
    root = os.path.normcase(os.path.abspath(DATA_DIR))
    target = os.path.normcase(str(resolved))
    if target != root and not target.startswith(root + os.sep):
        raise GuardError(f"{label} {resolved} is not under {DATA_DIR}")
    return resolved


def strip_env(base: dict[str, str]) -> dict[str, str]:
    """Copy of `base` without keys, satellite/Google/GPXZ credentials or MapNG switches."""
    out = {}
    for key, value in base.items():
        up = key.upper()
        if up.startswith(STRIP_PREFIXES) or up in STRIP_EXACT:
            continue
        out[key] = value
    return out


def build_env(
    base: dict[str, str],
    *,
    areas: Path,
    out: Path,
    home: Path,
    netlog: Path,
    allow_hosts: tuple[str, ...] = (),
    trees: bool = False,
    export_opts: dict | None = None,
    root: Path = MAPNG_ROOT,
    rules_file: Path = COUNTRY_RULES,
) -> dict[str, str]:
    """Environment for a MapNG export child process. Pure: takes the parent env, returns a new one."""
    env = strip_env(base)
    opts = dict(export_opts or {})
    opts["includeTrees"] = bool(trees)  # the wrapper decides; a caller's value never switches trees on
    hosts = tuple(allow_hosts) + (TREES_ALLOW_HOSTS if trees else ())
    env.update({
        "BASE": "none",                      # never Esri World Imagery
        "TREEMIX": "climate",                # never iNaturalist
        "EXPORT_OPTS": json.dumps(opts, separators=(",", ":")),
        "MAPNG_ROOT": str(root),
        "MAPNG_AREAS": str(areas),
        "MAPNG_SWEEP_OUT": str(out),
        "MAPNG_COUNTRY_RULES": str(rules_file),
        "MAPNG_ALLOW_HOSTS": ",".join(hosts),
        "MAPNG_NETLOG": str(netlog),
        "HOME": str(home),
        "USERPROFILE": str(home),
    })
    return env


def check_pin(root: Path = MAPNG_ROOT, runner=subprocess.run) -> str:
    """Require the submodule at PINNED_COMMIT with a clean tree (tracked files unchanged)."""
    def git(*args: str) -> str:
        r = runner(["git", "-C", str(root), *args], capture_output=True, text=True)
        if r.returncode != 0:
            raise GuardError(f"git {' '.join(args)} failed in {root}: {r.stderr.strip()}", EXIT_PIN)
        return r.stdout.strip()

    head = git("rev-parse", "HEAD")
    if head != PINNED_COMMIT:
        raise GuardError(f"MapNG is at {head}, not the pinned {PINNED_COMMIT}", EXIT_PIN)
    dirty = [ln for ln in git("status", "--porcelain", "--untracked-files=no").splitlines() if ln.strip()]
    if dirty:
        raise GuardError("MapNG has local changes (never edit the submodule): " + "; ".join(dirty[:5]), EXIT_PIN)
    return head


def find_node() -> str:
    node = shutil.which("node")
    if not node:
        raise GuardError("node not found on PATH (run tools/bootstrap.ps1)", EXIT_ERROR)
    return node


# ---------------------------------------------------------------- driving-side assertion

def read_level_info(zip_path: Path) -> tuple[str, dict]:
    """(entry name, parsed info.json) of the level inside a MapNG zip."""
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"levels/[^/]+/info\.json", n)]
        if len(names) != 1:
            raise DrivingSideError(f"{zip_path}: expected one levels/<name>/info.json, found {names}")
        return names[0], json.loads(z.read(names[0]).decode("utf-8"))


def assert_left_hand_traffic(info: dict, where: str = "info.json") -> dict:
    """Require roadRules.rightHandDrive is True (BeamNG's name for left-hand traffic) and region == 'europe'.

    Returns the values checked. Raises DrivingSideError, naming what was found, otherwise.
    """
    road_rules = info.get("roadRules")
    rhd = road_rules.get("rightHandDrive") if isinstance(road_rules, dict) else None
    region = info.get("region")
    found = {"rightHandDrive": rhd, "region": region, "country": info.get("country")}
    problems = []
    if rhd is not True:
        problems.append(f"roadRules.rightHandDrive is {rhd!r}, expected true (left-hand traffic)")
    if region != "europe":
        problems.append(f"region is {region!r}, expected 'europe'")
    if problems:
        raise DrivingSideError(f"{where}: driving-side check FAILED: " + "; ".join(problems) + f" (found {found})")
    return found


def check_zip(zip_path: Path) -> dict:
    name, info = read_level_info(zip_path)
    return assert_left_hand_traffic(info, f"{zip_path}!{name}")


# ---------------------------------------------------------------- stub images

def grey_png(size: int) -> bytes:
    """A flat mid-grey RGB PNG, size x size, from the standard library only."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    row = b"\x00" + b"\x80\x80\x80" * size
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size, 6))
            + chunk(b"IEND", b""))


def fix_stubs(zip_path: Path, root: Path = MAPNG_ROOT) -> dict:
    """Replace the 4-byte stub images MapNG's headless canvas leaves in a level zip.

    A Python port of scripts/sweep/fixStubs.mjs, because that script builds file paths from
    `new URL(import.meta.url).pathname` and cannot run on Windows (docs/mapng_export.md). Same rules:
    cubemap faces from public/cubemap, terrain.png grey at the terrain texture-set size, preview.png
    grey 512, and the building facade emissive map added beside the building materials.
    Returns {"fixed": n, "left": [names]}.
    """
    with zipfile.ZipFile(zip_path) as z:
        items = [(zi, z.read(zi.filename)) for zi in z.infolist()]
    contents = {zi.filename: data for zi, data in items}
    fixed, left = 0, []
    for zi, data in items:
        name = zi.filename
        if zi.is_dir() or len(data) > 16:
            continue
        face = re.search(r"cubemap/(skybox\d\.hdr\.dds)$", name)
        if face:
            contents[name] = (root / "public" / "cubemap" / face.group(1)).read_bytes()
        elif name.endswith("/art/terrains/terrain.png"):
            mats_name = next((n for n in contents if n.endswith("/art/terrains/main.materials.json")), None)
            size = 2048
            if mats_name:
                for mat in json.loads(contents[mats_name].decode("utf-8")).values():
                    if mat.get("class") == "TerrainMaterialTextureSet":
                        size = (mat.get("baseTexSize") or [2048])[0]
                        break
            contents[name] = grey_png(int(size))
        elif name.endswith("/preview.png"):
            contents[name] = grey_png(512)
        else:
            if not name.endswith(".json"):  # tiny .json files (empty item lists) are real content
                left.append(name)
            continue
        fixed += 1
    bld = next((n for n in contents if n.endswith("/custom_assets/osm_objects/main.materials.json")), None)
    if bld and not any(n.endswith("/osm_objects/beamng_facade_e.png") for n in contents):
        folder = bld[: -len("main.materials.json")]
        contents[folder + "beamng_facade_e.png"] = (root / "public" / "beamng_facade_e.png").read_bytes()
        mats = json.loads(contents[bld].decode("utf-8"))
        level = re.match(r"^levels/([^/]+)/", folder).group(1)
        if "mapng_bld_facade" in mats:
            mats["mapng_bld_facade"]["Stages"][0]["emissiveMap"] = (
                f"/levels/{level}/map_assets/custom_assets/osm_objects/beamng_facade_e.png")
        contents[bld] = json.dumps(mats, indent=2).encode("utf-8")
        fixed += 1
    tmp = zip_path.with_name(zip_path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        for name, data in contents.items():
            out.writestr(name, data)
    os.replace(tmp, zip_path)
    return {"fixed": fixed, "left": left}


# ---------------------------------------------------------------- network log

def read_netlog(path: Path) -> dict[str, dict[str, int]]:
    """{'ALLOW': {host: n}, 'BLOCK': {host: n}} from the preload's log (empty when no file)."""
    result: dict[str, dict[str, int]] = {"ALLOW": {}, "BLOCK": {}}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        verdict, _, host = line.partition(" ")
        if verdict in result and host:
            result[verdict][host] = result[verdict].get(host, 0) + 1
    return result


# ---------------------------------------------------------------- commands

def _prepare(netlog_name: str) -> tuple[Path, Path]:
    for d in (AREAS_DIR, OUT_DIR, HOME_DIR, NETLOG_DIR):
        require_under_data(d, "work folder").mkdir(parents=True, exist_ok=True)
    netlog = NETLOG_DIR / netlog_name
    netlog.unlink(missing_ok=True)
    return netlog, HOME_DIR


def _run_node(args: list[str], env: dict[str, str], max_old_space_mb: int) -> int:
    cmd = [find_node(), f"--max-old-space-size={max_old_space_mb}", "--import", PRELOAD.as_uri(), *args]
    print("+", " ".join(cmd), flush=True)
    # cwd outside the MapNG tree and outside the repo: nothing is written next to the sources
    return subprocess.run(cmd, env=env, cwd=str(WORK_DIR)).returncode


def cmd_fetch(args: argparse.Namespace) -> int:
    check_pin()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        raise GuardError(f"area name {args.name!r} must match [A-Za-z0-9_-]+")
    netlog, home = _prepare(f"fetch_{args.name}.log")
    env = build_env({**os.environ}, areas=AREAS_DIR, out=OUT_DIR, home=home, netlog=netlog,
                    allow_hosts=FETCH_ALLOW_HOSTS)
    env["MPP"] = "1"
    code = _run_node([str(FETCH_SCRIPT), args.name, str(args.lat), str(args.lng), str(args.size)], env, args.node_memory_mb)
    hosts = read_netlog(netlog)
    print("hosts contacted (fetch):", json.dumps(hosts))
    if code != 0:
        print(f"fetch FAILED (node exit {code})", file=sys.stderr)
        return EXIT_NODE
    print(f"fetched {AREAS_DIR / args.name}")
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    commit = check_pin()
    level = args.level or f"belfast_{args.name}"
    if not LEVEL_RE.fullmatch(level):
        raise GuardError(f"level name {level!r} must use only A-Z a-z 0-9 - _")
    area_dir = AREAS_DIR / args.name
    missing = [f for f in ("t30.f32", "meta.json", "osm.json") if not (area_dir / f).is_file()]
    if missing:
        print(f"area {area_dir} lacks {missing}; run: mapng_export.py fetch {args.name} LAT LNG", file=sys.stderr)
        return EXIT_INPUTS
    netlog, home = _prepare(f"export_{level}.log")
    allow = tuple(args.allow_host or ())
    env = build_env({**os.environ}, areas=AREAS_DIR, out=OUT_DIR, home=home, netlog=netlog,
                    allow_hosts=allow, trees=args.trees)
    zip_path = OUT_DIR / f"{level}.zip"
    zip_path.unlink(missing_ok=True)
    code = _run_node([str(EXPORT_SCRIPT), args.name, "t30", level, "--out", str(OUT_DIR)], env, args.node_memory_mb)
    hosts = read_netlog(netlog)
    print("hosts contacted (export):", json.dumps(hosts))
    if hosts["BLOCK"]:
        print(f"WARNING: MapNG tried blocked hosts: {sorted(hosts['BLOCK'])}", file=sys.stderr)
    if code != 0 or not zip_path.is_file():
        print(f"export FAILED (node exit {code}, zip present: {zip_path.is_file()})", file=sys.stderr)
        return EXIT_NODE
    stubs = None
    if not args.no_fix_stubs:
        stubs = fix_stubs(zip_path)
        print(f"stub images fixed: {stubs['fixed']}, left as stubs: {stubs['left']}")
    # the review's go condition: left-hand traffic in the built zip. Fail loudly, never fake it.
    try:
        found = check_zip(zip_path)
    except DrivingSideError as exc:
        print(f"DRIVING SIDE CHECK FAILED: {exc}", file=sys.stderr)
        raise
    summary = {
        "zip": str(zip_path), "bytes": zip_path.stat().st_size, "mapng_commit": commit,
        "driving_side": found, "hosts": hosts, "base": "none", "trees": bool(args.trees),
        "stubs": stubs,
    }
    (OUT_DIR / f"{level}.export.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"driving-side check PASSED: {found}")
    print(f"exported {zip_path} ({summary['bytes']} bytes)")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    found = check_zip(Path(args.zip))
    print(f"driving-side check PASSED: {found}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("fetch", help="write t30.f32, osm.json, meta.json for an area (no GPXZ key)")
    s.add_argument("name")
    s.add_argument("lat", type=float)
    s.add_argument("lng", type=float)
    s.add_argument("--size", type=int, default=512, help="power of two, metres at 1 m/px (default 512)")
    s.add_argument("--node-memory-mb", type=int, default=12288)
    s.set_defaults(func=cmd_fetch)
    s = sub.add_parser("export", help="build the level zip and assert left-hand traffic")
    s.add_argument("name")
    s.add_argument("--level", help="level name (default belfast_<name>)")
    s.add_argument("--trees", action="store_true", help="include trees (contacts ESA WorldCover)")
    s.add_argument("--allow-host", action="append", help="extra host the export may contact (repeatable)")
    s.add_argument("--no-fix-stubs", action="store_true",
                   help="leave the 4-byte stub images (default: replace them, as MapNG's --fix-stubs would)")
    s.add_argument("--node-memory-mb", type=int, default=12288)
    s.set_defaults(func=cmd_export)
    s = sub.add_parser("check", help="assert a built zip is left-hand traffic in Europe")
    s.add_argument("zip")
    s.set_defaults(func=cmd_check)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except GuardError as exc:
        print(f"mapng_export: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
