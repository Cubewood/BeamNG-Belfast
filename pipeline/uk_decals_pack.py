#!/usr/bin/env python3
"""Make a TEST copy of a level zip with the UK decal materials added (T-402). Never touches the original.

    uv run --no-project --python 3.12 --with pillow --with numpy --with pyyaml python pipeline/uk_decals_pack.py \
        [--zip H:/BelfastData/mapng/out/belfast_slice.zip] [--level belfast_slice] \
        [--out H:/BelfastData/uk_decals/test/belfast_slice.zip]

Regenerates the textures for `--level`, then copies every entry of the level zip and adds
`levels/<level>/art/uk_markings_cubewood/*`. The result is for the QA profile only (copy it over
`H:\\BeamNG-QA\\profile\\current\\mods\\belfast_slice.zip` after backing that up). T-403 will do the same job inside
the real build.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uk_decals as U  # noqa: E402

ALLOWED_ROOT = Path("H:/BelfastData")


def pack(src: Path, level: str, out: Path) -> int:
    out = out.resolve()
    if ALLOWED_ROOT.resolve() not in out.parents:
        raise SystemExit(f"output must be under {ALLOWED_ROOT}")
    build = out.parent / "build"
    if build.exists():
        shutil.rmtree(build)
    U.write_all(build, level, write_fragment=False)
    folder = build / U.FOLDER
    prefix = f"levels/{level}/art/{U.FOLDER}/"
    out.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename.startswith(prefix):
                continue  # replace any earlier copy
            zout.writestr(item, zin.read(item.filename))
        for p in sorted(folder.iterdir()):
            zout.write(p, prefix + p.name)
            added += 1
    return added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--zip", type=Path, default=Path("H:/BelfastData/mapng/out/belfast_slice.zip"))
    ap.add_argument("--level", default=U.DEFAULT_LEVEL)
    ap.add_argument("--out", type=Path, default=Path("H:/BelfastData/uk_decals/test/belfast_slice.zip"))
    args = ap.parse_args(argv)
    if not args.zip.is_file():
        print(f"missing {args.zip}")
        return 2
    n = pack(args.zip, args.level, args.out)
    print(f"wrote {args.out} with {n} files under levels/{args.level}/art/{U.FOLDER}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
