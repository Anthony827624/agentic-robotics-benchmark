"""The full-arm assembly cell (the from-scratch assembly), shared by the floating-part check's successor
`panda_assembly.py` (the privileged Panda program) and its video.

`FullArmCell(task, cache_dir)` builds, from the task's hidden kit (`verify/gt`) and its vendored cell code:
  * the six-servo cell with the mating graph's `default` scope (15 mates), only the base mounted on the fixture, every
    other part loose, no trays; recompiled from the build's spec with: contact exclusions bracket <-> its carrier and
    the servos and link <-> the servo whose horn it goes on (these interlocking interfaces are the coupling's,
    DESIGN.md 3), a joint equality per servo hinge (the
    holding lock of `coupling.py`), and a mocap JIG per loose part (a weld to a mocap body: the part feeder that holds
    a part where it is presented until something else holds it);
  * the Panda (position-controlled, `PandaController`), the shared `MateTracker` (at-rest horn fastening) and the
    `CouplingStateMachine`.
`targets()` gives every part's design pose in the world (the mating chain from the base, servos at their bus zero),
its insertion direction and depth, and for a link its slide-in pose; `tick()` advances one 10 ms control tick
(controller, jig poses, physics, state machine) and records a frame every 4 ticks for the video.
The build is cached (`cache_dir`): the same inputs give the same model.
"""
import hashlib
import json
import math
import os
import pickle
import time

# the manual's seven steps (gt_plan step numbers), each a list of mates in their order
SEQUENCE = [
    (1, "base: servo S1 into the base, its bracket", ["seat_S1", "seat_base_clamp"]),
    (2, "shoulder on S1's horn; servo S2 into the shoulder, its bracket", ["horn_base_yaw", "seat_S2", "seat_shoulder_clamp"]),
    (3, "upper arm on S2's horn", ["horn_shoulder_pitch"]),
    (4, "servo S3 into the upper arm; forearm on S3's horn", ["seat_S3", "horn_elbow_pitch"]),
    (5, "servo S4 into the forearm, its bracket", ["seat_S4", "seat_wrist_clamp"]),
    (6, "wrist on S4's horn; servo S5 into the wrist", ["horn_wrist_pitch", "seat_S5"]),
    (7, "gripper on S5's horn; servo S6 into the gripper; jaw on S6's horn", ["horn_wrist_roll", "seat_S6", "horn_jaw"]),
]
ORDER = [mid for _s, _t, mids in SEQUENCE for mid in mids]
# the base's yaw on the fixture: 0 = the arm (built at its calibration pose) reaches sideways (+y), every part 0.49-0.63 m
# from the Panda's shoulder and clear of the Panda's own links (+90 deg leans the arm into the robot, -90 deg / 180 deg
# put the wrist end 0.67-0.70 m away; yaw_eval of 2026-10-21); the elbow task uses -90 deg
BASE_YAW_RAD = 0.0
# the table layout: every loose part rests on the bench where the Panda reaches it (horizontal distance from the Panda's
# base axis within TABLE_REACH_M), outside the arm's build volume (KEEPOUT_XY) and TABLE_GAP_M apart
PANDA_XY = (-0.45, 0.0)
TABLE_REACH_M = (0.30, 0.62)
KEEPOUT_XY = ((-0.14, 0.30), (-0.14, 0.32))
TABLE_GAP_M = 0.015
# contact exclusions: "clamp" = a bracket <-> the servo it clamps, "link" = a link <-> the servo whose horn it goes on
# (the press fits / screwed interfaces, held by the coupling); "bracket" = the first version's bracket <-> its carrier
# and every servo (replaced by the carved carriers below)
EXCLUDES = ["clamp", "link"]
# parts whose collision hulls are recomputed finer (fine_collision.py): {part: CoACD threshold}
FINE_HULLS = {"base_clamp": 0.02, "shoulder_clamp": 0.02, "wrist_clamp": 0.02}
# the carriers' collision pieces carved by their brackets' true meshes along the insertion path (fine_collision.py)
CARVE_CARRIERS = True
# the unpowered servo's hinge (fastened joints turn freely within the arm's joint limits) or "lock" (the first version)
HOLD = "limits"
HORN_HINGE = {"frictionloss": 0.4, "damping": 0.05, "armature": 0.005}
# a servo or bracket counts as ON its pocket (ENGAGED, held by the soft coupling) only this close to home along the
# insertion axis: the robot pushes it home, the coupling does not pull it in
CAPTURE_SEAT_DEPTH_M = 0.003
# links carved by a part they slide past: [(link, other part)]
CARVE_LINKS = [("upper_arm", "shoulder_clamp")]
BUILD_TAG = ["table-layout/1", "excludes/2", "joint-locks/1", "mocap-jigs/1", "carve/3"]


