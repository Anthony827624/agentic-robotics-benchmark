"""Criteria of arm_requirements_replica_from_photos_i2 (rung I2; public code, catalogue-free): the same code in the grader
(hidden instance, the grader's truth catalogue) and in the practice checker (practice instances, the agent's own
transcription of the pages). Static: no agent code is executed.

    gt = load_gt(gt_dir)                              the GT bundle of one instance
    cat = catalog.Catalogue(path)                     the catalogue the BOM criteria use
    docs, problems = load_submission(sub, roles)      parse + schema + G0 rules (roles = {part_id: role} of the quote)
    out = grade(docs, gt, cat, cfg, optimum_mode)      {"criteria": {...}, "stats": {...}, "diagnostics": {...}}

Rung-I1 grading (req_info/grade.py: weights, bands, C4 verdict, C5 linear over one ECO) with the rung-I2 engine
(procedure / lotmodel: the lot's droop, SKU factors, the B-10 cap) and an injected catalogue.
"""
import json
import math
import os

import yaml

from . import schemas as S
from .procedure import JOINTS, ARM_JOINTS, Acceptance, ArmModel, bom_from_json
from . import optimum as AN
from . import repair as RP
from .layer import acc_layer, brief_constants

CRITERIA = ("kinematic_replication", "requirements_sheet", "derived_requirements", "bom_acceptance", "bom_economy")
DEFAULT_CFG = {
    "weights": {"kinematic_replication": 0.30, "requirements_sheet": 0.10, "derived_requirements": 0.20,
                "bom_acceptance": 0.10, "bom_economy": 0.30},
    "c4": {"mode": "all"},
    "c5": {"band_usd": 12.0},
    "c1": {"group_weights": {"lengths": 0.55, "stops": 0.30, "gripper": 0.10, "body_classes": 0.05},
           "length_mm": [0.5, 4.0], "stop_deg": [1.0, 6.0], "gripper_mm": [0.5, 3.0]},
    "c2": {"brief_rel": [0.02, 0.04], "photo_rel": [0.01, 0.06], "angle_deg": [1.5, 6.0], "clause_weight": 0.5},
    "c3": {"rel": [0.03, 0.15], "floors": {"N.m": 0.05, "rad/s": 0.2, "A": 0.2, "kg": 0.01}},
    "g0": {"links_tol_m": 0.001},
    "eco_usd": 12.0,
}


def band(err, full, zero):
    if err <= full:
        return 1.0
    if err >= zero:
        return 0.0
    return (zero - err) / (zero - full)


def _load(p):
    with open(p) as fh:
        return json.load(fh)


def load_gt(gt_dir):
    """The GT bundle of one instance (hidden: the grader's gt/; practice: practice/<k>/gt). The catalogue is NOT part of it
    (grader: gt/catalogue.json via load_catalogue; practice checker: the agent's transcription)."""
    gt = {"dir": gt_dir}
    for k in ("arm_model", "layer", "lot_truth", "key_dimensions", "reference_sheet", "optimum", "acceptance_set"):
        p = os.path.join(gt_dir, k + ".json")
        gt[k] = _load(p) if os.path.isfile(p) else None
    with open(os.path.join(gt_dir, "arm_dynamics.xml")) as fh:
        gt["xml"] = fh.read()
    return gt


def arm_from_gt(gt):
    am = gt["arm_model"]
    return ArmModel(xml=gt["xml"], slots={j: (s["body"], s["pos"], s["quat"]) for j, s in am["slots"].items()},
                    stops={j: tuple(v) for j, v in am["stops_qpos"].items()}, roll_zero=am["roll_zero"],
                    jaw_hold=am["jaw_hold"], jaw_lever_m=am["jaw_lever_m"], jaw_stroke_rad=am["jaw_stroke_rad"],
                    pocket_class=am["pocket_class"], printed_moving_mass_kg=am["printed_moving_mass_kg"],
                    name=am.get("name", "gt"), servo_box_m=am["servo_box_m"])


def acceptance_for(gt, cfg=None):
    arm = arm_from_gt(gt)
    ik = gt["acceptance_set"]["ik"] if gt.get("acceptance_set") else None
    return Acceptance(arm, acc_layer(gt["layer"]), ik_cache=ik)


