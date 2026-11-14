#!/usr/bin/env python3
"""Evaluation set for the image judge (T-305): planted faults plus clean frames.

    uv run --no-project --python 3.12 --with requests --with pillow python qa/eval_plant.py plan
    uv run --no-project --python 3.12 --with requests --with pillow python qa/eval_plant.py run [--only P001,P002]
    uv run --no-project --python 3.12 --with requests --with pillow python qa/eval_plant.py assemble

`plan` (needs the scene dump, which `run` makes through the game when it is missing) assigns one planted fault
to each of N driver's-eye poses of a junction sweep (docs/junction_sweep.md) and writes qa/eval/plan.json.
`run` starts the QA game through tools/gamectl.py (refuses if any BeamNG process exists or it is 09:00-18:00),
loads belfast_slice, and for every planned frame: breaks the live scene through Lua (hide or re-material
DecalRoads, move road nodes, raise or cut terrain cells, create TSStatic props), re-takes the sweep's shot at that
pose (qa/junction_sweep.take_shot), then puts everything back. Nothing on disk changes (no level file, no zip);
the faults exist only in the running game. `assemble` merges plan + captures + the visual labels
(qa/eval/visual_labels.json, written by looking at the frames) into qa/eval/labels.jsonl and prints the counts.

Images (not in git): H:\\BelfastData\\qa\\eval\\planted\\Pnnn.jpg and \\clean\\Cnnn.jpg. The image judge's model
never sees this set; it is the answer key T-306 measures that model against. See docs/eval_set.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import random
import shutil
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(HERE))

EVAL_REPO = HERE / "eval"
EVAL_DATA = Path(r"H:\BelfastData\qa\eval")
SWEEP_ROOT = Path(r"H:\BelfastData\qa\sweep")
DEFAULT_SWEEP = SWEEP_ROOT / "2026-11-13_220428"
ROUTES = HERE / "routes" / "belfast_slice.json"
SLICE_ZIP = Path(r"H:\BeamNG-QA\profile\current\mods\belfast_slice.zip")
NL = chr(10)
SEED = 305
LANE_AHEAD_M = 70                      # lane polyline length ahead of the car
MARK_RADIUS_M = 50.0                   # marking roads with a node this close to the car are touched
DEFECT_TYPES = ["none", "missing-markings", "wrong-markings", "building-in-road", "object-in-road", "terrain-step",
                "floating-object", "buried-object", "missing-texture", "wrong-side-sign", "road-geometry", "other"]

CL = "/art/shapes/garage_and_dealership/Clutter/"
OA = "/levels/belfast_slice/map_assets/official_assets/"
SHAPES = {
    "lamp": CL + "eca_lamppost.dae",
    "lamp2": "/assets/meshes/props/urban/lighting/s_lamp_post_street_single_01.dae",
    "sign_giveway": CL + "italy_traf_sign_give_way.dae",
    "sign_oneway": CL + "italy_traf_sign_one_way.dae",
    "sign_noentry": CL + "italy_traf_sign_noentry.dae",
    "sign_priority": CL + "italy_traf_sign_priorityroad.dae",
    "bench": OA + "east_coast_usa_assets/levels/east_coast_usa/art/shapes/clutter/clutter_city_bench_wood.dae",
    "woodfence": OA + "east_coast_usa_assets/levels/east_coast_usa/art/shapes/buildings/eca_bld_wood_fence_a.DAE",
    "gate": OA + "hirochi_raceway_assets/levels/hirochi_raceway/art/shapes/objects/farm_gate_metal1.dae",
    "stonewall": OA + "italy_assets/levels/italy/art/shapes/buildings/italy_wall_stone.dae",
    "brick": "/assets/meshes/architecture/modular/architectural_elements/walls/brick/C_B_EM_01_300x300.dae",
    "concwall": "/assets/meshes/architecture/modular/architectural_elements/fences/stone/s_conc_wall_001_400x400.dae",
    "orange_building": "/assets/meshes/architecture/buildings/industrial/ind_bld_03.dae",  # no material mapping: 'NO MATERIAL'
}
MARK_MATERIALS = {"m_line_white": "solid", "m_line_white_discontinue": "dash",
                  "italy_road_markings_zebra": "zebra", "road_chevrons": "zebra"}
ASPHALT = "road_asphalt_2lane"
MISSING_MATERIAL = "evalplant_missing_tex"


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


# ------------------------------------------------------------------------------- Lua

LUA_HEADER = """
local A = jsonDecode([==[%s]==])
package.loaded.evalPlant = package.loaded.evalPlant or {objs = {}, hidden = {}, mats = {}, terr = {}, nodes = {}, widths = {}}
local E = package.loaded.evalPlant
local function dist(ax, ay, bx, by) return math.sqrt((ax - bx) ^ 2 + (ay - by) ^ 2) end
local function at(s, d)   -- s metres ahead of the car along its lane, d metres to the left of the lane
  local L = A.lane
  local i = 1
  while i < #L - 1 and L[i + 1][1] <= s do i = i + 1 end
  local a, b = L[i], L[i + 1]
  local f = math.max(0, math.min(1, (s - a[1]) / (b[1] - a[1])))
  local x, y = a[2] + (b[2] - a[2]) * f, a[3] + (b[3] - a[3]) * f
  local hx, hy = a[4] + (b[4] - a[4]) * f, a[5] + (b[5] - a[5]) * f
  local n = math.sqrt(hx * hx + hy * hy)
  hx, hy = hx / n, hy / n
  return x - hy * d, y + hx * d, hx, hy
end
local function ground(x, y)
  local hit = castRay(vec3(x, y, A.z + 8), vec3(x, y, A.z - 15), true, true)
  return hit and hit.pt.z or A.z
