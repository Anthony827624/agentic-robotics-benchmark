"""rgrading: strict params.json parser and scoring for robocasa_object_dynamics_from_video (hidden grader).

params.json (numbers only):
{"objects": {"A": {"com": [x, y, z], "inertia": [[Ixx, Ixy, Ixz], [Ixy, Iyy, Iyz], [Ixz, Iyz, Izz]], "mu": m},
             "B": {...}, "C": {...}}}
World frame, SI units, at each object's reference pose (rest pose in frame 0 of clip00).
"""
import json
import math
import time

import numpy as np

import rphys as W
import rtasks as T

MAX_BYTES = 64 * 1024
OBJECTS = ("A", "B", "C")
FAMS = ("pin", "tip", "rock", "push")
MU_RANGE = (0.1, 1.2)


class InvalidSubmission(ValueError):
    pass


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise InvalidSubmission("duplicate key %r" % (k,))
        out[k] = v
    return out


def _reject_constant(name):
    raise InvalidSubmission("non-finite number %s" % name)


def _num(v, where):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise InvalidSubmission("%s must be a number" % where)
    try:
        f = float(v)
    except (OverflowError, ValueError):
        raise InvalidSubmission("%s is not a usable number" % where)
    if not math.isfinite(f):
        raise InvalidSubmission("%s must be finite" % where)
    return f


def parse_submission_bytes(data):
    """Returns (entries, invalid): entries[k] = dict(com, inertia(3x3), mu) for valid objects; invalid[k] = reason."""
    if data is None or len(data) > MAX_BYTES:
        raise InvalidSubmission("missing or larger than %d bytes" % MAX_BYTES)
    try:
        text = data.decode("utf-8")
        doc = json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_reject_constant)
    except InvalidSubmission:
        raise
    except (ValueError, RecursionError, MemoryError, UnicodeDecodeError) as exc:
        raise InvalidSubmission("not valid JSON (%s)" % type(exc).__name__)
    if not isinstance(doc, dict) or not isinstance(doc.get("objects"), dict):
        raise InvalidSubmission("top level must be an object with an 'objects' object")
    entries, invalid = {}, {}
    for k in OBJECTS:
        e = doc["objects"].get(k)
        try:
            if not isinstance(e, dict):
                raise InvalidSubmission("missing")
            com = e.get("com")
            if not isinstance(com, list) or len(com) != 3:
                raise InvalidSubmission("com must be a list of 3 numbers")
            com = [_num(x, "com") for x in com]
            I = e.get("inertia")
            if not isinstance(I, list) or len(I) != 3 or not all(isinstance(r, list) and len(r) == 3 for r in I):
                raise InvalidSubmission("inertia must be a 3x3 list")
            I = [[_num(x, "inertia") for x in r] for r in I]
            Ia = np.array(I)
            if np.max(np.abs(Ia - Ia.T)) > 1e-6 * max(1e-9, np.max(np.abs(Ia))):
                raise InvalidSubmission("inertia must be symmetric")
            mu = _num(e.get("mu"), "mu")
            entries[k] = dict(com=np.array(com), inertia=0.5 * (Ia + Ia.T), mu=mu)
        except InvalidSubmission as exc:
            invalid[k] = str(exc)
    return entries, invalid


def valid_inertia(I):
    ev = np.linalg.eigvalsh(I)
    if not np.all(np.isfinite(ev)) or ev.min() <= 0:
        return False
    a, b, c = sorted(ev)
    return a + b >= c * (1 - 1e-6)


def to_body(entry, pose):
    R = T.quat_to_mat(pose[3:])
    c = R.T @ (np.asarray(entry["com"], float) - np.asarray(pose[:3], float))
    Ib = R.T @ np.asarray(entry["inertia"], float) @ R
    return c, 0.5 * (Ib + Ib.T)


def to_world(com_b, I_b, mu, pose):
    R = T.quat_to_mat(pose[3:])
    return dict(com=(R @ np.asarray(com_b) + np.asarray(pose[:3])).tolist(), inertia=(R @ np.asarray(I_b) @ R.T).tolist(),
                mu=float(mu))


def load_ref(path):
    with open(path) as f:
        ref = json.load(f)
    out = {}
    for k, o in ref["objects"].items():
        geom = W.Geometry([np.array(p) for p in o["pieces"]])
        gt = dict(mass=o["mass"], com=np.array(o["com"]), I=np.array(o["inertia"]), mu=o["mu"])
        out[k] = dict(geom=geom, gt=gt, ref_pose=np.array(o["ref_pose"]), tests=o["tests"])
    return out


def score_object(ref_o, entry, deadline=None):
    """Returns (score, info). Any test not run before the deadline scores 0."""
    geom, gt = ref_o["geom"], ref_o["gt"]
    c, Ib = to_body(entry, ref_o["ref_pose"])
    if not (np.all(np.isfinite(c)) and np.all(np.isfinite(Ib))):
        return 0.0, {"invalid": "non-finite after transform"}
    if not valid_inertia(Ib):
        return 0.0, {"invalid": "inertia not positive definite or violates the triangle inequality"}
    lo, hi = geom.allv.min(0) - 0.01, geom.allv.max(0) + 0.01
    c = np.clip(c, lo, hi)
    mu = float(np.clip(entry["mu"], *MU_RANGE))
    sub = dict(mass=gt["mass"], com=c, I=Ib, mu=mu)
    gw = T.World(geom, gt)
    try:                # MuJoCo's own inertia checks are the last word (exact triangle inequality, mjMINVAL)
        sw = T.World(geom, sub)
    except Exception as exc:
        return 0.0, {"invalid": "the submitted inertial does not compile in MuJoCo (%s)" % str(exc).splitlines()[0][:120]}
    fam_q = {f: [] for f in FAMS}
    unscored = 0
    for t in ref_o["tests"]:
        if deadline is not None and time.time() > deadline:
            fam_q[t["fam"]].append(0.0)
            unscored += 1
            continue
        try:
            if t["fam"] == "pin":
                q, _ = T.test_pin(geom, gt, sub, t["yaw"])
            elif t["fam"] == "tip":
                q, _ = T.test_tip(sw, gw, t["xy"], t["yaw"], t["alpha"], t["hfrac"], t["gt_boundary"])
            elif t["fam"] == "rock":
                q, _ = T.test_rock(sw, gw, t["xy"], t["yaw"], t["alpha"], t["hfrac"], t["gt_boundary"])
            else:
                q, _ = T.test_push(sw, gw, t["xy"], t["yaw"], t["alpha"], t["h"], t["e"], t["d_target"])
            q = float(q) if math.isfinite(q) else 0.0
        except Exception:  # a submitted model that breaks the simulation scores 0 on that test
            q = 0.0
        fam_q[t["fam"]].append(q)
    fm = {f: float(np.mean(v)) for f, v in fam_q.items() if v}
    return float(np.mean(list(fm.values()))), dict(families=fm, unscored=unscored)


def score_submission(entries, ref, deadline=None):
    per = {}
    for k in OBJECTS:
        if k in entries:
            per[k] = score_object(ref[k], entries[k], deadline)
        else:
            per[k] = (0.0, {"invalid": "missing or invalid"})
    return float(np.mean([per[k][0] for k in OBJECTS])), per