# ---------------------------------------------------------------------------- submission + G0
def load_submission(sub_dir, part_roles):
    docs, problems = {}, []
    paths = {"requirements": "requirements.yaml", "bom": "bom.json", "kinematic_sketch": "kinematic_sketch.json"}
    for k, f in paths.items():
        p = os.path.join(sub_dir, f)
        if not os.path.isfile(p) or os.path.islink(p):
            problems.append("%s missing" % f)
            continue
        try:
            with open(p) as fh:
                txt = fh.read(2_000_000)
            docs[k] = yaml.safe_load(txt) if f.endswith(".yaml") else json.loads(txt)
        except Exception as e:  # noqa: BLE001
            problems.append("%s does not parse: %s" % (f, str(e)[:200]))
            continue
        if not isinstance(docs[k], dict):
            problems.append("%s is not a mapping" % f)
            docs.pop(k)
            continue
        errs = S.validate(docs[k], k)
        if errs:
            problems += ["%s: %s" % (f, e) for e in errs[:8]]
            docs.pop(k)
    if problems:
        return docs, problems
    problems += g0_rules(docs, part_roles)
    return docs, problems


def g0_rules(docs, part_roles, links_tol=0.001):
    """Structure rules of gate G0; `part_roles` = {part_id: role} of the quote (price_list.md)."""
    p = []
    req = docs["requirements"]["requirements"]
    ids = [r["id"] for r in req]
    if len(set(ids)) != len(ids):
        p.append("requirement ids are not unique")
    for r in req:
        conv = S.to_si(r["bound"], r["unit"])
        if conv is None or conv[1] != S.QUANTITY_SI_UNIT[r["quantity"]]:
            p.append("%s: unit %r is not a unit of %s" % (r["id"], r["unit"], r["quantity"]))
        if not math.isfinite(float(r["bound"])):
            p.append("%s: bound not finite" % r["id"])
    bom = docs["bom"]
    servo_lines = {}
    psu_lines = []
    for line in bom["parts"]:
        if line["part_id"] not in part_roles:
            p.append("bom part %r is not on the price list" % line["part_id"])
            continue
        if part_roles[line["part_id"]] != line["role"]:
            p.append("bom part %r has role %r, the price list says %r" % (line["part_id"], line["role"], part_roles[line["part_id"]]))
        if line["role"] == "servo":
            j = line.get("joint")
            if j is None:
                p.append("servo line %r without a joint" % line["part_id"])
                continue
            if j in servo_lines:
                p.append("two servo lines for joint %s" % j)
            servo_lines[j] = line
            if int(line["count"]) != 1:
                p.append("servo line of %s has count %s (one servo per joint)" % (j, line["count"]))
        elif line["role"] == "psu":
            psu_lines.append(line)
    for j in JOINTS:
        if j not in servo_lines:
            p.append("no servo line for joint %s" % j)
    if len(psu_lines) != 1 or int(psu_lines[0]["count"]) != 1:
        p.append("exactly one psu line with count 1 is required")
    if float(bom["bus_voltage_v"]) not in (7.5, 12.0):
        p.append("bus_voltage_v must be 7.5 or 12")
    sk = docs["kinematic_sketch"]
    g = sk["geometry"]
    for k, ref in (("link_1", "upper_arm_axis_distance"), ("link_2", "forearm_axis_distance"), ("link_3", "wrist_axis_to_tcp")):
        lk = sk["links"][k]
        lo = g[ref]["nominal"] - g[ref]["tol"]
        hi = g[ref]["nominal"] + g[ref]["tol"]
        if abs(lk["min"] - lo) > links_tol or abs(lk["max"] - hi) > links_tol:
            p.append("links.%s is not nominal +- tol of %s" % (k, ref))
    for j in JOINTS:
        sid = sk["joints"][j]["servo_id"]
        if j in servo_lines and servo_lines[j]["part_id"] != sid:
            p.append("sketch servo_id of %s (%s) differs from the BOM line (%s)" % (j, sid, servo_lines[j]["part_id"]))
    for j in ARM_JOINTS:
        a, b = sk["joint_ranges"][j]
        if not (math.isfinite(a) and math.isfinite(b)):
            p.append("joint range of %s not finite" % j)
    for k in S.GEOMETRY_KEYS:
        if not (math.isfinite(g[k]["nominal"]) and math.isfinite(g[k]["tol"])):
            p.append("geometry %s not finite" % k)
    return p