class FullArmCell(object):
    def __init__(self, gt_dir, panda_dir, cache_dir, panda_q=None, log=print, rest=None, excludes=None, staging=None, use_cache=True,
                 table_friction=0.8, fixture_xy=(0.10, 0.0), printed_rgba=None):
        import numpy as np
        import mujoco
        from arm.assembly_cell.controller import PandaController
        from arm.assembly_cell.predicates import MateTracker
        from arm.assembly_info.cell_info import RETRACT_Q
        from arm.assembly_six.cell_builder import build_cell, pose_mm, scope_parts
        from arm.assembly_six.server import CellServer
        from .coupling import CouplingStateMachine
        self.np, self.mj, self.pose_mm, self.log = np, mujoco, pose_mm, log
        gt = gt_dir
        a2 = os.path.join(gt, "derived", "a2_assembly")
        self.graph = json.load(open(os.path.join(a2, "mating_graph.json")))
        self.gmates = {mm["id"]: mm for mm in self.graph["mates"]}
        scope = list(self.graph["scopes"]["default"])
        # the information tier's bench (1.6 x 1.0 m, top at z = 0; the six-servo cell's default is 1.2 x 0.8 m)
        cfg = {"preassembly": {"groups": [{"name": "base", "root": "base", "fixed": True, "parts": [], "q": {}}]},
               "cell": {"table": {"size_xy": [1.6, 1.0], "thickness": 0.03, "floor_z": -0.994},
                        # the servos are unpowered during the assembly: the hinge back-drives against the gearbox's
                        # friction (the information tier's 0.4 N m)
                        "horn_hinge": dict(HORN_HINGE)}}
        if printed_rgba is not None:
            # the kit's print colour (the manual's photos show it); the default is the six-servo cell's blue
            cfg["cell"]["materials"] = {"printed": [float(v) for v in printed_rgba], "servo": [0.18, 0.18, 0.20, 1.0], "horn": [0.85, 0.72, 0.30, 1.0]}
        srv = CellServer(a2, panda_dir, assembly_json=os.path.join(gt, "hardware-design", "assembly.json"), config=cfg, scope_ids=scope)
        self.srv = srv
        _root, loose, _m, _g = scope_parts(srv.graph, scope, srv.manifest, srv.preassembly)
        names = [self.gmates[mid]["part_b"] for mid in ORDER]
        assert sorted(names) == sorted(loose), (names, loose)
        self.loose, self.part_of = names, {mid: self.gmates[mid]["part_b"] for mid in ORDER}
        # the initial configuration: every loose part resting on the bench within the Panda's reach (table_layout)
        self.staging = staging if staging is not None else table_layout(srv.parts, names, np, rest=rest)
        layout = {"seed": 0, "fixture": {"pos": [float(fixture_xy[0]), float(fixture_xy[1]), 0.0]}, "fixture_top_z_m": 0.02, "base_yaw_rad": BASE_YAW_RAD, "friction": float(table_friction),
                  "trays": {}, "sockets": False, "parts": {n: {"pos": p, "quat": q} for n, (p, q) in self.staging.items()},
                  "panda_joint_noise_rad": [0.0] * 7, "config": {}}
        excludes = sorted(EXCLUDES if excludes is None else excludes)
        key = hashlib.sha256(json.dumps([layout, scope, srv.cfg["cell"], BUILD_TAG, excludes, FINE_HULLS, CARVE_CARRIERS, CARVE_LINKS], sort_keys=True, default=str).encode()).hexdigest()[:16]
        cache = os.path.join(cache_dir, "fullarm_cell_%s" % key)
        self.cache_key = key
        self.build = None
        if use_cache and os.path.isfile(cache + ".mjb") and os.path.isfile(cache + ".meta.pkl"):
            m = mujoco.MjModel.from_binary_path(cache + ".mjb")
            meta = pickle.load(open(cache + ".meta.pkl", "rb"))
            log("[cell] from the cache %s" % os.path.basename(cache))
        else:
            t_b = time.time()
            from .fine_collision import fine_pieces
            from arm.assembly_six.cell_builder import DEFAULT_CELL_CONFIG
            ccfg = dict(DEFAULT_CELL_CONFIG, **srv.cfg["cell"])
            cc = {n: fine_pieces(srv.a2_dir, n, srv.parts, srv.graph, ccfg, th, cache_dir, log=log) for n, th in FINE_HULLS.items()}
            if CARVE_CARRIERS:
                from .fine_collision import carrier_pieces
                for mm in self.graph["mates"]:
                    if mm["id"] in scope and mm["kind"] == "part_seat":
                        cc[mm["part_a"]] = carrier_pieces(srv.a2_dir, mm["part_a"], mm["part_b"], srv.parts, srv.graph, ccfg, cache_dir,
                                                          bracket_pieces=cc.get(mm["part_b"]), log=log)
            if CARVE_LINKS:
                # a link carved by a part it slides past on its straight insertion (the upper arm past the shoulder's
                # bracket: the true meshes interfere by 16 mm^3 half way, the convex pieces by 1.97 mm, path_check.py)
                from .fine_collision import link_pieces
                from arm.assembly_cell.predicates import _inv as _inv4
                from arm.assembly_six.cell_builder import pose_mm as _pmm
                T_d = design_poses(srv, self.gmates, scope)
                for lk, oth in CARVE_LINKS:
                    hm = [x for x in self.graph["mates"] if x["kind"] == "horn_mate" and x["part_b"] == lk][0]
                    ins_w = T_d[hm["part_a"]][:3, :3] @ (np.asarray(hm["insertion_axis_in_a"], float) / np.linalg.norm(hm["insertion_axis_in_a"]))
                    T_rel = _inv4(T_d[lk]) @ T_d[oth]
                    ext_l = T_d[lk][:3, :3].T @ ins_w          # the other part moves along +ins relative to the link going in
                    cc[lk] = link_pieces(srv.a2_dir, lk, oth, T_rel, ext_l, float(hm.get("insertion_depth_mm") or 25.0) + 20.0, srv.parts, srv.graph,
                                         ccfg, cache_dir, base_pieces=cc.get(lk), other_pieces=cc.get(oth), log=log)
            build = build_cell(srv.a2_dir, srv.panda_dir, layout, srv.cfg["cell"], scope, srv.manifest, srv.graph, srv.design, srv.preassembly,
                               collision_cache=cc)
            meta = build.meta
            excl = []
            if "bracket" in excludes:
                excl += [(mm["part_b"], other) for mm in self.graph["mates"] if mm["id"] in scope and mm["kind"] == "part_seat"
                         for other in [mm["part_a"]] + list(meta["servos"])]
            # a link's fork <-> the servo whose horn it goes on (the straddle is the coupling's; the convex pieces of the
            # shoulder link's fork close 2.2 mm of its gap on the way in, path_check.py)
            # the bracket <-> only the servo it clamps (the seat it requires): the clamp is a press fit (its true mesh
            # overlaps the servo's case box by 0.2-0.3 cm^3 at home), the coupling holds it
            if "clamp" in excludes:
                excl += [(mm["part_b"], self.gmates[r]["part_b"]) for mm in self.graph["mates"] if mm["id"] in scope and mm["kind"] == "part_seat"
                         for r in mm.get("requires", [])]
            if "link" in excludes:
                excl += [(mm["part_b"], mm["part_a"]) for mm in self.graph["mates"] if mm["id"] in scope and mm["kind"] == "horn_mate"]
            for b1, b2 in excl:
                e = build.spec.add_exclude()
                e.bodyname1, e.bodyname2 = b1, b2
            for n, (pos, quat) in self.staging.items():
                gb = build.spec.worldbody.add_body(name="jig_" + n, mocap=True, pos=list(pos), quat=list(quat))
                gb.add_site(name="jig_site_" + n, size=[0.002, 0.002, 0.002], group=5)
                eq = build.spec.add_equality(name="jig_weld_" + n, type=mujoco.mjtEq.mjEQ_WELD, objtype=mujoco.mjtObj.mjOBJ_BODY,
                                             name1="jig_" + n, name2=n)
                eq.data = [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1]
                eq.solref = [0.01, 1.0]
                eq.active = False
            for sv in meta["servos"]:
                eq = build.spec.add_equality(name="lock_hj_" + sv, type=mujoco.mjtEq.mjEQ_JOINT, objtype=mujoco.mjtObj.mjOBJ_JOINT, name1="hj_" + sv)
                eq.active = False
            m = build.spec.compile()
            build.model = m
            self.build = build
            meta["contact_excludes"] = excl
            if use_cache:
                os.makedirs(cache_dir, exist_ok=True)
                mujoco.mj_saveModel(m, cache + ".mjb", None)
                pickle.dump(meta, open(cache + ".meta.pkl", "wb"))
            log("[cell] built in %.0f s" % (time.time() - t_b))
        meta["joint_axis_in_child"] = srv.joint_axis_in_child
        self.m, self.meta = m, meta
        d = mujoco.MjData(m)
        self.d = d
        mujoco.mj_resetDataKeyframe(m, d, m.key("home").id)
        self.tick_steps = int(meta["config"]["tick_steps"])
        self.tick_s = m.opt.timestep * self.tick_steps
        self.ctrl = PandaController(m, d, tick_s=self.tick_s)
        q0 = list(panda_q or RETRACT_Q)
        self.pj = [m.joint("panda/joint%d" % (i + 1)).id for i in range(7)]
        for i, j in enumerate(self.pj):
            d.qpos[m.jnt_qposadr[j]] = q0[i]
        mujoco.mj_forward(m, d)
        self.ctrl.q_cmd = np.array(q0, float)
        self.ctrl.q_goal = self.ctrl.q_cmd.copy()
        self.ctrl.width_cmd = self.ctrl.width_goal = self.ctrl.width()
        self.ctrl.write_ctrl()
        lt = srv.cfg["latch"]
        self.tk = MateTracker(m, meta, hold_ticks=lt["hold_ticks"], rel_speed_max=lt["rel_speed_max_m_s"], tick_s=self.tick_s,
                              tolerance_scale=lt.get("tolerance_scale", 1.0), horn_at_rest=True, rest_ang_speed_max=math.radians(2.0))
        axes = {mid: self.np.asarray(self.gmates[mid]["insertion_axis_in_a"], float) for mid in ORDER}
        design = json.load(open(os.path.join(gt, "hardware-design", "assembly.json")))
        self.joint_limits = {j["name"]: (float(j["limits"]["lower"]), float(j["limits"]["upper"])) for j in design["joints"]}
        self.sm = CouplingStateMachine(m, d, self.tk, insertion_axes=axes, hold=HOLD, joint_limits=self.joint_limits,
                                       capture_seat_depth_m=CAPTURE_SEAT_DEPTH_M)
        self.mates = {mm["id"]: mm for mm in self.tk.mates}
        # per-part geoms (collision switch), jig mocaps and welds
        self.geoms_of = {n: [] for n in names}
        for g in range(m.ngeom):
            b = m.geom_bodyid[g]
            while b != 0:
                nm = m.body(b).name
                if nm in self.geoms_of:
                    self.geoms_of[nm].append(g)
                    break
                b = m.body_parentid[b]
        self.col0 = {g: (int(m.geom_contype[g]), int(m.geom_conaffinity[g])) for n in names for g in self.geoms_of[n]}
        self.jig_mocap = {n: m.body_mocapid[m.body("jig_" + n).id] for n in names}
        self.jig_eq = {n: m.equality("jig_weld_" + n).id for n in names}
        self.jig = {}
        # let the parts settle on the bench (the Panda holds its posture); the settled poses are the initial state
        for _ in range(int(round(1.5 / self.tick_s))):
            self.ctrl.tick()
            for _s in range(self.tick_steps):
                mujoco.mj_step(m, d)
        mujoco.mj_forward(m, d)
        d.time = 0.0
        self.initial = {n: self.body_T(n) for n in names}
        self.panda_bodies = set(b for b in range(m.nbody) if m.body(b).name.startswith("panda/"))
        self.rec_t, self.rec_q, self.rec_s = [], [], []
        self.label = {"step": 0, "text": "start", "part": None, "mate": None}
        self.n_tick = 0
        self.hooks = []
        self.recording = True

    # ------------------------------------------------------------------ helpers
    def body_T(self, name):
        np = self.np
        b = self.m.body(name).id
        T = np.eye(4)
        T[:3, :3] = self.d.xmat[b].reshape(3, 3)
        T[:3, 3] = self.d.xpos[b]
        return T

    def collide(self, n, on):
        m = self.m
        for g in self.geoms_of[n]:
            m.geom_contype[g], m.geom_conaffinity[g] = self.col0[g] if on else (0, 0)

    def set_jig(self, n, T):
        self.jig[n] = T

    def grab_jig(self, n):
        """the jig takes the part where it is (the mocap at the part's pose, the weld at the identity)"""
        from arm.assembly_cell.predicates import quat_wxyz_from_mat
        T = self.body_T(n)
        k, e = self.jig_mocap[n], self.jig_eq[n]
        self.d.mocap_pos[k], self.d.mocap_quat[k] = T[:3, 3], quat_wxyz_from_mat(T[:3, :3])
        self.m.eq_data[e, :] = 0.0
        self.m.eq_data[e, 6] = 1.0
        self.m.eq_data[e, 10] = 1.0
        self.d.eq_active[e] = 1
        self.jig[n] = T
        return T

    def release_jig(self, n):
        self.jig.pop(n, None)
        self.d.eq_active[self.jig_eq[n]] = 0

    def record(self):
        sm = self.sm
        self.rec_t.append(float(self.d.time))
        self.rec_q.append(self.np.array(self.d.qpos, self.np.float32))
        self.rec_s.append({"states": dict(sm.state), "hold": {k: v for k, v in sm.hold.items() if v}, "score": sm.score(),
                           "label": dict(self.label)})

    def tick(self, n=1):
        for _ in range(n):
            self.tick_one()

    def tick_one(self):
        """one 10 ms control tick: the controller, the jig poses, the physics, the coupling state machine, the hooks and
        (every 4 ticks, when `recording`) a frame for the video"""
        from arm.assembly_cell.predicates import quat_wxyz_from_mat
        m, d = self.m, self.d
        self.ctrl.tick()
        for name, T in self.jig.items():
            k = self.jig_mocap[name]
            d.mocap_pos[k] = T[:3, 3]
            d.mocap_quat[k] = quat_wxyz_from_mat(T[:3, :3])
        for _s in range(self.tick_steps):
            self.mj.mj_step(m, d)
        self.mj.mj_forward(m, d)
        self.sm.tick(float(d.time))
        self.n_tick += 1
        for h in self.hooks:
            h()
        if self.recording and self.n_tick % 4 == 0:
            self.record()

    def lowest_point(self):
        """the lowest point of any loose part's collision geometry (its geoms' bounding boxes, world z, m): below the
        bench top (z = 0) a part has fallen off"""
        np, m, d = self.np, self.m, self.d
        z = 1e9
        for n in self.loose:
            for g in self.geoms_of[n]:
                if self.col0.get(g, (0, 0)) == (0, 0):
                    continue
                R = d.geom_xmat[g].reshape(3, 3)
                k = m.geom_dataid[g]
                if m.geom_type[g] == self.mj.mjtGeom.mjGEOM_MESH and k >= 0:
                    v = m.mesh_vert[m.mesh_vertadr[k]:m.mesh_vertadr[k] + m.mesh_vertnum[k]]
                    z = min(z, float(np.min(v @ R[2, :]) + d.geom_xpos[g][2]))
                else:
                    c = d.geom_xpos[g] + R @ m.geom_aabb[g][:3]
                    z = min(z, float(c[2] - np.abs(R[2, :]) @ m.geom_aabb[g][3:]))
        return z

    def contacts_of(self, n, ignore_bodies=()):
        """(count, deepest penetration in m, other body names) of the part's contacts in the live data"""
        own = set(self.geoms_of[n])
        cs = [self.d.contact[i] for i in range(self.d.ncon) if self.d.contact[i].geom1 in own or self.d.contact[i].geom2 in own]
        out = []
        for c in cs:
            other = self.m.body(self.m.geom_bodyid[c.geom2 if c.geom1 in own else c.geom1]).name
            if other not in ignore_bodies:
                out.append((other, c.dist))
        return len(out), -min([x[1] for x in out] + [0.0]), sorted(set(x[0] for x in out))

    # ------------------------------------------------------------------ the design poses
    def targets(self):
        """{mate id: {"part", "T" (the part's design pose in the world), "ins" (unit insertion direction, world), "depth"
        (m), "T_slide" (a link's slide-in pose), "psi_slide" (its rotation from the design about the horn axis),
        "T_h" (the horn frame), "F_b"}} along the mating chain from the mounted base, every servo at its bus zero."""
        np = self.np
        from arm.assembly_cell.predicates import _inv
        T = {"base": self.body_T("base")}
        out = {}
        for mid in ORDER:
            mm, gm = self.mates[mid], self.gmates[mid]
            ax = np.asarray(gm["insertion_axis_in_a"], float)
            ax = ax / np.linalg.norm(ax)
            depth = float(gm.get("insertion_depth_mm") or 20.0) * 1e-3
            if mm["kind"] in ("sleeve_seat", "part_seat"):
                Tp = T[mm["part_a"]] @ mm["F_a"] @ _inv(mm["F_b"])
                T[mm["part_b"]] = Tp
                out[mid] = {"part": mm["part_b"], "T": Tp, "ins": T[mm["part_a"]][:3, :3] @ ax, "depth": depth, "carrier": mm["part_a"]}
            else:
                sv = mm["part_a"]
                T_h = T[sv] @ mm["F_a"]
                F_b = mm["F_b_per_servo"].get(sv, mm["F_b"])
                Tp = T_h @ _inv(F_b)
                sl = gm.get("slide_in") or {}
                T_sl = T_h @ _inv(self.pose_mm(sl["frame_b_in_b"])) if sl.get("frame_b_in_b") else Tp
                E = _inv(T_h) @ T_sl @ F_b
                T[mm["part_b"]] = Tp
                out[mid] = {"part": mm["part_b"], "T": Tp, "ins": T[sv][:3, :3] @ ax, "depth": depth, "T_slide": T_sl,
                            "psi_slide": math.atan2(E[1, 0], E[0, 0]), "T_h": T_h, "F_b": F_b, "carrier": sv}
        return out

    def live_target(self, mid):
        """the same as targets()[mid] from the CURRENT poses of the part's carrier (a fastened horn rests within its
        window but not exactly at the design rotation, so the parts after it are where the arm really is)"""
        np = self.np
        from arm.assembly_cell.predicates import _inv
        mm, gm = self.mates[mid], self.gmates[mid]
        ax = np.asarray(gm["insertion_axis_in_a"], float)
        ax = ax / np.linalg.norm(ax)
        depth = float(gm.get("insertion_depth_mm") or 20.0) * 1e-3
        if mm["kind"] in ("sleeve_seat", "part_seat"):
            Tc = self.body_T(mm["part_a"])
            return {"part": mm["part_b"], "T": Tc @ mm["F_a"] @ _inv(mm["F_b"]), "ins": Tc[:3, :3] @ ax, "depth": depth, "carrier": mm["part_a"]}
        sv = mm["part_a"]
        phys = self.tk.seat_servo.get("seat_" + sv, sv)
        Ts = self.body_T(phys)
        T_h = Ts @ mm["F_a"]
        F_b = mm["F_b_per_servo"].get(sv, mm["F_b"])
        return {"part": mm["part_b"], "T": T_h @ _inv(F_b), "ins": Ts[:3, :3] @ ax, "depth": depth, "T_h": T_h, "F_b": F_b, "carrier": phys}


