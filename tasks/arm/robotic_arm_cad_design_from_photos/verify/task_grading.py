"""This task's settings on top of the vendored cad-photo grader (grader_config.json), applied by verify.py (and by the
authors' development grader alike).

`gates`: the grader's checks that multiply every criterion. The vendored grader gates on all four of cad_valid,
public_model_reuse, cad_json_consistent and servos_mounted (`GATES`); this task gates on the first three and reports
servos_mounted as a check only: servo retention stays scored inside component_fit. `band_overrides`: replacements for
the design rules' bands (here the two floors). Both were set on 2026-10-21 to give the from-scratch task a gradient
(the task's design record): with the predecessor's settings, made for template-based modelling, one unretained servo
or a mean IoU below 0.5 zeroed the whole score.
"""
import json
import os

ALL_CHECKS = ["cad_valid", "public_model_reuse", "cad_json_consistent", "servos_mounted"]


def load_config(stage_dir):
    with open(os.path.join(stage_dir, "grader_config.json")) as fh:
        return json.load(fh)


def apply(G, given, cfg):
    """Set the task's gate list on the grader module and its band overrides on the loaded design rules. Returns the gate
    names (the checks reported are ALL_CHECKS)."""
    gates = list(cfg.get("gates", G.GATES))
    unknown = [g for g in gates if g not in ALL_CHECKS]
    if unknown:
        raise ValueError("unknown gates in grader_config.json: %s" % unknown)
    G.GATES = gates
    bands = given["rules"].setdefault("grading", {}).setdefault("bands", {})
    bands.update({k: list(v) for k, v in cfg.get("band_overrides", {}).items()})
    return gates
