"""The keys of an instance layer the acceptance procedure reads (public code; the numbers are in the instance's layer.json:
its "brief" block carries the customer requirements and the brief / build-log constants the formulas use)."""


def acc_layer(lay):
    b = lay["brief"]
    return {"r_min": lay["r_min"], "r_max": lay["r_max"], "az_c": lay["az_c"], "az_half": lay["az_half"],
            "z_lo": lay["z_lo"], "z_max": lay["z_max"], "pitches_deg": list(lay["pitches_deg"]),
            "T": b["T"], "H": b["H"], "m_hi": b["m_hi"], "com_offset": b["com_offset"], "proof": b["proof"],
            "ceiling": b["ceiling"] if b.get("ceiling") is not None else 1e9, "jaw_close_s": b["jaw_close_s"]}


def brief_constants(lay):
    """The formula constants of brief B-10 / B-11 / B-13 / B-14 and build log BL-03 (harness drop, untested-vendor cap,
    weakest-unit quantile, bus voltages, ECO cost)."""
    b = lay["brief"]
    return {k: b[k] for k in ("harness_drop_v", "untested_cap", "p_quantile_z", "bus_voltages_v", "eco")}


def widest_object(lay):
    return max(o["diameter_m"] for o in lay["objects"])
