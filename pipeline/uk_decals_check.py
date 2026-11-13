#!/usr/bin/env python3
"""Static checks on the UK decal materials (T-402). The in-game load check is NOT done here.

    uv run --no-project --python 3.12 --with pillow --with numpy --with pyyaml python pipeline/uk_decals_check.py \
        [--folder H:/BelfastData/uk_decals/uk_markings_cubewood] [--level belfast_slice] [--no-stock]

Checks
  textures   every texture path in every material resolves to a file in the folder, opens as a PNG, is under 200 KB
  names      material names are unique, are `uk_*_cubewood`, equal mapTo, have unique persistentIds
  stock      none of the names or persistentIds appears in the game's own materials or in MapNG's slice zip
  periods    the repeat length in the manifest equals the YAML module, and the mark and gap measured in the PNG
             equal the YAML mark and gap (to 1.5 pixels)
  drift      the committed rules/uk_decals/main.materials.json equals what the generator produces

Exit code 0 when all pass, 1 when any fails, 2 when an input is missing.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uk_decals as U  # noqa: E402

GAME = Path("H:/SteamLibrary/steamapps/common/BeamNG.drive")
SLICE_ZIP = Path("H:/BelfastData/mapng/out/belfast_slice.zip")
DEFAULT_FOLDER = U.DEFAULT_OUT / U.FOLDER
MAX_PNG_BYTES = 200 * 1024


def iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_strings(v)


def check_textures(mats: dict, folder: Path, level: str) -> tuple[list[str], int]:
    errors, n = [], 0
    prefix = f"/levels/{level}/art/{U.FOLDER}/"
    for name, mat in mats.items():
        for s in iter_strings(mat.get("Stages", [])):
            if not s.lower().endswith(".png"):
                continue
            n += 1
            if not s.startswith(prefix):
                errors.append(f"{name}: texture path outside {prefix}: {s}")
                continue
            p = folder / s[len(prefix):]
            if not p.is_file():
                errors.append(f"{name}: missing texture {s}")
                continue
            if p.stat().st_size > MAX_PNG_BYTES:
                errors.append(f"{name}: {p.name} is {p.stat().st_size} bytes (limit {MAX_PNG_BYTES})")
            try:
                with Image.open(p) as im:
                    im.verify()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {p.name} is not a readable PNG ({e})")
    return errors, n


def check_zip(zip_path: Path, level: str) -> tuple[list[str], int]:
    """Every texture the materials file inside a level zip references is in that zip."""
    base = f"levels/{level}/art/{U.FOLDER}/"
    errors, n = [], 0
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        if base + "main.materials.json" not in names:
            return [f"{zip_path.name}: no {base}main.materials.json"], 0
        mats = json.loads(zf.read(base + "main.materials.json"))
    for name, mat in mats.items():
        for s in iter_strings(mat.get("Stages", [])):
            if s.lower().endswith(".png"):
                n += 1
                if s.lstrip("/") not in names:
                    errors.append(f"{name}: {s} is not in {zip_path.name}")
    return errors, n


def check_names(mats: dict) -> list[str]:
    errors, pids = [], {}
    for key, m in mats.items():
        if not (key.startswith("uk_") and key.endswith("_cubewood")):
            errors.append(f"{key}: name must be uk_*_cubewood")
        if m.get("name") != key or m.get("mapTo") != key:
            errors.append(f"{key}: name/mapTo do not equal the key")
        pid = m.get("persistentId")
        if not pid:
            errors.append(f"{key}: no persistentId")
        elif pid in pids:
            errors.append(f"{key}: persistentId duplicates {pids[pid]}")
        pids[pid] = key
    return errors


def stock_names(include_slice: bool = True) -> tuple[set[str], set[str], int]:
    """Material names and persistentIds in the game's content zips (and the slice zip)."""
    names, pids, files = set(), set(), 0
    zips = [GAME / "content" / "art_shapes.zip"]
    zips += sorted((GAME / "content" / "levels").glob("*.zip"))
    zips += sorted((GAME / "content" / "assets" / "materials").glob("*.zip"))
    zips += sorted((GAME / "content" / "vehicles").glob("*.zip"))
    if include_slice and SLICE_ZIP.is_file():
        zips.append(SLICE_ZIP)
    for z in zips:
        if not z.is_file():
            continue
        try:
            zf = zipfile.ZipFile(z)
        except zipfile.BadZipFile:
            continue
        with zf:
            for n in zf.namelist():
                if not n.endswith(".materials.json"):
                    continue
                try:
                    d = json.loads(zf.read(n))
                except Exception:  # noqa: BLE001
                    continue
                files += 1
                if isinstance(d, dict):
                    for k, v in d.items():
                        names.add(k)
                        if isinstance(v, dict):
                            if v.get("name"):
                                names.add(v["name"])
                            if v.get("mapTo"):
                                names.add(v["mapTo"])
                            if v.get("persistentId"):
                                pids.add(v["persistentId"])
    return names, pids, files


