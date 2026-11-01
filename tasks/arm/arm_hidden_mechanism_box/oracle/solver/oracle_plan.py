"""Privileged scripted plan: knows the box config, drives the stages in order."""
import math

import numpy as np
import skills as S

# calibration of the privileged primitives (measured offline in sim with GT; see R/tM/src/calib_*.py)
KNOB_CAL = {"knob_bias_deg": 12.0, "press_bias": -0.0012, "slide_bias": 0.0}


def run_plan(arm, cfg, pose, log=print):
    sh = cfg["shell"]
    bf = S.BoxFrame(pose, sh["D"], sh["W"], sh["H"])
    import fk as FKM
    sk = S.Skills(arm, bf, fk=FKM.get())
    el = {e["id"]: e for e in cfg["elements"]}
    for eid in cfg["required"]:
        if eid == "final":
            if cfg.get("final", "lid") == "lid":
                r = sk.open_lid(sh.get("lip_u", 0.0))
            else:
                dr = cfg["drawer"]
                r = sk.pull_drawer(dr["u"], dr["v"], dr["travel"])
        else:
            e = el[eid]
            win = e.get("window")
            if e["type"] == "button":
                if win:
                    r = sk.press_to(e["face"], e["u"], e["v"], 0.5 * (win[0] + win[1]) + KNOB_CAL.get("press_bias", 0.0))
                else:
                    r = sk.press(e["face"], e["u"], e["v"], e.get("travel", 0.010))
            elif e["type"] == "bolt":
                if e.get("latched"):
                    r = sk.press_slide_bolt(e["u"], e["v"], e["dir"], e["travel"])
                elif win:
                    r = sk.slide_bolt_to(e["u"], e["v"], e["dir"], 0.5 * (win[0] + win[1]) + KNOB_CAL.get("slide_bias", 0.0))
                else:
                    r = sk.slide_bolt(e["u"], e["v"], e["dir"], e["travel"])
            elif e["type"] == "knob":
                if win:
                    deg = math.degrees(0.5 * (win[0] + win[1]))
                    r = sk.turn_knob_to(e["u"], e["v"], e["sense"], deg)
                else:
                    r = sk.turn_knob(e["u"], e["v"], e["sense"])
            elif e["type"] == "panel":
                if e["slide"] == "up":
                    r = sk.panel_up(e["u"], e["v"], e["h"], e["travel"])
                else:
                    r = sk.panel_side(e["u"], e["v"], e["w"], e["dir"], e["travel"])
        log("stage %s -> %s (budget %d)" % (eid, r, arm.budget[0]))

