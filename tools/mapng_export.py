#!/usr/bin/env python3
"""Run MapNG's headless level export under the T-101 review conditions. Importable module and CLI.

    uv run --no-project --python 3.12 python tools/mapng_export.py fetch NAME LAT LNG [--size 512]
    uv run --no-project --python 3.12 python tools/mapng_export.py export NAME [--level LEVEL]
    uv run --no-project --python 3.12 python tools/mapng_export.py check ZIP
    ... mapng_export.py fetch NAME LAT LNG --pinned-osm slice   # OSM from the pinned Geofabrik cut (T-202)
    ... mapng_export.py pinned-osm NAME [--cut slice]           # rewrite NAME/osm.json from the cut, offline

`fetch` writes <areas>/NAME/{t30.f32, osm.json, meta.json} with tools/mapng_fetch_area.mjs (MapNG's
own fetchArea.mjs needs a GPXZ key). `export` wraps third_party/mapng/scripts/sweep/exportArea.mjs
and then asserts the zip's info.json is left-hand traffic in Europe. `check` runs only the assertion.

What the wrapper enforces (docs/spikes/mapng.md section 6; details in docs/mapng_export.md):
  * the submodule must sit at exactly PINNED_COMMIT plus the belfast patches (patches/mapng, applied by
    tools/mapng_patches.py), with no other local changes to MapNG files;
  * BASE=none (no Esri satellite tiles), TREEMIX=climate (no iNaturalist), trees off unless asked
    (no WorldCover), LIDAR off, Overture buildings off at fetch;
  * GOOGLE_*, GPXZ_*, TRACESTRACK_*, MAPILLARY_* and MapNG's own switches are stripped from the child
    environment, and HOME/USERPROFILE point at an empty folder so ~/.config/mapng/mapng.env is not read;
  * MAPNG_AREAS and MAPNG_SWEEP_OUT are set explicitly, and must be under H:\\BelfastData;
  * tools/mapng_preload.mjs serves /country_rules.json (so a UK area comes out left-hand traffic),
    blocks every host not on the allow list, and logs each request;
  * a finished zip must say roadRules.rightHandDrive == true and region == "europe", else exit 4;
  * a traffic light with no sequence (MapNG gives one only to clusters with two axes) gets a one-phase sequence,
    else it stays red for ever (pipeline/signals_fix.py, T-407);
  * when its DecalRoads use our UK marking materials (uk_*_cubewood, painted by the belfast patch 0004),
    the materials and textures (pipeline/uk_decals.py) are added under levels/<level>/art/uk_markings_cubewood/.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mapng_patches  # noqa: E402  (the belfast patches: pin plus series check)

REPO_ROOT = Path(__file__).resolve().parents[1]
# BELFAST_MAPNG_ROOT: where the pinned submodule is checked out when it is not under this repo folder
# (a git worktree has no submodule files). check_pin still demands the pinned commit and a clean tree.
MAPNG_ROOT = Path(os.environ.get("BELFAST_MAPNG_ROOT") or REPO_ROOT / "third_party" / "mapng")
PATCHES_DIR = mapng_patches.PATCHES_DIR
PINNED_COMMIT = "d605117d77857e91acb8234335e9f89263a94756"
DATA_DIR = Path(r"H:\BelfastData")
WORK_DIR = DATA_DIR / "mapng"
AREAS_DIR = WORK_DIR / "areas"
OUT_DIR = WORK_DIR / "out"
HOME_DIR = WORK_DIR / "home"
NETLOG_DIR = WORK_DIR / "netlog"
PRELOAD = REPO_ROOT / "tools" / "mapng_preload.mjs"
FETCH_SCRIPT = REPO_ROOT / "tools" / "mapng_fetch_area.mjs"
OSM_PARSE_SCRIPT = REPO_ROOT / "tools" / "mapng_osm_parse.mjs"
OSM_CUT_DIR = DATA_DIR / "osm" / "cuts"
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
PINNED_OSM_ALLOW_HOSTS = ("s3.amazonaws.com",)  # elevation tiles only; OSM comes from the pinned cut (T-202)
TREES_ALLOW_HOSTS = ("esa-worldcover.s3.eu-central-1.amazonaws.com",)

# Child environment variables removed before anything runs. Prefix match, case-insensitive.
STRIP_PREFIXES = ("GOOGLE", "GPXZ", "TRACESTRACK", "MAPILLARY", "MAPNG_", "BEAMNG_MCP", "NODE_OPTIONS")
# MapNG's exportArea.mjs switches; we set the ones we want and drop the rest.
STRIP_EXACT = ("BASE", "TREEMIX", "LIDAR", "RAW", "NOWIDTH", "STRUCT", "EXPORT_OPTS", "OSM_ONLY", "T30_ONLY", "MPP")

LEVEL_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Which height file in the area folder exportArea.mjs reads: `t30` is MapNG's 30 m Terrarium data (MapNG then
# adds synthetic relief), `gpxz` is a fine DEM such as the Belfast LiDAR (pipeline/elevation.py writes it).
DEMS = ("t30", "gpxz")

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


def check_pin(root: Path = MAPNG_ROOT, runner=subprocess.run, patches_dir: Path = PATCHES_DIR) -> str:
    """Require the submodule at PINNED_COMMIT plus exactly the belfast patches, with a clean tree.

    With no patches in `patches_dir` this is the original check: HEAD == PINNED_COMMIT. With patches
    (patches/mapng/series), HEAD must be the pin plus those patches, in order, each matching its patch
    file (tools/mapng_patches.py verify); a changed pin, a missing or edited patch all exit 3.
    """
    def git(*args: str) -> str:
        r = runner(["git", "-C", str(root), *args], capture_output=True, text=True)
        if r.returncode != 0:
            raise GuardError(f"git {' '.join(args)} failed in {root}: {r.stderr.strip()}", EXIT_PIN)
        return r.stdout.strip()

    head = git("rev-parse", "HEAD")
    if mapng_patches.read_series(patches_dir):
        try:
            mapng_patches.verify(root, PINNED_COMMIT, patches_dir, runner)
        except mapng_patches.PatchError as exc:
            raise GuardError(str(exc), EXIT_PIN) from exc
    elif head != PINNED_COMMIT:
        raise GuardError(f"MapNG is at {head}, not the pinned {PINNED_COMMIT}", EXIT_PIN)
    dirty = [ln for ln in git("status", "--porcelain", "--untracked-files=no").splitlines() if ln.strip()]
    if dirty:
        raise GuardError("MapNG has local changes (never edit the submodule): " + "; ".join(dirty[:5]), EXIT_PIN)
    return head


def required_area_files(dem: str) -> tuple[str, ...]:
    """Files exportArea.mjs needs in the area folder for this DEM."""
    return (f"{dem}.f32", "meta.json", "osm.json")


def check_dem_inputs(area_dir: Path, dem: str) -> None:
    """The height file must be size x size float32 as meta.json says. For `gpxz`, meta.json must also say
    gpxzSmooth false: exportArea.mjs treats gpxz as fine (`dem === 'gpxz' && !meta.gpxzSmooth`), which skips
    the synthetic 'coarse' relief; true would smooth it as 30 m data. Raises GuardError (exit 6)."""
    meta = json.loads((area_dir / "meta.json").read_text(encoding="utf-8"))
    want = int(meta["size"]) ** 2 * 4
    got = (area_dir / f"{dem}.f32").stat().st_size
    if got != want:
        raise GuardError(f"{area_dir / (dem + '.f32')} is {got} bytes, meta.json size {meta['size']} needs {want}", EXIT_INPUTS)
    if dem == "gpxz" and meta.get("gpxzSmooth"):
        raise GuardError(f"{area_dir / 'meta.json'} has gpxzSmooth true: MapNG would smooth the DEM as 30 m data", EXIT_INPUTS)


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


# ---------------------------------------------------------------- UK road-marking materials (T-403)

UK_DECALS_SCRIPT = REPO_ROOT / "pipeline" / "uk_decals.py"
UK_MARKING_FOLDER = "uk_markings_cubewood"
UK_MATERIAL_RE = re.compile(r"^uk_[a-z0-9_]+_cubewood$")


def decal_road_materials(zip_path: Path) -> dict[str, int]:
    """{material: number of DecalRoads} over every items.level.json of the level zip."""
    counts: dict[str, int] = {}
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.endswith("items.level.json"):
                continue
            for line in z.read(name).decode("utf-8").splitlines():
                if '"DecalRoad"' not in line:
                    continue
                obj = json.loads(line)
                if obj.get("class") == "DecalRoad":
                    counts[obj.get("material")] = counts.get(obj.get("material"), 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]))))


def build_uk_markings(level: str, build_dir: Path) -> Path:
    """Draw the UK marking textures and materials for `level` (pipeline/uk_decals.py, in its own uv
    environment because it needs numpy, pillow and pyyaml). Returns <build_dir>/uk_markings_cubewood."""
    uv = shutil.which("uv")
    if not uv:
        raise GuardError("uv not found on PATH: cannot draw the UK marking textures (pipeline/uk_decals.py)", EXIT_ERROR)
    if (build_dir / UK_MARKING_FOLDER).exists():
        shutil.rmtree(build_dir / UK_MARKING_FOLDER)  # nothing left over from an earlier build
    cmd = [uv, "run", "--no-project", "--python", "3.12", "--with", "pillow", "--with", "numpy", "--with", "pyyaml",
           "python", str(UK_DECALS_SCRIPT), "--out", str(build_dir), "--level", level, "--no-fragment"]
    r = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if r.returncode != 0:
        raise GuardError(f"pipeline/uk_decals.py failed ({r.returncode}): {r.stderr.strip()[-400:]}", EXIT_ERROR)
    return build_dir / UK_MARKING_FOLDER


def add_uk_markings(zip_path: Path, build_dir: Path, builder=build_uk_markings) -> dict | None:
    """Ship our UK marking materials in the level when its DecalRoads use them (the belfast MapNG patch
    paints uk_*_cubewood on UK roads; the textures are ours, drawn by pipeline/uk_decals.py).

    Adds levels/<level>/art/uk_markings_cubewood/ (main.materials.json and its PNGs) to the zip in place.
    Fails (exit 1) when a used material is not in the materials file, or a material's texture is not in
    the folder. Returns None for a level without UK paint, else {"materials": {name: count}, "files": n}.
    """
    used = {m: n for m, n in decal_road_materials(zip_path).items() if isinstance(m, str) and UK_MATERIAL_RE.match(m)}
    if not used:
        return None
    level = read_level_info(zip_path)[0].split("/")[1]
    folder = builder(level, require_under_data(build_dir, "UK marking build folder"))
    mats = json.loads((folder / "main.materials.json").read_text(encoding="utf-8"))
    missing = sorted(set(used) - set(mats))
    if missing:
        raise GuardError(f"{zip_path}: DecalRoads use UK materials that pipeline/uk_decals.py does not make: {missing}",
                         EXIT_ERROR)
    prefix = f"levels/{level}/art/{UK_MARKING_FOLDER}/"
    for mat in mats.values():
        for stage in mat.get("Stages", []):
            for path in stage.values():
                if not str(path).startswith("/" + prefix) or not (folder / str(path)[len(prefix) + 1:]).is_file():
                    raise GuardError(f"material {mat.get('name')}: texture {path} is not in {folder}", EXIT_ERROR)
    files = sorted(p for p in folder.iterdir() if p.is_file())
    tmp = zip_path.with_name(zip_path.name + ".tmp")
    with zipfile.ZipFile(zip_path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
        for item in zin.infolist():
            if not item.filename.startswith(prefix):  # replace any earlier copy
                zout.writestr(item, zin.read(item.filename))
        for p in files:
            zout.write(p, prefix + p.name)
    os.replace(tmp, zip_path)
    return {"materials": used, "files": len(files)}


def fix_signals(zip_path: Path) -> dict | None:
    """T-407: give every traffic light that has no sequence one (pipeline/signals_fix.py). MapNG makes a sequence only
    for a cluster with two axes or a protected turn, so a one-axis cluster is a light that stays red for ever in the
    game (core/trafficSignals.lua SignalInstance:setActive). Rewrites signals.json in the zip in place; None when the
    level has no signals file."""
    sys.path.insert(0, str(REPO_ROOT / "pipeline"))
    import signals_fix
    try:
        signals_fix.signals_name(zip_path)
    except ValueError:
        return None
    return signals_fix.fix_zip(zip_path)


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

def _prepare(netlog_name: str, out_dir: Path | None = None, netlog_dir: Path | None = None) -> tuple[Path, Path]:
    out_dir = out_dir or OUT_DIR
    netlog_dir = netlog_dir or NETLOG_DIR
    for d in (AREAS_DIR, out_dir, HOME_DIR, netlog_dir):
        require_under_data(d, "work folder").mkdir(parents=True, exist_ok=True)
    netlog = netlog_dir / netlog_name
    netlog.unlink(missing_ok=True)
    return netlog, HOME_DIR


def _run_node(args: list[str], env: dict[str, str], max_old_space_mb: int) -> int:
    cmd = [find_node(), f"--max-old-space-size={max_old_space_mb}", "--import", PRELOAD.as_uri(), *args]
    print("+", " ".join(cmd), flush=True)
    # cwd outside the MapNG tree and outside the repo: nothing is written next to the sources
    return subprocess.run(cmd, env=env, cwd=str(WORK_DIR)).returncode


def pinned_cut(area: str, manifest_path: Path = REPO_ROOT / "data" / "sources" / "osm.json",
               cut_dir: Path = OSM_CUT_DIR) -> Path:
    """The pinned OSM cut of an AOI square (pipeline/osm_pin.py), after checking its SHA-256 against the manifest."""
    import hashlib
    if not re.fullmatch(r"[A-Za-z0-9_-]+", area):
        raise GuardError(f"AOI area name {area!r} must match [A-Za-z0-9_-]+")
    path = require_under_data(cut_dir / f"{area}.overpass.json", "pinned OSM cut")
    if not path.is_file():
        raise GuardError(f"{path} is missing; run pipeline/osm_pin.py cut --area {area}", EXIT_INPUTS)
    if not manifest_path.is_file():
        raise GuardError(f"{manifest_path} is missing; run pipeline/osm_pin.py download", EXIT_INPUTS)
    want = json.loads(manifest_path.read_text(encoding="utf-8")).get("cuts", {}).get(area, {}).get("sha256")
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != want:
        raise GuardError(f"{path} has sha256 {got}, the manifest says {want}; recut with pipeline/osm_pin.py", EXIT_INPUTS)
    return path


def apply_speed_limits(area_dir: Path) -> dict:
    """T-408: give every road way of <area>/osm.json a limit in mph (pipeline/speed_limits.py). Safe to run twice."""
    sys.path.insert(0, str(REPO_ROOT / "pipeline"))
    import speed_limits
    summary = speed_limits.run(area_dir / "osm.json", write=True, report=area_dir / "speed_limits.json")
    print(f"speed limits: {summary['vehicle_road_features']} road ways, "
          f"{summary['vehicle_road_features_without_limit']} without a limit")
    return summary


def apply_road_widths(area_dir: Path, args: argparse.Namespace) -> dict | None:
    """T-406: give every road way of <area>/osm.json a `width` (DfI, then OSM tags, then NI class defaults;
    pipeline/road_widths.py). Safe to run twice. `--no-road-widths` leaves MapNG's own widths."""
    if getattr(args, "no_road_widths", False):
        return None
    sys.path.insert(0, str(REPO_ROOT / "pipeline"))
    import road_widths
    dfi = Path(getattr(args, "dfi_widths", None) or road_widths.DEFAULT_DFI)
    if not dfi.is_file():
        raise GuardError(f"DfI widths file {dfi} is missing; run pipeline/dfi.py match (or pass --no-road-widths)", EXIT_INPUTS)
    summary = road_widths.run(area_dir / "osm.json", dfi_path=dfi, write=True, report=area_dir / "road_widths.json")
    print("road widths: " + json.dumps({k: v["pieces"] for k, v in summary["by_source"].items()})
          + f"; below class minimum (capped) {len(summary['capped_below_class_minimum'])} ways, "
            f"over class maximum (not reachable) {len(summary['unreachable_over_class_maximum'])} ways")
    return summary


