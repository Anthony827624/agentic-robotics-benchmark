"""Capability of the lot's weakest expected unit (brief B-14 e, B-10) under a GIVEN lot truth and a GIVEN catalogue
(public formula code: no catalogue, lot or price number in it). The grader passes the hidden truth, the practice checker
a practice instance's truth.

Lot truth (format req-i2-lot-truth/1): {"delivered_lot": key, "lots": {key: {"lot_id", "report", "vendor", "f_tau",
"f_w", "f_c", "sigma", "droop", "tested_skus"}}, "sku_factor": {part_id: e}}. For an INSPECTED vendor a unit's ratio to
the datasheet (at the same voltage) is: stall = f_tau (1 + e_sku) (1 + sigma z1), no-load speed = f_w (1 + sigma z2),
continuous (rated) torque = f_c (1 + e_sku) (1 + sigma z3), z ~ N(0, 1) per unit and quantity; the lot factors (f_*,
sigma, droop) belong to one lot, the SKU factor e_sku to the SKU's design (the same in every lot of the vendor). The
weakest expected unit has z = -z_p (brief: 1.96). Torque-speed characteristic of every unit of a lot:
    w(tau) = w0 (1 - tau / tau_s) (1 - d tau / tau_s),   d = the lot's droop (0 = the datasheet's straight line)
so the torque available at speed w is tau_s u*(|w| / w0, d) with u* the root in [0, 1] of (1 - u)(1 - d u) = x.
A vendor WITHOUT inspection data (brief B-10) is counted at `untested_cap` x its typical (datasheet) stall torque, no-load
speed and rated torque, on the straight-line characteristic (d = 0).
Brief constants (layer.json "brief"): harness_drop_v (BL-03), untested_cap (B-10), p_quantile_z (B-14 e).
"""
import math


def droop_fraction(x, d):
    """u* in [0, 1]: the available torque fraction of stall at the speed fraction x = |w| / w0 for droop d."""
    x = min(max(float(x), 0.0), 1.0)
    d = float(d)
    if d <= 1e-12:
        return 1.0 - x
    b = 1.0 + d
    disc = max(b * b - 4.0 * d * (1.0 - x), 0.0)
    return max(0.0, min(1.0, (b - math.sqrt(disc)) / (2.0 * d)))


def droop_fraction_array(x, d):
    """Vectorised droop_fraction over a numpy array of speed fractions."""
    import numpy as np
    x = np.clip(np.asarray(x, float), 0.0, 1.0)
    d = float(d)
    if d <= 1e-12:
        return 1.0 - x
    b = 1.0 + d
    disc = np.maximum(b * b - 4.0 * d * (1.0 - x), 0.0)
    return np.clip((b - np.sqrt(disc)) / (2.0 * d), 0.0, 1.0)


def speed_at(tau, tau_s, w0, d):
    """w(tau) of the lot's characteristic (for tau in [0, tau_s])."""
    u = min(max(float(tau) / float(tau_s), 0.0), 1.0)
    return float(w0) * (1.0 - u) * (1.0 - float(d) * u)


def unit_capability(sku_id, bus_v, lot, cat, brief, z=None):
    """SI capability of a unit of `sku_id` on a bus at `bus_v` (the servo sees bus_v - harness_drop_v).
    Default: the lot's weakest expected unit (z = -p_quantile_z); `z` = an explicit standard-normal draw (stats).
    Returns {tau_s, w0, tau_c, droop, i_stall, i0, J_out, mass_kg, v_servo, tau_nom, w_nom}."""
    s = cat.si(sku_id)
    v_s = float(bus_v) - float(brief["harness_drop_v"])
    tau_nom, w_nom, i_nom = cat.nominal_at(s, v_s)
    if not s["inspected"]:
        k = float(brief["untested_cap"])
        tau_s, w0, tau_c, d = k * tau_nom, k * w_nom, k * s["rated_nm"], 0.0
    else:
        L = lot["lots"][lot["delivered_lot"]]
        e = float(lot["sku_factor"].get(sku_id, 0.0))
        zz = -float(brief["p_quantile_z"]) if z is None else float(z)
        q = 1.0 + zz * float(L["sigma"])
        tau_s = tau_nom * float(L["f_tau"]) * (1.0 + e) * q
        w0 = w_nom * float(L["f_w"]) * q
        tau_c = s["rated_nm"] * float(L["f_c"]) * (1.0 + e) * q
        d = float(L["droop"])
    return {"tau_s": tau_s, "w0": w0, "tau_c": tau_c, "droop": d, "i_stall": i_nom, "i0": s["i0"], "J_out": s["J_out"],
            "mass_kg": s["mass_kg"], "v_servo": v_s, "tau_nom": tau_nom, "w_nom": w_nom}
