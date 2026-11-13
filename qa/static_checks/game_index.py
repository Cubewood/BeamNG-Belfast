"""Index of the game's stock files and material names (paths only, no game content).

The index is a text file under H:\\BelfastData (never in git: it is derived from the game install, which is
read-only reference). One lowercase path per line, `M <name>` lines for every material the game's own
`*.materials.json` define. It is rebuilt when the content folder changes (stamp on the first line).

It answers one question for the static checks: "does the game ship this file or material?", so an export
that points at a stock asset that is not there (silently missing in game) is found without starting the game.
The format matches what MapNG's own linter expects (`scripts/lib/gameIndex.mjs`), so `lint_runner.mjs`
feeds the same file to MapNG's checks.
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from pathlib import Path

DEFAULT_GAME = Path(r"H:\SteamLibrary\steamapps\common\BeamNG.drive")
DEFAULT_INDEX = Path(r"H:\BelfastData\qa\static\game_index.txt")

_TEX = re.compile(rb'"(/(?:assets|levels|art)/[^"]+\.(?:png|dds|jpg|jpeg))"', re.I)


def _materials(data: bytes, out: list[str]) -> None:
    try:
        for t in set(_TEX.findall(data)):
            out.append("T " + t.decode("utf-8", "replace").lower())
        doc = json.loads(data)
    except Exception:  # an unreadable stock file is not our problem
        return
    if not isinstance(doc, dict):
        return
    for key, value in doc.items():
        out.append("M " + key)
        if isinstance(value, dict):
            for field in ("name", "mapTo", "internalName"):
                if isinstance(value.get(field), str):
                    out.append("M " + value[field])


def content_stamp(content: Path) -> str:
    """Cheap fingerprint of the content folder: zip count, total size, newest mtime."""
    n = size = 0
    newest = 0.0
    for d, _dirs, files in os.walk(content):
        for f in files:
            st = os.stat(os.path.join(d, f))
            n += 1
            size += st.st_size
            newest = max(newest, st.st_mtime)
    return f"{n}:{size}:{int(newest)}"


def build_index(content: Path) -> list[str]:
    lines: list[str] = []
    for d, _dirs, files in os.walk(content):
        for f in files:
            full = os.path.join(d, f)
            if f.lower().endswith(".zip"):
                try:
                    zf = zipfile.ZipFile(full)
                except Exception:
                    continue
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    lines.append(name.lower())
                    if name.endswith("materials.json"):
                        _materials(zf.read(name), lines)
            else:
                rel = os.path.relpath(full, content).replace(os.sep, "/")
                lines.append(rel.lower())
                if f.endswith("materials.json"):
                    try:
                        _materials(Path(full).read_bytes(), lines)
                    except OSError:
                        pass
    return lines


def ensure_index(game: Path = DEFAULT_GAME, index: Path = DEFAULT_INDEX, rebuild: bool = False) -> Path:
    """Path of an up-to-date index file, building it if it is missing or the game changed."""
    content = game / "content"
    if not content.is_dir():
        raise FileNotFoundError(f"game content folder not found: {content}")
    stamp = content_stamp(content)
    if index.exists() and not rebuild:
        with index.open("r", encoding="utf-8") as fh:
            if fh.readline().strip() == stamp:
                return index
    lines = build_index(content)
    index.parent.mkdir(parents=True, exist_ok=True)
    tmp = index.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:      # LF only: the node linter splits on "\n"
        fh.write(stamp + "\n" + "\n".join(lines))
    tmp.replace(index)
    return index


class GameIndex:
    """Lookup over an index file (or over plain sets, for tests)."""

    def __init__(self, files: set[str], materials: set[str], refs: set[str] | None = None):
        self.files = files
        self.materials = materials
        self.refs = refs or set()      # paths the game's own materials point at (some are missing in the game too)

    @classmethod
    def load(cls, path: Path) -> "GameIndex":
        files: set[str] = set()
        mats: set[str] = set()
        refs: set[str] = set()
        with Path(path).open("r", encoding="utf-8") as fh:
            fh.readline()
            for line in fh:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith("T "):
                    refs.add(line[2:].lstrip("/").lower())
                elif line.startswith("M "):
                    mats.add(line[2:])
                else:
                    files.add(line)
        return cls(files, mats, refs)

    def dangling(self, rel: str) -> bool:
        """True when the game's own materials reference `rel` although the game does not ship it."""
        return rel.lower().lstrip("/") in self.refs

    def has(self, rel: str) -> bool:
        """True when the game ships `rel` (leading slash allowed). Mirrors the engine's fallbacks:
        a texture may be .png, .dds or .jpg, a shape .dae or .cdae, and `<file>.link` redirects."""
        r = rel.lower().lstrip("/")
        alts = [r, r + ".link"]
        for a, b in ((".png", (".dds", ".jpg")), (".dds", (".png", ".jpg")), (".jpg", (".dds", ".png")),
                     (".dae", (".cdae",)), (".cdae", (".dae",))):
            if r.endswith(a):
                alts += [r[: -len(a)] + e for e in b]
        return any(a in self.files for a in alts)

    def has_material(self, name: str) -> bool:
        return name in self.materials
