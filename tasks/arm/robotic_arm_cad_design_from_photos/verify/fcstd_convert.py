"""Turn the extracted FreeCAD document (fcstd_extract.py) into the tier submission the cad-photo grader reads.

The agent is given no topology, so the grader infers it from the document (DESIGN.md section 5):

  parts      leaves labelled P01 ... P10 (the numbers on the photographed paper tags), one valid solid each;
  servos     leaves whose shape is the given servo STEP (volume and extents), six of them;
  links      groups of components held together by Fixed joints (and the Grounded joint), joined by Revolute joints;
             they must form one serial chain of six revolute joints from the grounded link, and the chain's links must
             carry the photographed unit's part sets in order (the mechanism; a different mechanism fails `cad_valid`);
  servo      each revolute joint is driven by the servo of its parent link whose horn axis lies closest to the joint
             axis; the joint's positive sense is that servo's +z (its datasheet's positive rotation);
  frame      the grounded link is brought onto the unit's base: the base-yaw axis onto the arm frame's z axis, the
             base's lowest point onto the mounting plane, the rotation about z by a search over the two base parts'
             surface points;
  zero       the document as saved is configuration Z (q = 0); each revolute joint's limits are read relative to it;
  tool point the T12 construction on the agent's own P09 (the same rule as the GT's).

Errors are returned as strings (they become `cad_valid = 0` with the reason recorded); nothing raises.
"""
import json
import math
import os
import re

import numpy as np

try:  # verify stage: flat vendored copies
    import cad_drawings_check as C
    import cad_photo_grade as G
except ImportError:  # the chain's shared package
    from arm.cad_drawings import checks as C
    from arm.cad_photo import grade as G

PART_LABEL = re.compile(r"^P(0[1-9]|10)$")
N_JOINTS = 6
TCP_BACK_MM = 10.0
HORN_POINT = np.array([0.0, 0.0, 33.1])
YAW_SEARCH_STEP_DEG = 2.0
N_SAMPLES = 4000
N_SAMPLES_GT = 20000
SAMPLE_SEED = 20261018


def _m(a):
    return np.asarray(a, float).reshape(4, 4)


class _UF(object):
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _line_distance(p1, d1, p2, d2):
    n = np.cross(d1, d2)
    if np.linalg.norm(n) < 1e-9:
        v = p2 - p1
        return float(np.linalg.norm(v - np.dot(v, d1) * d1))
    return float(abs(np.dot(p2 - p1, n)) / np.linalg.norm(n))


def _signed_angle(x1, x2, z):
    return math.atan2(float(np.dot(np.cross(x1, x2), z)), float(np.dot(x1, x2)))


def _rot_to(a, b):
    """Rotation matrix taking unit vector a onto unit vector b."""
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-12:
        if c > 0:
            return np.eye(3)
        axis = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0.0, 1.0, 0.0])
        axis = axis / np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))


def _rz(phi):
    c, s = math.cos(phi), math.sin(phi)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _chamfer(a, b_tree, b):
    da, _ = b_tree.query(a)
    from scipy.spatial import cKDTree
    db, _ = cKDTree(a).query(b)
    return float(np.mean(da) + np.mean(db))


def _samples(mesh, n):
    """Surface samples with a fixed seed: the grader's result must not depend on the run."""
    import trimesh
    pts, _ = trimesh.sample.sample_surface(mesh, n, seed=SAMPLE_SEED)
    return np.asarray(pts, float)


def _yaw_icp(pts, tree, gpts, phi, iters=60):
    """Refine a rotation about z (closed-form 2-D Procrustes on nearest-point pairs, about the z axis)."""
    for _ in range(iters):
        q = pts @ _rz(phi).T
        _d, idx = tree.query(q)
        g = gpts[idx]
        d = math.atan2(float(np.sum(q[:, 0] * g[:, 1] - q[:, 1] * g[:, 0])), float(np.sum(q[:, 0] * g[:, 0] + q[:, 1] * g[:, 1])))
        phi += d
        if abs(d) < 1e-10:
            break
    return phi


