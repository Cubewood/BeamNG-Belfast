#!/usr/bin/env python3
"""Traffic signal data of an exported level: read it, check it, fix what the game would get wrong (T-407).

    uv run --no-project --python 3.12 python pipeline/signals_fix.py check ZIP
    uv run --no-project --python 3.12 python pipeline/signals_fix.py fix   ZIP --out NEW.zip   # a copy, ZIP untouched

The level's `levels/<level>/signals.json` is MapNG's (`services/beamngSignals.js`). The game reads it with
`core_trafficSignals.loadSignals` (`lua/ge/extensions/core/trafficSignals.lua:1476`). Facts from that file:

  * A light with no sequence keeps its controller's `defaultIndex` for ever (`SignalInstance:setActive`,
    :556-585: no sequence, so `priorityStateIndex = defaultIndex`). For `lightsBasicEU` the default is 4,
    red. MapNG gives a cluster a sequence only when it has two axes or a protected turn
    (`beamngSignals.js:875`, `requiresSequence = axisCount > 1 || ...`), so a cluster with one axis (a
    mid-block crossing or a one-way approach) is a light that is red for ever: the AI stops at it and never
    goes, and a learner would meet a light that never changes. `fix` gives each such controller a one-phase
    sequence (`phases = [{controllerIds = [id], totalDuration = <the controller's own length>}]`), the same
    structure as the two-phase sequences in the stock `east_coast_usa/signals.json`.
  * UK order. The game type `lightsBasicEU` (`settings/trafficSignals.json:151`) lists
    redYellow, green, yellow, red: UK order (red, red and amber, green, amber, red). `check` reports any
    controller whose state list is not that cycle.

No game, no network. Only the signals file of a zip is read or rewritten.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import zipfile
from pathlib import Path

# UK order of a signal head's states, as the game's own `lightsBasicEU` lists them (cyclic).
UK_ORDER = ["redYellowTrafficLight", "greenTrafficLight", "yellowTrafficLight", "redTrafficLight"]
LIGHT_TYPES_WITH_CYCLE = ("lightsBasic", "lightsBasicEU", "lightsBasicProtectedGreen")


def signals_name(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.count("/") == 2 and n.endswith("/signals.json") and n.startswith("levels/")]
    if len(names) != 1:
        raise ValueError(f"{zip_path}: expected one levels/<name>/signals.json, found {names}")
    return names[0]


def controller_length(ctrl: dict) -> float:
    return float(sum(float(s.get("duration", 0)) for s in ctrl.get("states", [])))


def is_uk_cycle(states: list[str]) -> bool:
    """True when `states` is UK_ORDER rotated (the cycle, wherever it starts)."""
    n = len(UK_ORDER)
    if len(states) != n:
        return False
    return any(states == UK_ORDER[k:] + UK_ORDER[:k] for k in range(n))


def analyse(data: dict) -> dict:
    """Findings about a signals.json dict (no changes made)."""
    ctrls = {c["id"]: c for c in data.get("controllers", [])}
    seqs = {s["id"]: s for s in data.get("sequences", [])}
    never = []          # instances that would stay red for ever
    not_uk = []         # controllers whose cycle is not the UK order
    short_phase = []    # phases shorter than their controller (the next phase would cut a cycle)
    for c in ctrls.values():
        if c.get("isSimple"):
            continue
        order = [s["state"] for s in c.get("states", [])]
        if c.get("type") in LIGHT_TYPES_WITH_CYCLE and not is_uk_cycle(order):
            not_uk.append({"controller": c["id"], "name": c.get("name"), "states": order})
    for i in data.get("instances", []):
        c = ctrls.get(i.get("controllerId"))
        if c is None or c.get("isSimple") or c.get("type") not in LIGHT_TYPES_WITH_CYCLE:
            continue
        if not i.get("sequenceId") or i["sequenceId"] not in seqs:
            never.append({"instance": i["id"], "name": i.get("name"), "controller": c["id"],
                          "pos": [round(v, 1) for v in i.get("pos", [])[:2]]})
    for s in seqs.values():
        for k, ph in enumerate(s.get("phases", [])):
            need = max((controller_length(ctrls[cid]) for cid in ph.get("controllerIds", []) if cid in ctrls), default=0)
            if float(ph.get("totalDuration", 0)) + 1e-6 < need:
                short_phase.append({"sequence": s["id"], "phase": k + 1, "totalDuration": ph.get("totalDuration"), "needs": need})
    return {"instances": len(data.get("instances", [])), "controllers": len(ctrls), "sequences": len(seqs),
            "never_change": never, "not_uk_order": not_uk, "short_phase": short_phase}


def fix(data: dict) -> tuple[dict, dict]:
    """(new data, report): a one-phase sequence for every light that has none."""
    out = copy.deepcopy(data)
    found = analyse(out)["never_change"]
    ids = [e["id"] for k in ("instances", "controllers", "sequences") for e in out.get(k, [])]
    next_id = max(ids, default=0) + 1
    ctrls = {c["id"]: c for c in out.get("controllers", [])}
    made: dict[int, int] = {}
    out.setdefault("sequences", [])
    for hit in found:
        cid = hit["controller"]
        if cid not in made:
            made[cid] = next_id
            next_id += 1
            out["sequences"].append({
                "id": made[cid], "name": f"trafficLights_single_{ctrls[cid].get('name', cid)}",
                "startTime": 0, "startDisabled": False, "ignoreTimer": False,
                "phases": [{"controllerIds": [cid], "totalDuration": controller_length(ctrls[cid])}],
            })
    by_id = {i["id"]: i for i in out["instances"]}
    for hit in found:
        by_id[hit["instance"]]["sequenceId"] = made[hit["controller"]]
    return out, {"lights_fixed": len(found), "sequences_added": len(made), "controllers": sorted(made)}


def fix_zip(src: Path, dst: Path | None = None) -> dict:
    """Rewrite signals.json inside a level zip (in place when `dst` is None, else into a copy)."""
    name = signals_name(src)
    with zipfile.ZipFile(src) as z:
        data = json.loads(z.read(name))
    new, report = fix(data)
    report["before"] = {k: len(v) for k, v in analyse(data).items() if isinstance(v, list)}
    report["after"] = {k: len(v) for k, v in analyse(new).items() if isinstance(v, list)}
    if report["lights_fixed"] == 0 and dst is None:
        return report
    target = dst or src
    tmp = target.with_name(target.name + ".tmp")
    payload = json.dumps(new, indent=2).encode("utf-8")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
        for item in zin.infolist():
            zout.writestr(item, payload if item.filename == name else zin.read(item.filename))
    os.replace(tmp, target)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["check", "fix"])
    ap.add_argument("zip")
    ap.add_argument("--out", help="fix: write the changed zip here (default: in place)")
    a = ap.parse_args(argv)
    src = Path(a.zip)
    if a.cmd == "check":
        with zipfile.ZipFile(src) as z:
            res = analyse(json.loads(z.read(signals_name(src))))
        print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()}))
        for k in ("never_change", "not_uk_order", "short_phase"):
            for e in res[k]:
                print(f"{k}: {json.dumps(e)}")
        return 1 if (res["never_change"] or res["not_uk_order"] or res["short_phase"]) else 0
    if a.out and Path(r"H:\BelfastData").resolve() not in Path(a.out).resolve().parents:
        print("error: --out must be under H:/BelfastData", file=sys.stderr)
        return 2
    print(json.dumps(fix_zip(src, Path(a.out) if a.out else None)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
