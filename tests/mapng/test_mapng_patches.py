"""Tests for tools/mapng_patches.py and the patch-aware check_pin, on throwaway git repos. No MapNG, no network."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import mapng_export as me  # noqa: E402
import mapng_patches as mp  # noqa: E402

IDENT = ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def git(root, *args, **kw):
    r = subprocess.run(["git", *IDENT, "-C", str(root), *args], capture_output=True, text=True, **kw)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A one-commit 'upstream' repo (the pin), plus an empty patches dir."""
    root = tmp_path / "mapng"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "core.autocrlf", "false")
    (root / "a.js").write_text("one\ntwo\nthree\n", encoding="utf-8", newline="\n")
    git(root, "add", "a.js")
    git(root, "commit", "-q", "-m", "upstream")
    pin = git(root, "rev-parse", "HEAD")
    patches = tmp_path / "patches"
    patches.mkdir()
    return root, pin, patches


def make_patch(root, pin, patches, text="one\nTWO\nthree\n"):
    """Commit a change on branch belfast and save it as the series; return the patch name."""
    git(root, "checkout", "-q", "-B", "belfast", pin)
    (root / "a.js").write_text(text, encoding="utf-8", newline="\n")
    git(root, "commit", "-q", "-am", "make two upper case")
    return mp.save_patches(root, pin, patches)


def test_save_apply_verify_roundtrip(repo):
    root, pin, patches = repo
    names = make_patch(root, pin, patches)
    assert len(names) == 1 and (patches / "series").read_text().splitlines()[-1] == names[0]
    assert (patches / "BASE").read_text().strip() == pin
    assert mp.verify(root, pin, patches) == names
    # a fresh checkout at the pin fails verify loudly, then apply brings it to pin + series
    git(root, "checkout", "-q", "--detach", pin)
    with pytest.raises(mp.PatchError, match="none of the 1 belfast patches"):
        mp.verify(root, pin, patches)
    assert mp.apply_patches(root, pin, patches) == names
    assert mp.verify(root, pin, patches) == names
    assert (root / "a.js").read_text() == "one\nTWO\nthree\n"
    assert mp.apply_patches(root, pin, patches) == names  # idempotent


def test_changed_pin_fails_loudly(repo):
    root, pin, patches = repo
    make_patch(root, pin, patches)
    other = "e" * 40
    with pytest.raises(mp.PatchError, match="the pin changed"):
        mp.verify(root, other, patches)
    with pytest.raises(mp.PatchError, match="rebase the patches first"):
        mp.apply_patches(root, other, patches)


def test_extra_or_edited_commit_fails(repo):
    root, pin, patches = repo
    make_patch(root, pin, patches)
    (root / "a.js").write_text("one\nTWO\nthree\nfour\n", encoding="utf-8", newline="\n")
    git(root, "commit", "-q", "-am", "unlisted change")
    with pytest.raises(mp.PatchError, match="2 commits above the pin but the series lists 1"):
        mp.verify(root, pin, patches)
    git(root, "reset", "-q", "--hard", "HEAD~1")
    git(root, "commit", "-q", "--amend", "-m", "x", "--allow-empty")
    (root / "a.js").write_text("one\nTWO!\nthree\n", encoding="utf-8", newline="\n")
    git(root, "commit", "-q", "-am", "edit", "--amend")
    with pytest.raises(mp.PatchError, match="does not match"):
        mp.verify(root, pin, patches)


def test_apply_refuses_unknown_state_without_reset(repo):
    root, pin, patches = repo
    make_patch(root, pin, patches)
    (root / "a.js").write_text("one\nTWO\nthree\nfour\n", encoding="utf-8", newline="\n")
    git(root, "commit", "-q", "-am", "unlisted change")
    with pytest.raises(mp.PatchError, match="--reset"):
        mp.apply_patches(root, pin, patches)
    names = mp.apply_patches(root, pin, patches, reset=True)
    assert mp.verify(root, pin, patches) == names


def test_apply_reports_a_patch_that_no_longer_applies(repo):
    root, pin, patches = repo
    make_patch(root, pin, patches)
    # a different upstream commit, standing in for a re-pin: the patch touches lines that changed
    git(root, "checkout", "-q", "--detach", pin)
    (root / "a.js").write_text("one\nzwei\nthree\n", encoding="utf-8", newline="\n")
    git(root, "commit", "-q", "-am", "upstream moved on")
    new_pin = git(root, "rev-parse", "HEAD")
    (patches / "BASE").write_text(new_pin + "\n", encoding="utf-8")  # BASE updated without rebasing the patch
    with pytest.raises(mp.PatchError, match="does not apply"):
        mp.apply_patches(root, new_pin, patches, reset=True)
    assert git(root, "rev-parse", "HEAD") == new_pin  # left clean at the pin, mid-apply state aborted


def test_dirty_tree_is_refused(repo):
    root, pin, patches = repo
    make_patch(root, pin, patches)
    (root / "a.js").write_text("changed\n", encoding="utf-8", newline="\n")
    with pytest.raises(mp.PatchError, match="uncommitted"):
        mp.apply_patches(root, pin, patches, reset=True)


def test_check_pin_uses_the_series(repo, monkeypatch):
    root, pin, patches = repo
    monkeypatch.setattr(me, "PINNED_COMMIT", pin)
    # no patches: the original rule, HEAD is the pin
    assert me.check_pin(root, patches_dir=patches) == pin
    make_patch(root, pin, patches)
    head = me.check_pin(root, patches_dir=patches)
    assert head != pin
    git(root, "checkout", "-q", "--detach", pin)
    with pytest.raises(me.GuardError) as exc:
        me.check_pin(root, patches_dir=patches)
    assert exc.value.code == me.EXIT_PIN


def test_check_pin_catches_a_changed_pin_with_patches(repo, monkeypatch):
    root, pin, patches = repo
    make_patch(root, pin, patches)
    monkeypatch.setattr(me, "PINNED_COMMIT", "f" * 40)
    with pytest.raises(me.GuardError) as exc:
        me.check_pin(root, patches_dir=patches)
    assert exc.value.code == me.EXIT_PIN and "pin changed" in str(exc.value)


def test_real_series_matches_the_repo_base():
    """The committed patches were made against the pin in tools/mapng_export.py."""
    if mp.read_series():
        assert mp.read_base() == me.PINNED_COMMIT
    for name in mp.read_series():
        assert (mp.PATCHES_DIR / name).is_file()
