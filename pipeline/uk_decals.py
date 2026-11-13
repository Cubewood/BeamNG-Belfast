#!/usr/bin/env python3
"""UK road-marking decal textures and materials, drawn from rules/uk_markings.yaml (T-402).

    uv run --no-project --python 3.12 --with pillow --with numpy --with pyyaml python pipeline/uk_decals.py \
        [--out H:/BelfastData/uk_decals] [--level belfast_slice] [--no-fragment]

Every texture is drawn here, from the dimensions in the YAML. Nothing is copied from the game or from MapNG.
One run writes

    <out>/uk_markings_cubewood/*.png            textures (not committed: *.png is git-ignored)
    <out>/uk_markings_cubewood/main.materials.json
    rules/uk_decals/main.materials.json         the same materials file, for the default level (committed)
    rules/uk_decals/manifest.json               rule -> material, repeat length, width (committed; T-403 reads it)

Copy the folder `uk_markings_cubewood` to `levels/<level>/art/uk_markings_cubewood/` in the level. The game loads
every `*.materials.json` under the level folder, which is how MapNG's own `art/roads` and `art/decals` files load.

How a DecalRoad uses these materials (BeamNG DecalRoad, read from MapNG's use of the stock line materials):
the texture is stretched ONCE across the road width and tiled every `textureLength` metres along the road. Image
columns run across the road, image rows run along it. So `textureLength` in the manifest must be set on the
DecalRoad, and the DecalRoad width is `paint_width / fill_across` for the line materials (the paint fills the
middle `fill_across` of the texture, the rest is clear margin so the quad edges do not bleed).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import yaml
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
YAML_PATH = REPO / "rules" / "uk_markings.yaml"
FRAGMENT_DIR = REPO / "rules" / "uk_decals"
DEFAULT_OUT = Path("H:/BelfastData/uk_decals")
DEFAULT_LEVEL = "belfast_slice"
FOLDER = "uk_markings_cubewood"
SUFFIX = "cubewood"
SS = 4  # samples per pixel along each axis

FILL_LINE = 0.75   # paint share of the texture width for a scalable line
FILL_SYMBOL = 0.875  # paint share, across and along, for a symbol drawn once

COLOURS = {"white": (238, 238, 232), "yellow": (240, 196, 40)}
ROUGHNESS = 150  # 0..255 on the .data.png, paint is fairly matt
SHARED = {
    "white": f"uk_paint_white_{SUFFIX}_b.color.png",
    "yellow": f"uk_paint_yellow_{SUFFIX}_b.color.png",
    "normal": f"uk_flat_{SUFFIX}_nm.normal.png",
    "rough": f"uk_paint_{SUFFIX}_r.data.png",
}

# Annotations that exist in the game's tech/annotations.json.
ANN_SOLID, ANN_DASHED, ANN_AREA, ANN_SYMBOL = "SOLID_LINE", "DASHED_LINE", "RESTRICTED_STREET", "DRIVING_INSTRUCTIONS"


@dataclass
class Spec:
    key: str                      # short id, the material is uk_<key>_cubewood
    px_w: int                     # texture width in pixels (across the road)
    px_h: int                     # texture height in pixels (along the road)
    tex_w_mm: float               # texture width in mm at the nominal paint width
    tex_l_mm: float               # texture length in mm = textureLength
    draw: Callable                # draw(X, Y) -> bool/float array, X across (mm), Y along (mm)
    colour: str
    annotation: str
    scalable: bool                # True: DecalRoad width = paint width / fill_across, any width works
    fill_across: float
    paint_nominal_mm: float       # painted width across at the nominal texture width
    tiles_along: bool             # True: repeats every tex_l_mm; False: a symbol drawn once (road length = tex_l_mm)
    rules: list[str]
    period_mm: float | None = None
    mark_mm: float | None = None
    gap_mm: float | None = None
    notes: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def material(self) -> str:
        return f"uk_{self.key}_{SUFFIX}"

    @property
    def opacity_file(self) -> str:
        return f"{self.material}_o.data.png"


# ----------------------------------------------------------------------------------------------------- YAML

def load_rules(path: Path = YAML_PATH) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {r["id"]: r for r in doc["rules"]}


# ----------------------------------------------------------------------------------------------------- shapes

def dash_mask(Y: np.ndarray, mark: float, gap: float) -> np.ndarray:
    """True on the painted part of a dash pattern whose mark is centred in each module."""
    module = mark + gap
    m = np.mod(Y, module)
    return (m >= gap / 2.0) & (m < gap / 2.0 + mark)


def band(X: np.ndarray, centre: float, width: float) -> np.ndarray:
    return np.abs(X - centre) <= width / 2.0


def line_spec(key: str, rule_id: str, rules: dict, colour="white", dashed=True, extra_rules=()) -> Spec:
    g = rules[rule_id]["geometry"]
    paint = float(g.get("default_width_mm") or g["width_mm"])
    tex_w = paint / FILL_LINE
    if dashed:
        mark, gap = float(g["mark_mm"]), float(g["gap_mm"])
        module = mark + gap
        assert abs(module - g["module_mm"]) < 1e-6, f"{rule_id}: mark+gap != module_mm"
        px_h = 2048 if module >= 4000 else (512 if module >= 1500 else 256)
        draw = lambda X, Y: band(X, tex_w / 2, paint) & dash_mask(Y, mark, gap)  # noqa: E731
        return Spec(key, 16, px_h, tex_w, module, draw, colour, ANN_DASHED, True, FILL_LINE, paint, True,
                    [rule_id, *extra_rules], module, mark, gap)
    draw = lambda X, Y: band(X, tex_w / 2, paint)  # noqa: E731
    return Spec(key, 16, 16, tex_w, 2000.0, draw, colour, ANN_SOLID, True, FILL_LINE, paint, True,
                [rule_id, *extra_rules])


def build_specs(rules: dict) -> list[Spec]:
    specs: list[Spec] = []

    # Single lines. Centre, lane and warning lines, small and large module.
    for key, rid in [("centre_1008", "centre_1008"), ("centre_1008_1", "centre_1008_1"),
                     ("lane_1005", "lane_1005"), ("lane_1005_1", "lane_1005_1"),
                     ("warning_1004", "warning_1004"), ("warning_1004_1", "warning_1004_1")]:
        specs.append(line_spec(key, rid, rules))

    # Continuous white and yellow. One texture each, used for every continuous line.
    specs.append(line_spec("solid_white", "edge_1012_1", rules, "white", dashed=False,
                           extra_rules=["stop_1002_1", "stop_signals_1001", "bus_lane_1049a", "hatched_1040"]))
    specs.append(line_spec("solid_yellow", "yellow_single_1017", rules, "yellow", dashed=False,
                           extra_rules=["yellow_box_1043"]))

    # Short dash patterns.
    specs.append(line_spec("dash_600_300", "edge_1009A", rules))
    specs.append(line_spec("dash_1000_1000", "edge_1010", rules, extra_rules=["giveway_roundabout_1003_1"]))
    specs.append(line_spec("giveway_mini_1003_3", "giveway_mini_roundabout_1003_3", rules))

    # Give Way, double dashed (1003A): two rows of dashes.
    g = rules["giveway_1003a"]["geometry"]
    row, rgap = float(g["row_width_mm"]), float(g["row_gap_mm"])
    mark, gap = float(g["mark_mm"]), float(g["gap_mm"])
    paint = 2 * row + rgap
    tex_w = paint / FILL_SYMBOL
    c = tex_w / 2

    def draw_gw(X, Y, c=c, row=row, rgap=rgap, mark=mark, gap=gap):
        rows = band(X, c - (rgap + row) / 2, row) | band(X, c + (rgap + row) / 2, row)
        return rows & dash_mask(Y, mark, gap)

    specs.append(Spec("giveway_1003a", 64, 256, tex_w, mark + gap, draw_gw, "white", ANN_DASHED, False,
                      FILL_SYMBOL, paint, True, ["giveway_1003a"], mark + gap, mark, gap))

    # Double white lines (1013.1) and double yellow (1018.1).
    g = rules["double_white_1013_1"]["geometry"]
    lw = float(g["line_width_mm"]["recommended"])
    dgap = float(g["gap_between_lines_mm"]["recommended"])
    bm, bg = float(g["broken_line"]["mark_mm"]), float(g["broken_line"]["gap_mm"])
    paint = 2 * lw + dgap
    tex_w = paint / FILL_LINE
    c = tex_w / 2

    def draw_dw_mixed(X, Y, c=c, lw=lw, dgap=dgap, bm=bm, bg=bg):
        left = band(X, c - (dgap + lw) / 2, lw)
        right = band(X, c + (dgap + lw) / 2, lw) & dash_mask(Y, bm, bg)
        return left | right

    def draw_dw_solid(X, Y, c=c, lw=lw, dgap=dgap):
        return band(X, c - (dgap + lw) / 2, lw) | band(X, c + (dgap + lw) / 2, lw)

    specs.append(Spec("double_white_mixed_1013_1", 64, 2048, tex_w, bm + bg, draw_dw_mixed, "white", ANN_SOLID,
                      True, FILL_LINE, paint, True, ["double_white_1013_1"], bm + bg, bm, bg,
                      notes="continuous line on the image-left side, broken line on the image-right side; "
                            "reverse the DecalRoad node order to swap the sides"))
    specs.append(Spec("double_white_solid_1013_1", 64, 16, tex_w, 2000.0, draw_dw_solid, "white", ANN_SOLID,
                      True, FILL_LINE, paint, True, ["double_white_1013_1"],
                      notes="both lines continuous (no crossing either way)"))

    g = rules["yellow_double_1018_1"]["geometry"]
    rw = float(g["default_row_width_mm"])
    yg = rw  # gap equals the line width (rule notes)
    paint = 2 * rw + yg
    tex_w = paint / FILL_LINE
    c = tex_w / 2

    def draw_dy(X, Y, c=c, rw=rw, yg=yg):
        return band(X, c - (yg + rw) / 2, rw) | band(X, c + (yg + rw) / 2, rw)

    specs.append(Spec("double_yellow_1018_1", 32, 16, tex_w, 2000.0, draw_dy, "yellow", ANN_SOLID, True,
                      FILL_LINE, paint, True, ["yellow_double_1018_1"]))

    # Give Way triangle (1023A): outline of an inverted triangle, apex at image row 0.
    g = rules["triangle_1023a"]["geometry"]
    tw, tl, tlw = float(g["width_mm"]), float(g["length_mm"]), float(g["line_width_mm"])
    tex_w, tex_l = tw / FILL_SYMBOL, tl / FILL_SYMBOL
    ox, oy = (tex_w - tw) / 2, (tex_l - tl) / 2
    apex = np.array([ox + tw / 2, oy])
    bl = np.array([ox, oy + tl])
    br = np.array([ox + tw, oy + tl])
    centroid = (apex + bl + br) / 3
    edges = []
    for a, b in ((apex, bl), (bl, br), (br, apex)):
        d = b - a
        n = np.array([-d[1], d[0]]) / np.hypot(*d)
        if np.dot(centroid - a, n) < 0:
            n = -n
        edges.append((a, n))  # inward normal

    def draw_tri(X, Y, edges=edges, tlw=tlw):
        dist = [((X - a[0]) * n[0] + (Y - a[1]) * n[1]) for a, n in edges]
        outer = (dist[0] >= 0) & (dist[1] >= 0) & (dist[2] >= 0)
        inner = (dist[0] >= tlw) & (dist[1] >= tlw) & (dist[2] >= tlw)
        return outer & ~inner

    specs.append(Spec("giveway_triangle_1023a", 128, 512, tex_w, tex_l, draw_tri, "white", ANN_SYMBOL, False,
                      FILL_SYMBOL, tw, False, ["triangle_1023a"],
                      notes="drawn once, not tiled. Apex at image row 0 (start of the DecalRoad), base at the end. "
                            "Run the nodes in the direction of travel so the apex meets the driver first. "
                            "Whether row 0 is the start of the road in game is not verified"))

    # Zig-zag controlled area (zebra and signal crossings). Marks of 2000 with 150 gaps, alternating direction.
    g = rules["zigzag_zebra_1001_4"]["geometry"]
    zm, zg = float(g["standard_mark_length_mm"]), float(g["gap_between_marks_mm"])
    zlw, zspan = float(g["zigzag_line_width_mm"]), float(g["guide_lines_apart_mm"])
    period = 2 * (zm + zg)
    tex_w = zspan / FILL_SYMBOL
    off = (tex_w - zspan) / 2
    hz = zg / 2
    # one zig (lat 0.15 to 0.85 of the span) and one zag, each a band of width zlw about its centre line
    inset = zlw / 2
    strokes = [((inset, hz), (zspan - inset, hz + zm)),
               ((zspan - inset, hz + zm + zg), (inset, hz + 2 * zm + zg))]

    def draw_zz(X, Y, strokes=strokes, off=off, period=period, zspan=zspan, zlw=zlw, hz=hz, zm=zm, zg=zg):
        lat = X - off
        Ym = np.mod(Y, period)
        out = np.zeros(X.shape, dtype=bool)
        for (p0, p1) in strokes:
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            n = math.hypot(dx, dy)
            cross = np.abs((lat - p0[0]) * dy - (Ym - p0[1]) * dx) / n
            out |= (cross <= zlw / 2) & (Ym >= p0[1]) & (Ym <= p1[1])
        return out & (lat >= 0) & (lat <= zspan)

    specs.append(Spec("zigzag_1001_4", 256, 2048, tex_w, period, draw_zz, "white", ANN_AREA, False,
                      FILL_SYMBOL, zspan, True, ["zigzag_zebra_1001_4", "zigzag_signal_crossing_1001_3"],
                      period, zm, zg,
                      notes="one repeat = one zig plus one zag. Draw it along the kerb line, DecalRoad width fixed"))

    # Yellow box junction hatch (1043): criss-cross diagonals, a square tile that repeats both ways.
    g = rules["yellow_box_1043"]["geometry"]
    dlw = float(g["diagonal_width_mm"])
    for key, spacing in (("box_hatch_1043", g["diagonal_spacing_mm"]["shortest_side_under_9m"]),
                         ("box_hatch_1043_wide", g["diagonal_spacing_mm"]["shortest_side_9m_or_more"])):
        p = float(spacing) * math.sqrt(2)  # axis period for lines a perpendicular `spacing` apart

        def draw_hatch(X, Y, p=p, dlw=dlw):
            u = np.abs(np.mod(X + Y + p / 2, p) - p / 2) / math.sqrt(2)
            v = np.abs(np.mod(X - Y + p / 2, p) - p / 2) / math.sqrt(2)
            return (u <= dlw / 2) | (v <= dlw / 2)

        specs.append(Spec(key, 512, 512, p, p, draw_hatch, "yellow", ANN_AREA, False, 1.0, p, True,
                          ["yellow_box_1043"], p, None, None,
                          notes=f"square tile, diagonals {spacing:.0f} mm apart at right angles to each other, "
                                f"{dlw:.0f} mm wide. Lay side by side in strips {p / 1000:.3f} m wide. "
                                "The 200 mm border is uk_solid_yellow_cubewood",
                          extra={"perpendicular_spacing_mm": float(spacing)}))
    return specs


# ----------------------------------------------------------------------------------------------------- render

def render(spec: Spec) -> np.ndarray:
    """Return an (px_h, px_w) uint8 coverage image, supersampled SSxSS per pixel."""
    ys = (np.arange(spec.px_h * SS) + 0.5) / (spec.px_h * SS) * spec.tex_l_mm
    xs = (np.arange(spec.px_w * SS) + 0.5) / (spec.px_w * SS) * spec.tex_w_mm
    X, Y = np.meshgrid(xs, ys)
    m = spec.draw(X, Y).astype(np.float32)
    m = m.reshape(spec.px_h, SS, spec.px_w, SS).mean(axis=(1, 3))
    return np.clip(np.rint(m * 255), 0, 255).astype(np.uint8)


def save_png(arr: np.ndarray, path: Path, mode: str) -> None:
    Image.fromarray(arr, mode).save(path, format="PNG", optimize=True)


def write_textures(specs: list[Spec], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        save_png(render(spec), folder / spec.opacity_file, "L")
    for colour, rgb in COLOURS.items():
        save_png(np.full((8, 8, 3), rgb, np.uint8), folder / SHARED[colour], "RGB")
    save_png(np.dstack([np.full((8, 8), v, np.uint8) for v in (128, 128, 255)]), folder / SHARED["normal"], "RGB")
    save_png(np.full((8, 8), ROUGHNESS, np.uint8), folder / SHARED["rough"], "L")


# ----------------------------------------------------------------------------------------------------- materials

def material_def(spec: Spec, level: str) -> dict:
    base = f"/levels/{level}/art/{FOLDER}/"
    # Structure follows the stock decal-road line materials (art/shapes/common/decalroads/main.materials.json):
    # translucent, RoadAndPath, an annotation, base colour, normal, opacity and roughness maps.
    return {
        "name": spec.material,
        "mapTo": spec.material,
        "class": "Material",
        "persistentId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"beamng-belfast/uk_decals/{spec.material}")),
        "Stages": [
            {
                "baseColorMap": base + SHARED[spec.colour],
                "normalMap": base + SHARED["normal"],
                "opacityMap": base + spec.opacity_file,
                "roughnessMap": base + SHARED["rough"],
            },
            {}, {}, {},
        ],
        "alphaRef": 8,
        "annotation": spec.annotation,
        "castShadows": False,
        "groundType": "ASPHALT",
        "materialTag0": "RoadAndPath",
        "materialTag1": "beamng",
        "translucent": True,
        "translucentZWrite": True,
        "version": 1.5,
    }


def materials_file(specs: list[Spec], level: str) -> dict:
    return {s.material: material_def(s, level) for s in specs}


def manifest(specs: list[Spec], level: str, rules: dict) -> dict:
    mats = {}
    rule_map: dict[str, list[str]] = {}
    for s in specs:
        entry = {
            "texture_length_m": round(s.tex_l_mm / 1000, 6),
            "tiles_along": s.tiles_along,
            "colour": s.colour,
            "annotation": s.annotation,
            "paint_width_nominal_mm": round(s.paint_nominal_mm, 3),
            "opacity_texture": s.opacity_file,
            "texture_px": [s.px_w, s.px_h],
            "rules": s.rules,
        }
        if s.scalable:
            entry["decal_width_m"] = "paint_width_m / fill_across"
            entry["fill_across"] = s.fill_across
            entry["decal_width_nominal_m"] = round(s.tex_w_mm / 1000, 6)
        else:
            entry["decal_width_m"] = round(s.tex_w_mm / 1000, 6)
            entry["fill_across"] = s.fill_across
        if s.period_mm is not None:
            entry["period_mm"] = s.period_mm
        if s.mark_mm is not None:
            entry["mark_mm"], entry["gap_mm"] = s.mark_mm, s.gap_mm
        if s.notes:
            entry["notes"] = s.notes
        entry.update(s.extra)
        mats[s.material] = entry
        for r in s.rules:
            rule_map.setdefault(r, []).append(s.material)
    unverified = sorted(r for r, v in rules.items() if not v.get("verified", True))
    return {
        "generator": "pipeline/uk_decals.py",
        "task": "T-402",
        "default_level": level,
        "texture_folder": f"levels/<level>/art/{FOLDER}/",
        "materials": mats,
        "rule_to_materials": dict(sorted(rule_map.items())),
        "rules_not_drawn": {
            "mini_roundabout_1003_4": "central arrows and disc are a symbol (atlas decals), not a repeat texture",
            "zebra_crossing_1001_4": "stripe pitch is not in the YAML; MapNG's own zebra material stays",
            "crossing_studs_1055_1": "studs are objects, not paint",
            "yellow_bars_roundabout_1067": "not in the slice, and NI uses 1003.6",
            "hatched_1040": "boundary lines only (uk_solid_white or the 1004 materials); diagonals not transcribed",
            "edge_width_variants": "all single lines scale with DecalRoad width, so 100/150/200 mm need no extra material",
        },
        "rules_verified_false_in_yaml": unverified,
    }


def write_all(out: Path, level: str, write_fragment: bool = True, yaml_path: Path = YAML_PATH) -> list[Spec]:
    rules = load_rules(yaml_path)
    specs = build_specs(rules)
    folder = out / FOLDER
    write_textures(specs, folder)
    mats = json.dumps(materials_file(specs, level), indent=2) + "\n"
    (folder / "main.materials.json").write_text(mats, encoding="utf-8")
    if write_fragment:
        FRAGMENT_DIR.mkdir(parents=True, exist_ok=True)
        (FRAGMENT_DIR / "main.materials.json").write_text(
            json.dumps(materials_file(specs, DEFAULT_LEVEL), indent=2) + "\n", encoding="utf-8")
        (FRAGMENT_DIR / "manifest.json").write_text(
            json.dumps(manifest(specs, DEFAULT_LEVEL, rules), indent=2) + "\n", encoding="utf-8")
    return specs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="build folder (default H:/BelfastData/uk_decals)")
    ap.add_argument("--level", default=DEFAULT_LEVEL, help="level name used in the texture paths")
    ap.add_argument("--no-fragment", action="store_true", help="do not rewrite rules/uk_decals/")
    ap.add_argument("--rules", type=Path, default=YAML_PATH)
    args = ap.parse_args(argv)
    specs = write_all(args.out, args.level, not args.no_fragment, args.rules)
    for s in specs:
        print(f"{s.material}: {s.px_w}x{s.px_h} px, textureLength {s.tex_l_mm / 1000:g} m")
    print(f"wrote {len(specs)} materials to {args.out / FOLDER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
