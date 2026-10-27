"""cad_photo.grade: the grader of arm_links_from_photo_set (hardware_design.md 6 as amended by its Editor's changes,
E-HD-12 and the as-built deviations of the task's DESIGN.md). Shipped twice, byte-identical: verify/cad_photo_grade.py
(hidden run: the GT of the photographed unit) and the agent's practice checker (the GT of a practice unit). It never
reads hidden material itself: the caller passes the reference folder, the practice templates and the cases. Lengths mm,
angles rad unless named.

    given = load_given(given_dir)                         servo, schema, rules (the payload files)
    ref = load_reference(ref_dir, given)                  a GT folder: parts/*.step, assembly.json, gt.json
    templates = load_templates([(name, dir)], given)      the practice units' CAD (the product-line templates)
    cases = make_cases(rng)                               hidden per-run draws (Halton shifts, sampling seed)
    rewards, stats, details = grade(sub_dir, given, ref, cases, log, templates=templates)
    rewards, stats, details = self_check(sub_dir, given, cases, log)     public rules only (no ground truth)

Gates (binary, multiply into every criterion): cad_valid (ten single valid watertight STEP solids, the tier schema and
topology, the drawings converter's MJCF compiles without warnings and its FK matches the chain within 1 mm / 0.5 deg at
16 configurations; verify.py also zeroes it when an entry of the submission was written after the session ended, stat
submission_final), public_model_reuse, cad_json_consistent, servos_mounted.

Criteria (weights in grader_config / design_rules): kinematic_fidelity 0.30, feature_fidelity 0.25, shape_fidelity 0.25,
mass_properties 0.10, component_fit 0.05, interference_free 0.05. Each of the first four is a weighted mean of items
(band scores); its plain value is sum(w s) / sum(w). Template-relative credit (grader 2.0.0): every item is also scored
for the practice templates' own CAD (each template's parts and links; for the chain items every per-link combination of
the templates' links), t = the best template score of the item; relative = sum(w max(0, s - t)) / sum(w (1 - t)); the
criterion = min(plain, relative), so copying the templates earns 0 and no submission scores above its plain value.
Floors: F_s = mean over parts clamp((IoU - 0.50) / 0.25, 0, 1); F_k = band(median FK tool error; 5, 15 mm); F_s
multiplies kinematic and feature fidelity, F_s x F_k multiplies mass, fit and interference; shape fidelity is not floored.
"""

import json
import math
import os
import tempfile

import numpy as np

try:                                    # vendored names (task payload / verify stage)
    import cad_drawings_check as C
    import cad_photo_align as A
    import cad_photo_features as FT
except ImportError:                     # the shared tree (author side)
    from arm.cad_drawings import checks as C
    from arm.cad_photo import align as A
    from arm.cad_photo import features as FT

GRADER_VERSION = "cad-photo-grade/2.0.0"   # 2.0.0: template-relative credit (DESIGN.md D19)
TIER_LINKS = ["base", "shoulder", "upper_arm", "forearm", "wrist", "gripper", "jaw"]
TO_DRAWINGS = {"forearm": "lower_arm"}
TO_TIER = {"lower_arm": "forearm"}
JOINTS = list(C.JOINT_ORDER)
ARM_JOINTS = list(C.ARM_JOINTS)
PART_IDS = ["P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09", "P10"]
LINK_OF_PART = {"P01": "base", "P02": "base", "P03": "shoulder", "P04": "shoulder", "P05": "upper_arm", "P06": "forearm",
                "P07": "forearm", "P08": "wrist", "P09": "gripper", "P10": "jaw"}
GATES = ["cad_valid", "public_model_reuse", "cad_json_consistent", "servos_mounted"]
CRITERIA = ["kinematic_fidelity", "feature_fidelity", "shape_fidelity", "mass_properties", "component_fit", "interference_free"]
RELATIVE_CRITERIA = ["kinematic_fidelity", "feature_fidelity", "shape_fidelity", "mass_properties"]
DEFAULT_WEIGHTS = {"kinematic_fidelity": 0.30, "feature_fidelity": 0.25, "shape_fidelity": 0.25, "mass_properties": 0.10,
                   "component_fit": 0.05, "interference_free": 0.05}
STRUCTURE = {"joints": [dict(j) for j in C.DEFAULT_STRUCTURE["joints"]], "link_parts": dict(C.DEFAULT_STRUCTURE["link_parts"]),
             "disclosed_parts": []}
POCKETS = (("S3", "P05"), ("S4", "P06"))
SLICER_DENSITY = 520.0
SERVO_MASS = 0.060
TCP_BACK_MM = 10.0
DEFAULT_BANDS = {
    "axis_mm": [0.3, 3.0], "length_mm": [0.3, 3.0], "fk_median_mm": [0.5, 8.0], "fk_p90_mm": [1.0, 15.0],
    "jaw_tip_median_mm": [0.5, 8.0], "cutout_offset_mm": [0.3, 3.0], "cutout_area_rel": [0.10, 0.50], "pocket_floor_mm": [0.3, 3.0],
    "extent_mm": [0.3, 3.0], "opening_mm": [0.3, 3.0], "surface_mm": [0.3, 2.5], "one_minus_iou": [0.05, 0.40],
    "mass_rel": [0.02, 0.15], "com_mm": [0.5, 6.0], "floor_iou": [0.50, 0.75], "floor_fk_mm": [5.0, 15.0]}
DEFAULT_SUBWEIGHTS = {"kinematic": {"axes": 0.35, "lengths": 0.15, "fk": 0.40, "range": 0.10},
                      "fk": {"median": 0.40, "p90": 0.35, "jaw_tip": 0.25},
                      "feature": {"cutouts": 0.50, "pocket_floors": 0.10, "extents": 0.25, "openings": 0.15},
                      "shape": {"surface": 0.6, "iou": 0.4}}
LENGTH_KEYS = ["shoulder_axis_height", "yaw_to_shoulder_offset", "L2", "L3", "wrist_pitch_to_roll_mm", "roll_joint_to_tcp_mm"]


def band(v, b):
    return C.band(v, b[0], b[1])


