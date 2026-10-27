"""Rung-I2 deliverable schemas (public; requirements.md section 5 as in rung I1): requirements.yaml (req-info/2), bom.json
(bom-info/2), kinematic_sketch.json (sketch-info/2), the unit table and the requirement vocabulary; plus the schema of a
catalogue TRANSCRIPTION (req-i2-catalogue/1: the agent's own reading of the datasheet pages, the erratum and the quote,
which the practice checker needs to score the BOM criteria on a practice instance). Rung-I1 copy with two changes: a lot
report is traced by its document number (source_id LR-yyyy-nnnn, or LR) and the catalogue schema is new.

    write_schemas(out_dir)             the three JSON schema files shipped to the agent
    validate(doc, which)               -> list of problems (jsonschema)
    to_si(value, unit)                 -> (value_si, si_unit) or None
"""
import json
import os

JOINTS = ("base_yaw", "shoulder_pitch", "elbow_pitch", "wrist_pitch", "wrist_roll", "jaw")
ARM_JOINTS = JOINTS[:5]
PITCH_JOINTS = ("base_yaw", "shoulder_pitch", "elbow_pitch", "wrist_pitch")

UNIT_TO_SI = {
    "kg": ("kg", 1.0), "g": ("kg", 1e-3),
    "m": ("m", 1.0), "mm": ("m", 1e-3), "cm": ("m", 1e-2),
    "rad": ("rad", 1.0), "deg": ("rad", 0.017453292519943295),
    "s": ("s", 1.0), "ms": ("s", 1e-3),
    "N.m": ("N.m", 1.0), "N m": ("N.m", 1.0), "Nm": ("N.m", 1.0), "kg.cm": ("N.m", 0.0980665), "oz.in": ("N.m", 0.00706155181),
    "rad/s": ("rad/s", 1.0), "rpm": ("rad/s", 0.10471975511965977), "deg/s": ("rad/s", 0.017453292519943295),
    "A": ("A", 1.0), "mA": ("A", 1e-3),
    "V": ("V", 1.0), "USD": ("USD", 1.0), "count": ("count", 1.0), "fraction": ("fraction", 1.0), "N": ("N", 1.0),
}

# requirement vocabulary -> SI unit
QUANTITY_SI_UNIT = {
    "payload_mass_max": "kg", "payload_mass_min": "kg", "payload_com_offset": "m",
    "reach_radius_min": "m", "reach_radius_max": "m", "workspace_azimuth_min": "rad", "workspace_azimuth_max": "rad",
    "workspace_height_min": "m", "workspace_height_max": "m", "grasp_pitch_angles": "rad",
    "move_time": "s", "dwell_time": "s", "proof_load_factor": "fraction", "gripper_close_time": "s",
    "gripper_opening_min": "m", "psu_count": "count", "servo_count": "count", "revolute_dof": "count",
    "total_cost": "USD", "eco_cost": "USD", "unit_count": "count", "bus_voltage": "V",
    "untested_vendor_derating": "fraction", "harness_drop": "V",
    "grip_torque": "N.m", "supply_peak_current": "A", "moving_mass": "kg",
}
for _j in ARM_JOINTS:
    QUANTITY_SI_UNIT["joint_peak_torque:" + _j] = "N.m"
    QUANTITY_SI_UNIT["joint_peak_speed:" + _j] = "rad/s"
for _j in PITCH_JOINTS:
    QUANTITY_SI_UNIT["joint_rms_torque:" + _j] = "N.m"

# the graded derived items (brief B-16): the acceptance moves never move the wrist roll (roll 0 at every target), so its
# peak torque / speed are structurally ~0 and are not graded (15 items; the vocabulary keeps them for completeness)
DERIVED_QUANTITIES = tuple(["joint_peak_torque:" + j for j in PITCH_JOINTS] + ["joint_rms_torque:" + j for j in PITCH_JOINTS]
                           + ["joint_peak_speed:" + j for j in PITCH_JOINTS] + ["grip_torque", "supply_peak_current", "moving_mass"])