def design_poses(srv, gmates, scope):
    """{part: design pose in the base's frame (m)} along the mating chain from the base, servos at their bus zero"""
    import numpy as np
    from arm.assembly_cell.predicates import _inv
    from arm.assembly_six.cell_builder import pose_mm
    T = {"base": np.eye(4)}
    for mid in ORDER:
        gm = gmates[mid]
        F_a, F_b = pose_mm(gm["frame_a_in_a"]), pose_mm(gm["frame_b_in_b"])
        if gm["kind"] == "horn_mate" and gm.get("frame_b_in_b_per_servo", {}).get(gm["part_a"]):
            F_b = pose_mm(gm["frame_b_in_b_per_servo"][gm["part_a"]])
        T[gm["part_b"]] = T[gm["part_a"]] @ F_a @ _inv(F_b)
    return T


def table_layout(parts, names, np, rest=None):
    """{part: (pos, quat wxyz)}: each loose part with its largest bounding-box face down (or the orientation `rest[part]`,
    a 3 x 3 world rotation, e.g. the Panda program's rest plan), yaw 0 or 90 deg added, placed
    largest footprint first on a 10 mm grid inside the Panda's reach annulus, outside KEEPOUT_XY and TABLE_GAP_M from
    every other part, nearest the middle of the annulus first; the bodies drop 3 mm onto the bench and settle."""
    from arm.assembly_cell.predicates import quat_wxyz_from_mat
    rows = []
    for n in names:
        lo, hi = np.asarray(parts[n]["bbox_mm"][0], float) * 1e-3, np.asarray(parts[n]["bbox_mm"][1], float) * 1e-3
        ext = hi - lo
        k = int(np.argmin(ext))                      # the shortest axis vertical = the largest face down
        others = [i for i in range(3) if i != k]
        R = np.zeros((3, 3))
        R[2, k] = -1.0                               # body axis k points down
        R[0, others[0]] = 1.0
        R[1, others[1]] = 1.0
        if np.linalg.det(R) < 0:
            R[1, others[1]] = -1.0
        if rest and n in rest:
            R = np.asarray(rest[n]["R"] if isinstance(rest[n], dict) else rest[n], float)
            c = np.array([[a, b, cc] for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for cc in (lo[2], hi[2])]) @ R.T
            ext_w = c.max(axis=0) - c.min(axis=0)
            rows.append((float(ext_w[0] * ext_w[1]), n, R, lo, hi))
            continue
        rows.append((float(ext[others[0]] * ext[others[1]]), n, R, lo, hi))
    rows.sort(key=lambda r: -r[0])
    placed, out = [], {}
    # the bench plan's parts first: the others are placed around them
    for _area, n, R0, lo, hi in rows:
        if rest and isinstance(rest.get(n), dict) and rest[n].get("exact"):
            corners = np.array([[a, b, c] for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for c in (lo[2], hi[2])]) @ R0.T
            x, y = rest[n]["xy"]
            placed.append((x + corners[:, 0].min(), x + corners[:, 0].max(), y + corners[:, 1].min(), y + corners[:, 1].max()))
    r_mid = 0.5 * (TABLE_REACH_M[0] + TABLE_REACH_M[1])
    grid = [(x, y) for x in np.arange(-0.40, 0.25, 0.01) for y in np.arange(-0.48, 0.49, 0.01)]
    grid.sort(key=lambda p: abs(np.hypot(p[0] - PANDA_XY[0], p[1] - PANDA_XY[1]) - r_mid))
    for _area, n, R0, lo, hi in rows:
        if rest and isinstance(rest.get(n), dict) and rest[n].get("exact"):
            # the Panda program's bench plan: exactly there (its own spacing and pick checks), 1 mm above the bench
            x, y = rest[n]["xy"]
            z0 = rest[n].get("z")
            if z0 is None:
                corners = np.array([[a, b, c] for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for c in (lo[2], hi[2])]) @ R0.T
                z0 = float(-corners.min(axis=0)[2])
            out[n] = ([float(x), float(y), float(z0) + 0.001], [float(v) for v in quat_wxyz_from_mat(R0)])
            continue
        done = False
        pref = rest[n].get("xy") if (rest and isinstance(rest.get(n), dict)) else None
        cells = grid if pref is None else sorted(grid, key=lambda p: np.hypot(p[0] - pref[0], p[1] - pref[1]))
        yaws = (0.0, np.pi / 2) if not (rest and n in rest) else (0.0,)
        for yaw in yaws:
            Rz = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1.0]])
            R = Rz @ R0
            corners = np.array([[a, b, c] for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for c in (lo[2], hi[2])]) @ R.T
            cmin, cmax = corners.min(axis=0), corners.max(axis=0)
            for x, y in cells:
                bx = (x + cmin[0], x + cmax[0])
                by = (y + cmin[1], y + cmax[1])
                cs = [(cx, cy) for cx in bx for cy in by]
                rr = [np.hypot(cx - PANDA_XY[0], cy - PANDA_XY[1]) for cx, cy in cs]
                if min(rr) < TABLE_REACH_M[0] or max(rr) > TABLE_REACH_M[1] or abs(by[0]) > 0.49 or abs(by[1]) > 0.49:
                    continue
                if bx[1] > KEEPOUT_XY[0][0] and bx[0] < KEEPOUT_XY[0][1] and by[1] > KEEPOUT_XY[1][0] and by[0] < KEEPOUT_XY[1][1]:
                    continue
                g = TABLE_GAP_M
                if any(bx[0] < q[1] + g and bx[1] > q[0] - g and by[0] < q[3] + g and by[1] > q[2] - g for q in placed):
                    continue
                placed.append((bx[0], bx[1], by[0], by[1]))
                out[n] = ([float(x), float(y), float(-cmin[2] + 0.003)], [float(v) for v in quat_wxyz_from_mat(R)])
                done = True
                break
            if done:
                break
        if not done:
            raise RuntimeError("table_layout: no place for %s" % n)
    return out


