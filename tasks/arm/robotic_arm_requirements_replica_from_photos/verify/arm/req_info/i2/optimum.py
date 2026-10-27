"""Live optimal BOM (the C5 reference cost J*, public code): enumeration over bus family x per-joint admissible SKUs x boards x PSU of
the GIVEN catalogue, with the regressor making every candidate's joint traces a few multiply-adds; per-joint feasibility
memoised per distal-mass tuple; branch and bound on price. The capability is the one of C4 (lotmodel.unit_capability:
the lot's weakest expected unit, an uninspected vendor at the B-10 cap), so J* is the optimum of the grader's own feasible
set (E-REQ-15).

    best = optimal_bom(acc, lot, cat, brief)  -> {"bom", "cost", "bus_voltage_v", "peak_current_a", "n_leaves"}
"""
import numpy as np

from . import lotmodel as LM
from .procedure import JOINTS, SLOT_JOINTS

DFS_ORDER = ("jaw", "wrist_roll", "wrist_pitch", "elbow_pitch", "shoulder_pitch", "base_yaw")


def admissible(acc, joint, bus_v, cat):
    out = []
    for i, s in enumerate(cat.servos):
        if s["body_class"] != acc.arm.pocket_class.get(joint, "standard"):
            continue
        if not cat.servo_ok_on_bus(s["part_id"], bus_v):
            continue
        out.append((float(s["price_usd"]), i, s["part_id"]))
    return [pid for _, _, pid in sorted(out)]


def joint_trace(acc, j, masses, payload):
    k = JOINTS.index(j)
    mv = acc.mv0[:, k].copy()
    hd = acc.hd0[:, k].copy()
    for jj, m in masses.items():
        if jj in acc.mvb:
            mv += m * acc.mvb[jj][:, k]
            hd += m * acc.hdb[jj][:, k]
    pr = hd + acc.layer["proof"] * payload * acc.hdb["payload"][:, k]
    mv = mv + payload * acc.mvb["payload"][:, k]
    hd = hd + payload * acc.hdb["payload"][:, k]
    return mv, hd, pr


def joint_worst(acc, j, cap, masses):
    """Worst utilisation of a capability at joint j for the given (distal) servo masses."""
    if j == "jaw":
        return acc.gripper_util(cap)["worst"]
    k = JOINTS.index(j)
    mv, hd, pr = joint_trace(acc, j, masses, float(acc.layer["m_hi"]))
    mvf = np.zeros((len(mv), 6))
    mvf[:, k] = mv
    hdf = np.zeros((len(hd), 6))
    hdf[:, k] = hd
    prf = np.zeros((len(pr), 6))
    prf[:, k] = pr
    return acc.joint_util(j, cap, mvf, hdf, prf)["worst"]


def distal_joints(j):
    i = JOINTS.index(j)
    return [x for x in SLOT_JOINTS if JOINTS.index(x) > i]


def optimal_bom(acc, lot, cat, brief, buses=None):
    buses = buses or tuple(float(v) for v in brief["bus_voltages_v"])
    best = None
    n_leaves = 0
    memo = {}
    for bus in buses:
        cand = {j: admissible(acc, j, bus, cat) for j in JOINTS}
        if any(not c for c in cand.values()):
            continue
        caps = {pid: LM.unit_capability(pid, bus, lot, cat, brief) for j in JOINTS for pid in cand[j]}
        min_price = {j: min(cat.price(p) for p in cand[j]) for j in JOINTS}
        psus = sorted([p for p in cat.psus if abs(float(p["output_v"]) - bus) < 1e-9],
                      key=lambda p: (float(p["price_usd"]), float(p["rated_current_a"])))
        if not psus:
            continue
        min_psu = float(psus[0]["price_usd"])
        min_board = min(float(b["price_usd"]) for b in cat.boards)
        chosen = {}

        def feasible(j, pid):
            dm = tuple(round(caps[chosen[x]]["mass_kg"], 6) for x in distal_joints(j))
            key = (bus, j, pid, dm)
            if key not in memo:
                masses = {x: caps[chosen[x]]["mass_kg"] for x in distal_joints(j)}
                memo[key] = joint_worst(acc, j, caps[pid], masses)
            return memo[key] <= 1.0 + 1e-12

        def rec(depth, cost):
            nonlocal best, n_leaves
            if depth == len(DFS_ORDER):
                n_leaves += 1
                servos = dict(chosen)
                protocols = sorted({cat.si(servos[j])["protocol"] for j in JOINTS})
                bc = cat.cheapest_board_cover(protocols, bus)
                if bc is None:
                    return
                capj = {j: caps[servos[j]] for j in JOINTS}
                masses = {j: capj[j]["mass_kg"] for j in SLOT_JOINTS}
                mv, hd, pr = acc.loads(masses, float(acc.layer["m_hi"]))
                peak = float(acc.currents(capj, mv).max())
                psu = next((p for p in psus if float(p["rated_current_a"]) >= peak - 1e-12), None)
                if psu is None:
                    return
                total = round(cost + bc[0] + float(psu["price_usd"]), 2)
                bom = {"bus_voltage_v": float(bus), "servos": servos, "boards": list(bc[1]), "psu": psu["part_id"]}
                if best is None or total < best["cost"] - 1e-9:
                    best = {"bom": bom, "cost": total, "bus_voltage_v": float(bus), "peak_current_a": peak}
                return
            j = DFS_ORDER[depth]
            rest = sum(min_price[x] for x in DFS_ORDER[depth + 1:])
            for pid in cand[j]:
                c = cost + cat.price(pid)
                if best is not None and c + rest + min_board + min_psu > best["cost"] + 1e-9:
                    break                              # candidates are sorted by price
                if not feasible(j, pid):
                    continue
                chosen[j] = pid
                rec(depth + 1, c)
                del chosen[j]

        rec(0, 0.0)
    if best is not None:
        best["n_leaves"] = n_leaves
        best["cost"] = cat.bom_cost(best["bom"])
    return best


def utilisation_table(acc, lot, cat, brief, bom):
    """Worst utilisation of EVERY admissible SKU at every joint with the rest of `bom` installed (distal masses from
    `bom`), at bom's bus voltage (near-boundary rule, stats)."""
    bus = float(bom["bus_voltage_v"])
    out = {}
    for j in JOINTS:
        masses = {x: LM.unit_capability(bom["servos"][x], bus, lot, cat, brief)["mass_kg"] for x in distal_joints(j)}
        out[j] = {pid: joint_worst(acc, j, LM.unit_capability(pid, bus, lot, cat, brief), masses)
                  for pid in admissible(acc, j, bus, cat)}
    return out
