"""Deterministic ECO repair and the economy criterion (brief B-13, C5; public code over a GIVEN catalogue).

While an item fails, replace the failing line by the cheapest line of the catalogue that makes it pass given the rest
(servo joints distal to proximal, then the board set, then the PSU; ties by price, then quote order) and add the brief's
ECO cost per replaced line. The bus voltage is never changed by a repair: a joint with no passing SKU at that voltage makes
the BOM irreparable (J_eff = inf, economy 0). The budget is not a line.

    r = repair(acc, bom, lot, cat, brief, eco_usd)   -> {"bom", "replaced", "J", "J_eff", "log", "repairable"}
    economy(J_star, J_eff, ceiling, band_usd)        clamp(1 - (J_eff - J*) / band, 0, 1) x taper(J_eff vs ceiling)
"""
import copy
import math

from .procedure import JOINTS

REPAIR_ORDER = ("jaw", "wrist_roll", "wrist_pitch", "elbow_pitch", "shoulder_pitch", "base_yaw")


def repair(acc, bom, lot, cat, brief, eco_usd, max_steps=40):
    b = copy.deepcopy(bom)
    b.setdefault("boards", [])
    replaced = 0
    log = []
    J0 = None
    for _ in range(max_steps):
        res = acc.evaluate(b, lot, cat, brief)
        if J0 is None:
            J0 = res["cost"]
        failing = [j for j in REPAIR_ORDER if not res["items"][j]]
        if failing:
            j = failing[0]
            fixed = False
            ordered = sorted(enumerate(cat.servos), key=lambda t: (float(t[1]["price_usd"]), t[0]))
            for _, s in ordered:
                if s["part_id"] == b["servos"][j]:
                    continue
                trial = copy.deepcopy(b)
                trial["servos"][j] = s["part_id"]
                if acc.evaluate(trial, lot, cat, brief)["items"][j]:
                    log.append("%s: %s -> %s" % (j, b["servos"][j], s["part_id"]))
                    b = trial
                    replaced += 1
                    fixed = True
                    break
            if not fixed:
                return {"bom": b, "replaced": replaced, "J": J0, "J_eff": math.inf, "log": log + ["%s: no SKU passes" % j],
                        "repairable": False}
            continue
        if not res["items"]["bus_board"]:
            protocols = sorted({cat.si(b["servos"][j])["protocol"] for j in JOINTS})
            bc = cat.cheapest_board_cover(protocols, float(b["bus_voltage_v"]))
            if bc is None:
                return {"bom": b, "replaced": replaced, "J": J0, "J_eff": math.inf, "log": log + ["boards: no cover"],
                        "repairable": False}
            old = list(b["boards"])
            new = list(bc[1])
            n_changed = max(1, len([x for x in new if x not in old]))
            log.append("boards: %s -> %s" % (old, new))
            b["boards"] = new
            replaced += n_changed
            continue
        if not res["items"]["psu"]:
            peak = res["peak_current_a"]
            ok = [p for p in sorted(enumerate(cat.psus), key=lambda t: (float(t[1]["price_usd"]), t[0]))
                  if abs(float(p[1]["output_v"]) - float(b["bus_voltage_v"])) < 1e-9 and float(p[1]["rated_current_a"]) >= peak - 1e-12]
            if not ok or not all(cat.servo_ok_on_bus(b["servos"][j], float(b["bus_voltage_v"])) for j in JOINTS):
                return {"bom": b, "replaced": replaced, "J": J0, "J_eff": math.inf, "log": log + ["psu: none fits"],
                        "repairable": False}
            log.append("psu: %s -> %s" % (b.get("psu"), ok[0][1]["part_id"]))
            b["psu"] = ok[0][1]["part_id"]
            replaced += 1
            continue
        break
    res = acc.evaluate(b, lot, cat, brief)
    J_eff = res["cost"] + float(eco_usd) * replaced
    return {"bom": b, "replaced": replaced, "J": J0, "J_eff": round(J_eff, 2), "log": log, "repairable": True,
            "final_items": res["items"]}


def economy(J_star, J_eff, ceiling, band_usd):
    if not math.isfinite(J_eff) or J_eff <= 0:
        return 0.0
    over = round(float(J_eff) - float(J_star), 6)
    ratio = min(1.0, max(0.0, 1.0 - over / float(band_usd)))
    if J_eff <= ceiling + 1e-9:
        taper = 1.0
    else:
        taper = max(0.0, 1.0 - (J_eff - ceiling) / (0.10 * ceiling))
    return ratio * taper