def cmd_pinned_osm(args: argparse.Namespace) -> int:
    """Write <areas>/NAME/osm.json from a pinned cut. No network: every request would be blocked and logged."""
    check_pin()
    cut = pinned_cut(args.cut or args.name)
    area_dir = AREAS_DIR / args.name
    if not (area_dir / "meta.json").is_file():
        print(f"area {area_dir} lacks meta.json (it gives the bounds); run fetch --pinned-osm first", file=sys.stderr)
        return EXIT_INPUTS
    netlog, home = _prepare(f"pinned_osm_{args.name}.log")
    env = build_env({**os.environ}, areas=AREAS_DIR, out=OUT_DIR, home=home, netlog=netlog, allow_hosts=())
    code = _run_node([str(OSM_PARSE_SCRIPT), str(cut), str(area_dir)], env, args.node_memory_mb)
    hosts = read_netlog(netlog)
    print("hosts contacted (pinned-osm):", json.dumps(hosts))
    if code != 0 or hosts["ALLOW"] or hosts["BLOCK"]:
        print(f"pinned-osm FAILED (node exit {code}, requests {hosts})", file=sys.stderr)
        return EXIT_NODE
    apply_speed_limits(area_dir)
    apply_road_widths(area_dir, args)
    return EXIT_OK