def _transform_solid(solid, T):
    """A rigid transform of a solid (the rotation re-orthonormalised: OCC rejects a numerically skewed one)."""
    import cadquery as cq
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf
    U, _s, Vt = np.linalg.svd(np.asarray(T, float)[:3, :3])
    Rm = U @ Vt
    t = np.asarray(T, float)[:3, 3]
    tr = gp_Trsf()
    tr.SetValues(*(float(v) for v in np.hstack([Rm, t[:, None]]).ravel()))
    return cq.Shape.cast(BRepBuilderAPI_Transform(solid.wrapped, tr, True).Shape())


def _write_step(solid, path):
    import cadquery as cq
    cq.exporters.export(cq.Workplane().add(solid), path, exportType="STEP")
    solid.exportBrep(_brep_sibling(path))


def _brep_sibling(path):
    return os.path.splitext(path)[0] + ".brep"


def use_brep_siblings():
    """Make every loaded copy of the cad-drawings checks read a part from the BREP file beside its STEP when there is one.
    A STEP round trip can turn a valid solid invalid (OCCT re-reads a face's wire nesting differently: on an Opus 5.5
    document of 2026-10-20 three planar faces of a part FreeCAD checks as valid came back InvalidImbricationOfWires); the
    BREP file holds the shape exactly. The extractor writes both for the agent's leaves and _write_step for the converted
    parts; the reference parts have no BREP siblings and are read from STEP as before."""
    import sys
    for name in ("cad_drawings_check", "arm.cad_drawings.checks"):
        mod = sys.modules.get(name)
        if mod is None or getattr(mod.load_step_solids, "_brep_first", False):
            continue
        step_reader = mod.load_step_solids

        def load(path, _step_reader=step_reader):
            brep = _brep_sibling(path)
            if not os.path.isfile(brep):
                return _step_reader(path)
            import cadquery as cq
            try:
                shape = cq.Shape.importBrep(brep)
            except Exception as exc:  # noqa: BLE001
                return [], "BREP reader failed: {}".format(str(exc)[:200])
            return list(shape.Solids()), None
        load._brep_first = True
        mod.load_step_solids = load


def gt_structure(ref_assembly):
    """The reference unit's chain: link names in order, their part sets, the joint names (tier vocabulary)."""
    links = {l["name"]: l for l in ref_assembly["links"]}
    order = [ref_assembly["root_link"]]
    joints = []
    while True:
        nxt = [j for j in ref_assembly["joints"] if j["parent_link"] == order[-1]]
        if not nxt:
            break
        joints.append(nxt[0]["name"])
        order.append(nxt[0]["child_link"])
    return {"links": order, "parts": [sorted(p["part_id"] for p in links[n]["parts"]) for n in order], "joints": joints}


def tool_point(mesh):
    """T12 on a fixed-finger part (gripper link frame): on its gripping face (the -y face of the finger section), at the
    middle of the finger's width, 10.0 mm below the fingertip (the part's highest point at q = 0) (gt_arm
    outputs.finger_frame, rule legacy, face min_y)."""
    z_tip = float(np.asarray(mesh.vertices)[:, 2].max())
    z = z_tip - TCP_BACK_MM
    loops = [l for l in G._section_loops(mesh, z) if G._poly_centroid(l)[1] > 0]
    if not loops:
        raise ValueError("no finger section 10 mm below the tip of P09")
    pts = np.vstack(loops)
    return np.array([0.5 * (pts[:, 0].min() + pts[:, 0].max()), pts[:, 1].min(), z])


