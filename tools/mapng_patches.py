#!/usr/bin/env python3
"""The `belfast` patches to MapNG: patch files in this repo, applied to the pinned commit.

    uv run --no-project --python 3.12 python tools/mapng_patches.py status
    uv run --no-project --python 3.12 python tools/mapng_patches.py apply [--reset]
    uv run --no-project --python 3.12 python tools/mapng_patches.py save

Design (docs/mapng_patches.md, decision in docs/decisions/001-generator.md):

  * `third_party/mapng` stays a submodule pinned to the upstream commit (no fork, no pushes).
  * Our changes are `git format-patch` files in `patches/mapng/`, listed in order in
    `patches/mapng/series`; `patches/mapng/BASE` names the commit they were made against.
  * `apply` checks out a local branch `belfast` at the pin and runs `git am` for each patch, so the
    submodule's history reads: upstream pin, then one commit per patch.
  * `verify` (used by tools/mapng_export.py check_pin) requires: BASE equals the pin in
    tools/mapng_export.py; the submodule HEAD sits exactly `len(series)` commits above the pin; each
    of those commits has the same `git patch-id --stable` as its patch file; the tracked files are
    unchanged. Anything else fails loudly with exit code 3. Re-pinning MapNG therefore fails until
    the patches are rebased (`apply --reset`, fix conflicts, `save`, update BASE).
  * To add or change a patch: `apply`, commit on the `belfast` branch inside the submodule, then
    `save`. Never edit MapNG files without committing and saving: the next `apply --reset` drops them.

Nothing here touches the network (the pinned commit must already be in the submodule) or the game.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAPNG_ROOT = REPO_ROOT / "third_party" / "mapng"
PATCHES_DIR = REPO_ROOT / "patches" / "mapng"
BRANCH = "belfast"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_PIN = 3  # same code as mapng_export.EXIT_PIN: patches and pin disagree

# committer identity for `git am` (the author stays whoever wrote the patch); nothing personal
_IDENT = ("-c", "user.name=Belfast Patches", "-c", "user.email=belfast-patches@users.noreply.github.com",
          "-c", "commit.gpgsign=false")


class PatchError(Exception):
    """The patches and the pinned MapNG do not match; carries the exit code."""

    def __init__(self, message: str, code: int = EXIT_PIN):
        super().__init__(message)
        self.code = code


def read_series(patches_dir: Path = PATCHES_DIR) -> list[str]:
    """Patch file names in order. Missing series file means no patches. `#` starts a comment."""
    series = patches_dir / "series"
    if not series.is_file():
        return []
    names = []
    for line in series.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


def read_base(patches_dir: Path = PATCHES_DIR) -> str | None:
    base = patches_dir / "BASE"
    return base.read_text(encoding="utf-8").strip() if base.is_file() else None


def _git(root: Path, *args: str, runner=subprocess.run, input_bytes: bytes | None = None,
         check: bool = True, ident: bool = False) -> str:
    cmd = ["git", *(_IDENT if ident else ()), "-C", str(root), *args]
    r = runner(cmd, capture_output=True, input=input_bytes)
    out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else (r.stdout or "")
    err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else (r.stderr or "")
    if check and r.returncode != 0:
        raise PatchError(f"git {' '.join(args)} failed in {root}: {(err or out).strip()}")
    return out.strip()


def patch_id(root: Path, diff_bytes: bytes, runner=subprocess.run) -> str:
    """`git patch-id --stable` of a diff or a format-patch file."""
    out = _git(root, "patch-id", "--stable", runner=runner, input_bytes=diff_bytes)
    first = out.split()
    if not first:
        raise PatchError("git patch-id produced no id (empty or malformed patch)")
    return first[0]


def verify(root: Path = MAPNG_ROOT, pin: str = "", patches_dir: Path = PATCHES_DIR, runner=subprocess.run) -> list[str]:
    """Require the submodule to be exactly `pin` plus the series. Returns the patch names applied.

    Raises PatchError (code 3) naming the first difference.
    """
    if not pin:
        raise PatchError("verify needs the pinned commit", EXIT_USAGE)
    names = read_series(patches_dir)
    base = read_base(patches_dir)
    if names and base != pin:
        raise PatchError(
            f"patches/mapng/BASE is {base!r} but the pinned MapNG commit is {pin}: the pin changed. "
            f"Rebase the patches onto the new pin (docs/mapng_patches.md, 'Re-pinning') and update BASE")
    head = _git(root, "rev-parse", "HEAD", runner=runner)
    if not names:
        if head != pin:
            raise PatchError(f"MapNG is at {head}, not the pinned {pin}, and there are no patches")
        return []
    if head == pin:
        raise PatchError(f"MapNG is at the pin with none of the {len(names)} belfast patches applied: "
                         f"run `python tools/mapng_patches.py apply`")
    anc = runner(["git", "-C", str(root), "merge-base", "--is-ancestor", pin, head], capture_output=True)
    if anc.returncode != 0:
        raise PatchError(f"MapNG HEAD {head} does not descend from the pin {pin}")
    commits = _git(root, "rev-list", "--reverse", f"{pin}..{head}", runner=runner).split()
    if len(commits) != len(names):
        raise PatchError(f"MapNG has {len(commits)} commits above the pin but the series lists {len(names)} patches "
                         f"(run `apply --reset`, or `save` if the commits are intended)")
    for name, commit in zip(names, commits):
        path = patches_dir / name
        if not path.is_file():
            raise PatchError(f"series names {name}, which is not in {patches_dir}")
        want = patch_id(root, path.read_bytes(), runner)
        have_diff = runner(["git", "-C", str(root), "diff-tree", "-p", "--no-commit-id", "-M", commit], capture_output=True)
        if have_diff.returncode != 0:
            raise PatchError(f"git diff-tree failed for {commit}")
        have = patch_id(root, have_diff.stdout, runner)
        if want != have:
            raise PatchError(f"commit {commit[:10]} does not match {name} (patch-id {have[:10]} against {want[:10]}); "
                             f"run `save` if you changed MapNG on purpose, or `apply --reset`")
    return names


def apply_patches(root: Path = MAPNG_ROOT, pin: str = "", patches_dir: Path = PATCHES_DIR, reset: bool = False,
                  runner=subprocess.run) -> list[str]:
    """Create branch `belfast` at the pin and `git am` the series. Idempotent when already applied."""
    names = read_series(patches_dir)
    base = read_base(patches_dir)
    if names and base != pin:
        raise PatchError(f"patches/mapng/BASE is {base!r} but the pin is {pin}: rebase the patches first")
    try:
        verify(root, pin, patches_dir, runner)
        dirty = _git(root, "status", "--porcelain", "--untracked-files=no", runner=runner)
        if not dirty:
            return names  # already pin + series, nothing to do
    except PatchError:
        pass
    have = runner(["git", "-C", str(root), "cat-file", "-e", f"{pin}^{{commit}}"], capture_output=True)
    if have.returncode != 0:
        raise PatchError(f"the pinned commit {pin} is not in {root}: run `git submodule update --init third_party/mapng`")
    head = _git(root, "rev-parse", "HEAD", runner=runner)
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no", runner=runner)
    if dirty:
        raise PatchError("MapNG has uncommitted changes; commit them on the belfast branch and run `save`, "
                         "or discard them yourself: " + "; ".join(dirty.splitlines()[:5]))
    if head != pin and not reset:
        raise PatchError(f"MapNG is at {head}, not the pin, and does not match the series. "
                         f"`apply --reset` rebuilds branch {BRANCH} from the pin (commits on it are lost: `save` them first)")
    _git(root, "checkout", "-q", "-B", BRANCH, pin, runner=runner)
    for name in names:
        path = patches_dir / name
        if not path.is_file():
            raise PatchError(f"series names {name}, which is not in {patches_dir}")
        r = runner(["git", *_IDENT, "-C", str(root), "am", "--quiet", str(path)], capture_output=True)
        if r.returncode != 0:
            runner(["git", "-C", str(root), "am", "--abort"], capture_output=True)
            runner(["git", "-C", str(root), "checkout", "-q", "--detach", pin], capture_output=True)
            msg = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()
            raise PatchError(f"patch {name} does not apply to {pin}: {msg}")
    verify(root, pin, patches_dir, runner)
    return names


def save_patches(root: Path = MAPNG_ROOT, pin: str = "", patches_dir: Path = PATCHES_DIR, runner=subprocess.run) -> list[str]:
    """Write the commits above the pin as patch files, the series and BASE."""
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no", runner=runner)
    if dirty:
        raise PatchError("commit your MapNG changes on the belfast branch before `save`: " + "; ".join(dirty.splitlines()[:5]))
    head = _git(root, "rev-parse", "HEAD", runner=runner)
    commits = _git(root, "rev-list", "--reverse", f"{pin}..{head}", runner=runner).split()
    if not commits:
        raise PatchError("no commits above the pin: nothing to save")
    patches_dir.mkdir(parents=True, exist_ok=True)
    for old in patches_dir.glob("*.patch"):
        old.unlink()
    out = _git(root, "format-patch", "--no-stat", "--zero-commit", "--no-signature", "-N", "--no-numbered",
               "-o", str(patches_dir), f"{pin}..{head}", runner=runner)
    names = sorted(Path(line.strip()).name for line in out.splitlines() if line.strip())
    (patches_dir / "series").write_text(
        "# MapNG patches, applied in this order on top of BASE by tools/mapng_patches.py\n" + "\n".join(names) + "\n",
        encoding="utf-8", newline="\n")
    (patches_dir / "BASE").write_text(pin + "\n", encoding="utf-8", newline="\n")
    return names


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="verify the submodule is the pin plus exactly the series")
    a = sub.add_parser("apply", help="check out branch belfast at the pin and apply the series")
    a.add_argument("--reset", action="store_true", help="rebuild branch belfast from the pin even if it has other commits")
    sub.add_parser("save", help="write the commits above the pin as patch files and update series and BASE")
    args = p.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mapng_export import PINNED_COMMIT  # lazy: mapng_export imports this module
    try:
        if args.cmd == "status":
            names = verify(MAPNG_ROOT, PINNED_COMMIT)
        elif args.cmd == "apply":
            names = apply_patches(MAPNG_ROOT, PINNED_COMMIT, reset=args.reset)
        else:
            names = save_patches(MAPNG_ROOT, PINNED_COMMIT)
    except PatchError as exc:
        print(f"mapng_patches: {exc}", file=sys.stderr)
        return exc.code
    print(f"MapNG = {PINNED_COMMIT[:10]} + {len(names)} patch(es): {', '.join(names) or 'none'}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