# ---------------------------------------------------------------------------- criteria
def c1_kinematic(sk, kd, cfg):
    c = cfg["c1"]
    items = {}
    lengths = []
    for k in S.LENGTH_KEYS:
        err = abs(float(sk["geometry"][k]["nominal"]) - float(kd["appendix_d"][k])) * 1000.0
        items["len_err_mm:" + k] = err
        lengths.append(band(err, *c["length_mm"]))
    stops = []
    for j in ARM_JOINTS:
        a = sorted(float(x) for x in sk["joint_ranges"][j])
        g = sorted(float(x) for x in kd["stops_rad"][j])
        for side, ai, gi in (("lower", a[0], g[0]), ("upper", a[1], g[1])):
            err = abs(math.degrees(ai - gi))
            items["stop_err_deg:%s:%s" % (j, side)] = err
            stops.append(band(err, *c["stop_deg"]))
    grip = []
    for k in S.GRIPPER_KEYS:
        err = abs(float(sk["geometry"][k]["nominal"]) - float(kd["appendix_d"][k])) * 1000.0
        items["grip_err_mm:" + k] = err
        grip.append(band(err, *c["gripper_mm"]))
    bodies = [1.0 if sk["joints"][j]["body_class"] == kd["pocket_class"][j] else 0.0 for j in JOINTS]
    groups = {"lengths": sum(lengths) / len(lengths), "stops": sum(stops) / len(stops), "gripper": sum(grip) / len(grip),
              "body_classes": sum(bodies) / len(bodies)}
    w = c["group_weights"]
    score = sum(w[k] * groups[k] for k in w) / sum(w.values())
    # calibrated uncertainty stats
    cov, tols = [], []
    for k in S.GEOMETRY_KEYS:
        tol = float(sk["geometry"][k]["tol"])
        cov.append(1.0 if abs(float(sk["geometry"][k]["nominal"]) - float(kd["appendix_d"][k])) <= tol + 1e-12 else 0.0)
        tols.append(tol * 1000.0)
    items["tol_coverage"] = sum(cov) / len(cov)
    items["tol_mean_mm"] = sum(tols) / len(tols)
    return score, groups, items


def _item_band(ref, cfg):
    kind = ref.get("band", "brief")
    if kind == "angle":
        return "abs_deg", cfg["c2"]["angle_deg"]
    if kind == "photo":
        return "rel", cfg["c2"]["photo_rel"]
    return "rel", cfg["c2"]["brief_rel"]


COMPARATOR_NORM = {"<=": "<=", "<": "<=", "=<": "<=", ">=": ">=", ">": ">=", "=>": ">=", "==": "==", "=": "=="}