def convert(extract_dir, ref_dir, out_dir):
    """Returns {"ok", "errors", "stats", "details"}; on success `out_dir` holds assembly.json + parts/*.step."""
    use_brep_siblings()
    errors, stats, details = [], {}, {}
    res = {"ok": False, "errors": errors, "stats": stats, "details": details}
    path = os.path.join(extract_dir, "model.json")
    if not os.path.isfile(path):
        errors.append("the document could not be read (no extraction record)")
        return res
    with open(path) as fh:
        model = json.load(fh)
    if model.get("errors"):
        errors += ["document: " + e for e in model["errors"]]
        return res
    with open(os.path.join(ref_dir, "assembly.json")) as fh:
        ref_asm = json.load(fh)
    gs = gt_structure(ref_asm)

    # ---- parts and servos (leaves of the assembly's components)
    comp_of, parts, servos = {}, {}, []
    for comp in model["components"]:
        for leaf in comp["leaves"]:
            leaf = dict(leaf, component=comp["name"])
            if leaf.get("error"):
                errors.append("component %s: %s" % (comp["label"], leaf["error"]))
                continue
            if leaf.get("is_servo"):
                servos.append(leaf)
            elif PART_LABEL.match(leaf["label"] or ""):
                if leaf["label"] in parts:
                    errors.append("part label %s is used twice" % leaf["label"])
                parts[leaf["label"]] = leaf
            comp_of.setdefault(comp["name"], []).append(leaf)
    stats["n_parts_labelled"] = len(parts)
    stats["n_servos_found"] = len(servos)
    expected = sorted(p for ps in gs["parts"] for p in ps)
    missing = [p for p in expected if p not in parts]
    if missing:
        errors.append("the Assembly has no body labelled %s" % ", ".join(missing))
    for pid, leaf in sorted(parts.items()):
        if not leaf.get("valid") or leaf.get("n_solids") != 1:
            errors.append("part %s is not one valid solid (%s solids, valid %s)" % (pid, leaf.get("n_solids"), leaf.get("valid")))
    if len(servos) != N_JOINTS:
        errors.append("the Assembly holds %d copies of the given servo (expected %d)" % (len(servos), N_JOINTS))
    if errors:
        return res

    # ---- links and the chain
    uf = _UF()
    for name in comp_of:
        uf.find(name)
    revolute, ground = [], [g for g in model.get("grounded", []) if g]
    for j in model["joints"]:
        if j.get("suppressed"):
            continue
        if j.get("error"):
            errors.append("joint %s: %s" % (j["label"], j["error"]))
            continue
        if j["type"] == "Fixed":
            if j.get("part1") and j.get("part2"):
                uf.union(j["part1"], j["part2"])
        elif j["type"] == "Revolute":
            revolute.append(j)
        else:
            errors.append("joint %s is of type %s (the arm's joints are revolute; rigid connections are Fixed)" % (j["label"], j["type"]))
    if not ground:
        errors.append("no Grounded joint (the base must be grounded)")
    if errors:
        return res
    for g in ground[1:]:
        uf.union(ground[0], g)
    g0 = uf.find(ground[0])
    groups = {}
    for name in comp_of:
        groups.setdefault(uf.find(name), []).append(name)
    adj = {}
    for j in revolute:
        a, b = uf.find(j["part1"]), uf.find(j["part2"])
        if a == b:
            errors.append("revolute joint %s connects two components of one rigid group" % j["label"])
            continue
        adj.setdefault(a, []).append((b, j))
        adj.setdefault(b, []).append((a, j))
    chain, joints, seen = [g0], [], {g0}
    while True:
        nxt = [(b, j) for b, j in adj.get(chain[-1], []) if b not in seen]
        if len(nxt) != 1:
            if len(nxt) > 1:
                errors.append("the chain branches after link %d" % (len(chain) - 1))
            break
        b, j = nxt[0]
        chain.append(b)
        joints.append(j)
        seen.add(b)
    floating = [n for g, ns in groups.items() if g not in seen for n in ns]
    if floating:
        errors.append("components not connected to the grounded base by the joints: %s" % ", ".join(sorted(floating))[:300])
    if len(joints) != N_JOINTS:
        errors.append("the chain from the grounded base has %d revolute joints (the unit has %d)" % (len(joints), N_JOINTS))
    if errors:
        return res
    link_parts = [sorted(l["label"] for n in groups[g] for l in comp_of[n] if l["label"] in parts) for g in chain]
    details["links_found"] = link_parts
    for k, (have, want) in enumerate(zip(link_parts, gs["parts"])):
        if have != want:
            errors.append("link %d of the chain carries %s; the photographed unit's carries %s" % (k, have or "no part", want))
    if errors:
        return res

    # ---- servos per joint, axes, limits
    servo_group = {s["component"]: uf.find(s["component"]) for s in servos}
    used, jinfo = set(), []
    for k, j in enumerate(joints):
        # FreeCAD's revolute angle: Reference2's part relative to Reference1's, about JCS1's z (right-handed); limits are
        # read relative to the saved pose (configuration Z), then turned into the child-relative-to-parent angle about
        # the driving servo's +z
        J1, J2 = _m(j["jcs1"]), _m(j["jcs2"])
        p, z = J1[:3, 3], J1[:3, 2] / np.linalg.norm(J1[:3, 2])
        theta0 = _signed_angle(J1[:3, 0], J2[:3, 0], z)
        lo = math.radians(j["angle_min_deg"]) - theta0 if j.get("enable_angle_min") else -math.pi
        hi = math.radians(j["angle_max_deg"]) - theta0 if j.get("enable_angle_max") else math.pi
        if uf.find(j["part1"]) != chain[k]:  # Reference1 is the child: the angle is the parent's relative to the child
            lo, hi = -hi, -lo
        best = None
        for s in servos:
            if s["component"] in used or servo_group[s["component"]] != chain[k]:
                continue
            P = _m(s["placement"])
            hz = P[:3, 2] / np.linalg.norm(P[:3, 2])
            ang = math.degrees(math.acos(min(1.0, abs(float(np.dot(hz, z))))))
            dist = _line_distance(p, z, P[:3, 3], hz)
            key = (ang > 5.0, dist, ang)
            if best is None or key < best[0]:
                best = (key, s, P, hz, ang, dist)
        if best is None:
            errors.append("revolute joint %d has no servo in its parent link" % (k + 1))
            continue
        _key, s, P, hz, ang, dist = best
        used.add(s["component"])
        sign = 1.0 if float(np.dot(hz, z)) >= 0 else -1.0
        if sign < 0:
            lo, hi = -hi, -lo
        lo, hi = max(lo, -math.pi), min(hi, math.pi)
        stats["servo_axis_angle_deg_%d" % (k + 1)] = ang
        stats["servo_axis_offset_mm_%d" % (k + 1)] = dist
        if not j.get("enable_angle_min") or not j.get("enable_angle_max"):
            details.setdefault("notes", []).append("joint %d: angle limits not enabled (read as -pi / pi)" % (k + 1))
        jinfo.append({"p": p, "axis": sign * z, "servo": P, "lo": lo, "hi": hi})
    if errors:
        return res

    # ---- frame: the base-yaw axis onto z, the base's lowest point onto the mounting plane, yaw by the base parts
    base_pids = gs["parts"][0]
    a_base = []
    solids = {}
    for pid, leaf in parts.items():
        sols, err = C.load_step_solids(os.path.join(extract_dir, leaf["step"]))
        if err or len(sols) != 1:
            errors.append("part %s could not be re-read (%s)" % (pid, err or "%d solids" % len(sols)))
            continue
        solids[pid] = sols[0]
    if errors:
        return res
    g_base, g_low, a_low = [], [], []
    for pid in base_pids:
        fname = [p["part_file"] for l in ref_asm["links"] for p in l["parts"] if p["part_id"] == pid][0]
        gsol = C.load_step_solids(os.path.join(ref_dir, fname))[0][0]
        gm, am = C.Body(pid, gsol).mesh, C.Body(pid, solids[pid]).mesh
        g_base.append(_samples(gm, N_SAMPLES_GT))
        a_base.append(_samples(am, N_SAMPLES))
        g_low.append(np.asarray(gm.vertices, float))
        a_low.append(np.asarray(am.vertices, float))
    a_base, g_base = np.vstack(a_base), np.vstack(g_base)
    R0 = _rot_to(jinfo[0]["axis"], np.array([0.0, 0.0, 1.0]))
    p0 = R0 @ jinfo[0]["p"]
    pts = a_base @ R0.T - np.array([p0[0], p0[1], 0.0])
    dz = float(np.vstack(g_low)[:, 2].min() - (np.vstack(a_low) @ R0.T)[:, 2].min())  # exact lowest vertices
    pts[:, 2] += dz
    from scipy.spatial import cKDTree
    tree = cKDTree(g_base)
    phis = np.radians(np.arange(0.0, 360.0, YAW_SEARCH_STEP_DEG))
    costs = [_chamfer(pts @ _rz(f).T, tree, g_base) for f in phis]
    phi = _yaw_icp(pts, tree, g_base, float(phis[int(np.argmin(costs))]))
    Rz = _rz(phi)
    R = Rz @ R0
    t = Rz @ np.array([-p0[0], -p0[1], dz])
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, t
    stats["base_alignment_mean_mm"] = _chamfer(pts @ Rz.T, tree, g_base) / 2.0
    stats["base_alignment_yaw_deg"] = math.degrees(phi)

    # ---- link frames (world-aligned at the zero; origins on the joint axes at the driving horn) and the tier record
    def tp(x):
        return R @ np.asarray(x, float) + t

    origins = [np.zeros(3)]
    for ji in jinfo:
        P = ji["servo"]
        horn = P[:3, :3] @ HORN_POINT + P[:3, 3]
        p, d = tp(ji["p"]), R @ ji["axis"]
        h = tp(horn)
        origins.append(p + float(np.dot(h - p, d)) * d)
    os.makedirs(os.path.join(out_dir, "parts"), exist_ok=True)
    ref_links = gs["links"]
    servo_by_link = {}
    for k, ji in enumerate(jinfo):
        Ts = T @ ji["servo"]
        servo_by_link.setdefault(k, []).append({"servo_id": "S%d" % (k + 1), "xyz": [float(v) for v in Ts[:3, 3] - origins[k]],
                                                "rpy": [float(v) for v in C.matrix_to_rpy(Ts[:3, :3])]})
    links_out = []
    for k, name in enumerate(ref_links):
        L = np.eye(4)
        L[:3, 3] = -origins[k]
        recs = []
        for pid in gs["parts"][k]:
            rel = "parts/%s.step" % pid
            _write_step(_transform_solid(solids[pid], L @ T), os.path.join(out_dir, rel))
            recs.append({"part_id": pid, "part_file": rel})
        links_out.append({"name": name, "parts": recs, "servo_occurrences": servo_by_link.get(k, [])})
    joints_out = []
    for k, ji in enumerate(jinfo):
        joints_out.append({"name": gs["joints"][k], "type": "revolute", "parent_link": ref_links[k], "child_link": ref_links[k + 1],
                           "origin": {"xyz": [float(v) for v in origins[k + 1] - origins[k]], "rpy": [0.0, 0.0, 0.0]},
                           "axis": [float(v) for v in R @ ji["axis"]], "limits": {"lower": float(ji["lo"]), "upper": float(ji["hi"])},
                           "driven_by": "S%d" % (k + 1)})
    tcp = [0.0, 0.0, 0.0]
    try:
        tcp_link = ref_links.index(ref_asm["tcp"]["link"])
        p09 = C.load_step_solids(os.path.join(out_dir, "parts", gs["parts"][tcp_link][0] + ".step"))[0][0]
        tcp = [float(v) for v in tool_point(C.Body("P09", p09).mesh)]
    except Exception as exc:  # noqa: BLE001 - a P09 without a finger: the tool point items score low, not an error
        details.setdefault("notes", []).append("tool point construction failed on P09: %s" % str(exc)[:160])
    spec = {"units": {"length": "mm", "angle": "rad"}, "root_link": ref_links[0], "links": links_out, "joints": joints_out,
            "tcp": {"link": ref_asm["tcp"]["link"], "xyz": tcp, "approach": [0.0, 0.0, 1.0]}}
    with open(os.path.join(out_dir, "assembly.json"), "w") as fh:
        json.dump(spec, fh, indent=1)
    res["ok"] = True
    return res