GEOMETRY_KEYS = ("shoulder_axis_height", "shoulder_axis_offset", "upper_arm_axis_distance", "forearm_axis_distance",
                 "wrist_axis_to_tcp", "jaw_lever", "jaw_stroke")
LENGTH_KEYS = GEOMETRY_KEYS[:5]
GRIPPER_KEYS = GEOMETRY_KEYS[5:]
BODY_CLASSES = ("standard", "compact", "large")
SOURCE_PATTERN = r"^(B|BL)-[0-9]{2}$|^PH-[0-9]{2}$|^DS-[A-Za-z0-9-]+$|^LR(-[0-9]{4}-[0-9]{4})?$|^derived$"


def to_si(value, unit):
    if unit not in UNIT_TO_SI:
        return None
    c, f = UNIT_TO_SI[unit]
    return float(value) * f, c


def requirements_schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "req-info/2 requirements sheet",
        "description": ("Traced requirements of the replica fleet: one item per quantity the brief, the build log, the "
                        "photos or the documents fix, plus the derived items of brief B-16 (source_id 'derived' with a "
                        "derivation note). Bounds in the listed units; every item traced to its source."),
        "type": "object",
        "required": ["requirements"],
        "properties": {
            "project": {"type": "string"}, "units": {"type": "object"}, "notes": {"type": "string"},
            "requirements": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["id", "quantity", "comparator", "bound", "unit", "source_id"],
                "properties": {
                    "id": {"type": "string", "pattern": "^REQ-[A-Za-z0-9_.-]+$"},
                    "quantity": {"type": "string", "enum": sorted(QUANTITY_SI_UNIT)},
                    "comparator": {"type": "string", "enum": [">=", "<=", "==", ">", "<"]},
                    "bound": {"type": "number"},
                    "unit": {"type": "string", "enum": sorted(UNIT_TO_SI)},
                    "source_id": {"type": "string", "pattern": SOURCE_PATTERN},
                    "source_clause": {"type": "string"},
                    "derivation": {"type": "string"}},
                "additionalProperties": True}}},
        "additionalProperties": True}


def bom_schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "bom-info/2 bill of materials (per replica unit)",
        "type": "object",
        "required": ["bus_voltage_v", "parts"],
        "properties": {
            "format": {"type": "string"},
            "bus_voltage_v": {"type": "number", "enum": [7.5, 12, 12.0]},
            "total_cost_usd": {"type": "number"},
            "parts": {"type": "array", "minItems": 8, "items": {
                "type": "object", "required": ["part_id", "count", "role"],
                "properties": {"part_id": {"type": "string"}, "count": {"type": "integer", "minimum": 1},
                               "role": {"type": "string", "enum": ["servo", "bus_board", "psu"]},
                               "joint": {"type": "string", "enum": list(JOINTS)}, "note": {"type": "string"}},
                "additionalProperties": True}},
            "notes": {"type": "string"}},
        "additionalProperties": True}


def _nt():
    return {"type": "object", "required": ["nominal", "tol"],
            "properties": {"nominal": {"type": "number"}, "tol": {"type": "number", "minimum": 0}}, "additionalProperties": True}


def sketch_schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "sketch-info/2 kinematic sketch of the replica",
        "type": "object",
        "required": ["units", "geometry", "joint_ranges", "joints", "links"],
        "properties": {
            "units": {"type": "object", "required": ["length", "angle"],
                      "properties": {"length": {"const": "m"}, "angle": {"const": "rad"}}},
            "topology": {"type": "string"},
            "geometry": {"type": "object", "required": list(GEOMETRY_KEYS),
                         "properties": {k: _nt() for k in GEOMETRY_KEYS}, "additionalProperties": True},
            "joint_ranges": {"type": "object", "required": list(ARM_JOINTS),
                             "properties": {j: {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
                                            for j in JOINTS}, "additionalProperties": False},
            "joints": {"type": "object", "required": list(JOINTS),
                       "properties": {j: {"type": "object", "required": ["servo_id", "body_class"],
                                          "properties": {"servo_id": {"type": "string"},
                                                         "body_class": {"type": "string", "enum": list(BODY_CLASSES)}}}
                                      for j in JOINTS}, "additionalProperties": False},
            "links": {"type": "object", "required": ["link_1", "link_2", "link_3"],
                      "properties": {k: {"type": "object", "required": ["min", "max"],
                                         "properties": {"min": {"type": "number"}, "max": {"type": "number"}}}
                                     for k in ("link_1", "link_2", "link_3")}}},
        "additionalProperties": True}