end
local function mk(shape, x, y, z, yaw, sx, sy, sz, tag)
  local o = createObject('TSStatic')
  o:setField('shapeName', 0, shape)
  o:setField('collisionType', 0, 'None')
  o:setField('decalType', 0, 'None')
  local name = 'evalplant_' .. tag .. '_' .. (#E.objs + 1)
  o:registerObject(name)
  local q = quatFromEuler(0, 0, math.rad(yaw))
  local m = QuatF(q.x, q.y, q.z, q.w):getMatrix()
  m:setPosition(vec3(x, y, z))
  o:setTransform(m)
  o:setScale(vec3(sx, sy or sx, sz or sx))
  E.objs[#E.objs + 1] = name
  return o
end
local function setMat(name, mat)
  local o = scenetree.findObject(name)
  if not o then return false end
  E.mats[#E.mats + 1] = {name = name, mat = tostring(o:getField('material', 0))}
  o:setField('material', 0, mat)
  o:postApply()
  return true
end
local function missingMaterial()
  if not scenetree.findObject(A.missing_mat) then
    local m = createObject('Material')
    m:setField('name', 0, A.missing_mat)
    m:setField('diffuseMap', 0, 0, '/levels/belfast_slice/art/evalplant_does_not_exist.color.png')
    m:registerObject(A.missing_mat)
  end
end
local function shiftNode(name, idx, dx, dy, dz)
  local o = scenetree.findObject(name)
  if not o then return false end
  local p = o:getNodePosition(idx)
  E.nodes[#E.nodes + 1] = {name = name, idx = idx, x = p.x, y = p.y, z = p.z}
  o:setNodePosition(idx, vec3(p.x + dx, p.y + dy, p.z + dz))
  return true
end
local function scaleWidth(name, f, cap)
  local o = scenetree.findObject(name)
  if not o then return false end
  for k = 0, o:getNodeCount() - 1 do
    local w = o:getNodeWidth(k)
    E.widths[#E.widths + 1] = {name = name, idx = k, w = w}
    o:setNodeWidth(k, cap and math.min(w * f, cap) or w * f)
  end
  return true
end
local function hide(names)
  local n = 0
  for _, nm in ipairs(names) do
    local o = scenetree.findObject(nm)
    if o then o:setHidden(true); E.hidden[#E.hidden + 1] = nm; n = n + 1 end
  end
  return n
end
"""

LUA_REVERT = """
local E = package.loaded.evalPlant
if not E then return 'nothing to revert' end
local n = 0
for _, nm in ipairs(E.objs) do local o = scenetree.findObject(nm) if o then o:delete() n = n + 1 end end
for i = #E.nodes, 1, -1 do local r = E.nodes[i] local o = scenetree.findObject(r.name) if o then o:setNodePosition(r.idx, vec3(r.x, r.y, r.z)) end end
for i = #E.widths, 1, -1 do local r = E.widths[i] local o = scenetree.findObject(r.name) if o then o:setNodeWidth(r.idx, r.w) end end
for _, nm in ipairs(E.hidden) do local o = scenetree.findObject(nm) if o then o:setHidden(false) end end
for i = #E.mats, 1, -1 do local r = E.mats[i] local o = scenetree.findObject(r.name) if o then o:setField('material', 0, r.mat) o:postApply() end end
if #E.terr > 0 then
  local tb = scenetree.findObject(scenetree.findClassObjects('TerrainBlock')[1])
  local x0, y0, x1, y1 = 1e9, 1e9, -1e9, -1e9
  for _, c in ipairs(E.terr) do
    tb:setHeightGrid(c[1], c[2], c[3])
    x0, y0, x1, y1 = math.min(x0, c[1]), math.min(y0, c[2]), math.max(x1, c[1]), math.max(y1, c[2])
  end
  tb:updateGrid(vec3(x0, y0, 0), vec3(x1, y1, 0))
end
package.loaded.evalPlant = nil
return string.format('reverted: %d objects deleted, %d nodes, %d widths, %d hidden, %d materials, %d terrain cells', n, #E.nodes, #E.widths, #E.hidden, #E.mats, #E.terr)
"""

LUA_ITEMS = """
local made = {}
for _, it in ipairs(A.items) do
  local x, y, hx, hy = at(it.s, it.d)
  local yaw = math.deg(math.atan2(hy, hx)) + (it.yaw or 0)
  mk(A.shapes[it.shape], x, y, ground(x, y) + (it.dz or 0), yaw, it.sx or 1, it.sy, it.sz, it.shape)
  made[#made + 1] = string.format('%s@(%.1f,%.1f)', it.shape, x, y)
end
return table.concat(made, ' ')
"""

LUA_TERRAIN = """
local tb = scenetree.findObject(scenetree.findClassObjects('TerrainBlock')[1])
local tp = tb:getPosition()
local x0, y0, x1, y1 = 1e9, 1e9, -1e9, -1e9
local seen, n = {}, 0
local s = A.s0
while s <= A.s1 do
  local d = -A.half
  while d <= A.half do
    local wx, wy = at(s, d)
    local gx, gy = math.floor(wx - tp.x + 0.5), math.floor(wy - tp.y + 0.5)
    local k = gx * 100000 + gy
    if not seen[k] then
      seen[k] = true
      local old = tb:getHeightGrid(gx, gy)
      E.terr[#E.terr + 1] = {gx, gy, old}
      tb:setHeightGrid(gx, gy, math.max(0, old + A.dz))
      x0, y0, x1, y1 = math.min(x0, gx), math.min(y0, gy), math.max(x1, gx), math.max(y1, gy)
      n = n + 1
    end
    d = d + 0.5
  end
  s = s + 0.5
end
tb:updateGrid(vec3(x0, y0, 0), vec3(x1, y1, 0))
return 'terrain cells changed ' .. n
"""

LUA_HIDE = "return 'hidden ' .. hide(A.names)"
LUA_MATS = """
if A.new_mat == A.missing_mat then missingMaterial() end
local n = 0
for name, mat in pairs(A.mat_for) do if setMat(name, mat) then n = n + 1 end end
return 'material changed on ' .. n
"""
LUA_WIDTH = """
local n = 0
for _, nm in ipairs(A.names) do if scaleWidth(nm, A.factor, A.cap) then n = n + 1 end end
if A.mat_for then for name, mat in pairs(A.mat_for) do setMat(name, mat) end end
return 'width scaled on ' .. n .. ' roads'
"""
LUA_SHIFT = """
local n = 0
for _, it in ipairs(A.nodes) do if shiftNode(it[1], it[2], it[3], it[4], it[5]) then n = n + 1 end end
return 'nodes moved ' .. n
"""


# ------------------------------------------------------------------------------- scene, poses, lane

DUMP_ROADS = """
local out = {}
local drs = scenetree.findClassObjects('DecalRoad')
for i = %d, %d do
  local nm = drs[i]
  if nm then
    local o = scenetree.findObject(nm)
    local n = o:getNodeCount()
    local nodes = {}
    for k = 0, n - 1 do local p = o:getNodePosition(k) nodes[#nodes+1] = {math.floor(p.x*10)/10, math.floor(p.y*10)/10, math.floor(p.z*10)/10} end
    out[#out+1] = {nm, tostring(o:getField('material', 0)), nodes}
  end
end
return jsonEncode(out)
"""


def dump_scene(client, path: Path) -> dict:
    roads, statics = [], []
    for a in range(1, 4600, 500):
        roads += json.loads(client.run_lua(DUMP_ROADS % (a, a + 499), timeout=60))
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"roads": roads, "taken": dt.datetime.now().isoformat(timespec="seconds")}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def load_scene(path: Path = EVAL_DATA / "scene_dump.json") -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_poses(sweep: Path) -> list[dict]:
    manifest = json.loads((sweep / "manifest.json").read_text(encoding="utf-8"))
    poses = []
    for s in manifest["shots"]:
        if s.get("ok") and s.get("pose_file"):
            poses.append(json.loads((sweep / s["pose_file"]).read_text(encoding="utf-8")))
    return poses


def lane_polyline(pose: dict, doc: dict) -> list[list[float]]:
    """[[s, x, y, hx, hy], ...]: the car's lane, s metres ahead of the car (s = 0 at the car), 1 m steps, extended
    straight past the end of the route. The lane is the route line moved left by the pose's lane offset."""
    import junction_sweep as js
    route = next(r for r in doc["routes"] if r["id"] == pose["route"])
    path = route["path"]
    cum = js.cumulative(path)
    s0 = pose["at_m"] - pose["actual_distance_m"]
    off = pose["intended"]["lane_offset_m"]
    out = []
    for t in range(0, LANE_AHEAD_M + 1):
        s = s0 + t
        sc = min(s, cum[-1])
        x, y, _z = js.point_at(path, cum, sc)
        hx, hy = js.heading_at(path, cum, sc)
        if s > cum[-1]:
            x, y = x + hx * (s - cum[-1]), y + hy * (s - cum[-1])
        out.append([float(t), round(x - hy * off, 2), round(y + hx * off, 2), round(hx, 4), round(hy, 4)])
    return out


def near_roads(scene: dict, x: float, y: float, radius: float, classes: dict[str, str]) -> dict[str, list]:
    """{class: [road names]} of DecalRoads whose material is in `classes` and that have a node within radius."""
    res: dict[str, list] = {}
    for name, mat, nodes in scene["roads"]:
        cls = classes.get(mat) or (("asph" if mat == ASPHALT else None) if "asph" in classes.values() else None)
        if cls and any(math.hypot(nx - x, ny - y) < radius for nx, ny, *_ in nodes):
            res.setdefault(cls, []).append(name)
    return res


# ------------------------------------------------------------------------------- fault specs
# one function per variant: (pose, ctx) -> dict(kind, variant, lua, args, detail, needs)

def items_spec(kind, variant, items, detail):
    return {"kind": kind, "variant": variant, "lua": LUA_ITEMS, "args": {"items": items, "shapes": SHAPES},
            "detail": detail}


def marks_of(ctx, classes=None):
    names = []
    for cls, ns in ctx["marks"].items():
        if cls != "asph" and (classes is None or cls in classes):
            names += ns
    return sorted(names)


def build_specs(rng: random.Random) -> list[dict]:
    """The ordered list of planted faults (without poses), over-planned so that after the frames whose fault cannot be
    seen are dropped at least 80 remain. 'needs' names a predicate on the pose's survey (see meets())."""
    specs: list[dict] = []

    def add(kind, variant, needs=None, **kw):
        specs.append({"kind": kind, "variant": variant, "needs": needs, "params": kw})

    for _ in range(5): add("missing-markings", "hide_all_markings", "vis_marks")
    for _ in range(3): add("missing-markings", "hide_dashed", "vis_dash")
    for _ in range(3): add("missing-markings", "hide_solid", "vis_solid")
    for _ in range(3): add("missing-markings", "hide_zebra", "vis_zebra")
    for _ in range(3): add("wrong-markings", "yellow_lines", "vis_marks")
    for _ in range(4): add("wrong-markings", "fat_lines", "vis_marks", factor=7.0)
    for _ in range(2): add("wrong-markings", "yellow_fat_lines", "vis_marks", factor=7.0)
    for _ in range(3): add("wrong-markings", "shift_markings", "vis_marks", shift_m=rng.choice([-1, 1]) * rng.uniform(3.0, 3.6))
    for _ in range(2): add("wrong-markings", "checkered", "vis_marks")
    for sh, sc in [("brick", (4, 3, 2.5)), ("brick", (3, 3, 2)), ("brick", (5, 2.5, 3)), ("concwall", (3, 3, 2)),
                   ("concwall", (4, 4, 2.5)), ("stonewall", (3, 3, 3)), ("stonewall", (4, 3, 4)), ("brick", (4, 4, 3))]:
        add("building-in-road", "block", "view", shape=sh, scale=sc, s=rng.uniform(11, 15), d=rng.uniform(-0.8, 0.8))
    for sh, s in [("lamp", 10), ("lamp", 12), ("lamp2", 11), ("sign_giveway", 10), ("sign_priority", 12), ("sign_oneway", 11),
                  ("woodfence", 11), ("woodfence", 13), ("gate", 10), ("gate", 12), ("bench", 9), ("bench", 11)]:
        add("object-in-road", "prop_in_lane", "view", shape=sh, s=s + rng.uniform(-1, 1), d=rng.uniform(-0.5, 0.5))
    for _ in range(5): add("terrain-step", "ridge", "view", dz=rng.uniform(0.7, 1.0), s0=rng.uniform(8, 10))
    for _ in range(5): add("terrain-step", "trench", "view", dz=-rng.uniform(0.9, 1.3), s0=rng.uniform(8, 10))
    for sh, dz in [("bench", 2.0), ("bench", 2.6), ("bench", 1.8), ("brick", 2.2), ("brick", 2.8), ("stonewall", 2.0), ("stonewall", 2.6),
                   ("sign_giveway", 2.2), ("sign_oneway", 2.4), ("lamp", 3.5), ("lamp", 4.0), ("concwall", 2.4)]:
        add("floating-object", "lifted", "view", shape=sh, dz=dz, s=rng.uniform(8, 11), d=rng.choice([-2.5, 3.0, 3.5, 4.0, 4.5]))
    for sh, sink in [("lamp", 3.5), ("lamp", 4.0), ("sign_giveway", 1.3), ("sign_oneway", 1.4), ("bench", 0.4), ("bench", 0.45),
                     ("brick", 1.9), ("brick", 2.0), ("stonewall", 1.7), ("stonewall", 1.9)]:
        add("buried-object", "sunk", "view", shape=sh, dz=-sink, s=rng.uniform(8, 11), d=rng.choice([-2.5, 0.0, 3.0, 3.5, 4.0]))
    for _ in range(4): add("missing-texture", "road_surface", "view")
    for _ in range(2): add("missing-texture", "markings", "vis_marks")
    for i in range(4): add("missing-texture", "orange_building", "view", s=rng.uniform(14, 22), d=rng.choice([-1, 1]) * rng.uniform(9, 13))
    for sh, d in [("sign_oneway", 3.8), ("sign_giveway", 4.2), ("sign_noentry", 4.0), ("sign_priority", 4.5)]:
        add("wrong-side-sign", "back_to_driver", "view", shape=sh, yaw=180, s=rng.uniform(8, 12), d=d)
    for sh, d in [("sign_oneway", -2.8), ("sign_giveway", -3.0), ("sign_noentry", -2.8), ("sign_oneway", -3.2)]:
        add("wrong-side-sign", "offside", "view", shape=sh, yaw=0, s=rng.uniform(9, 13), d=d)
    for _ in range(4): add("road-geometry", "gap", "view", s=rng.uniform(11, 15))
    for _ in range(3): add("road-geometry", "narrow", "view", s=rng.uniform(11, 15), factor=0.35)
    for _ in range(3): add("road-geometry", "shift_road", "view", s=rng.uniform(10, 14), lateral=rng.choice([-1, 1]) * rng.uniform(3.0, 4.0))
    return specs


def survey_pose(pose: dict, scene: dict, doc: dict) -> dict:
    """Counts used to pick poses where a fault can be seen: DecalRoads by class near the car, marking sample points
    (2 m apart) in the view cone ahead (4 to 35 m, within 9 m of the heading), and whether the lane stays in front
    of the car (bearing under 16 degrees) for 8 to 15 m ahead."""
    i = pose["intended"]
    x, y, dx, dy = i["x"], i["y"], i["dir_x"], i["dir_y"]
    cls = dict(MARK_MATERIALS)
    cls[ASPHALT] = "asph"
    sv = {k: len(v) for k, v in near_roads(scene, x, y, MARK_RADIUS_M, cls).items()}
    vis = {"dash": 0, "solid": 0, "zebra": 0}
    for _n, mat, nodes in scene["roads"]:
        c = MARK_MATERIALS.get(mat)
        if not c:
            continue
        for (ax, ay, *_a), (bx, by, *_b) in zip(nodes, nodes[1:]):
            seg = math.hypot(bx - ax, by - ay)
            steps = max(1, int(seg / 2.0))
            for k in range(steps):
                f = (k + 0.5) / steps
                px, py = ax + (bx - ax) * f - x, ay + (by - ay) * f - y
                ahead, left = px * dx + py * dy, -px * dy + py * dx
                if 4 <= ahead <= 35 and abs(left) <= 9:
                    vis[c] += 1
    for c, n in vis.items():
        sv["vis_" + c] = n
    sv["vis_marks"] = vis["dash"] + vis["solid"]
    lane = lane_polyline(pose, doc)
    bearings = []
    for s_ in (8, 10, 12, 15):
        _, lx, ly, _hx, _hy = lane[s_]
        bearings.append(abs(math.degrees(math.atan2(-(lx - x) * dy + (ly - y) * dx, (lx - x) * dx + (ly - y) * dy))))
    sv["view"] = int(max(bearings) < 16)
    return sv


def meets(need: str | None, sv: dict) -> bool:
    if need is None:
        return True
    thresholds = {"vis_marks": 8, "vis_dash": 5, "vis_solid": 5, "vis_zebra": 2, "view": 1}
    return sv.get(need, 0) >= thresholds[need]


def realise(spec: dict, pose: dict, scene: dict, doc: dict) -> dict:
    """Turn a spec + pose into the Lua to run and the ground-truth record."""
    i = pose["intended"]
    p = spec["params"]
    kind, var = spec["kind"], spec["variant"]
    lane = lane_polyline(pose, doc)
    base = {"x": i["x"], "y": i["y"], "z": i["z"], "dx": i["dir_x"], "dy": i["dir_y"], "lane": lane,
            "missing_mat": MISSING_MATERIAL}
    marks = near_roads(scene, i["x"], i["y"], MARK_RADIUS_M, MARK_MATERIALS)
    mat_of = {n: m for n, m, _ in scene["roads"]}
    allm = sorted(n for ns in marks.values() for n in ns)
    out = {"kind": kind, "variant": var}

    def finish(lua, args, detail, **extra):
        out.update({"lua": lua, "args": {**base, **args}, "detail": detail, **extra})
        return out

    if var == "hide_all_markings":
        return finish(LUA_HIDE, {"names": allm}, f"hid {len(allm)} marking DecalRoads (centre/edge lines, zebra, chevrons) within {MARK_RADIUS_M:.0f} m of the car", objects=allm)
    if var in ("hide_dashed", "hide_solid", "hide_zebra"):
        cls = {"hide_dashed": "dash", "hide_solid": "solid", "hide_zebra": "zebra"}[var]
        names = sorted(marks.get(cls, []))
        return finish(LUA_HIDE, {"names": names}, f"hid {len(names)} '{cls}' marking DecalRoads within {MARK_RADIUS_M:.0f} m of the car", objects=names)
    white = [n for n in allm if mat_of[n] in ("m_line_white", "m_line_white_discontinue")]
    if var in ("fat_lines", "yellow_fat_lines"):
        mf = {}
        if var == "yellow_fat_lines":
            mf = {n: ("m_line_yellow_double" if mat_of[n] == "m_line_white" else "m_line_yellow_discontinue") for n in white}
        txt = f"widened the nodes of {len(white)} white-line DecalRoads x{p['factor']:g} (capped at 1.4 m), smeared paint"
        if mf:
            txt += " and re-materialled them yellow"
        return finish(LUA_WIDTH, {"names": white, "factor": p["factor"], "cap": 1.4, "mat_for": mf or None}, txt, objects=white, offset_m=p["factor"])
    if var == "yellow_lines":
        m = {n: ("m_line_yellow_double" if mat_of[n] == "m_line_white" else "m_line_yellow_discontinue")
             for n in allm if mat_of[n] in ("m_line_white", "m_line_white_discontinue")}
        return finish(LUA_MATS, {"mat_for": m, "new_mat": "yellow"}, f"re-materialled {len(m)} white lines to yellow (m_line_yellow_double / m_line_yellow_discontinue)", objects=sorted(m))
    if var == "swap_dash_solid":
        m = {n: ("m_line_white_discontinue" if mat_of[n] == "m_line_white" else "m_line_white")
             for n in allm if mat_of[n] in ("m_line_white", "m_line_white_discontinue")}
        return finish(LUA_MATS, {"mat_for": m, "new_mat": "swap"}, f"swapped solid and dashed white materials on {len(m)} lines", objects=sorted(m))
    if var == "checkered":
        m = {n: "checkered_line" for n in allm if mat_of[n] in ("m_line_white", "m_line_white_discontinue")}
        return finish(LUA_MATS, {"mat_for": m, "new_mat": "checkered"}, f"re-materialled {len(m)} white lines to checkered_line", objects=sorted(m))
    if var == "shift_markings":
        names = [n for n in allm if mat_of[n] in ("m_line_white", "m_line_white_discontinue")]
        sh = p["shift_m"]
        nodes = []
        for n in names:
            for k, node in enumerate(next(nn for nm, _, nn in scene["roads"] if nm == n)):
                nodes.append([n, k, -i["dir_y"] * sh, i["dir_x"] * sh, 0.0])
        return finish(LUA_SHIFT, {"nodes": nodes}, f"moved every node of {len(names)} white-line DecalRoads {sh:+.1f} m sideways (positive = left of the car)", objects=names, offset_m=round(sh, 2))
    if kind == "building-in-road":
        sx, sy, sz = p["scale"]
        it = [{"shape": p["shape"], "s": p["s"], "d": p["d"], "yaw": 90, "sx": sx, "sy": sy, "sz": sz}]
        return finish(LUA_ITEMS, {"items": it, "shapes": SHAPES}, f"{p['shape']} scaled {sx}x{sy}x{sz} across the lane {p['s']:.1f} m ahead, {p['d']:+.1f} m from the lane centre", objects=[p["shape"]], offset_m=round(p["s"], 1))
    if kind == "object-in-road":
        yaw = 90 if p["shape"] in ("woodfence", "gate") else 0
        it = [{"shape": p["shape"], "s": p["s"], "d": p["d"], "yaw": yaw}]
        return finish(LUA_ITEMS, {"items": it, "shapes": SHAPES}, f"{p['shape']} standing in the car's lane {p['s']:.1f} m ahead ({p['d']:+.1f} m from the lane centre)", objects=[p["shape"]], offset_m=round(p["s"], 1))
    if kind == "terrain-step":
        s0 = p["s0"]
        return finish(LUA_TERRAIN, {"s0": s0, "s1": s0 + 3.0, "half": 7.0, "dz": p["dz"]},
                      f"terrain cells in a 3 m x 14 m strip across the road {s0:.1f} m ahead changed by {p['dz']:+.2f} m ({var})", objects=["TerrainBlock"], offset_m=round(p["dz"], 2))
    if kind == "floating-object":
        it = [{"shape": p["shape"], "s": p["s"], "d": p["d"], "dz": p["dz"]}]
        return finish(LUA_ITEMS, {"items": it, "shapes": SHAPES}, f"{p['shape']} {p['dz']:.1f} m above the ground, {p['s']:.1f} m ahead, {p['d']:+.1f} m from the lane", objects=[p["shape"]], offset_m=round(p["dz"], 2))
    if kind == "buried-object":
        it = [{"shape": p["shape"], "s": p["s"], "d": p["d"], "dz": p["dz"]}]
        if p.get("scale"):
            it[0].update(sx=p["scale"], sy=p["scale"], sz=p["scale"])
        return finish(LUA_ITEMS, {"items": it, "shapes": SHAPES}, f"{p['shape']}{' scaled x%g' % p['scale'] if p.get('scale') else ''} sunk {-p['dz']:.1f} m into the ground, {p['s']:.1f} m ahead, {p['d']:+.1f} m from the lane", objects=[p["shape"]], offset_m=round(p["dz"], 2))
    if var == "road_surface":
        a = [n for n in near_roads(scene, i["x"], i["y"], 40.0, {ASPHALT: "asph"}).get("asph", [])]
        return finish(LUA_MATS, {"mat_for": {n: MISSING_MATERIAL for n in a}, "new_mat": MISSING_MATERIAL},
                      f"{len(a)} asphalt DecalRoads within 40 m given a material whose texture file does not exist (the game draws its orange 'NO MATERIAL' surface)", objects=sorted(a))
    if var == "markings":
        m = {n: MISSING_MATERIAL for n in allm}
        return finish(LUA_MATS, {"mat_for": m, "new_mat": MISSING_MATERIAL}, f"{len(m)} marking DecalRoads given a material with a missing texture file", objects=sorted(m))
    if var == "orange_building":
        it = [{"shape": "orange_building", "s": p["s"], "d": p["d"]}]
        return finish(LUA_ITEMS, {"items": it, "shapes": SHAPES}, f"industrial building shape without material mapping (orange 'NO MATERIAL' surface) {p['s']:.1f} m ahead, {p['d']:+.1f} m beside the road", objects=["ind_bld_03"], offset_m=round(p["s"], 1))
    if kind == "wrong-side-sign":
        it = [{"shape": p["shape"], "s": p["s"], "d": p["d"], "yaw": p["yaw"]}]
        what = "on the offside (right) side of the road facing the driver" if p["yaw"] == 0 else "on the nearside (left) verge with its back to the driver"
        return finish(LUA_ITEMS, {"items": it, "shapes": SHAPES}, f"{p['shape']} {what}, {p['s']:.1f} m ahead", objects=[p["shape"]], offset_m=round(p["s"], 1))
    if var == "gap":
        cx, cy, _hx, _hy = lane_at(lane, p["s"])
        a = sorted(near_roads(scene, cx, cy, 16.0, {ASPHALT: "asph"}).get("asph", []))
        return finish(LUA_HIDE, {"names": a}, f"hid {len(a)} asphalt DecalRoads with a node within 16 m of the lane point {p['s']:.1f} m ahead (a gap in the road surface)", objects=a, offset_m=round(p["s"], 1))
    if var == "narrow":
        cx, cy, _hx, _hy = lane_at(lane, p["s"])
        a = sorted(near_roads(scene, cx, cy, 16.0, {ASPHALT: "asph"}).get("asph", []))
        return finish(LUA_WIDTH, {"names": a, "factor": p["factor"], "cap": None, "mat_for": None},
                      f"width of {len(a)} asphalt DecalRoads near the lane point {p['s']:.1f} m ahead scaled x{p['factor']} (road mesh pinched)", objects=a, offset_m=p["factor"])
    if var == "shift_road":
        cx, cy, _hx, _hy = lane_at(lane, p["s"])
        near = sorted(((min(math.hypot(nx - cx, ny - cy) for nx, ny, *_r in nodes), n, nodes) for n, m, nodes in scene["roads"] if m == ASPHALT))[:2]
        lat = p["lateral"]
        nodes_out = []
        for _d, n, nodes in near:
            for k in range(len(nodes)):
                nodes_out.append([n, k, -i["dir_y"] * lat, i["dir_x"] * lat, 0.0])
        return finish(LUA_SHIFT, {"nodes": nodes_out}, f"every node of the {len(near)} asphalt DecalRoads nearest to the lane point {p['s']:.1f} m ahead moved {lat:+.1f} m sideways (misaligned road piece)",
                      objects=[n for _d, n, _nn in near], offset_m=round(lat, 2))
    raise ValueError(f"unknown fault {kind}/{var}")


def lane_at(lane, s):
    k = min(int(s), len(lane) - 2)
    a, b = lane[k], lane[k + 1]
    f = s - a[0]
    return a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3], a[4]


# ------------------------------------------------------------------------------- plan

def make_plan(sweep: Path, scene: dict, doc: dict) -> dict:
    rng = random.Random(SEED)
    poses = load_poses(sweep)
    poses.sort(key=lambda p: p["id"])
    surveys = {p["id"]: survey_pose(p, scene, doc) for p in poses}
    specs = build_specs(rng)
    order = poses[:]
    rng.shuffle(order)
    uses: dict[str, int] = {}
    plan_of: dict[str, list] = {}
    plan = []
    # hardest constraints first so they find a pose, then restore the spec order for numbering
    ranked = sorted(range(len(specs)), key=lambda k: (specs[k]["needs"] is None, k))
    assigned: dict[int, dict] = {}
    for k in ranked:
        sp = specs[k]
        ok = [p for p in order if uses.get(p["id"], 0) < 2 and meets(sp["needs"], surveys[p["id"]])
              and not any(q["kind"] == sp["kind"] for q in plan_of.get(p["id"], []))]
        pick = min(ok, key=lambda p: uses.get(p["id"], 0), default=None)
        if pick is None:
            raise RuntimeError(f"no free pose for {sp['kind']}/{sp['variant']}")
        uses[pick["id"]] = uses.get(pick["id"], 0) + 1
        plan_of.setdefault(pick["id"], []).append(sp)
        assigned[k] = pick
    for n, k in enumerate(range(len(specs)), 1):
        sp, pose = specs[k], assigned[k]
        plan.append({"frame": f"P{n:03d}", "source_frame": pose["id"], "kind": sp["kind"], "variant": sp["variant"],
                     "params": sp["params"], "survey": surveys[pose["id"]]})
    return {"seed": SEED, "sweep": str(sweep), "frames": plan, "counts": count_by(plan, "kind")}


def count_by(rows, key):
    out: dict[str, int] = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return dict(sorted(out.items()))


def cmd_plan(a) -> int:
    scene = load_scene()
    if scene is None:
        print("no scene dump yet: run `run` once (it dumps the scene through the game), or pass --dump", file=sys.stderr)
        return 1
    doc = json.loads(ROUTES.read_text(encoding="utf-8"))
    plan = make_plan(Path(a.sweep), scene, doc)
    EVAL_REPO.mkdir(parents=True, exist_ok=True)
    (EVAL_REPO / "plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    print(json.dumps({"frames": len(plan["frames"]), "by_kind": plan["counts"]}, indent=1))
    return 0


# ------------------------------------------------------------------------------- run (game)

def in_blocked_hours() -> bool:
    return 9 <= dt.datetime.now().hour < 18


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_lua_checked(link, code: str) -> str:
    return link.lua(code, timeout=60)


def plant_and_shoot(link, js, frame: dict, pose: dict, scene: dict, doc: dict, outdir: Path, tod: dict) -> dict:
    spec = {"kind": frame["kind"], "variant": frame["variant"], "params": frame["params"]}
    real = realise(spec, pose, scene, doc)
    code = LUA_HEADER % json.dumps(real["args"]) + real["lua"]
    applied = run_lua_checked(link, code)
    time.sleep(0.4)
    shot = dict(pose)
    shot["id"] = frame["frame"]
    shot["route"] = "planted"
    shot["junction_id"] = pose["junction_id"]
    try:
        rec = js.take_shot(link, shot, outdir, tod, None)
    finally:
        reverted = run_lua_checked(link, LUA_REVERT)
    return {"frame": frame["frame"], "source_frame": frame["source_frame"], "kind": real["kind"], "variant": real["variant"],
            "detail": real["detail"], "objects": real.get("objects"), "offset_m": real.get("offset_m"),
            "applied": applied, "reverted": reverted, "ok": rec["ok"], "problems": rec["problems"],
            "image": f"planted/{frame['frame']}.jpg", "image_sha256": rec["image_sha256"],
            "camera": rec["camera"], "car": rec["car"], "captured": rec["captured"]}


def cmd_run(a) -> int:
    import beamng_mcp  # noqa: F401
    import drive_suite as ds
    import gamectl
    import junction_sweep as js

    only = {s for s in a.only.split(",") if s} if a.only else None
    plan_file = EVAL_REPO / "plan.json"
    scene = load_scene()
    doc = json.loads(ROUTES.read_text(encoding="utf-8"))
    cap_file = EVAL_DATA / "captures.json"
    caps = json.loads(cap_file.read_text(encoding="utf-8")) if cap_file.is_file() and not a.restart else {"frames": {}}
    zip_before = sha256(SLICE_ZIP)
    t0 = time.monotonic()
    deadline = t0 + a.timeout
    rc = 0
    started = False
    summary: dict = {"zip_sha256_before": zip_before}
    hard = threading.Timer(a.timeout + 120, lambda: None)
    try:
        gamectl.log("eval_plant: starting the QA game")
        gamectl.cmd_start()
        started = True
        gamectl.cmd_load_level(js.LEVEL, timeout=900)
        _state, client = gamectl.require_our_game()
        link = ds.Link(client, deadline)
        if scene is None or a.redump:
            scene = dump_scene(client, EVAL_DATA / "scene_dump.json")
            log(f"scene dumped: {len(scene['roads'])} DecalRoads")
        if not plan_file.is_file() or a.replan:
            plan = make_plan(Path(a.sweep), scene, doc)
            EVAL_REPO.mkdir(parents=True, exist_ok=True)
            plan_file.write_text(json.dumps(plan, indent=1), encoding="utf-8")
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        poses = {p["id"]: p for p in load_poses(Path(plan["sweep"]))}
        tod = js.fix_time_of_day(link)
        ds.spawn_fresh(link)
        log(f"driver camera: {js.ensure_driver_camera(link)}")
        EVAL_DATA.mkdir(parents=True, exist_ok=True)
        outdir = EVAL_DATA
        # baseline: an untouched frame must look like the sweep's
        for n, fr in enumerate(plan["frames"], 1):
            link.check()
            if in_blocked_hours():
                log("it is 09:00 or later: stopping")
                rc = js.EXIT_INCOMPLETE
                break
            if only and fr["frame"] not in only:
                continue
            if fr["frame"] in caps["frames"] and caps["frames"][fr["frame"]].get("ok") and not only:
                continue
            try:
                rec = plant_and_shoot(link, js, fr, poses[fr["source_frame"]], scene, doc, outdir, tod)
            except (RuntimeError, KeyError, ValueError, beamng_mcp.McpError, beamng_mcp.ToolError) as e:
                if isinstance(e, ds.GameLost):
                    raise
                log(f"  {fr['frame']}: error: {e}")
                rec = {"frame": fr["frame"], "ok": False, "problems": [f"error: {str(e)[:300]}"]}
                try:
                    link.lua(LUA_REVERT)
                except Exception:  # noqa: BLE001
                    pass
            caps["frames"][fr["frame"]] = rec
            cap_file.write_text(json.dumps(caps, indent=1), encoding="utf-8")
            log(f"[{n}/{len(plan['frames'])}] {fr['frame']} {fr['kind']}/{fr['variant']} on {fr['source_frame']}: "
                f"{'ok' if rec.get('ok') else 'FAIL'} {rec.get('applied', '')[:70]}")
        # revert check: re-take an untouched pose and compare it with the sweep image
        chk_id = plan["frames"][0]["source_frame"]
        chk_pose = dict(poses[chk_id])
        chk_pose["id"] = "REVERTCHECK"
        chk_pose["route"] = "planted"
        rec = js.take_shot(link, chk_pose, outdir, tod, None)
        summary["revert_check"] = revert_check(outdir / "planted" / "REVERTCHECK.jpg", Path(plan["sweep"]) / poses[chk_id]["image"], chk_id)
        log(f"revert check: {summary['revert_check']}")
    except ds.SuiteTimeout:
        rc = js.EXIT_TIMEOUT
        log("overall timeout")
    except gamectl.GamectlError as e:
        rc = e.code
        log(f"gamectl: {e.message}")
    except Exception as e:  # noqa: BLE001
        rc = 1
        log(f"error: {type(e).__name__}: {e}")
    finally:
        hard.cancel()
        stopped = {}
        if started or gamectl.read_state() is not None:
            try:
                stopped = gamectl.cmd_stop()
            except Exception as e:  # noqa: BLE001
                stopped = {"error": str(e)}
                rc = rc or 1
        end = gamectl.end_state()
        summary["game_stopped"] = gamectl.end_state_clean(end)
        if started:
            con = gamectl.log_errors(ignore=js.KNOWN_CONSOLE)
            summary["console_errors"] = con["errors"]
            summary["console_warnings"] = con["warnings"]
        summary["zip_sha256_after"] = sha256(SLICE_ZIP)
        summary["zip_unchanged"] = summary["zip_sha256_after"] == zip_before
        (EVAL_DATA / "run_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: (v if k not in ("console_errors", "console_warnings") else len(v)) for k, v in summary.items()}, indent=1))
    return rc


def revert_check(new: Path, old: Path, src: str) -> dict:
    from PIL import Image, ImageChops, ImageStat
    a = Image.open(new).convert("L")
    b = Image.open(old).convert("L")
    # the lower part is the car interior, the same in both: compare the whole frame
    diff = ImageChops.difference(a, b)
    mean = ImageStat.Stat(diff).mean[0]
    return {"pose": src, "mean_abs_diff_0_255": round(mean, 2)}


# ------------------------------------------------------------------------------- assemble

def spot_reasons(r: dict) -> list[str]:
    """Why a frame is on the spot-check list (empty when the planted truth and the visual label agree and nothing is unsure)."""
    v = r["visual_label"] or {}
    why = []
    if r["planted"] and v.get("defect_type") != r["planted_fault"]["type"]:
        why.append(f"planted {r['planted_fault']['type']} but visual label {v.get('defect_type')}")
    if r["planted"] and v.get("planted_visible") not in (None, "yes"):
        why.append(f"planted fault visible: {v.get('planted_visible')}")
    if not r["planted"] and v.get("defect_type") != "none":
        why.append(f"clean frame (nothing planted) labelled {v.get('defect_type')}")
    if v.get("unsure"):
        why.append("unsure")
    if v.get("other_defects"):
        why.append("other defect also visible: " + v["other_defects"])
    return why


def cmd_assemble(a) -> int:
    plan = json.loads((EVAL_REPO / "plan.json").read_text(encoding="utf-8"))
    caps = json.loads((EVAL_DATA / "captures.json").read_text(encoding="utf-8"))["frames"]
    vis = json.loads((EVAL_REPO / "visual_labels.json").read_text(encoding="utf-8"))
    sweep = Path(plan["sweep"])
    clean_sel = json.loads((EVAL_REPO / "clean_selection.json").read_text(encoding="utf-8"))
    dropped = vis.get("dropped", {})
    rows = []
    for fr in plan["frames"]:
        cap = caps.get(fr["frame"], {})
        if fr["frame"] in dropped:
            continue
        src_pose = json.loads((sweep / f"{fr['source_frame'].rsplit('-J', 1)[0]}/{fr['source_frame']}.json").read_text(encoding="utf-8"))
        rows.append({"frame": fr["frame"], "image": f"planted/{fr['frame']}.jpg", "image_sha256": cap.get("image_sha256"),
                     "source_frame": fr["source_frame"], "planted": True,
                     "diff_vs_source": diff_vs_source(EVAL_DATA / "planted" / f"{fr['frame']}.jpg", sweep / src_pose["image"]),
                     "planted_fault": {"type": fr["kind"], "variant": fr["variant"], "detail": cap.get("detail"),
                                       "objects": cap.get("objects"), "offset_m": cap.get("offset_m")},
                     "visual_label": vis["labels"].get(fr["frame"])})
    for c in clean_sel["frames"]:
        pose = json.loads((sweep / c["pose_file"]).read_text(encoding="utf-8"))
        rows.append({"frame": c["frame"], "image": f"clean/{c['frame']}.jpg", "image_sha256": pose.get("image_sha256"),
                     "source_frame": c["source_frame"], "planted": False, "planted_fault": None,
                     "visual_label": vis["labels"].get(c["frame"])})
    missing = [r["frame"] for r in rows if not r["visual_label"]]
    if missing:
        print(f"no visual label for: {missing}", file=sys.stderr)
        return 1
    for r in rows:
        r["spot_check"] = spot_reasons(r)
    out = EVAL_REPO / "labels.jsonl"
    with out.open("w", encoding="utf-8", newline=NL) as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + NL)
    planted = [r for r in rows if r["planted"]]
    clean = [r for r in rows if not r["planted"]]
    stats = {
        "frames": len(rows), "planted": len(planted), "clean": len(clean),
        "planted_by_type": count_rows(planted, lambda r: r["planted_fault"]["type"]),
        "visual_label_by_type_planted": count_rows(planted, lambda r: r["visual_label"]["defect_type"]),
        "visual_label_by_type_clean": count_rows(clean, lambda r: r["visual_label"]["defect_type"]),
        "planted_visual_agrees": sum(1 for r in planted if r["visual_label"]["defect_type"] == r["planted_fault"]["type"]),
        "spot_check": sum(1 for r in rows if r["spot_check"]),
        "dropped_planted": len(dropped),
    }
    (EVAL_REPO / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats, indent=1))
    print(f"wrote {out}: {len(rows)} frames")
    return 0


def diff_vs_source(planted_img: Path, source_img: Path) -> dict:
    """How much of the windscreen changed against the untouched sweep frame of the same pose: mean absolute grey
    difference and the share of pixels that differ by more than 30 grey levels (rows 70-500, the glass area)."""
    from PIL import Image, ImageChops
    a = Image.open(planted_img).convert("L").crop((0, 70, 1280, 500))
    b = Image.open(source_img).convert("L").crop((0, 70, 1280, 500))
    d = ImageChops.difference(a, b)
    hist = d.histogram()
    n = sum(hist)
    return {"mean_abs_diff": round(sum(i * c for i, c in enumerate(hist)) / n, 2),
            "share_over_30": round(sum(hist[31:]) / n, 4)}


def count_rows(rows, key) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[key(r)] = out.get(key(r), 0) + 1
    return dict(sorted(out.items()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run", "assemble"):
        p = sub.add_parser(name)
        p.add_argument("--sweep", default=str(DEFAULT_SWEEP))
        if name == "run":
            p.add_argument("--only", help="comma separated frame ids to (re)take")
            p.add_argument("--restart", action="store_true", help="forget the capture index")
            p.add_argument("--redump", action="store_true")
            p.add_argument("--replan", action="store_true")
            p.add_argument("--timeout", type=float, default=3000.0)
    a = ap.parse_args(argv)
    if a.cmd == "run" and in_blocked_hours():
        print("refusing: 09:00-18:00", file=sys.stderr)
        return 3
    return {"plan": cmd_plan, "run": cmd_run, "assemble": cmd_assemble}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