def to_drawings_names(spec):
    s = json.loads(json.dumps(spec))
    for l in s.get("links", []):
        if isinstance(l, dict) and l.get("name") in TO_DRAWINGS:
            l["name"] = TO_DRAWINGS[l["name"]]
    for j in s.get("joints", []):
        if not isinstance(j, dict):
            continue
        for k in ("parent_link", "child_link"):
            if j.get(k) in TO_DRAWINGS:
                j[k] = TO_DRAWINGS[j[k]]
    if isinstance(s.get("tcp"), dict) and s["tcp"].get("link") in TO_DRAWINGS:
        s["tcp"]["link"] = TO_DRAWINGS[s["tcp"]["link"]]
    return s


def tier_link(name):
    return TO_TIER.get(name, name)


# ----------------------------------------------------------------------------- given files
def load_given(given_dir):
    import yaml
    with open(os.path.join(given_dir, "design_rules.yaml")) as f:
        rules = yaml.safe_load(f)
    with open(os.path.join(given_dir, "assembly_schema.json")) as f:
        schema = json.load(f)
    servo = C.Servo(os.path.join(given_dir, "servo", "servo.json"), os.path.join(given_dir, "servo", "servo.step"))
    blocklist = {"sha256": {}, "vertex_fingerprints": {}, "invariants": []}
    p = os.path.join(given_dir, "public_blocklist.json")
    if os.path.isfile(p):
        with open(p) as f:
            doc = json.load(f)
        blocklist = {"sha256": {k: True for k in doc.get("sha256", [])},
                     "vertex_fingerprints": {k: True for k in doc.get("vertex_fingerprints", [])}, "invariants": doc.get("invariants", [])}
    return {"rules": rules, "schema": schema, "servo": servo, "facts": dict(servo.facts), "blocklist": blocklist, "dir": given_dir}


def _bands(rules):
    b = dict(DEFAULT_BANDS)
    b.update((rules or {}).get("grading", {}).get("bands", {}))
    return b


def _subweights(rules):
    w = json.loads(json.dumps(DEFAULT_SUBWEIGHTS))
    for k, v in (rules or {}).get("grading", {}).get("subweights", {}).items():
        w[k].update(v)
    return w


# ----------------------------------------------------------------------------- submission loading (tier names)
class TierSubmission(C.Submission):
    """C.Submission on the TIER vocabulary: the assembly.json is validated against the tier schema, renamed to the
    drawings names (forearm -> lower_arm) for the shared checks, and its parts are loaded from `sub_dir` (regular files
    inside it, one valid watertight solid each)."""

    def __init__(self, sub_dir, schema):
        import jsonschema
        self.dir = sub_dir
        self.gate_errors, self.notes = [], []
        self.spec = self.tier_spec = None
        self.chain = None
        self.parts, self.part_files = {}, {}
        path = os.path.join(sub_dir, "assembly.json")
        if not os.path.isfile(path) or os.path.islink(path):
            self.gate_errors.append("no assembly.json")
            return
        try:
            with open(path) as f:
                spec = json.load(f)
        except (OSError, ValueError) as exc:
            self.gate_errors.append("assembly.json unreadable: {}".format(str(exc)[:200]))
            return
        if not isinstance(spec, dict):
            self.gate_errors.append("assembly.json is not an object")
            return
        try:
            jsonschema.validate(spec, schema)
        except jsonschema.ValidationError as exc:
            self.gate_errors.append("assembly.json violates the schema: {}".format(exc.message[:300]))
            return
        self.tier_spec = spec
        self.spec = to_drawings_names(spec)
        self.gate_errors += C.validate_assembly_dict(self.spec, {}, STRUCTURE)
        if self.gate_errors:
            self.gate_errors = [e.replace("lower_arm", "forearm") for e in self.gate_errors]
            return
        self.chain = C.Chain(self.spec)
        for name, link in self.chain.links.items():
            for pid, rel in link.parts:
                ppath = os.path.normpath(os.path.join(sub_dir, rel))
                if not ppath.startswith(os.path.normpath(sub_dir) + os.sep) or not os.path.isfile(ppath) or os.path.islink(ppath):
                    self.gate_errors.append("part {}: file {} missing (or a symlink / outside the submission)".format(pid, rel))
                    continue
                solids, err = C.load_step_solids(ppath)
                if err:
                    self.gate_errors.append("part {}: {}".format(pid, err))
                    continue
                if len(solids) != 1:
                    self.gate_errors.append("part {}: STEP must contain exactly one solid (has {})".format(pid, len(solids)))
                    continue
                body = C.Body(pid, solids[0])
                if not body.ok:
                    self.gate_errors.append("part {}: {}".format(pid, "; ".join(body.errors)))
                    continue
                for n in body.notes:
                    self.notes.append("{}: {}".format(pid, n))
                self.parts[pid] = body
                self.part_files[pid] = ppath
                link.bodies.append(body)


def load_reference(ref_dir, given):
    """A GT folder (the hidden unit's or a practice unit's): parts/*.step + assembly.json (tier names) + gt.json."""
    sub = TierSubmission(ref_dir, given["schema"])
    if not sub.ok:
        raise RuntimeError("reference folder {} does not load: {}".format(ref_dir, sub.gate_errors[:3]))
    with open(os.path.join(ref_dir, "gt.json")) as f:
        gt = json.load(f)
    box = {j.name: [j.lower, j.upper] for j in sub.chain.joints}
    return {"sub": sub, "gt": gt, "box": box, "dir": ref_dir}


# ----------------------------------------------------------------------------- per-run cases
def make_cases(rng, n_fk=64, n_int=64, n_decl=16, n_mjcf=16, stream_mult=24):
    return {"fk_unit": C.halton(n_fk, 6, shift=rng.uniform(0, 1, 6), skip=50),
            "int_stream": C.halton(stream_mult * n_int, 6, shift=rng.uniform(0, 1, 6)), "n_int": int(n_int), "n_decl": int(n_decl),
            "mjcf_unit": C.halton(n_mjcf, 6, shift=rng.uniform(0, 1, 6), skip=200), "sample_seed": int(rng.integers(2 ** 31 - 1))}