def check_stock(mats: dict) -> tuple[list[str], int]:
    names, pids, files = stock_names()
    errors = []
    for key, m in mats.items():
        if key in names:
            errors.append(f"{key}: collides with a stock or MapNG material name")
        if m.get("persistentId") in pids:
            errors.append(f"{key}: persistentId collides with a stock or MapNG material")
    return errors, files


def runs_circular(col: np.ndarray, thresh: int = 128) -> list[int]:
    """Lengths of the opaque runs along a column, treating the tile as wrapping."""
    on = col >= thresh
    if on.all() or not on.any():
        return [len(on)] if on.all() else []
    start = int(np.argmin(on))  # first off pixel
    on = np.roll(on, -start)
    runs, cur = [], 0
    for v in on:
        if v:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return runs


def yaml_dash(g: dict):
    """(mark, gap) in mm of the dashed part of a rule, or None."""
    if "mark_mm" in g and "gap_mm" in g:
        return float(g["mark_mm"]), float(g["gap_mm"])
    if "broken_line" in g:
        return float(g["broken_line"]["mark_mm"]), float(g["broken_line"]["gap_mm"])
    return None


def check_periods(manifest: dict, folder: Path, rules: dict) -> tuple[list[str], list[str]]:
    errors, lines = [], []
    for name, m in manifest["materials"].items():
        if "period_mm" not in m:
            continue
        rid = m["rules"][0]
        g = rules[rid]["geometry"]
        if "module_mm" in g:
            yaml_period = float(g["module_mm"])
        elif "broken_line" in g:
            yaml_period = float(g["broken_line"]["mark_mm"]) + float(g["broken_line"]["gap_mm"])
        elif "standard_mark_length_mm" in g:
            yaml_period = 2 * (float(g["standard_mark_length_mm"]) + float(g["gap_between_marks_mm"]))
        elif "diagonal_spacing_mm" in g:
            sp = float(m["perpendicular_spacing_mm"])
            if sp not in [float(v) for v in g["diagonal_spacing_mm"].values()]:
                errors.append(f"{name}: spacing {sp} is not a YAML diagonal_spacing_mm value")
            yaml_period = sp * 2 ** 0.5
        else:
            yaml_period = float(g["mark_mm"]) + float(g["gap_mm"])
        if abs(m["texture_length_m"] * 1000 - yaml_period) > 0.01 or abs(m["period_mm"] - yaml_period) > 0.01:
            errors.append(f"{name}: repeat {m['texture_length_m'] * 1000} mm, YAML {rid} says {yaml_period}")
        dash = yaml_dash(g)
        if dash is None or m["annotation"] == U.ANN_AREA:
            continue  # zig-zag and hatch: the mark is diagonal, checked by the repeat length only
        ymark, ygap = dash
        img = np.array(Image.open(folder / m["opacity_texture"]).convert("L"))
        px_h = img.shape[0]
        mm_px = m["texture_length_m"] * 1000 / px_h
        # the column with the fewest opaque pixels (the broken one), among columns with any paint
        counts = (img >= 128).sum(axis=0)
        cols = np.where(counts > 0)[0]
        col = cols[np.argmin(counts[cols])]
        runs = runs_circular(img[:, col])
        if len(runs) != 1:
            errors.append(f"{name}: {len(runs)} opaque runs per repeat, expected 1")
            continue
        mark_meas = runs[0] * mm_px
        gap_meas = (px_h - runs[0]) * mm_px
        ok = abs(mark_meas - ymark) <= 1.5 * mm_px and abs(gap_meas - ygap) <= 1.5 * mm_px
        lines.append(f"{name}: repeat {m['texture_length_m'] * 1000:g} mm, measured mark {mark_meas:.0f} "
                     f"gap {gap_meas:.0f} (YAML {ymark:g}/{ygap:g}) {'ok' if ok else 'FAIL'}")
        if not ok:
            errors.append(f"{name}: measured mark/gap {mark_meas:.0f}/{gap_meas:.0f} != YAML {ymark:g}/{ygap:g}")
    return errors, lines


