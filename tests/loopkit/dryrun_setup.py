#!/usr/bin/env python3
"""Build the throwaway repo for the loop kit dry run.

Usage: python tests/loopkit/dryrun_setup.py [--root H:\\BelfastData\\spikes\\loopkit]

Creates <root>\\repo (a git repo on branch `dev`) and <root>\\remote.git (bare, its `origin`),
copies in the real kit (agents, skills, settings, hooks, the three task tools, CLAUDE.md) and
puts tests/loopkit/dummy_task.md in as its task.md. The real repo is only read.
Run it again to start over (the old <root>\\repo and remote.git are deleted first).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_OUT = Path(r"H:\BelfastData\spikes\loopkit")


def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=str(DEFAULT_OUT))
    a = p.parse_args(argv)
    out = Path(a.root)
    if "spikes" not in out.parts:
        print("refusing: --root must be a spikes folder", file=sys.stderr)
        return 2
    repo, remote = out / "repo", out / "remote.git"
    for d in (repo, remote):
        if d.exists():
            shutil.rmtree(d, onerror=lambda f, path, e: (Path(path).chmod(0o700), f(path)))
    for f in ("hello.txt", "fail.txt", "game.txt"):
        (out / f).unlink(missing_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    repo.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(remote)], check=True, capture_output=True)
    git(repo, "init", "-b", "dev")
    git(repo, "config", "user.name", "loopkit-dryrun")
    git(repo, "config", "user.email", "dryrun@example.invalid")
    git(repo, "remote", "add", "origin", str(remote))

    (repo / "tools").mkdir()
    for name in ("taskfile.py", "next_task.py", "task_state.py"):
        shutil.copy2(ROOT / "tools" / name, repo / "tools" / name)
    shutil.copytree(ROOT / ".claude" / "agents", repo / ".claude" / "agents")
    shutil.copytree(ROOT / ".claude" / "skills", repo / ".claude" / "skills")
    shutil.copytree(ROOT / ".claude" / "hooks", repo / ".claude" / "hooks")
    shutil.copy2(ROOT / ".claude" / "settings.json", repo / ".claude" / "settings.json")
    shutil.copy2(ROOT / "CLAUDE.md", repo / "CLAUDE.md")
    shutil.copy2(HERE / "dummy_task.md", repo / "task.md")
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    for d in ("issues", "feedback", "reports", "docs"):
        (repo / d).mkdir()
        (repo / d / ".gitkeep").write_text("", encoding="utf-8")

    git(repo, "add", "-A")
    git(repo, "commit", "-m", "dry run baseline")
    git(repo, "push", "-u", "origin", "dev")
    print(f"repo   {repo}")
    print(f"remote {remote}")
    print(git(repo, "log", "--oneline"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