# ----------------------------------------------------------------------------- kinematic quantities of a chain
def horn_points(chain, servo, q=None):
    """World horn centre (the axis anchor) and axis direction of every joint's driving servo."""
    T = chain.fk(np.zeros(len(chain.joints)) if q is None else q)
    out = {}
    for j in chain.joints:
        Tw, _carrier = chain.servo_axis_world(j.driven_by, T)
        p = C.apply(Tw, servo.horn_point)
        d = T[j.child][:3, :3] @ j.axis
        out[j.name] = (p, d / np.linalg.norm(d))
    return out, T


def hd_lengths(chain, servo):
    """The six HD length items (gt_arm key_quantities `hd_lengths` definitions) of a chain at q = 0."""
    ax, T = horn_points(chain, servo)
    p = {k: v[0] for k, v in ax.items()}
    d = {k: v[1] for k, v in ax.items()}
    tcp_w, _a = chain.tcp(np.zeros(len(chain.joints)), T)
    o_roll = T["gripper"][:3, 3]
    dr = d["wrist_roll"]
    return {"shoulder_axis_height": float(p["shoulder_pitch"][2]), "yaw_to_shoulder_offset": abs(float(p["shoulder_pitch"][1])),
            "L2": C.line_dist(p["shoulder_pitch"], d["shoulder_pitch"], p["elbow_pitch"]),
            "L3": C.line_dist(p["elbow_pitch"], d["elbow_pitch"], p["wrist_pitch"]),
            "wrist_pitch_to_roll_mm": float((o_roll - p["wrist_pitch"]) @ dr),
            "roll_joint_to_tcp_mm": float((tcp_w - o_roll) @ dr)}


def _section_loops(mesh, z):
    return FT._loops_in_plane(mesh, (np.array([0.0, 0.0, float(z)]), np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0]),
                                     np.array([0.0, 1.0, 0.0])))