def cmd_fetch(args: argparse.Namespace) -> int:
    check_pin()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        raise GuardError(f"area name {args.name!r} must match [A-Za-z0-9_-]+")
    cut = pinned_cut(args.pinned_osm) if args.pinned_osm else None
    netlog, home = _prepare(f"fetch_{args.name}.log")
    # with a pinned cut the Overpass mirrors are not even on the allow list: elevation tiles only
    env = build_env({**os.environ}, areas=AREAS_DIR, out=OUT_DIR, home=home, netlog=netlog,
                    allow_hosts=PINNED_OSM_ALLOW_HOSTS if cut else FETCH_ALLOW_HOSTS)
    if cut:
        env["MAPNG_OSM_CUT"] = str(cut)
    env["MPP"] = "1"
    code = _run_node([str(FETCH_SCRIPT), args.name, str(args.lat), str(args.lng), str(args.size)], env, args.node_memory_mb)
    hosts = read_netlog(netlog)
    print("hosts contacted (fetch):", json.dumps(hosts))
    if code != 0:
        print(f"fetch FAILED (node exit {code})", file=sys.stderr)
        return EXIT_NODE
    apply_speed_limits(AREAS_DIR / args.name)
    apply_road_widths(AREAS_DIR / args.name, args)
    print(f"fetched {AREAS_DIR / args.name}")
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    commit = check_pin()
    patches = mapng_patches.read_series()
    level = args.level or f"belfast_{args.name}"
    if not LEVEL_RE.fullmatch(level):
        raise GuardError(f"level name {level!r} must use only A-Z a-z 0-9 - _")
    area_dir = AREAS_DIR / args.name
    dem = args.dem
    missing = [f for f in required_area_files(dem) if not (area_dir / f).is_file()]
    if missing:
        print(f"area {area_dir} lacks {missing}; run: mapng_export.py fetch {args.name} LAT LNG"
              + ("" if dem == "t30" else " and pipeline/elevation.py build"), file=sys.stderr)
        return EXIT_INPUTS
    # --out-dir: a private output folder (zip, provenance, netlog) so a test build never overwrites the shared one
    custom_out = getattr(args, "out_dir", None)
    check_dem_inputs(area_dir, dem)
    apply_speed_limits(area_dir)   # no-op when fetch or pinned-osm already did it
    apply_road_widths(area_dir, args)
    if custom_out:
        out_dir = require_under_data(Path(custom_out), "out-dir")
        netlog, home = _prepare(f"export_{level}.log", out_dir, out_dir / "netlog")
    else:
        out_dir = OUT_DIR
        netlog, home = _prepare(f"export_{level}.log")
    allow = tuple(args.allow_host or ())
    env = build_env({**os.environ}, areas=AREAS_DIR, out=out_dir, home=home, netlog=netlog,
                    allow_hosts=allow, trees=args.trees)
    zip_path = out_dir / f"{level}.zip"
    zip_path.unlink(missing_ok=True)
    code = _run_node([str(EXPORT_SCRIPT), args.name, dem, level, "--out", str(out_dir)], env, args.node_memory_mb)
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
    uk = add_uk_markings(zip_path, out_dir / "uk_markings_build")
    if uk:
        print(f"UK marking materials shipped: {uk['files']} files, DecalRoads per material {json.dumps(uk['materials'])}")
    sig = fix_signals(zip_path)
    if sig:
        print(f"traffic signals: {sig['lights_fixed']} lights with no sequence given one ({sig['sequences_added']} sequences added)")
    # the review's go condition: left-hand traffic in the built zip. Fail loudly, never fake it.
    try:
        found = check_zip(zip_path)
    except DrivingSideError as exc:
        print(f"DRIVING SIDE CHECK FAILED: {exc}", file=sys.stderr)
        raise
    summary = {
        "zip": str(zip_path), "bytes": zip_path.stat().st_size, "mapng_commit": commit,
        "mapng_pin": PINNED_COMMIT, "mapng_patches": patches,
        "driving_side": found, "hosts": hosts, "base": "none", "trees": bool(args.trees), "dem": dem,
        "stubs": stubs, "uk_markings": uk, "signals": sig,
    }
    (out_dir / f"{level}.export.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"driving-side check PASSED: {found}")
    print(f"exported {zip_path} ({summary['bytes']} bytes)")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    found = check_zip(Path(args.zip))
    print(f"driving-side check PASSED: {found}")
    return EXIT_OK


def _width_args(s: argparse.ArgumentParser) -> None:
    s.add_argument("--dfi-widths", metavar="FILE",
                   help=r"DfI widths file (pipeline/dfi.py match; default H:\BelfastData\dfi\widths_full.json)")
    s.add_argument("--no-road-widths", action="store_true", help="leave MapNG's own road widths (no width tags)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("fetch", help="write t30.f32, osm.json, meta.json for an area (no GPXZ key)")
    s.add_argument("name")
    s.add_argument("lat", type=float)
    s.add_argument("lng", type=float)
    s.add_argument("--size", type=int, default=512, help="power of two, metres at 1 m/px (default 512)")
    s.add_argument("--pinned-osm", metavar="AREA",
                   help="take OSM from the pinned cut of this AOI square (pipeline/osm_pin.py), not Overpass")
    s.add_argument("--node-memory-mb", type=int, default=12288)
    _width_args(s)
    s.set_defaults(func=cmd_fetch)
    s = sub.add_parser("pinned-osm", help="write osm.json for an area from a pinned cut, offline")
    s.add_argument("name", help="area folder under areas/ (needs meta.json)")
    s.add_argument("--cut", metavar="AREA", help="AOI square whose cut to use (default: same as NAME)")
    s.add_argument("--node-memory-mb", type=int, default=4096)
    _width_args(s)
    s.set_defaults(func=cmd_pinned_osm)
    s = sub.add_parser("export", help="build the level zip and assert left-hand traffic")
    s.add_argument("name")
    s.add_argument("--level", help="level name (default belfast_<name>)")
    s.add_argument("--out-dir", help=r"output folder under H:\BelfastData (default H:\BelfastData\mapng\out)")
    s.add_argument("--dem", choices=DEMS, default="t30",
                   help="height file: t30 (MapNG 30 m, default) or gpxz (the area folder's gpxz.f32, e.g. LiDAR)")
    s.add_argument("--trees", action="store_true", help="include trees (contacts ESA WorldCover)")
    s.add_argument("--allow-host", action="append", help="extra host the export may contact (repeatable)")
    s.add_argument("--no-fix-stubs", action="store_true",
                   help="leave the 4-byte stub images (default: replace them, as MapNG's --fix-stubs would)")
    s.add_argument("--node-memory-mb", type=int, default=12288)
    _width_args(s)
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