def c2_sheet(items, rstar, cfg):
    """Facet F1 between the agent's non-derived items and R*: an agent item matches a reference item with the same
    quantity AND the same comparator (E-REQ-16, the r2 rule: a reversed comparator does not match; strict and non-strict
    forms are the same comparator), scored by value; a matched item with a source_id outside R*'s listed clause ids keeps
    `clause_weight` of its weight."""
    agent = [r for r in items if str(r.get("source_id")) != "derived" and r["quantity"] not in S.DERIVED_QUANTITIES]
    ref = rstar["items"]
    used = set()
    total = 0.0
    rep = {"matched": [], "partial": [], "missing": [], "extra": [], "clause_mismatch": []}
    for rf in ref:
        mode, (full, zero) = _item_band(rf, cfg)
        best, best_i, best_clause = 0.0, None, True
        for i, it in enumerate(agent):
            if i in used or it["quantity"] != rf["quantity"]:
                continue
            if COMPARATOR_NORM.get(str(it.get("comparator", "")).strip()) != COMPARATOR_NORM.get(str(rf["comparator"]).strip()):
                continue
            conv = S.to_si(it["bound"], it["unit"])
            if conv is None or conv[1] != S.QUANTITY_SI_UNIT[rf["quantity"]]:
                continue
            if mode == "abs_deg":
                err = abs(math.degrees(conv[0] - rf["bound"]))
            else:
                err = abs(conv[0] - rf["bound"]) / max(abs(rf["bound"]), 1e-9)
            w = band(err, full, zero)
            clause_ok = str(it.get("source_id", "")) in rf.get("source_ids", [])
            if not clause_ok:
                w *= cfg["c2"]["clause_weight"]
            if w > best:
                best, best_i, best_clause = w, i, clause_ok
        if best_i is not None:
            used.add(best_i)
        total += best
        if best_i is None or best <= 0:
            rep["missing"].append(rf["id"])
        elif best >= 1 - 1e-9:
            rep["matched"].append(rf["id"])
        else:
            rep["partial"].append(rf["id"])
        if best_i is not None and not best_clause:
            rep["clause_mismatch"].append(rf["id"])
    rep["extra"] = [str(agent[i].get("id")) for i in range(len(agent)) if i not in used]
    if not agent or not ref or total <= 0:
        return 0.0, rep
    P = total / len(agent)
    R = total / len(ref)
    return 2 * P * R / (P + R), rep


def c3_derived(items, gt_derived, cfg):
    by_q = {}
    for it in items:
        if it["quantity"] in S.DERIVED_QUANTITIES and it["quantity"] not in by_q:
            by_q[it["quantity"]] = it
    scores, errs = [], {}
    full, zero = cfg["c3"]["rel"]
    for q in S.DERIVED_QUANTITIES:
        g = float(gt_derived[q])
        unit = S.QUANTITY_SI_UNIT[q]
        floor = cfg["c3"]["floors"].get(unit, 1e-6)
        it = by_q.get(q)
        if it is None:
            scores.append(0.0)
            errs[q] = None
            continue
        conv = S.to_si(it["bound"], it["unit"])
        if conv is None or conv[1] != unit:
            scores.append(0.0)
            errs[q] = None
            continue
        e = abs(conv[0] - g) / max(abs(g), floor)
        errs[q] = e
        scores.append(band(e, full, zero))
    return sum(scores) / len(scores), errs