def _poly_centroid(xy):
    x, y = xy[:, 0], xy[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cr = x * yn - xn * y
    a = 0.5 * cr.sum()
    if abs(a) < 1e-12:
        return np.array([x.mean(), y.mean()]), 0.0
    return np.array([((x + xn) * cr).sum() / (6 * a), ((y + yn) * cr).sum() / (6 * a)]), a


def jaw_tip_point(mesh):
    """The T12 construction on a moving-jaw part (jaw link frame): on its gripping face (the +y face of the finger
    section), on the finger's mid-line (the middle of the section's x extent), 10.0 mm below the fingertip (the part's
    highest point at q = 0); the section's outer loops only (gt_arm outputs.finger_frame)."""
    z_tip = float(np.asarray(mesh.vertices)[:, 2].max())
    loops = [l for l in _section_loops(mesh, z_tip - TCP_BACK_MM) if _poly_centroid(l)[1] > 0]
    if not loops:
        raise ValueError("no finger section 10 mm below the tip")
    pts = np.vstack(loops)
    return np.array([0.5 * (pts[:, 0].min() + pts[:, 0].max()), pts[:, 1].max(), z_tip - TCP_BACK_MM])


def outer_shell(mesh):
    """The outer shell of a solid's tessellation: enclosed voids (inner shells) are dropped, so hollowing a part changes
    neither its shape nor its features here (the slicer mass rule counts voids as material too)."""
    try:
        comps = mesh.split(only_watertight=False)
    except Exception:  # noqa: BLE001
        return mesh
    if len(comps) <= 1:
        return mesh
    return max(comps, key=lambda c: abs(float(c.volume)))


def fk_errors(chain_a, chain_g, qs, jaw_a, jaw_g):
    e_tcp, e_jaw = [], []
    for q in qs:
        Ta, Tg = chain_a.fk(q), chain_g.fk(q)
        pa, _ = chain_a.tcp(q, Ta)
        pg, _ = chain_g.tcp(q, Tg)
        e_tcp.append(float(np.linalg.norm(pa - pg)))
        if jaw_a is not None and jaw_g is not None:
            e_jaw.append(float(np.linalg.norm(C.apply(Ta["jaw"], jaw_a) - C.apply(Tg["jaw"], jaw_g))))
    return np.array(e_tcp), (np.array(e_jaw) if e_jaw else None)


# ----------------------------------------------------------------------------- mass (the published slicer rule)
def link_mass_properties(sub, servo):
    """Per link (tier names): mass = outer-shell volume x 520 kg/m3 + 0.060 kg per servo carried; CoM (link frame, mm)."""
    out = {}
    for name, link in sub.chain.links.items():
        m_p = link.solid_volume * 1e-9 * SLICER_DENSITY
        c = link.centroid * m_p
        m = m_p
        for _sid, Tocc in link.occurrences:
            m += SERVO_MASS
            c = c + SERVO_MASS * C.apply(Tocc, servo.com)
        out[tier_link(name)] = {"mass_kg": float(m), "com_mm": (c / m).tolist()}
    return out


# ----------------------------------------------------------------------------- the gates shared by grade / self_check
def _gates(sub_dir, given, cases, log, rewards, stats, details):
    servo, rules = given["servo"], given["rules"]
    sub = TierSubmission(sub_dir, given["schema"])
    stats["part_count"] = float(len(sub.parts))
    if not sub.ok:
        details["cad_valid"] = sub.gate_errors[:12]
        log("[check] cad_valid FAILED: " + " | ".join(sub.gate_errors[:6]))
        return None
    reuse, det, matches, similar = C.check_public_model_reuse(sub, given["blocklist"])
    rewards["public_model_reuse"] = reuse
    details["public_model_reuse"] = det or ["no file matches the digest list"]
    stats["source_geometry_matches"] = float(matches)
    stats["source_geometry_similar"] = float(similar)
    log("[check] public_model_reuse {:.0f}".format(reuse))
    tol = rules["grading_tolerances"]
    gate, det = C.check_cad_json_consistent(sub, servo, tol["axis_angle_deg"], tol["axis_offset_mm"], tol["contact_mm"])
    rewards["cad_json_consistent"] = gate
    details["cad_json_consistent"] = [d.replace("lower_arm", "forearm") for d in det]
    log("[check] cad_json_consistent {:.0f}".format(gate))
    fit, det, retention = C.check_component_fit(sub, servo, float(rules["servo_mounting"]["keep_out_volume_ratio"]))
    stats["min_retention"] = float(min(retention.values())) if retention else 0.0
    unmounted = sorted(s for s, r in retention.items() if r < C.MOUNTED_MIN_RETENTION)
    rewards["servos_mounted"] = 0.0 if (unmounted or not retention) else 1.0
    details["servos_mounted"] = ["retention per servo (gate at {:.2f}): {}{}".format(C.MOUNTED_MIN_RETENTION, ", ".join(
        "{} {:.2f}".format(s, retention[s]) for s in sorted(retention)), "; NOT MOUNTED: {}".format(unmounted) if unmounted else "")]
    details["component_fit"] = [d.replace("lower_arm", "forearm") for d in det]
    stats["component_fit_raw"] = float(fit)
    log("[check] servos_mounted {:.0f} (min retention {:.2f}); component fit {:.3f}".format(rewards["servos_mounted"], stats["min_retention"], fit))
    with tempfile.TemporaryDirectory(prefix="arm_mjcf_") as work:
        down, det = C.check_downstream(sub, servo, SLICER_DENSITY, cases["mjcf_unit"], work, given["facts"])
    details["cad_valid"] = ["{} parts load as single valid watertight solids; assembly.json valid".format(len(sub.parts))] + sub.notes[:4] + det
    rewards["cad_valid"] = 1.0 if down >= 1.0 else 0.0
    stats["mjcf_ok"] = float(down)
    log("[check] cad_valid {:.0f} ({})".format(rewards["cad_valid"], " | ".join(det)))
    return sub, fit


# ----------------------------------------------------------------------------- measurement: item lists
# Every criterion that measures the photographed unit (kinematic, feature, shape, mass) is a weighted mean of ITEMS
# [(item_id, weight, score)], score = the item's band in [0, 1]. The plain criterion is sum(w s) / sum(w) (the v1 formula,
# unchanged). Template-relative credit (grader 2.0.0, DESIGN.md D19): each item is also measured for the practice
# templates' own CAD (every template's parts; for the chain items every per-link combination of the templates' links),
# t = the best of those scores; relative = sum(w max(0, s - t)) / sum(w (1 - t)); the criterion = min(plain, relative).
def _chain_points(chain, qs, jaw_local):
    tcp, jaw = [], []
    for q in qs:
        T = chain.fk(q)
        tcp.append(chain.tcp(q, T)[0])
        if jaw_local is not None:
            jaw.append(C.apply(T["jaw"], jaw_local))
    return np.asarray(tcp), (np.asarray(jaw) if jaw_local is not None else None)


def _unit_cache(cg, gs, servo, qs):
    """The unit's own kinematic quantities that every compared chain is measured against."""
    jaw_g = None
    try:
        jaw_g = jaw_tip_point(gs.parts["P10"].mesh)
    except Exception:  # noqa: BLE001
        jaw_g = None
    hg, T0g = horn_points(cg, servo)
    tcp, jaw = _chain_points(cg, qs, jaw_g)
    return {"horn": hg, "len": hd_lengths(cg, servo), "tcp": tcp, "jaw": jaw, "jaw_local": jaw_g, "T0": T0g}


def _kin_items(ca, U, servo, B, W, box, qs, jaw_a):
    """kinematic_fidelity items of chain `ca` against the unit cache `U`; returns (items, measured values)."""
    kw, fkw = W["kinematic"], W["fk"]
    ha, _ = horn_points(ca, servo)
    items, m = [], {"axis": {}, "angle": {}, "length": {}, "range": {}}
    for j in JOINTS:
        e = float(np.linalg.norm(ha[j][0] - U["horn"][j][0]))
        ang = C.angle_between(ha[j][1], U["horn"][j][1])
        m["axis"][j], m["angle"][j] = e, min(ang, 180.0 - ang)
        items.append(("axis:" + j, kw["axes"] / len(JOINTS), band(e, B["axis_mm"])))
    la = hd_lengths(ca, servo)
    for k in LENGTH_KEYS:
        e = la[k] - U["len"][k]
        m["length"][k] = float(e)
        items.append(("length:" + k, kw["lengths"] / len(LENGTH_KEYS), band(e, B["length_mm"])))
    tcp, jaw = _chain_points(ca, qs, jaw_a if U["jaw"] is not None else None)
    e_tcp = np.linalg.norm(tcp - U["tcp"], axis=1)
    med, p90 = float(np.median(e_tcp)), float(np.percentile(e_tcp, 90))
    jaw_med = float(np.median(np.linalg.norm(jaw - U["jaw"], axis=1))) if jaw is not None else float("inf")
    m.update(fk_median=med, fk_p90=p90, jaw_median=jaw_med)
    sfk = sum(fkw.values())
    items += [("fk:median", kw["fk"] * fkw["median"] / sfk, band(med, B["fk_median_mm"])),
              ("fk:p90", kw["fk"] * fkw["p90"] / sfk, band(p90, B["fk_p90_mm"])),
              ("fk:jaw_tip", kw["fk"] * fkw["jaw_tip"] / sfk, band(jaw_med, B["jaw_tip_median_mm"]) if math.isfinite(jaw_med) else 0.0)]
    for j in ARM_JOINTS:
        jd = ca.joint(j)
        inter = max(0.0, min(jd.upper, box[j][1]) - max(jd.lower, box[j][0]))
        cov = inter / max(1e-9, box[j][1] - box[j][0])
        m["range"][j] = cov
        items.append(("range:" + j, kw["range"] / len(ARM_JOINTS), cov))
    return items, m


def _mass_items(mp, T0, mpg, T0g, B):
    """mass_properties items: per link the slicer-rule mass (relative error) and the CoM (arm frame at q = 0)."""
    items, m = [], {}
    for ln in TIER_LINKS:
        ld = TO_DRAWINGS.get(ln, ln)
        rel = (mp[ln]["mass_kg"] - mpg[ln]["mass_kg"]) / mpg[ln]["mass_kg"]
        dc = float(np.linalg.norm(C.apply(T0[ld], np.asarray(mp[ln]["com_mm"])) - C.apply(T0g[ld], np.asarray(mpg[ln]["com_mm"]))))
        m[ln] = (float(rel), dc)
        items += [("mass:" + ln, 0.5 / len(TIER_LINKS), band(rel, B["mass_rel"])), ("com:" + ln, 0.5 / len(TIER_LINKS), band(dc, B["com_mm"]))]
    return items, m


def _part_items(sub, gs, gt, B, W, seed):
    """Every part aligned onto the unit's (6.4), then the shape and feature items. Returns (shape_items, feature_items,
    measured values incl. the IoUs of the shape floor, feature details)."""
    import trimesh  # noqa: F401
    from scipy.spatial import cKDTree
    cg = gs.chain
    T0a = sub.chain.fk(np.zeros(len(sub.chain.joints)))
    T0g = cg.fk(np.zeros(len(cg.joints)))
    sw, fw = W["shape"], W["feature"]
    ssw = sum(sw.values())
    aligned, shape_items, m = {}, [], {"iou": {}, "surf": {}, "surf_mean": {}, "align": {}, "extent": {}, "cut": [], "floor": {}}
    for k, pid in enumerate(PART_IDS):
        link_d = TO_DRAWINGS.get(LINK_OF_PART[pid], LINK_OF_PART[pid])
        ma = outer_shell(sub.parts[pid].mesh).copy()
        mg = gs.parts[pid].mesh.copy()
        ma.apply_transform(T0a[link_d])
        mg.apply_transform(T0g[link_d])
        al = A.align_part(ma, mg, seed + 101 * k)
        Tl = np.linalg.inv(T0g[link_d]) @ al["T"] @ T0a[link_d]          # submitted link frame -> GT link frame
        m_al = outer_shell(sub.parts[pid].mesh).copy()
        m_al.apply_transform(Tl)
        aligned[pid] = m_al
        m["align"][pid] = (al["rms_mm"], al["offset_mm"], al["rotation_deg"], 1.0 if al["multistart"] else 0.0)
        mg_l = gs.parts[pid].mesh
        pa, _n = A.sample_surface(m_al, A.N_SRC, seed + 7 * k + 3)
        pg, _n = A.sample_surface(mg_l, A.N_SRC, seed + 7 * k + 4)
        dense_a, dense_an = A.sample_surface(m_al, A.N_DST, seed + 7 * k + 5)
        Ti = np.linalg.inv(T0g[link_d])
        dense_g = al["gt_pts"] @ Ti[:3, :3].T + Ti[:3, 3]
        dense_gn = al["gt_normals"] @ Ti[:3, :3].T
        d1 = np.sort(A.surface_distances(pa, dense_g, dense_gn, cKDTree(dense_g)))
        d2 = np.sort(A.surface_distances(pg, dense_a, dense_an, cKDTree(dense_a)))
        tm = lambda d: float(np.mean(d[: max(1, int(0.95 * len(d)))]))  # noqa: E731
        m["surf_mean"][pid] = 0.5 * (tm(d1) + tm(d2))
        sd = float(np.percentile(np.concatenate([d1, d2]), 90))       # symmetric 90th percentile (local misfit counts)
        man_a = C.trimesh_to_manifold(m_al)
        man_g = gs.parts[pid].manifold
        vi = (man_a ^ man_g).volume()
        vu = man_a.volume() + man_g.volume() - vi
        iou = float(vi / vu) if vu > 0 else 0.0
        m["iou"][pid], m["surf"][pid] = iou, sd
        shape_items += [("surface:" + pid, sw["surface"] / (ssw * len(PART_IDS)), band(sd, B["surface_mm"])),
                        ("iou:" + pid, sw["iou"] / (ssw * len(PART_IDS)), band(1.0 - iou, B["one_minus_iou"]))]
    feat_items, fdet = [], []
    feats = [f for f in gt.get("features", []) if f.get("placed", True)]
    for n, f in enumerate(feats):
        r = FT.cutout_item(aligned[f["part"]], gs.parts[f["part"]].mesh, f)
        tag = "{}_{}".format(f["kind"].replace("F-", ""), f["part"])
        m["cut"].append((tag, r))
        feat_items.append(("cutout:{}:{}".format(n + 1, tag), fw["cutouts"] / len(feats), r["score"]))
        fdet.append("{} {}: present {:.0f} (overlap {:.2f}), offset {} mm, area error {} -> {:.2f}".format(
            f["kind"], f["part"], r["present"], r["overlap"], r["offset_mm"], r["area_error"], r["score"]))
    if not feats:
        feat_items.append(("cutout:none", fw["cutouts"], 1.0))
    for sid, pid in POCKETS:
        _lk, Tocc = cg.occurrence(sid)
        dg = FT.pocket_floor_mm(gs.parts[pid].mesh, Tocc)
        da = FT.pocket_floor_mm(aligned[pid], Tocc)
        m["floor"][sid] = float(da - dg)
        feat_items.append(("pocket_floor:" + sid, fw["pocket_floors"] / len(POCKETS), band(da - dg, B["pocket_floor_mm"])))
        fdet.append("{} pocket floor ({}): {:+.2f} mm".format(sid, pid, da - dg))
    for pid in PART_IDS:
        ea = aligned[pid].bounds[1] - aligned[pid].bounds[0]
        eg = gs.parts[pid].mesh.bounds[1] - gs.parts[pid].mesh.bounds[0]
        err = np.abs(ea - eg)
        m["extent"][pid] = float(err.max())
        feat_items += [("extent:{}:{}".format(pid, "xyz"[i]), fw["extents"] / (3 * len(PART_IDS)), band(err[i], B["extent_mm"])) for i in range(3)]
    hole_a = C.base_bottom_holes(sub.chain.links["base"])
    hole_g = C.base_bottom_holes(cg.links["base"])
    oe = FT.openings_error(hole_a, hole_g, B["opening_mm"][1])
    open_item = 1.0 if oe is None else band(oe, B["opening_mm"])
    m["openings"] = (len(hole_g), len(hole_a), oe)
    feat_items.append(("openings", fw["openings"], open_item))
    fdet.append("base openings: {} GT, {} submitted, mean matched error {} -> {:.2f}".format(len(hole_g), len(hole_a), oe, open_item))
    return shape_items, feat_items, m, fdet


def _groups(items):
    """Group means of an item list (the v1 sub-criterion values: axes, lengths, fk, range; cutouts, ...)."""
    acc = {}
    for iid, w, s in items:
        g = iid.split(":")[0]
        a = acc.setdefault(g, [0.0, 0.0])
        a[0] += w * s
        a[1] += w
    return {g: (a[0] / a[1] if a[1] > 0 else 0.0) for g, a in acc.items()}


def load_templates(dirs, given):
    """[(name, TierSubmission)] of practice-template GT folders (parts/*.step + assembly.json): the product-line CAD the
    agent is given. `dirs` = [(name, path)]."""
    out = []
    for name, d in dirs:
        t = TierSubmission(d, given["schema"])
        if not t.ok:
            raise RuntimeError("template folder {} does not load: {}".format(d, t.gate_errors[:3]))
        out.append((name, t))
    return out


def _mixed_chain(specs, choice):
    """The chain of a per-link combination of the templates' links: link `L` (its servo occurrences), the joint whose
    parent is `L` (its origin, axis and limits) and, for the gripper, the tool point come from template choice[L]."""
    first = specs[0]
    by = [{l["name"]: l for l in s["links"]} for s in specs]
    links = [by[choice[l["name"]]][l["name"]] for l in first["links"]]
    joints = []
    for j0 in first["joints"]:
        src = specs[choice[j0["parent_link"]]]
        joints.append(next(j for j in src["joints"] if j["name"] == j0["name"]))
    spec = {"links": links, "joints": joints, "root_link": first["root_link"], "tcp": specs[choice[first["tcp"]["link"]]]["tcp"]}
    return C.Chain(spec)


def _combo_mass(bodies, occ, servo):
    """The slicer-rule mass and link-frame CoM of a link made of `bodies` (possibly from different templates, each in
    its template's frame of this link) carrying the servo occurrences `occ` (link_mass_properties' formula)."""
    vs = sum(b.solid_volume for b in bodies)
    v = sum(b.volume for b in bodies)
    cen = sum(b.centroid * b.volume for b in bodies) / max(v, 1e-9)
    m = vs * 1e-9 * SLICER_DENSITY
    c = cen * m
    for _sid, Tocc in occ:
        m += SERVO_MASS
        c = c + SERVO_MASS * C.apply(Tocc, servo.com)
    return m, c / m


def template_baseline(templates, gs, gt, servo, B, W, box, qs, U, mpg, T0g, seed):
    """{criterion: {item_id: t}}: per item the best score any combination of the practice templates' CAD earns against
    the unit. Part items (shape, feature): every template's parts. Chain items (kinematics): every per-link combination
    of the templates' links (2^7 chains for two templates). Link items (mass, CoM): every per-part combination inside the
    link (the base, shoulder and forearm links have two parts), the CoM placed by every chain combination with the
    link's servo occurrences from its chain template. Also the per-template plain criteria (stats)."""
    import itertools
    best = {c: {} for c in RELATIVE_CRITERIA}
    per_template = {}

    def take(crit, items):
        d = best[crit]
        for iid, _w, s in items:
            d[iid] = max(d.get(iid, 0.0), float(s))
    for name, t in templates:
        sh, fe, _m, _d = _part_items(t, gs, gt, B, W, seed)
        take("shape_fidelity", sh)
        take("feature_fidelity", fe)
        per_template[name] = {"shape_fidelity": _plain(sh), "feature_fidelity": _plain(fe)}
    nt = len(templates)
    specs = [t.spec for _n, t in templates]
    jaws = []
    for _n, t in templates:
        try:
            jaws.append(jaw_tip_point(t.parts["P10"].mesh))
        except Exception:  # noqa: BLE001
            jaws.append(None)
    # link items: the per-part combinations of every link (bodies in the link frame), servo occurrences per template
    combos = {}
    for ln in TIER_LINKS:
        ld = TO_DRAWINGS.get(ln, ln)
        pids = [pid for pid in PART_IDS if LINK_OF_PART[pid] == ln]
        occs = [t.chain.links[ld].occurrences for _n, t in templates]
        combos[ln] = [([templates[k][1].parts[pid] for k, pid in zip(ks, pids)], ks) for ks in itertools.product(range(nt), repeat=len(pids))]
        m_best = 0.0
        for bodies, _ks in combos[ln]:
            m, _c = _combo_mass(bodies, occs[0], servo)
            m_best = max(m_best, band((m - mpg[ln]["mass_kg"]) / mpg[ln]["mass_kg"], B["mass_rel"]))
        best["mass_properties"]["mass:" + ln] = m_best
        combos[ln] = (combos[ln], occs)
    order = list(templates[0][1].chain.order)
    for combo in itertools.product(range(nt), repeat=len(order)):
        choice = dict(zip(order, combo))
        ch = _mixed_chain(specs, choice)
        ki, _km = _kin_items(ch, U, servo, B, W, box, qs, jaws[choice["jaw"]])
        take("kinematic_fidelity", ki)
        T0 = ch.fk(np.zeros(len(ch.joints)))
        for ln in TIER_LINKS:
            ld = TO_DRAWINGS.get(ln, ln)
            cl, occs = combos[ln]
            cg_w = C.apply(T0g[ld], np.asarray(mpg[ln]["com_mm"]))
            sc = 0.0
            for bodies, _ks in cl:
                _m, c = _combo_mass(bodies, occs[choice[ld]], servo)
                sc = max(sc, band(float(np.linalg.norm(C.apply(T0[ld], c) - cg_w)), B["com_mm"]))
            iid = "com:" + ln
            best["mass_properties"][iid] = max(best["mass_properties"].get(iid, 0.0), sc)
        if len(set(combo)) == 1:
            per_template[templates[combo[0]][0]].update(kinematic_fidelity=_plain(ki))
    return best, per_template


def _plain(items):
    sw = sum(w for _i, w, _s in items)
    return sum(w * s for _i, w, s in items) / sw if sw > 0 else 0.0


def relative_score(items, tbest):
    """(criterion, plain, relative, template_share): relative = sum(w max(0, s - t)) / sum(w (1 - t)); the criterion is
    min(plain, relative); template_share = the share of the criterion's weight a template copy already earns."""
    plain = _plain(items)
    if tbest is None:
        return plain, plain, None, None
    sw = sum(w for _i, w, _s in items)
    avail = sum(w * (1.0 - tbest.get(i, 0.0)) for i, w, _s in items)
    cred = sum(w * max(0.0, s - tbest.get(i, 0.0)) for i, w, s in items)
    rel = cred / avail if avail > 1e-9 else plain
    return min(plain, rel), plain, rel, 1.0 - avail / sw


# ----------------------------------------------------------------------------- the hidden / practice grade
def grade(sub_dir, given, ref, cases, log=print, templates=None):
    """Grade a submission against a GT folder `ref`. `templates` = load_templates(...) of the practice units the agent
    was given (verify: P1 and P2; the practice checker: the other practice unit); None = plain scores only (v1)."""
    rules = given["rules"]
    servo = given["servo"]
    B = _bands(rules)
    W = _subweights(rules)
    rewards = {k: 0.0 for k in GATES + CRITERIA}
    stats, details = {"grader_version_" + GRADER_VERSION.split("/")[1].replace(".", "_"): 1.0}, {}
    res = _gates(sub_dir, given, cases, log, rewards, stats, details)
    if res is None:
        return rewards, stats, details
    sub, fit = res
    gs = ref["sub"]
    gt = ref["gt"]
    box = ref["box"]
    ca, cg = sub.chain, gs.chain
    lo = np.array([box[j][0] for j in JOINTS])
    hi = np.array([box[j][1] for j in JOINTS])
    qs = lo + np.asarray(cases["fk_unit"]) * (hi - lo)
    U = _unit_cache(cg, gs, servo, qs)
    seed = int(cases["sample_seed"])
    # ---- kinematic fidelity
    jaw_a = None
    try:
        jaw_a = jaw_tip_point(sub.parts["P10"].mesh)
    except Exception as exc:  # noqa: BLE001 - a jaw part without a finger section: the jaw item is 0
        details.setdefault("kinematic_fidelity", []).append("jaw tip construction failed on the submitted P10: {}".format(str(exc)[:120]))
    if U["jaw_local"] is None:
        jaw_a = None
    kin_items, km = _kin_items(ca, U, servo, B, W, box, qs, jaw_a)
    for j in JOINTS:
        stats["axis_err_mm_" + j] = km["axis"][j]
        stats["axis_angle_deg_" + j] = km["angle"][j]
    for k in LENGTH_KEYS:
        stats["length_err_mm_" + k] = km["length"][k]
    med, p90, jaw_med = km["fk_median"], km["fk_p90"], km["jaw_median"]
    stats.update({"fk_tcp_median_mm": med, "fk_tcp_p90_mm": p90, "fk_jaw_tip_median_mm": jaw_med if math.isfinite(jaw_med) else -1.0})
    kg = _groups(kin_items)
    for k, v in (("axes", kg["axis"]), ("lengths", kg["length"]), ("fk", kg["fk"]), ("range", kg["range"])):
        stats["kinematic_" + k] = v
    details.setdefault("kinematic_fidelity", []).insert(0, "axes {:.3f}, lengths {:.3f}, FK {:.3f} (tool median {:.2f} mm, p90 {:.2f} mm, "
                                                        "jaw tip median {:.2f} mm), range coverage {:.3f}".format(
                                                            kg["axis"], kg["length"], kg["fk"], med, p90, jaw_med, kg["range"]))
    # ---- per-part alignment: shape and feature fidelity
    shape_items, feat_items, pm, fdet = _part_items(sub, gs, gt, B, W, seed)
    ious, surf = pm["iou"], pm["surf"]
    for pid in PART_IDS:
        r_, o_, a_, ms_ = pm["align"][pid]
        stats["align_rms_mm_" + pid], stats["align_offset_mm_" + pid], stats["align_rot_deg_" + pid] = r_, o_, a_
        stats["align_multistart_" + pid] = ms_
        stats["shape_surface_mean_mm_" + pid] = pm["surf_mean"][pid]
        stats["shape_surface_mm_" + pid] = surf[pid]
        stats["shape_iou_" + pid] = ious[pid]
        stats["extent_err_mm_" + pid] = pm["extent"][pid]
    for n, (tag, r) in enumerate(pm["cut"]):
        stats["cutout_{}_{}".format(tag, n + 1)] = r["score"]
    for sid in pm["floor"]:
        stats["pocket_floor_err_mm_" + sid] = pm["floor"][sid]
    stats["base_openings_gt"], stats["base_openings_agent"] = float(pm["openings"][0]), float(pm["openings"][1])
    fl_lo, fl_hi = B["floor_iou"]
    F_s = float(np.mean([min(1.0, max(0.0, (ious[p] - fl_lo) / (fl_hi - fl_lo))) for p in PART_IDS]))
    F_k = band(med, B["floor_fk_mm"])
    stats.update({"floor_shape": F_s, "floor_kinematic": F_k, "iou_mean": float(np.mean(list(ious.values()))),
                  "surface_mm_mean": float(np.mean(list(surf.values())))})
    details["shape_fidelity"] = ["per part surface distance mm / IoU: " + ", ".join(
        "{} {:.2f}/{:.3f}".format(p, surf[p], ious[p]) for p in PART_IDS)]
    fg = _groups(feat_items)
    feat_parts = {"cutouts": fg["cutout"], "pocket_floors": fg["pocket_floor"], "extents": fg["extent"], "openings": fg["openings"]}
    for k, v in feat_parts.items():
        stats["feature_" + k] = v
    details["feature_fidelity"] = ["cut-outs {cutouts:.3f}, pocket floors {pocket_floors:.3f}, extents {extents:.3f}, "
                                   "base openings {openings:.3f}".format(**feat_parts)] + fdet
    # ---- mass properties (CoM compared in the arm frame at q = 0: independent of where along its axis a link frame sits)
    mpa, mpg = link_mass_properties(sub, servo), link_mass_properties(gs, servo)
    mass_items, mm = _mass_items(mpa, ca.fk(np.zeros(len(ca.joints))), mpg, U["T0"], B)
    for ln in TIER_LINKS:
        stats["mass_rel_err_" + ln], stats["com_err_mm_" + ln] = mm[ln]
    stats["moving_mass_kg"] = float(sum(mpa[ln]["mass_kg"] for ln in TIER_LINKS[1:]))
    details["mass_properties"] = ["per link mass rel. error / CoM error mm: " + ", ".join(
        "{} {:+.3f}/{:.1f}".format(ln, mm[ln][0], mm[ln][1]) for ln in TIER_LINKS)]
    # ---- template-relative credit (the practice units' own CAD as the baseline)
    items = {"kinematic_fidelity": kin_items, "feature_fidelity": feat_items, "shape_fidelity": shape_items, "mass_properties": mass_items}
    tbest = None
    if templates:
        tbest, per_t = template_baseline(templates, gs, gt, servo, B, W, box, qs, U, mpg, U["T0"], seed)
        stats["templates_used"] = float(len(templates))
        for name, d in per_t.items():
            for c, v in d.items():
                stats["template_plain_{}_{}".format(name, c)] = float(v)
    crit = {}
    for c in RELATIVE_CRITERIA:
        val, plain, rel, share = relative_score(items[c], tbest[c] if tbest else None)
        crit[c] = val
        stats["plain_" + c] = plain
        if rel is not None:
            stats["relative_" + c] = rel
            stats["template_share_" + c] = share
            stats["relative_binding_" + c] = 1.0 if rel < plain else 0.0
            details.setdefault(c, []).insert(0, "plain {:.3f}, relative to the practice templates {:.3f} (a copy of the templates "
                                                "already earns {:.0%} of this criterion's weight) -> {:.3f}".format(plain, rel, share, val))
    kin, feat, shape, mass = crit["kinematic_fidelity"], crit["feature_fidelity"], crit["shape_fidelity"], crit["mass_properties"]
    log("[check] kinematic {:.3f}: {}".format(kin, details["kinematic_fidelity"][0]))
    log("[check] shape {:.3f} (mean IoU {:.3f}, mean surface distance {:.2f} mm); floors F_s {:.3f}, F_k {:.3f}".format(
        shape, stats["iou_mean"], stats["surface_mm_mean"], F_s, F_k))
    log("[check] feature {:.3f}: {}".format(feat, details["feature_fidelity"][0]))
    # ---- interference over the GT operating box (+ the over-declared share)
    inter, idet = _interference(sub, servo, rules, cases, {C.JOINT_ORDER[i]: box[C.JOINT_ORDER[i]] for i in range(6)}, stats)
    details["interference_free"] = idet
    # ---- combine: gates x floors
    gate = 1.0
    for k in GATES:
        gate *= rewards[k]
    F = F_s * F_k
    raw = {"kinematic_fidelity": kin * F_s, "feature_fidelity": feat * F_s, "shape_fidelity": shape, "mass_properties": mass * F,
           "component_fit": fit * F, "interference_free": inter * F}
    for k in CRITERIA:
        stats["raw_" + k] = {"kinematic_fidelity": kin, "feature_fidelity": feat, "shape_fidelity": shape, "mass_properties": mass,
                             "component_fit": fit, "interference_free": inter}[k]
        rewards[k] = float(max(0.0, min(1.0, gate * raw[k])))
    errs = list(km["axis"].values()) + [abs(v) for v in km["length"].values()]
    stats["layout_max_error_mm"] = float(max(errs))
    stats["layout_min_error_mm"] = float(min(errs))
    return rewards, stats, details


def _interference(sub, servo, rules, cases, ranges, stats):
    configs, examined, skipped = C.operating_configs(sub, servo, cases["int_stream"], int(cases["n_int"]), ranges)
    extra, _ex, inside_share = C.declared_range_configs(sub, servo, cases["int_stream"], int(cases["n_decl"]), ranges)
    stats["operating_configs"] = float(len(configs))
    stats["configs_outside_declared_limits"] = float(skipped)
    stats["declared_range_configs"] = float(len(extra))
    stats["declared_inside_share"] = float(inside_share)
    adj = float(rules["clearances"]["adjacent_ratio"])
    non = float(rules["clearances"]["nonadjacent_ratio"])
    if len(configs) == 0:
        return 0.0, ["no configuration of the operating box lies inside the declared limits above the mounting plane"]
    inter, det, _ = C.check_interference(sub, servo, configs, adj, non)
    note = ""
    if len(extra):
        ix, dx, _ = C.check_interference(sub, servo, extra, adj, non)
        w_out = 1.0 - inside_share
        inter = (1.0 - w_out) * inter + w_out * ix
        note = "; declared limits beyond the operating box ({:.0%} of the declared box): {} -> {:.3f}".format(w_out, dx[0], ix)
    return float(inter), [(det[0] + note).replace("lower_arm", "forearm")]


# ----------------------------------------------------------------------------- the public-rule self check
def self_check(sub_dir, given, cases, log=print):
    """Public rules only: no ground truth is read or estimated. The gates (validity + MJCF, reuse list, consistency, servo
    retention), the servo fit, self-collision over the submission's OWN declared box, the published mass rule."""
    rewards = {k: 0.0 for k in GATES + CRITERIA}
    stats, details = {}, {}
    res = _gates(sub_dir, given, cases, log, rewards, stats, details)
    if res is None:
        return rewards, stats, details
    sub, fit = res
    rewards["component_fit"] = fit
    own = {j.name: [j.lower, j.upper] for j in sub.chain.joints}
    inter, idet = _interference(sub, given["servo"], given["rules"], cases, own, stats)
    rewards["interference_free"] = inter
    details["interference_free"] = idet
    mp = link_mass_properties(sub, given["servo"])
    stats["moving_mass_kg"] = float(sum(mp[ln]["mass_kg"] for ln in TIER_LINKS[1:]))
    details["mass_properties"] = ["published rule (outer shell x 520 kg/m3 + 0.060 kg per servo): " + ", ".join(
        "{} {:.4f} kg".format(ln, mp[ln]["mass_kg"]) for ln in TIER_LINKS)]
    lens = hd_lengths(sub.chain, given["servo"])
    details["kinematic_fidelity"] = ["your chain at q = 0 (no comparison): " + ", ".join("{} {:.2f} mm".format(k, v) for k, v in lens.items())]
    return rewards, stats, details