def catalogue_schema():
    num = {"type": "number"}
    rng = {"type": "array", "items": num, "minItems": 2, "maxItems": 2}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "req-i2-catalogue/1 transcription of the vendor pages and the quote (SI units)",
        "description": ("Your reading of datasheets/DS-*.png (with the erratum applied) and price_list.md, in SI units: "
                        "torques N.m, speeds rad/s, currents A, voltages V, inertia kg m2, masses kg, prices USD. Lists in "
                        "the quote's order. Only the practice checker reads this file (to score the BOM criteria of a "
                        "practice instance under YOUR reading); the grader uses the vendors' own numbers."),
        "type": "object",
        "required": ["servos", "boards", "psus", "vendors"],
        "properties": {
            "format": {"type": "string"},
            "vendors": {"type": "object", "additionalProperties": {
                "type": "object", "required": ["inspected"], "properties": {"inspected": {"type": "boolean"}}}},
            "servos": {"type": "array", "items": {
                "type": "object",
                "required": ["part_id", "vendor", "body_class", "protocol", "voltage_range_v", "points", "no_load_current_a",
                             "rated_torque_nm", "output_inertia_kgm2", "mass_kg", "price_usd"],
                "properties": {
                    "part_id": {"type": "string"}, "vendor": {"type": "string"},
                    "body_class": {"type": "string", "enum": list(BODY_CLASSES)}, "protocol": {"type": "string"},
                    "voltage_range_v": rng,
                    "points": {"type": "array", "minItems": 1, "maxItems": 2, "items": {
                        "type": "object", "required": ["voltage_v", "stall_torque_nm", "no_load_speed_rad_s", "stall_current_a"],
                        "properties": {"voltage_v": num, "stall_torque_nm": num, "no_load_speed_rad_s": num,
                                       "stall_current_a": num}}},
                    "no_load_current_a": num, "rated_torque_nm": num, "output_inertia_kgm2": num, "mass_kg": num,
                    "price_usd": num},
                "additionalProperties": True}},
            "boards": {"type": "array", "items": {
                "type": "object", "required": ["part_id", "protocols", "input_range_v", "price_usd"],
                "properties": {"part_id": {"type": "string"}, "protocols": {"type": "array", "items": {"type": "string"}},
                               "input_range_v": rng, "price_usd": num}, "additionalProperties": True}},
            "psus": {"type": "array", "items": {
                "type": "object", "required": ["part_id", "output_v", "rated_current_a", "price_usd"],
                "properties": {"part_id": {"type": "string"}, "output_v": num, "rated_current_a": num, "price_usd": num},
                "additionalProperties": True}}},
        "additionalProperties": True}


SCHEMAS = {"requirements": requirements_schema, "bom": bom_schema, "kinematic_sketch": sketch_schema,
           "catalogue": catalogue_schema}


def write_schemas(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    out = {}
    for k, f in SCHEMAS.items():
        p = os.path.join(out_dir, "%s.schema.json" % k)
        with open(p, "w") as fh:
            json.dump(f(), fh, indent=2, sort_keys=True)
            fh.write("\n")
        out[k] = p
    return out


def validate(doc, which):
    import jsonschema
    v = jsonschema.Draft202012Validator(SCHEMAS[which]())
    return ["%s: %s" % ("/".join(str(x) for x in e.absolute_path) or "(root)", e.message)
            for e in sorted(v.iter_errors(doc), key=lambda e: list(e.absolute_path))]