def grade(docs, gt, cat, cfg=None, acc=None, optimum_mode="check"):
    """optimum_mode: "check" (grader: the live optimum J* over `cat` must equal gt/optimum.json, else a task error), "stored"
    (practice checker: J* = the instance's stored optimum under the vendors' own numbers; the live optimum over the given
    catalogue is reported as a stat)."""
    cfg = cfg or DEFAULT_CFG
    out = {"criteria": {c: 0.0 for c in CRITERIA}, "stats": {}, "diagnostics": {}}
    if acc is None:
        acc = acceptance_for(gt, cfg)
    lot = gt["lot_truth"]
    brief = brief_constants(gt["layer"])
    bom = bom_from_json(docs["bom"])
    kd = gt["key_dimensions"]
    items = docs["requirements"]["requirements"]
    c1, groups, c1i = c1_kinematic(docs["kinematic_sketch"], kd, cfg)
    out["criteria"]["kinematic_replication"] = c1
    out["stats"].update({"c1_" + k: v for k, v in groups.items()})
    out["stats"].update({k: v for k, v in c1i.items()})
    c2, rep = c2_sheet(items, gt["reference_sheet"], cfg)
    out["criteria"]["requirements_sheet"] = c2
    out["stats"].update({"c2_matched": len(rep["matched"]), "c2_partial": len(rep["partial"]),
                         "c2_missing": len(rep["missing"]), "c2_extra": len(rep["extra"]),
                         "c2_clause_mismatch": len(rep["clause_mismatch"])})
    out["diagnostics"]["requirements_sheet"] = "missing %s; partial %s; extra %s" % (rep["missing"], rep["partial"], rep["extra"][:10])
    missing_ids = sorted({p for p in list(bom["servos"].values()) + list(bom.get("boards", [])) + [bom.get("psu")]
                          if p is None or not cat.has(p)})
    if missing_ids:
        out["diagnostics"]["bom"] = "catalogue lacks %s: the BOM criteria are 0" % missing_ids
        out["stats"]["catalogue_missing_parts"] = float(len(missing_ids))
        w = cfg["weights"]
        out["overall"] = sum(w[c] * out["criteria"][c] for c in CRITERIA) / sum(w.values())
        return out
    gder = acc.derived(bom, lot, cat, brief)
    c3, errs = c3_derived(items, gder, cfg)
    out["criteria"]["derived_requirements"] = c3
    for q, e in errs.items():
        out["stats"]["c3_relerr:" + q] = -1.0 if e is None else e
    ev = acc.evaluate(bom, lot, cat, brief)
    all_pass = all(bool(v) for v in ev["items"].values())
    out["criteria"]["bom_acceptance"] = (1.0 if all_pass else 0.0) if (cfg.get("c4") or {}).get("mode", "all") == "all" else ev["pass_fraction"]
    out["stats"]["c4_item_fraction"] = ev["pass_fraction"]
    for i, v in ev["items"].items():
        out["stats"]["c4_pass:" + i] = 1.0 if v else 0.0
    for j, u in ev["util"].items():
        out["stats"]["util:" + j] = float(u["worst"])
    out["stats"]["peak_current_a"] = ev["peak_current_a"]
    out["stats"]["bom_cost_usd"] = ev["cost"]
    out["stats"]["bus_voltage"] = float(bom["bus_voltage_v"])
    stored = gt["optimum"]
    if optimum_mode == "check":
        best = AN.optimal_bom(acc, lot, cat, brief)
        if best is None or abs(best["cost"] - float(stored["cost"])) > 1e-6:
            raise RuntimeError("live optimum %s != stored optimum %s (task error)" % (None if best is None else best["cost"], stored["cost"]))
        J_star = best["cost"]
    else:
        J_star = float(stored["cost"])
        best = AN.optimal_bom(acc, lot, cat, brief)
        out["stats"]["live_optimum_under_this_catalogue"] = -1.0 if best is None else best["cost"]
        out["stats"]["live_optimum_matches_stored"] = 1.0 if (best is not None and abs(best["cost"] - J_star) < 1e-6
                                                             and best["bom"]["servos"] == stored["bom"]["servos"]) else 0.0
    rp = RP.repair(acc, bom, lot, cat, brief, eco_usd=cfg["eco_usd"])
    ceiling = float(gt["layer"]["brief"]["ceiling"])
    c5 = RP.economy(J_star, rp["J_eff"], ceiling, band_usd=(cfg.get("c5") or {}).get("band_usd", 12.0))
    out["criteria"]["bom_economy"] = c5
    raw = (J_star / rp["J_eff"]) if (math.isfinite(rp["J_eff"]) and rp["J_eff"] > 0) else 0.0
    out["stats"].update({"J": rp["J"], "J_eff": rp["J_eff"] if math.isfinite(rp["J_eff"]) else -1.0, "J_star": J_star,
                         "repairs": rp["replaced"], "repairable": 1.0 if rp["repairable"] else 0.0, "ceiling": ceiling,
                         "c5_raw_ratio": raw,
                         "c5_over_usd": (rp["J_eff"] - J_star) if math.isfinite(rp["J_eff"]) else -1.0,
                         "optimum_beaten": 1.0 if (math.isfinite(rp["J_eff"]) and rp["J_eff"] < J_star - 1e-9
                                                  and ev["pass_fraction"] >= 1 - 1e-12) else 0.0})
    out["diagnostics"]["bom_economy"] = "; ".join(rp["log"])[:1500]
    out["diagnostics"]["bom_acceptance"] = ", ".join("%s %s (u %.3f)" % (j, "ok" if ev["items"][j] else "FAIL",
                                                                         ev["util"][j]["worst"]) for j in JOINTS)
    w = cfg["weights"]
    out["overall"] = sum(w[c] * out["criteria"][c] for c in CRITERIA) / sum(w.values())
    return out