def jitter_layout(parts, rest, seed, xy_m=0.03, yaw_rad=math.radians(30.0), gap_m=0.006, tries=200, layouts=50):
    """The episode's bench layout: every part of the bench plan `rest` ({part: {R, xy, z}}) in its planned stable
    orientation, turned about the vertical by a uniform draw within +-yaw_rad and moved within +-xy_m (each axis), redrawn
    until its footprint (the bounding box turned) keeps gap_m from every part placed before it and stays inside the Panda's
    reach annulus and on the bench; parts are placed in a seeded random order. A part with no valid draw in `tries`
    restarts the whole layout (up to `layouts` times, then with the amplitudes halved), so every part of the result keeps
    the gap. xy_m = yaw_rad = 0 returns the plan itself. Returns {part: (pos, quat wxyz)} (1 mm above the bench: the drop
    settles it)."""
    import numpy as np
    from arm.assembly_cell.predicates import quat_wxyz_from_mat
    if xy_m == 0.0 and yaw_rad == 0.0:
        return {n: ([float(rest[n]["xy"][0]), float(rest[n]["xy"][1]), float(rest[n]["z"]) + 0.001],
                    [float(v) for v in quat_wxyz_from_mat(np.asarray(rest[n]["R"], float))]) for n in rest}
    rng = np.random.default_rng([int(seed) & 0xFFFFFFFF, (int(seed) >> 32) & 0xFFFFFFFF, 0xB3C4])
    names = list(rest)

    def box(n, R, x, y):
        lo, hi = np.asarray(parts[n]["bbox_mm"][0], float) * 1e-3, np.asarray(parts[n]["bbox_mm"][1], float) * 1e-3
        c = np.array([[a, b, cc] for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for cc in (lo[2], hi[2])]) @ R.T
        return (x + c[:, 0].min(), x + c[:, 0].max(), y + c[:, 1].min(), y + c[:, 1].max())

    def ok(b, placed):
        cs = [(cx, cy) for cx in b[:2] for cy in b[2:]]
        rr = [math.hypot(cx - PANDA_XY[0], cy - PANDA_XY[1]) for cx, cy in cs]
        if min(rr) < TABLE_REACH_M[0] - 0.02 or max(rr) > TABLE_REACH_M[1] + 0.02 or abs(b[2]) > 0.49 or abs(b[3]) > 0.49:
            return False
        if b[1] > KEEPOUT_XY[0][0] and b[0] < KEEPOUT_XY[0][1] and b[3] > KEEPOUT_XY[1][0] and b[2] < KEEPOUT_XY[1][1]:
            return False
        return not any(b[0] < q[1] + gap_m and b[1] > q[0] - gap_m and b[2] < q[3] + gap_m and b[3] > q[2] - gap_m for q in placed)

    amp = 1.0
    for attempt in range(4 * layouts):
        if attempt and attempt % layouts == 0:
            amp *= 0.5
        order = [names[i] for i in rng.permutation(len(names))]
        placed, out, failed = [], {}, False
        for n in order:
            R0 = np.asarray(rest[n]["R"], float)
            x0, y0 = [float(v) for v in rest[n]["xy"]]
            b0 = box(n, R0, x0, y0)
            cx, cy = 0.5 * (b0[0] + b0[1]), 0.5 * (b0[2] + b0[3])
            off = np.array([x0 - cx, y0 - cy, 0.0])
            choice = None
            for _ in range(tries):
                psi = float(rng.uniform(-yaw_rad, yaw_rad)) * amp
                Rz = np.array([[math.cos(psi), -math.sin(psi), 0.0], [math.sin(psi), math.cos(psi), 0.0], [0.0, 0.0, 1.0]])
                # turn about the part's footprint centre, not its frame origin
                x, y = (np.array([cx, cy, 0.0]) + Rz @ off + np.array(list(rng.uniform(-xy_m, xy_m, 2) * amp) + [0.0]))[:2]
                R = Rz @ R0
                b = box(n, R, x, y)
                if ok(b, placed):
                    choice = (R, float(x), float(y), b)
                    break
            if choice is None:
                failed = True
                break
            R, x, y, b = choice
            placed.append(b)
            out[n] = ([x, y, float(rest[n]["z"]) + 0.001], [float(v) for v in quat_wxyz_from_mat(R)])
        if not failed:
            return out
    return jitter_layout(parts, rest, seed, xy_m=0.0, yaw_rad=0.0)
