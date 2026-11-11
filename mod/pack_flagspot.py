"""Pack mod/flagspot_cubewood into a mod zip with forward-slash entry names.

Usage: uv run --no-project --python 3.12 python mod/pack_flagspot.py OUT_DIR
Writes OUT_DIR/flagspot_cubewood.zip (no version in the name, as the modder rules ask).
"""
import sys
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent / "flagspot_cubewood"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "flagspot_cubewood.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(SRC.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(SRC).as_posix())
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