def check_drift(level: str) -> list[str]:
    rules = U.load_rules()
    expect = json.dumps(U.materials_file(U.build_specs(rules), U.DEFAULT_LEVEL), indent=2) + "\n"
    frag = U.FRAGMENT_DIR / "main.materials.json"
    if not frag.is_file():
        return [f"{frag} missing, run pipeline/uk_decals.py"]
    if frag.read_text(encoding="utf-8") != expect:
        return [f"{frag} is out of date, run pipeline/uk_decals.py"]
    mf = U.FRAGMENT_DIR / "manifest.json"
    expect_m = json.dumps(U.manifest(U.build_specs(rules), U.DEFAULT_LEVEL, rules), indent=2) + "\n"
    if not mf.is_file() or mf.read_text(encoding="utf-8") != expect_m:
        return [f"{mf} is missing or out of date, run pipeline/uk_decals.py"]
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--folder", type=Path, default=DEFAULT_FOLDER)
    ap.add_argument("--level", default=U.DEFAULT_LEVEL)
    ap.add_argument("--zip", type=Path, help="also check that a level zip (from uk_decals_pack.py) holds every texture")
    ap.add_argument("--no-stock", action="store_true", help="skip the scan of the game's own materials")
    args = ap.parse_args(argv)

    mfile = args.folder / "main.materials.json"
    if not mfile.is_file():
        print(f"missing {mfile}; run pipeline/uk_decals.py first")
        return 2
    mats = json.loads(mfile.read_text(encoding="utf-8"))
    manifest = json.loads((U.FRAGMENT_DIR / "manifest.json").read_text(encoding="utf-8"))
    rules = U.load_rules()
    failures = 0

    errs, n = check_textures(mats, args.folder, args.level)
    print(f"textures: {len(mats)} materials, {n} texture references, {len(errs)} problems")
    failures += len(errs)
    allerrs = list(errs)
    if args.zip:
        errs, n = check_zip(args.zip, args.level)
        print(f"zip: {args.zip.name}: {n} texture references, {len(errs)} problems")
        failures += len(errs)
        allerrs += errs
    errs = check_names(mats)
    print(f"names: {len(mats)} materials, {len(errs)} problems")
    failures += len(errs)
    allerrs += errs
    if args.no_stock or not GAME.is_dir():
        print("stock: SKIPPED (game folder not found or --no-stock)")
    else:
        errs, files = check_stock(mats)
        print(f"stock: compared with {files} stock and MapNG materials files, {len(errs)} collisions")
        failures += len(errs)
        allerrs += errs
    errs, lines = check_periods(manifest, args.folder, rules)
    for line in lines:
        print("periods:", line)
    print(f"periods: {len(errs)} problems")
    failures += len(errs)
    allerrs += errs
    errs = check_drift(args.level)
    print(f"drift: {len(errs)} problems")
    failures += len(errs)
    allerrs += errs
    for e in allerrs:
        print("FAIL", e)
    print("PASS" if not failures else f"FAIL ({failures} problems)")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
