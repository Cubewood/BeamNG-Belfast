"""`python qa/static_checks ...` runs the checks (qa/ is put on the path so the package imports normally)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from static_checks.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
