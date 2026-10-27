#!/usr/bin/env python3
"""The Panda assembles the whole arm from the base: a privileged program in the full-arm cell (no observation).

    (in the task image, OSMesa)
    python3 panda_assembly.py --task <task folder with verify/gt> --out <dir> [--cache <dir>] [--no-video]

Here the Panda, its gripper and the physics do every placement; nothing else moves a part (no feeder, no jig).
Privileged state only: the parts' exact poses, the design poses from the mating chain, the Panda's own kinematics
(`verify/anchor/panda_ik.py`). The arm being built is physical: its servos are unpowered, a fastened joint turns
against the gearbox friction within the arm's joint limits (`coupling.py`), so every target is computed from the arm
as it is when the part's turn comes and is followed while the part goes in.

The parts start resting on the bench within the Panda's reach, as the bench plan put them (computed once, cached as
rest_plan.json): the parts are placed in the REVERSE assembly order, so when a part's place is chosen every part that
will still be on the bench when it is picked is already there; per part one of its stable resting orientations (the
convex hull of its collision geometry) x 4 yaws x the bench cells in reach, the first cell where a grasp that also works
at the insertion is feasible. Per part:
  grasp     candidates from the part's own geometry (rays through its collision geoms: two antipodal pads whose contact
            planes face the closing direction, closing width 4-65 mm, approach depths 4-48 mm), each checked at the
            pick (every IK solution) and from the pre-insertion pose inwards (the fingers, closed to the grasp's width,
            keep 1 mm from the arm; where they stop short of the capture the part must be at least half in a pocket
            that does not open downwards, to be pushed home)
  motion    every free-space move is planned (`motion.Planner`: the Panda grown by 8 mm, the fingers by 2 mm, the held
            part by 4 mm, against the bench, the arm built so far at its current joint angles and the other parts;
            straight in joint space when free, else RRT-Connect); every Cartesian move is checked before it is executed
  pick      pre-grasp, straight approach, close (15 N per finger) until both fingers touch, lift 60 mm: the part must
            stay in the hand; the insertion is re-checked with the grasp as it is, and again at the pre-insertion pose
            (a part that moved in the fingers is put back and picked again)
  insert    closed loop on the privileged state: align at the pre-insertion pose, then along the insertion direction
            at 15 mm/s at stiffness 0.4, the target re-computed every tick from the carrier's current pose, stopped as
            soon as a joint of the arm turns 1.5 deg (a jam pushing the unpowered arm), backed off and retried; a servo or
            bracket is held until the cell has it ENGAGED (on its pocket, at most 3 mm from home) and let go; where
            the fingers cannot follow it home (a deep pocket) it is let go as deep as they can follow and pushed home
            with the closed fingertips; a link is held until LOCKED
  retreat   open, back out along a checked direction, plan the way to the next pick.
A part the Panda cannot place stays where it is (`placed_by: nobody`); there is no fallback. A contact monitor logs
every contact of the Panda with anything but the part in its fingers (`unplanned_contacts`), and of the part in work
with anything but its carrier, the hand and the bench. After the 15 mates: 3 s under gravity (servos unpowered),
then the six servos are powered and each joint swings +-15 deg and back.
Report: <out>/panda_assembly.json; video: <out>/panda_assembly.avi.
"""
import json
import math
import os
import time
import types

READY = [0.0, -0.4, 0.0, -2.3, 0.0, 1.9, 0.785]
# going in, the fingers keep this far from the arm (a finger that presses on the unpowered arm drags it along)
INSERT_FINGER_CLEAR_M = 0.001
# grasp candidates kept per part (by width and nearness to the centre of mass; shallow grasps at a part's end are what
# lets the fingers follow a servo into a deep pocket)
GRASP_CAP = 150
# the closing command this far below the part's width: 2500 N/m x squeeze / 2 = 15 N per finger (37 N pressed the pads
# 4 mm into the wrist bracket's thin hull pieces and hooked it on a finger)
GRIP_SQUEEZE_M = 0.012
# a pad's contact plane must face the closing direction within this angle (else the part turns in the fingers) ...
ANTIPODAL_DEG = 15.0
# ... and for a closing direction along one of the part's box axes (curved or drafted faces: the wrist bracket's clamp
# faces are 25-35 deg off; a closing direction turned 30 deg off a box's faces is what turned S2 in the fingers)
ANTIPODAL_AXIS_DEG = 35.0
SLIDE_TURN = False                 # links go straight in at the design rotation


def program(cell, rest=None, out=None, cache=None, only="", probe="", no_video=True, save_trajectory=False, video_stride=1,
            final_checks=True, rebuild=None, plan_path=None, log=print, snap=False):
    """Run the privileged assembler on `cell` (a FullArmCell). `rest` = the bench plan ({part: {R, xy, z, grasp}}); None:
    compute it (written to plan_path), then `rebuild(rest)` gives the cell built with it (None: return the plan only).
    Returns the report (dict)."""
    a = types.SimpleNamespace(out=out, cache=cache, only=only, probe=probe, no_video=no_video, save_trajectory=save_trajectory,
                              video_stride=video_stride)
    import numpy as np
    import mujoco
    from arm.assembly_cell.predicates import _inv, _rotz, rotation_angle
    from .panda_ik import PandaKin
    from .cell import SEQUENCE, CAPTURE_SEAT_DEPTH_M as CAPTURE_M

    if a.out:
        os.makedirs(a.out, exist_ok=True)
    rest_path = plan_path
    m, d, ctrl, sm, tk = cell.m, cell.d, cell.ctrl, cell.sm, cell.tk
    kin = PandaKin(model=m)
    targets = cell.targets()
    parts_info = cell.srv.parts
    mid_of = {cell.mates[x]["part_b"]: x for x in cell.mates}

    hj_adr = {s_: m.jnt_qposadr[m.joint("hj_" + s_).id] for s_ in cell.meta["servos"]}

    # ------------------------------------------------------------------ debug snapshots (PANDA_SNAP=1)
    snap_state = {"r": None, "n": 0}

    def snap(tag, part=None):
        if not snap or not a.out:
            return
        from PIL import Image
        if snap_state["r"] is None:
            snap_state["r"] = mujoco.Renderer(m, 480, 640)
        rr = snap_state["r"]
        imgs = []
        for az, el, dist in ((135.0, -25.0, 0.30), (45.0, -10.0, 0.30), (225.0, -60.0, 0.35)):
            cam = mujoco.MjvCamera()
            cam.lookat[:] = cell.body_T(part)[:3, 3] if part else [0.1, 0.0, 0.1]
            cam.distance, cam.azimuth, cam.elevation = dist, az, el
            rr.update_scene(d, cam)
            imgs.append(rr.render())
        snap_state["n"] += 1
        Image.fromarray(np.concatenate(imgs, axis=1)).save(os.path.join(a.out, "snap_%03d_%s.png" % (snap_state["n"], tag)))

    # ------------------------------------------------------------------ motion primitives
    from .motion import Planner
    planner = Planner(cell)
    work = {"part": None, "carrier": None, "phase": "start", "touch_ok": set()}
    contact_log = []

    def monitor():
        """every tick: contacts of the Panda with anything but the part in its fingers, and of the part in work with
        anything but its carrier (and the servo for a link), the hand and the bench, above 0.5 N"""
        f6 = np.zeros(6)
        for i in range(d.ncon):
            c = d.contact[i]
            b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            n1, n2 = m.body(b1).name, m.body(b2).name
            p1, p2 = b1 in cell.panda_bodies, b2 in cell.panda_bodies
            if p1 != p2:
                other = n2 if p1 else n1
                if other == work["part"]:
                    continue
                kind = "panda"
                who = (n1 if p1 else n2, other)
            elif work["part"] in (n1, n2) and not (p1 or p2):
                other = n2 if n1 == work["part"] else n1
                if other in work["touch_ok"] or other == "world":
                    continue
                kind = "part"
                who = (work["part"], other)
            else:
                continue
            mujoco.mj_contactForce(m, d, i, f6)
            if abs(f6[0]) > 0.5:
                contact_log.append((round(float(d.time), 2), kind, who[0], who[1], round(abs(float(f6[0])), 1), work["phase"]))

    cell.hooks.append(monitor)

    def contact_summary(cs):
        """[{kind, a, b, phase, ticks, max_N, t0, t1}] per pair and phase"""
        out = {}
        for t_, kind, a_, b_, f_, ph in cs:
            o = out.setdefault((kind, a_, b_, ph), {"kind": kind, "a": a_, "b": b_, "phase": ph, "ticks": 0, "max_N": 0.0, "t0": t_, "t1": t_})
            o["ticks"] += 1
            o["max_N"] = max(o["max_N"], f_)
            o["t1"] = t_
        return sorted(out.values(), key=lambda o: -o["ticks"])

    def tcp_T():
        T = np.eye(4)
        s = m.site("tcp").id
        T[:3, :3] = d.site_xmat[s].reshape(3, 3)
        T[:3, 3] = d.site_xpos[s]
        return T

    def cmd_T():
        p0, R0 = ctrl.fk_cmd()
        T = np.eye(4)
        T[:3, :3], T[:3, 3] = R0, p0
        return T

    def wait_arrived(tol=0.004, max_ticks=300):
        for _ in range(max_ticks):
            if float(np.max(np.abs(ctrl.q() - ctrl.q_goal))) < tol and float(np.max(np.abs(ctrl.q_cmd - ctrl.q_goal))) < 1e-6:
                return True
            cell.tick()
        return False

    def move_joint(q_t, speed=0.4):
        q0 = np.array(ctrl.q_cmd, float)
        q_t = np.asarray(q_t, float)
        n = max(1, int(math.ceil(float(np.max(np.abs(q_t - q0))) / (speed * cell.tick_s))))
        for i in range(1, n + 1):
            ctrl.command_joint(q0 + (q_t - q0) * i / n)
            cell.tick()
        wait_arrived()

    why = {"last": None}

    def go(q_t, held=None, allow=(), speed=0.4):
        """a planned free-space move to q_t (straight in joint space when it is free); False if no path. A start inside
        the clearance (the hand just let go of a part) first backs straight up 50 mm."""
        planner.sync(held=held, allow=allow)
        q_now = np.array(ctrl.q_cmd, float)
        if planner.hits(q_now) and held is None:
            ctrl.command_width(0.08)
            cell.tick(30)
            T_up = cmd_T()
            T_up[2, 3] += 0.05
            move_line(T_up, speed=0.04)
            planner.sync(held=held, allow=allow)
            q_now = np.array(ctrl.q_cmd, float)
        path = planner.plan(q_now, np.asarray(q_t, float))
        if path is None:
            hs, h0 = planner.hits(np.asarray(q_t, float)), planner.hits(q_now)
            why["last"] = "goal: %s; start: %s" % (sorted(set((h[0], h[1], round(h[2] * 1000, 1)) for h in hs))[:6],
                                                  sorted(set((h[0], h[1], round(h[2] * 1000, 1)) for h in h0))[:6])
            return False
        for q in path[1:]:
            move_joint(q, speed=speed)
        return True

    def line_qs(T1, speed=0.03, ang_speed=math.radians(15.0), T0=None):
        """the joint postures of a straight tcp path from the commanded pose (or T0) to T1, IK per 10 ms tick"""
        if T0 is None:
            T0 = cmd_T()
        from scipy.spatial.transform import Rotation, Slerp
        dist = float(np.linalg.norm(T1[:3, 3] - T0[:3, 3]))
        ang = rotation_angle(T0[:3, :3].T @ T1[:3, :3])
        dur = max(dist / speed, ang / ang_speed, 2 * cell.tick_s)
        n = max(1, int(math.ceil(dur / cell.tick_s)))
        sl = Slerp([0, 1], Rotation.from_matrix([T0[:3, :3], T1[:3, :3]]))
        q = np.array(ctrl.q_cmd, float)
        qs, ok_all = [], True
        for i in range(1, n + 1):
            s_ = i / n
            q, ok = kin.solve_one((1 - s_) * T0[:3, 3] + s_ * T1[:3, 3], sl([s_]).as_matrix()[0], q, q, margin=0.0)
            ok_all = ok_all and ok
            qs.append(q.copy())
        return qs, ok_all

    def line_free(qs, held=None, allow=(), held_clear=None, every=3):
        """the Cartesian path's postures are free (a contact present at its start may stay while it does not get closer)"""
        planner.sync(held=held, allow=allow)
        sub = qs[::every] + qs[-1:]
        h0 = {(h[0], h[1]): h[2] for h in planner.hits(np.array(ctrl.q_cmd, float), held_clear=held_clear)}
        for i_, q in enumerate(sub):
            hs = [h for h in planner.hits(q, held_clear=held_clear) if not ((h[0], h[1]) in h0 and h[2] >= h0[(h[0], h[1])] - 0.0005)]
            if hs:
                why["last"] = "at %d / %d: %s" % (i_ * every, len(qs), sorted(set((h[0], h[1], round(h[2] * 1000, 1)) for h in hs))[:6])
                return False
        return True

    def run_qs(qs):
        for q in qs:
            ctrl.command_joint(q)
            cell.tick()
        wait_arrived()

    def move_line(T1, speed=0.03, ang_speed=math.radians(15.0), check=None):
        """straight tcp path; with check=(held, allow) it is only executed when it is collision-free"""
        qs, ok = line_qs(T1, speed=speed, ang_speed=ang_speed)
        if check is not None and not line_free(qs, *check):
            return False
        run_qs(qs)
        return ok

    def align(part, T_goal, iters=3, speed=0.01, tol_mm=0.3, tol_deg=0.3, target=None):
        """closed loop on the privileged state: move the hand until the held part is at T_goal (or target(), re-computed
        from the carrier's current pose) -- corrects the part's actual pose in the hand and the controller's lag"""
        for _ in range(iters):
            if target is not None:
                T_goal = target()
            T_part, T_act, T_cmd = cell.body_T(part), tcp_T(), cmd_T()
            if np.linalg.norm(T_part[:3, 3] - T_act[:3, 3]) > 0.15:
                break
            E = _inv(T_goal) @ T_part
            if np.linalg.norm(E[:3, 3]) * 1000 < tol_mm and math.degrees(rotation_angle(E[:3, :3])) < tol_deg:
                break
            G_act = _inv(T_part) @ T_act
            move_line(T_goal @ G_act @ (_inv(T_act) @ T_cmd), speed=speed, ang_speed=math.radians(5.0))
            cell.tick(10)
        if target is not None:
            T_goal = target()
        E = _inv(T_goal) @ cell.body_T(part)
        return float(np.linalg.norm(E[:3, 3]) * 1000), math.degrees(rotation_angle(E[:3, :3]))

    arm_guard = {"moved": False, "deg": 0.0}

    def arm_q():
        """the angles of the arm's fastened joints"""
        return np.array([float(d.qpos[hj_adr[s_]]) for s_ in sorted(sm.limits_set)])

    def insert(part, mid, back_m, stop_m=0.0, speed=0.015, stop_when=None, guard_deg=1.5):
        """the held part from back_m before home along the insertion direction to stop_m before home, the target
        re-computed every tick from the carrier's current pose, the hand corrected for the part's pose in it and the
        controller's lag (measured once at the start); stops early when stop_when() is true"""
        T_part, T_act, T_cmd = cell.body_T(part), tcp_T(), cmd_T()
        G_act, lag = _inv(T_part) @ T_act, _inv(T_act) @ T_cmd
        x = back_m
        q = np.array(ctrl.q_cmd, float)
        n_ = 0
        qa0 = arm_q()
        n_ik_fail = 0
        arm_guard.update(moved=False, deg=0.0)
        while x > stop_m:
            # the arm is unpowered: a part that jams pushes the arm's free joints; following the carrier would push it
            # further. Stop as soon as any joint of the arm has turned guard_deg
            dq_ = float(np.max(np.abs(arm_q() - qa0))) if len(qa0) else 0.0
            if math.degrees(dq_) > guard_deg:
                arm_guard.update(moved=True, deg=math.degrees(dq_))
                log("[panda]     insert %s: the arm moved %.1f deg, stop" % (mid, math.degrees(dq_)))
                break
            x = max(stop_m, x - speed * cell.tick_s)
            tg_ = cell.live_target(mid)
            T_des = tg_["T"].copy()
            T_des[:3, 3] -= tg_["ins"] * x
            T_c = T_des @ G_act @ lag
            q, ik_ok = kin.solve_one(T_c[:3, 3], T_c[:3, :3], q, q, margin=0.0)
            n_ik_fail = (n_ik_fail + 1) if not ik_ok else 0
            ctrl.command_joint(q)
            cell.tick()
            n_ += 1
            if n_ % 40 == 0:
                ax_, lat_ = remaining(part, mid)
                log("[panda]     insert %s: commanded %.1f mm short, part %.1f mm short / %.1f mm off axis, %s, touching %s, IK %s, joint margin %.3f rad, "
                      "tracking %.1f mm" % (mid, x * 1000, ax_ * 1000, lat_ * 1000, sm.state[mid], cell.contacts_of(part)[2], "ok" if n_ik_fail == 0 else "FAILS",
                                            kin.margin(q), float(np.linalg.norm(tcp_T()[:3, 3] - cmd_T()[:3, 3])) * 1000))
            if stop_when is not None and stop_when():
                break
        cell.tick(20)
        return x

    def remaining(part, mid):
        """the part's distance from home along the insertion direction (m), and across it"""
        tg_ = cell.live_target(mid)
        dv = tg_["T"][:3, 3] - cell.body_T(part)[:3, 3]
        ax = float(dv @ tg_["ins"])
        return ax, float(np.linalg.norm(dv - ax * tg_["ins"]))

    def retreat(part, allow=()):
        """open and back the hand out: along -approach, else straight up, else back along the insertion direction"""
        work["phase"] = "release"
        ctrl.command_width(0.08)
        cell.tick(40)
        work["phase"] = "retreat"
        T = cmd_T()
        for v in (-T[:3, 2], np.array([0.0, 0.0, 1.0]), -cell.live_target(mid_of[part])["ins"]):
            T_b = T.copy()
            T_b[:3, 3] += v * 0.06
            if move_line(T_b, speed=0.05, check=(None, (part,) + tuple(allow))):
                return True
        return move_line(T_b, speed=0.05)


    # ------------------------------------------------------------------ grasps
    def part_rays(name, T_part):
        """two antipodal hits along `c` through the line point g (world), only on the part's own geoms"""
        bid = m.body(name).id

        def ray(o, v):
            geomid = np.zeros(1, np.int32)
            for _ in range(4):
                dist = mujoco.mj_ray(m, kin.d, o, v, None, 1, -1, geomid)
                if dist < 0:
                    return None
                g = int(geomid[0])
                if m.geom_bodyid[g] == bid and cell.col0.get(g, (0, 0)) != (0, 0):
                    return o + v * dist
                o = o + v * (dist + 1e-4)
            return None
        return ray

    def grasp_candidates(name, T_part, cap=GRASP_CAP):
        """[(G = tcp pose in the part frame, width m, desc)] from rays on the part placed at T_part in the IK scratch data:
        per closing axis and approach axis of the part's box (and the closing axis turned +-30 deg about the approach),
        approach depths 6-48 mm from the face, offsets across; two antipodal pads (>= 3 of 5 ray pairs, widths within
        6 mm), closing width 4-65 mm; near-duplicates dropped; the `cap` best by width (15-60 mm preferred) and nearness
        to the centre of mass"""
        lo, hi = (np.asarray(parts_info[name]["bbox_mm"][0], float) * 1e-3, np.asarray(parts_info[name]["bbox_mm"][1], float) * 1e-3)
        sync_kin(held=(name, T_part))
        mujoco.mj_kinematics(m, kin.d)
        ray = part_rays(name, T_part)
        R = T_part[:3, :3]
        com = np.asarray(m.body_ipos[m.body(name).id], float)
        out = []
        for ci in range(3):
            for ai in range(3):
                if ai == ci:
                    continue
                bi = 3 - ci - ai
                for sgn in (1.0, -1.0):
                    a_loc = np.zeros(3)
                    a_loc[ai] = sgn
                    ext_a = hi[ai] - lo[ai]
                    for depth in (0.004, 0.006, 0.010, 0.014, 0.020, 0.028, 0.036, 0.048):
                        if depth > ext_a - 0.003:
                            continue
                        for fb in (-0.4, -0.2, 0.0, 0.2, 0.4):
                            for rot in (0.0, math.radians(30.0), -math.radians(30.0)):
                                g = 0.5 * (lo + hi)
                                g[bi] += fb * (hi[bi] - lo[bi])
                                g[ai] = (lo[ai] + depth) if sgn > 0 else (hi[ai] - depth)
                                c0, b0 = np.zeros(3), np.zeros(3)
                                c0[ci], b0[bi] = 1.0, 1.0
                                c_loc = math.cos(rot) * c0 + math.sin(rot) * b0
                                b_loc = np.cross(a_loc, c_loc)
                                cw = R @ c_loc
                                hits = []
                                for off_a, off_b in ((0, 0), (-0.005, 0), (0.005, 0), (0, -0.005), (0, 0.005)):
                                    gg = g + a_loc * off_a + b_loc * off_b
                                    gw = R @ gg + T_part[:3, 3]
                                    p1 = ray(gw - cw * 0.12, cw)
                                    p2 = ray(gw + cw * 0.12, -cw)
                                    if p1 is not None and p2 is not None:
                                        hits.append((p1, p2))
                                if len(hits) < 3:
                                    continue
                                ws = sorted(float((p2 - p1) @ cw) for p1, p2 in hits)
                                w = ws[len(ws) // 2]
                                if w > 0.065 or w < 0.004 or ws[-1] - ws[0] > 0.006:
                                    continue
                                # antipodal: each pad's contact patch (the plane through its ray hits) faces the closing
                                # direction within 15 deg, else the part turns in the fingers when they close
                                flat = True
                                for side in (0, 1):
                                    pts = np.array([h_[side] for h_ in hits])
                                    if len(pts) >= 3:
                                        _u, _s, vt = np.linalg.svd(pts - pts.mean(axis=0))
                                        lim = ANTIPODAL_DEG if rot != 0.0 else ANTIPODAL_AXIS_DEG
                                        if _s[1] > 1e-4 and abs(float(vt[2] @ cw)) < math.cos(math.radians(lim)):
                                            flat = False
                                if not flat:
                                    continue
                                centre_w = np.mean([0.5 * (p1 + p2) for p1, p2 in hits], axis=0)
                                centre = R.T @ (centre_w - T_part[:3, 3])
                                z = a_loc
                                y = c_loc
                                x = np.cross(y, z)
                                G = np.eye(4)
                                G[:3, :3] = np.column_stack([x, y, z])
                                G[:3, 3] = centre
                                if any(np.linalg.norm(G[:3, 3] - o[0][:3, 3]) < 0.003 and rotation_angle(G[:3, :3].T @ o[0][:3, :3]) < 0.2 for o in out):
                                    continue
                                out.append((G, w, "close along %s%s, approach %s%s, depth %.0f mm, offset %+.1f" % (
                                    "xyz"[ci], "" if rot == 0 else " %+.0f deg" % math.degrees(rot), "+-"[sgn < 0], "xyz"[ai], depth * 1000, fb)))
        out.sort(key=lambda o: (0.0 if 0.015 <= o[1] <= 0.060 else 0.03) + float(np.linalg.norm(o[0][:3, 3] - com)))
        return out[:cap]

    def sync_kin(held=None, q=None, extra=None):
        from arm.assembly_cell.predicates import quat_wxyz_from_mat
        kin.d.qpos[:] = d.qpos
        for name, T in (extra or {}).items():
            adr = m.jnt_qposadr[m.joint("free_" + name).id]
            kin.d.qpos[adr:adr + 3] = T[:3, 3]
            kin.d.qpos[adr + 3:adr + 7] = quat_wxyz_from_mat(T[:3, :3])
        if q is not None:
            kin.d.qpos[kin.qadr] = q
        if held is not None:
            from arm.assembly_cell.predicates import quat_wxyz_from_mat
            name, T = held
            adr = m.jnt_qposadr[m.joint("free_" + name).id]
            kin.d.qpos[adr:adr + 3] = T[:3, 3]
            kin.d.qpos[adr + 3:adr + 7] = quat_wxyz_from_mat(T[:3, :3])

    def panda_collides(q, held_name, T_held, allow=(), extra=None, width=None, finger_clear=0.002, link_clear=None):
        """[(other body, distance mm)] of the Panda's contacts closer than the planner's clearance at posture q, the part
        `held_name` at T_held (in the hand or where it rests; its contacts with the fingers are allowed), `extra` {part:
        T} where other parts are"""
        poses = dict(extra or {})
        poses[held_name] = T_held
        planner.sync(allow=(held_name,) + tuple(allow), poses=poses, width=width)
        return [(h[1], round(h[2] * 1000, 2)) for h in planner.hits(np.asarray(q, float), finger_clear=finger_clear, link_clear=link_clear)]

    def tip_extent():
        """how far the closed fingertips reach beyond the tcp along the approach (m), from the finger geoms' boxes"""
        T = tcp_T()
        z = 0.0
        for g in range(m.ngeom):
            if "finger" not in m.body(m.geom_bodyid[g]).name or m.geom_contype[g] == 0 and m.geom_conaffinity[g] == 0:
                continue
            c, h = m.geom_aabb[g][:3], m.geom_aabb[g][3:]
            R, p = d.geom_xmat[g].reshape(3, 3), d.geom_xpos[g]
            for sx in (-1, 1):
                for sy in (-1, 1):
                    for sz in (-1, 1):
                        w = p + R @ (c + h * np.array([sx, sy, sz]))
                        z = max(z, float((w - T[:3, 3]) @ T[:3, 2]))
        return z

    TIP = tip_extent()

    def part_box(part):
        return (np.asarray(parts_info[part]["bbox_mm"][0], float) * 1e-3, np.asarray(parts_info[part]["bbox_mm"][1], float) * 1e-3)

    def push_poses(part, tg):
        """tcp poses (in the part's frame at home) to push the part home with the closed fingertips on its rear face's
        centre, the fingers along the insertion direction, four rotations about it"""
        lo, hi = part_box(part)
        ins_p = tg["T"][:3, :3].T @ tg["ins"]
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        rear = float(np.min(corners @ ins_p))
        c = 0.5 * (lo + hi)
        c = c - ins_p * float(c @ ins_p) + ins_p * rear            # the rear face's centre
        z = ins_p / np.linalg.norm(ins_p)
        t = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        out = []
        for k in range(4):
            x = np.cross(t, z)
            x /= np.linalg.norm(x)
            y = np.cross(z, x)
            a_ = k * math.pi / 2
            x, y = math.cos(a_) * x + math.sin(a_) * y, -math.sin(a_) * x + math.cos(a_) * y
            G = np.eye(4)
            G[:3, :3] = np.column_stack([x, y, z])
            G[:3, 3] = c - z * TIP
            out.append(G)
        return out

    def place_checks(part, tg, G, extra=None, width=None):
        """(q_pre, margin, hold_x, push) when the pre-insertion pose is reachable with this grasp and the Panda touches
        nothing of the arm built so far there; hold_x = how far short of home the fingers can follow the part into its
        pocket (0 = all the way); when the fingers stop short of the capture, push = a feasible push pose (else the grasp
        is rejected).
        Else (None, reason, None, None)."""
        T_end = tg["T"]
        q_pre = None
        for standoff in (0.02, 0.008):
            T_pre = T_end.copy()
            T_pre[:3, 3] -= tg["ins"] * (tg["depth"] + standoff)
            q_pre, mrg2 = kin.solve(T_pre @ G, np.array(READY))
            if q_pre is not None:
                break
        if q_pre is None:
            return None, "pre-insertion IK", None, None
        if panda_collides(q_pre, part, T_pre, extra=extra, width=width):
            return None, "Panda hits %s at the pre-insertion" % sorted(set(h[0] for h in panda_collides(q_pre, part, T_pre, extra=extra, width=width))), None, None
        # from the pre-insertion pose inwards, 2 mm steps: the fingers follow the part until the first pose where the
        # Panda would touch the arm (or IK fails); hold_x = how far short of home that leaves the part
        hold_x, why = 0.0, ""
        q_x = q_pre
        xs = list(np.arange(tg["depth"] + standoff, -1e-9, -0.002))
        for i_x, x in enumerate(xs):
            T_x = T_end.copy()
            T_x[:3, 3] -= tg["ins"] * x
            q_x2, ok = kin.solve_one((T_x @ G)[:3, 3], (T_x @ G)[:3, :3], q_x, q_x, margin=0.0)
            # going in, the fingers keep 1 mm from the arm: a finger sliding on the unpowered arm drags it along (the
            # wrist bracket's fingers turned the forearm 1.6 deg at 14 mm short)
            hit = panda_collides(q_x2, part, T_x, extra=extra, width=width, finger_clear=INSERT_FINGER_CLEAR_M, link_clear=0.003) if ok else []
            if not ok or hit:
                why = "insertion IK at %.0f mm" % (x * 1000) if not ok else "Panda hits %s in the insertion at %.0f mm" % (sorted(set(h for h in hit))[:4], x * 1000)
                hold_x = float(xs[i_x - 1]) if i_x > 0 else None
                break
            q_x = q_x2
        if hold_x is None or hold_x > tg["depth"]:
            return None, why or "insertion", None, None
        is_seat = cell.mates[mid_of[part]]["kind"] in ("sleeve_seat", "part_seat")
        if is_seat and hold_x > CAPTURE_M - 0.0008 and (float(tg["ins"][2]) > 0.3 or hold_x > 0.5 * tg["depth"]):
            # let go with less than half of it in its pocket, or in a pocket that opens downwards, the part tips or
            # falls out before it can be pushed home
            return None, "the fingers stop %.0f mm short of %.0f mm (insertion direction z %.2f)" % (hold_x * 1000, tg["depth"] * 1000, float(tg["ins"][2])), None, None
        if hold_x > CAPTURE_M - 0.0008 and not is_seat:
            return None, "a link cannot be pushed (%s at home)" % why, None, None
        push = None
        if hold_x > CAPTURE_M - 0.0008:
            for P in push_poses(part, tg):
                T_p = T_end @ P
                q_p, mrg_p = kin.solve(T_p, np.array(READY))
                if q_p is None:
                    continue
                if panda_collides(q_p, part, T_end, allow=(part,), extra=extra, width=0.0):
                    continue
                push = P
                break
            if push is None:
                return None, "the fingers follow it to %.0f mm short of home and no push pose is feasible (%s)" % (hold_x * 1000, why), None, None
        return q_pre, min(mrg2, kin.margin(q_pre)), hold_x, push

    def fast_ik(T):
        """IK from three seeds only (the bench plan's inner loop): (q, margin) or (None, 0)"""
        from .panda_ik import REFERENCE_POSTURES
        for seed in (READY, REFERENCE_POSTURES[0], REFERENCE_POSTURES[1]):
            q, ok = kin.solve_one(T[:3, 3], T[:3, :3], np.asarray(seed, float), np.asarray(seed, float))
            if ok:
                return q, kin.margin(q)
        return None, 0.0

    def pick_checks(part, T_pick, G, extra=None, w_open=None, fast=False):
        """(q_pick, q_pg, margin) for a grasp of the part where it rests: from above or the side (never from below the
        bench), a pre-grasp 80 mm back along the approach; the Panda, the fingers open to the grasp's width + 25 mm, clear
        of the bench and every other part (the planner's clearance) at the pre-grasp, along the approach and at the pick.
        Every IK solution of the pick is tried (the arm's posture decides what it touches)."""
        P = T_pick @ G
        if P[2, 2] > 0.2:
            return None, None, "approach from below"
        P_pg = P.copy()
        P_pg[:3, 3] -= P[:3, 2] * 0.08
        sols = []
        q_f, m_f = fast_ik(P)
        if q_f is not None:
            sols.append((q_f, m_f))
        if not fast:
            sols += [(c_[1], c_[2]) for c_ in kin.candidates(P, np.array(READY))]
        if not sols:
            return None, None, "pick IK"
        poses = dict(extra or {})
        poses[part] = T_pick
        width = min(0.08, w_open + 0.025) if w_open else None
        why_ = "pre-grasp IK"
        for q_pick, mrg in sols[:4]:
            q_pg, ok = kin.solve_one(P_pg[:3, 3], P_pg[:3, :3], q_pick, q_pick, margin=0.0)
            if not ok:
                continue
            planner.sync(allow=(part,), poses=poses, width=width)
            hs = []
            for k in range(6):
                q_k, ok = kin.solve_one(((1 - k / 5) * P_pg[:3, 3] + k / 5 * P[:3, 3]), P[:3, :3], q_pg, q_pg, margin=0.0)
                hs = planner.hits(q_k)
                if hs:
                    break
            if hs:
                why_ = "Panda hits %s at the pick" % sorted(set(h[1] for h in hs))
                continue
            return q_pick, q_pg, mrg
        return None, None, why_

    def plan(mid, tg):
        """feasible grasps of the part where it rests now, best first: [(score, G, width, desc, q_pick, q_pg)]"""
        part = tg["part"]
        T_pick = cell.body_T(part)
        cands = grasp_candidates(part, tg["T"])             # the part frame's grasps (the same as the rest plan's)
        gb = (rest or {}).get(part, {}).get("grasp") if isinstance((rest or {}).get(part), dict) else None
        if gb is not None and gb["desc"] not in [c_[2] for c_ in cands]:
            cands = [(np.asarray(gb["G"], float), float(gb["w"]), gb["desc"] + " (bench plan)")] + cands
        feasible, rejected = [], {}
        com = np.asarray(m.body_ipos[m.body(part).id], float)
        for G, w, desc in cands:
            q_pick, q_pg, mrg1 = pick_checks(part, T_pick, G, w_open=w)
            if q_pick is None:
                rejected[desc] = mrg1
                continue
            q_pre, mrg2, hold_x, push = place_checks(part, tg, G, width=w)
            if q_pre is None:
                rejected[desc] = mrg2
                continue
            # prefer joint margin, a grasp near the part's centre of mass (a long link held at its end swings in the
            # fingers), a grasp wide enough to hold, the fingers following the part home (no push)
            score = (min(mrg1, mrg2) - 2.0 * float(np.linalg.norm(G[:3, 3] - com)) - (0.3 if w < 0.012 else 0.0)
                     - 5.0 * hold_x - 30.0 * max(0.0, hold_x - 0.0025))
            feasible.append((score, G, w, desc, q_pick, q_pg, hold_x, push))
        feasible.sort(key=lambda x: -x[0])
        return feasible, len(cands), rejected

    def collision_points(part):
        """the vertices of the part's collision geoms in its body frame (m): mesh vertices, box corners"""
        b = m.body(part).id
        pts = []
        for g in cell.geoms_of[part]:
            if cell.col0.get(g, (0, 0)) == (0, 0) or m.geom_bodyid[g] != b:
                continue
            Rg = np.zeros(9)
            mujoco.mju_quat2Mat(Rg, m.geom_quat[g])
            Rg = Rg.reshape(3, 3)
            if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
                k = m.geom_dataid[g]
                v = m.mesh_vert[m.mesh_vertadr[k]:m.mesh_vertadr[k] + m.mesh_vertnum[k]]
            elif m.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX:
                h = m.geom_size[g]
                v = np.array([[x, y, z] for x in (-h[0], h[0]) for y in (-h[1], h[1]) for z in (-h[2], h[2])])
            else:
                continue
            pts.append(v @ Rg.T + m.geom_pos[g])
        return np.concatenate(pts, axis=0)

    def stable_rests(part, n=6):
        """[(R, probability, hull vertices)]: the part's stable resting orientations on a flat bench (the faces of its
        collision geometry's convex hull under which the centre of mass lies, trimesh.poses.compute_stable_poses; a box
        face that is not stable tips over when the part is put down: the shoulder came to rest 58 deg from such an
        orientation; the visual mesh of a servo, with its horn, rests 4 deg off its collision box)"""
        import trimesh
        mesh = trimesh.Trimesh(vertices=collision_points(part)).convex_hull   # what the simulation rests it on
        Ts, probs = trimesh.poses.compute_stable_poses(mesh, center_mass=np.asarray(m.body_ipos[m.body(part).id], float), n_samples=1, threshold=0.02)
        hv = np.asarray(mesh.vertices, float)
        return [(np.asarray(T_, float)[:3, :3], float(p_), hv) for T_, p_ in list(zip(Ts, probs))[:n]]

    def rest_plan():
        """the bench plan: where each part rests and in which orientation. Parts are placed in the REVERSE assembly
        order, so when a part's place is chosen, every part that will still be on the bench when it is picked is
        already placed (and every part before it is on the arm, at its design pose): per part, among its bounding-box
        faces down (at least half the largest face's area) x 4 yaws x the bench cells in the Panda's reach (20 mm from
        every other part), the first cell (nearest the middle of the reach annulus) where a grasp that also works at the
        insertion is feasible with the planner's clearance; the best orientation by that grasp's score"""
        from .cell import ORDER, PANDA_XY, TABLE_REACH_M, KEEPOUT_XY
        tgs = cell.targets()
        built, place_ok = {}, {}
        po_path = os.path.join(a.cache or a.out or ".", "place_ok_%s.pkl" % cell.cache_key)
        if os.path.isfile(po_path):
            import pickle
            place_ok = pickle.load(open(po_path, "rb"))
            built = {tgs[mid]["part"]: tgs[mid]["T"] for mid in ORDER}
        for mid in ([] if place_ok else ORDER):
            tg = tgs[mid]
            part = tg["part"]
            ok = {}
            for G, w, desc in grasp_candidates(part, tg["T"]):
                q_pre, mrg2, hold_x, _push = place_checks(part, tg, G, extra=built, width=w)
                if q_pre is not None:
                    ok[desc] = (G, w, mrg2 - 5.0 * hold_x - (0.3 if w < 0.012 else 0.0))
            place_ok[part] = dict(sorted(ok.items(), key=lambda kv: -kv[1][2])[:20])
            built[part] = tg["T"]
        if not os.path.isfile(po_path):
            import pickle
            pickle.dump(place_ok, open(po_path, "wb"))
        design = dict(built)
        rest, info, bench, boxes = {}, {}, {}, []
        r_mid = 0.5 * (TABLE_REACH_M[0] + TABLE_REACH_M[1])
        grid = [(x, y) for x in np.arange(-0.40, 0.25, 0.01) for y in np.arange(-0.48, 0.49, 0.01)]
        grid.sort(key=lambda p_: abs(np.hypot(p_[0] - PANDA_XY[0], p_[1] - PANDA_XY[1]) - r_mid))
        gap = 0.02
        for mid in reversed(ORDER):
            part = tgs[mid]["part"]
            lo, hi = part_box(part)
            ext = hi - lo
            areas = [ext[(k + 1) % 3] * ext[(k + 2) % 3] for k in range(3)]
            com = np.asarray(m.body_ipos[m.body(part).id], float)
            scene = {n: T for n, T in design.items() if n != part}
            scene.update(bench)                      # the later parts on the bench, the earlier ones on the arm
            best = None
            for R0, _prob, hull_v in stable_rests(part):
                for yaw in (0.0, math.pi / 2, math.pi, -math.pi / 2):
                    Rz = np.array([[math.cos(yaw), -math.sin(yaw), 0], [math.sin(yaw), math.cos(yaw), 0], [0, 0, 1.0]])
                    R = Rz @ R0
                    corners = hull_v @ R.T                   # the footprint and the height from the part's hull
                    cmin, cmax = corners.min(axis=0), corners.max(axis=0)
                    n_tried = 0
                    for x, y in grid:
                        bx, by = (x + cmin[0], x + cmax[0]), (y + cmin[1], y + cmax[1])
                        rr = [np.hypot(cx - PANDA_XY[0], cy - PANDA_XY[1]) for cx in bx for cy in by]
                        if min(rr) < TABLE_REACH_M[0] or max(rr) > TABLE_REACH_M[1] or abs(by[0]) > 0.49 or abs(by[1]) > 0.49:
                            continue
                        if bx[1] > KEEPOUT_XY[0][0] and bx[0] < KEEPOUT_XY[0][1] and by[1] > KEEPOUT_XY[1][0] and by[0] < KEEPOUT_XY[1][1]:
                            continue
                        if any(bx[0] < q_[1] + gap and bx[1] > q_[0] - gap and by[0] < q_[3] + gap and by[1] > q_[2] - gap for q_ in boxes):
                            continue
                        n_tried += 1
                        if n_tried > 12:
                            break
                        T_pick = np.eye(4)
                        T_pick[:3, :3] = R
                        T_pick[:3, 3] = [x, y, -cmin[2]]          # resting on the bench (the drop settles it there)
                        found = None
                        for desc, (G, w, sc_place) in place_ok[part].items():
                            q_pick, q_pg, mrg1 = pick_checks(part, T_pick, G, extra=scene, w_open=w, fast=True)
                            if q_pick is None:
                                continue
                            sc = min(mrg1, sc_place) - 2.0 * float(np.linalg.norm(G[:3, 3] - com)) + 0.05 * _prob
                            if found is None or sc > found[0]:
                                found = (sc, desc)
                        if found is not None:
                            if best is None or found[0] > best[0]:
                                best = (found[0], R, (float(x), float(y)), found[1], (bx[0], bx[1], by[0], by[1]), T_pick)
                            break
            if best is None:
                info[part] = {"place_feasible_grasps": len(place_ok[part]), "best": None}
                log("[bench] %-15s no feasible place (%d grasps feasible at the insertion)" % (part, len(place_ok[part])))
                continue
            G_b, w_b, _sc = place_ok[part][best[3]]
            rest[part] = {"R": best[1].tolist(), "xy": list(best[2]), "z": float(best[5][2, 3]), "exact": True,
                          "grasp": {"desc": best[3], "G": G_b.tolist(), "w": float(w_b)}}
            boxes.append(best[4])
            bench[part] = best[5]
            info[part] = {"place_feasible_grasps": len(place_ok[part]), "best": best[3], "score": best[0]}
            log("[bench] %-15s at (%.2f, %.2f), %d grasps feasible at the insertion, picked by %s" % (part, best[2][0], best[2][1], len(place_ok[part]), best[3]))
        return rest, info

    if a.probe:
        tgs = cell.targets()
        built = {}
        for mid in [x for _s, _t, mids in SEQUENCE for x in mids]:
            tg = tgs[mid]
            if tg["part"] in a.probe.split(","):
                for G, w, desc in grasp_candidates(tg["part"], tg["T"]):
                    r_ = place_checks(tg["part"], tg, G, extra=built, width=w)
                    log("[probe] %-10s %-55s w %4.1f mm: %s" % (tg["part"], desc, w * 1000, r_[1] if r_[0] is None else "OK hold %.0f mm push %s" % (r_[2] * 1000, r_[3] is not None)))
            built[tg["part"]] = tg["T"]
        return

    # ------------------------------------------------------------------ the rest plan (once; cached next to the cell)
    if rest is None:
        t_r = time.time()
        rest, rest_info = rest_plan()
        if rest_path:
            with open(rest_path, "w") as f:
                json.dump({"rest": rest, "info": rest_info, "wall_s": time.time() - t_r}, f, indent=1)
        if rebuild is None:
            return {"rest": rest, "info": rest_info}
        cell = rebuild(rest)
        m, d, ctrl, sm, tk = cell.m, cell.d, cell.ctrl, cell.sm, cell.tk
        kin = PandaKin(model=m)
        targets = cell.targets()
        parts_info = cell.srv.parts
        planner = Planner(cell)
        cell.hooks.append(monitor)

    # ------------------------------------------------------------------ the run
    report = {"mates": {}, "ready": READY, "initial": {n: cell.initial[n][:3, 3].tolist() for n in cell.loose}}
    # how far each part settled from where the bench plan put it (a part that tips over invalidates its pick)
    settle = {}
    for n_, r_ in (rest or {}).items():
        if isinstance(r_, dict) and r_.get("exact"):
            T_n = cell.initial[n_]
            settle[n_] = [round(float(np.linalg.norm(T_n[:2, 3] - np.asarray(r_["xy"]))) * 1000, 1),
                          round(math.degrees(rotation_angle(np.asarray(r_["R"], float).T @ T_n[:3, :3])), 1)]
    report["bench_settle_mm_deg"] = settle
    log("[panda] settled on the bench (mm, deg from the plan): %s" % settle)
    cell.record()
    cell.tick(30)
    stop = False
    def joints_deg():
        return {s_: round(math.degrees(float(d.qpos[a_])), 2) for s_, a_ in hj_adr.items()}

    for step, text, mids in SEQUENCE:
        for mid in mids:
            tg = cell.live_target(mid)
            part = tg["part"]
            cell.label.update(step=step, text=text, part=part, mate=mid)
            mm = cell.mates[mid]
            is_seat = mm["kind"] in ("sleeve_seat", "part_seat")
            carrier = tg["carrier"]
            work.update(part=part, phase="plan", touch_ok={carrier, "fixture"} | ({mm["part_a"]} if not is_seat else set()))
            rec = {"part": part, "placed_by": None, "joints_before_deg": joints_deg()}
            t0 = float(d.time)
            missing = [r_ for r_ in cell.gmates[mid].get("requires", []) if not tk.state[r_]["achieved"]]
            # a part goes on the arm only: its carrier must be the base or already fastened to it
            if mm["kind"] in ("sleeve_seat", "part_seat") and mm["part_a"] != "base":
                on = [x_ for x_ in cell.mates if cell.mates[x_]["part_b"] == mm["part_a"]]
                missing += [x_ for x_ in on if not tk.state[x_]["achieved"]]
            if mm["kind"] == "horn_mate":
                missing += [s_ for s_ in ["seat_" + mm["part_a"]] if s_ in tk.state and not tk.state[s_]["achieved"]]
            if missing:
                rec.update(placed_by="nobody", skipped="needs %s" % missing, state=sm.state[mid], achieved=False)
                report["mates"][mid] = rec
                log("[panda] step %d %-20s skipped: needs %s" % (step, mid, missing))
                if a.only and mid == a.only:
                    stop = True
                    break
                continue
            t_plan = time.time()
            feasible, ncand, rejected = plan(mid, tg)
            rec.update(grasp_candidates=ncand, grasps_feasible=len(feasible), plan_wall_s=round(time.time() - t_plan, 1), rejected=rejected)
            ok_panda, attempts = False, []
            n_c0 = len(contact_log)
            # the best six grasps, then the best two again with a slow transport (a part held by the tips of the pads
            # shifts in the fingers when the arm swings fast: the wrist bracket moved 1.3 mm and its fingers then hit
            # the forearm)
            tries = [(f_, 0.3) for f_ in feasible[:6]] + [(f_, 0.08) for f_ in feasible[:2]]
            for (_score, G, w, desc, q_pick, q_pg, hold_x, push), v_transport in tries:
                if ok_panda:
                    break
                att = {"grasp": desc, "width_mm": round(w * 1000, 1), "hold_short_mm": round(hold_x * 1000, 1), "push": push is not None,
                       "transport_rad_s": v_transport}
                attempts.append(att)
                T_present = cell.body_T(part)
                q_pick, q_pg, _mrg = pick_checks(part, T_present, G, w_open=w)
                if q_pick is None:
                    att["result"] = "no longer feasible where the part rests (%s)" % _mrg
                    continue
                # ---- to the pre-grasp (planned), approach (checked), close, lift (checked)
                work["phase"] = "to the pick"
                ctrl.command_width(min(0.08, w + 0.025))
                if not go(q_pg):
                    att["result"] = "no collision-free path to the pre-grasp (%s)" % why["last"]
                    continue
                work["phase"] = "approach"
                if not move_line(T_present @ G, speed=0.04, check=(None, (part,))):
                    att["result"] = "approach path not free (%s)" % why["last"]
                    go(READY)
                    continue
                work["phase"] = "grasp"
                ctrl.command_width(max(0.0, w - GRIP_SQUEEZE_M))  # ~15 N per finger once closed on the part
                both, w_prev = False, None
                for _k in range(20):
                    cell.tick(10)
                    touching = cell.contacts_of(part)[2]
                    both = "panda/left_finger" in touching and "panda/right_finger" in touching
                    w_now = ctrl.width()
                    if both and w_prev is not None and abs(w_now - w_prev) < 2e-4:
                        break
                    w_prev = w_now
                att["both_fingers"] = both
                if not both:
                    retreat(part)
                    att["result"] = "the fingers did not close on the part"
                    go(READY)
                    continue
                E_grip = _inv(tcp_T()) @ cell.body_T(part)
                G_held = _inv(cell.body_T(part)) @ tcp_T()
                work["phase"] = "lift"
                P = cmd_T()
                P[2, 3] += 0.06
                if not move_line(P, speed=0.04, check=((part, _inv(G_held)), (part,))):
                    move_line(P, speed=0.04)                     # straight up is the only way off the bench
                cell.tick(20)
                E0 = _inv(tcp_T()) @ cell.body_T(part)
                held = float(np.linalg.norm(E0[:3, 3] - E_grip[:3, 3])) < 0.005
                att["held_after_lift"] = held
                if not held:
                    retreat(part)
                    att["result"] = "slipped in the lift"
                    go(READY)
                    continue
                # ---- the insertion re-checked with the grasp as it is (the part settles in the fingers when they close)
                G_act = _inv(cell.body_T(part)) @ tcp_T()
                E_g = _inv(G) @ G_act
                att["grasp_as_planned_off"] = [round(float(v) * 1000, 1) for v in E_g[:3, 3]] + [round(math.degrees(rotation_angle(E_g[:3, :3])), 1)]
                chk = place_checks(part, cell.live_target(mid), G_act, width=ctrl.width())
                if chk[0] is None:
                    att["result"] = "with the grasp as it is the insertion is not feasible (%s)" % chk[1]
                    work["phase"] = "put back"
                    P = cmd_T()
                    P[2, 3] -= 0.055
                    move_line(P, speed=0.03)
                    retreat(part)
                    go(READY)
                    continue
                hold_x, push = chk[2], chk[3]
                att["hold_short_mm_as_grasped"] = round(hold_x * 1000, 1)
                tg_now = cell.live_target(mid)
                if is_seat and hold_x > CAPTURE_M - 0.0005 and (float(tg_now["ins"][2]) > 0.3 or hold_x > 0.5 * tg_now["depth"]):
                    # let go short of the capture with less than half of it in the pocket, or in a pocket that opens
                    # downwards: the part would tip or fall out before the push; put it back and try the next grasp
                    att["result"] = "with the grasp as it is the fingers stop %.0f mm short of %.0f mm (insertion direction z %.2f)" % (
                        hold_x * 1000, tg_now["depth"] * 1000, float(tg_now["ins"][2]))
                    work["phase"] = "put back"
                    P = cmd_T()
                    P[2, 3] -= 0.055
                    move_line(P, speed=0.03)
                    retreat(part)
                    go(READY)
                    continue
                # ---- transport (planned with the part in the hand) to the pre-insertion pose
                work["phase"] = "transport"
                held_spec = (part, _inv(G_act))
                tg = cell.live_target(mid)
                back = tg["depth"] + 0.02
                T_pre = tg["T"].copy()
                T_pre[:3, 3] -= tg["ins"] * back
                T_via = T_pre.copy()
                T_via[:3, 3] -= tg["ins"] * 0.06
                q_via, _m = kin.solve(T_via @ G_act, np.array(ctrl.q_cmd), T_after=T_pre @ G_act)
                q_pre = q_via
                if q_via is None or not go(q_via, held=held_spec, speed=v_transport) or not move_line(T_pre @ G_act, speed=0.03, check=(held_spec, (), 0.0)):
                    att["result"] = "no collision-free path to the pre-insertion pose (%s)" % (why["last"] if q_pre is not None else "IK")
                    work["phase"] = "put back"
                    go(q_pick, held=held_spec, allow=("world",))  # put it back down where it was
                    retreat(part)
                    go(READY)
                    continue
                def pre_target():
                    t_ = cell.live_target(mid)
                    T_ = t_["T"].copy()
                    T_[:3, 3] -= t_["ins"] * back
                    return T_
                e_pre = align(part, None, target=pre_target)
                rec["pre_insertion_error"] = e_pre
                # the part may have turned in the fingers on the way: check the insertion again with the grasp as it is now
                G_now = _inv(cell.body_T(part)) @ tcp_T()
                E_t = _inv(G_act) @ G_now
                att["grasp_moved_in_transport"] = [round(float(np.linalg.norm(E_t[:3, 3])) * 1000, 1), round(math.degrees(rotation_angle(E_t[:3, :3])), 1)]
                chk2 = place_checks(part, cell.live_target(mid), G_now, width=ctrl.width())
                if chk2[0] is None or chk2[2] > hold_x + 0.002:
                    att["result"] = "the part moved in the fingers on the way (%.1f mm / %.1f deg): %s" % (
                        att["grasp_moved_in_transport"][0], att["grasp_moved_in_transport"][1], chk2[1] if chk2[0] is None else "the fingers now stop %.0f mm short" % (chk2[2] * 1000))
                    work["phase"] = "put back"
                    go(q_pick, held=(part, _inv(G_now)), allow=("world",))
                    retreat(part)
                    go(READY)
                    continue
                # ---- insert
                work["phase"] = "insert"
                ctrl.set_stiffness(0.4)
                engaged = lambda: sm.state[mid] in ("ENGAGED", "HOLDING", "LOCKED")
                x_end = insert(part, mid, back, stop_m=hold_x, stop_when=engaged if is_seat else None)
                for k_retry in range(2):
                    if not arm_guard["moved"] or engaged():
                        break
                    # the part jammed and pushed the arm: back off, let the arm settle, re-align on where the arm is now
                    att.setdefault("jams", []).append(round(arm_guard["deg"], 1))
                    T_b = cmd_T()
                    T_b[:3, 3] -= cell.live_target(mid)["ins"] * 0.012
                    move_line(T_b, speed=0.01)
                    cell.tick(50)
                    ctrl.set_stiffness(1.0)
                    x_now = max(remaining(part, mid)[0], 0.0)

                    def back_target(x_=x_now):
                        t_ = cell.live_target(mid)
                        T_ = t_["T"].copy()
                        T_[:3, 3] -= t_["ins"] * x_
                        return T_
                    align(part, None, target=back_target, iters=2)
                    ctrl.set_stiffness(0.3)
                    x_end = insert(part, mid, max(remaining(part, mid)[0], 0.0), stop_m=hold_x, speed=0.008, stop_when=engaged if is_seat else None)
                snap("%s_inserted" % mid, part)
                ax_rem, lat_rem = remaining(part, mid)
                att["after_insert_mm"] = [round(ax_rem * 1000, 2), round(lat_rem * 1000, 2), sm.state[mid]]
                if is_seat:
                    if not engaged() and hold_x <= CAPTURE_M - 0.0008 and not arm_guard["moved"]:
                        insert(part, mid, max(ax_rem, 0.0), stop_m=-0.001, speed=0.005, stop_when=engaged)
                    retreat(part)
                    snap("%s_released" % mid, part)
                    if not engaged() and push is not None:
                        # ---- push it home with the closed fingertips on its rear face
                        work["phase"] = "push"
                        ctrl.command_width(0.0)
                        tg = cell.live_target(mid)
                        ax_rem, _l = remaining(part, mid)
                        T_p0 = tg["T"] @ push
                        T_p0[:3, 3] -= tg["ins"] * (max(ax_rem, 0.0) + 0.03)
                        q_p0, _m = kin.solve(T_p0, np.array(ctrl.q_cmd))
                        if q_p0 is not None and go(q_p0):
                            work["touch_ok"] = work["touch_ok"]
                            T_now = cell.body_T(part)
                            G_push = push
                            x = max(ax_rem, 0.0) + 0.03
                            q = np.array(ctrl.q_cmd, float)
                            while x > -0.001 and not engaged():
                                x -= 0.010 * cell.tick_s
                                tg_ = cell.live_target(mid)
                                T_c = tg_["T"] @ G_push
                                T_c[:3, 3] -= tg_["ins"] * x
                                q, _ok = kin.solve_one(T_c[:3, 3], T_c[:3, :3], q, q, margin=0.0)
                                ctrl.command_joint(q)
                                cell.tick()
                            cell.tick(30)
                            att["pushed_to_mm"] = round(remaining(part, mid)[0] * 1000, 2)
                            snap("%s_pushed" % mid, part)
                            r_ = sm._eval(mm)
                            if r_ is not None:
                                ax_, lat_ = sm.seat_offsets(mm, r_)
                                att["coupling_after_push"] = {"servo": r_.get("servo"), "pos_err_mm": round(r_["pos_err"] * 1000, 2), "ang_err_deg": round(math.degrees(r_["ang_err"]), 2),
                                                              "axial_mm": round(ax_ * 1000, 2), "lateral_mm": round(lat_ * 1000, 2), "state": sm.state[mid]}
                            T_b = cmd_T()
                            T_b[:3, 3] -= T_b[:3, 2] * 0.05
                            move_line(T_b, speed=0.05)
                        else:
                            att["result"] = "no path to the push pose"
                    for _ in range(200):
                        if tk.state[mid]["fastened"]:
                            break
                        cell.tick()
                else:
                    ctrl.set_stiffness(1.0)
                    for i_ in range(400):
                        if tk.state[mid]["fastened"]:
                            break
                        cell.tick()
                    retreat(part)
                ctrl.set_stiffness(1.0)
                work["phase"] = "back"
                ok_panda = bool(tk.state[mid]["fastened"])
                go(READY, speed=0.6) or move_joint(READY, speed=0.3)
                cell.tick(30)
                ok_panda = bool(tk.state[mid]["fastened"])
                att["result"] = "fastened" if ok_panda else "not fastened (%s)" % sm.state[mid]
                if ok_panda:
                    break
                # not fastened: the part is wherever it ended up; the next attempt picks it up from there if it can
            rec["grasp_attempts"] = attempts
            rec["placed_by"] = "panda" if ok_panda else "nobody"
            st = tk.state[mid]
            rec.update(state=sm.state[mid], achieved=bool(st["achieved"]), t_start_s=round(t0, 2), t_locked_s=st["t_s"],
                       pos_err_mm=st["pos_err_mm"], ang_err_deg=st["ang_err_deg"], yaw_err_deg=st.get("yaw_err_deg"), score_after=sm.score(),
                       joints_after_deg=joints_deg(), n_unplanned_contacts=len(contact_log) - n_c0,
                       unplanned_contacts=contact_summary(contact_log[n_c0:]))
            report["mates"][mid] = rec
            with open(os.path.join(a.out or ".", "panda_assembly_partial.json"), "w") if a.out else open(os.devnull, "w") as f_:
                json.dump(dict(report, contacts=contact_log[-2000:]), f_, indent=1, default=float)
            log("[panda] step %d %-20s %-10s by %-6s t=%6.1f s score %.3f  attempts %s  contacts %d" % (
                step, mid, sm.state[mid], rec["placed_by"], d.time, sm.score(), [x_.get("result") for x_ in attempts], len(contact_log) - n_c0))
            if a.only and mid == a.only:
                stop = True
                break
        if stop:
            break
    work.update(part=None, phase="assembled", touch_ok=set())


    if not final_checks:
        by = [r["placed_by"] for r in report["mates"].values()]
        report.update(score=sm.score(), states=dict(sm.state), placed_by_panda=by.count("panda"), not_placed=by.count("nobody"),
                      unplanned_contacts_total=len(contact_log))
        return report

    # ------------------------------------------------------------------ the assembled arm: gravity, then powered swings
    welded = []
    for mm in tk.mates:
        st = tk.state[mm["id"]]
        if not st["fastened"]:
            continue
        if mm["kind"] == "horn_mate":
            welded += [("horn_" + p, mm["part_b"], mm["id"]) for p in st["servos"]]
        else:
            welded.append((mm["part_a"], st["servo"] if mm["kind"] == "sleeve_seat" else mm["part_b"], mm["id"]))

    def rel(x, y):
        return _inv(cell.body_T(x)) @ cell.body_T(y)

    rel0 = {(x, y): rel(x, y) for x, y, _ in welded}
    drift = {}

    def track():
        for x, y, mid in welded:
            E = _inv(rel0[(x, y)]) @ rel(x, y)
            o = drift.setdefault(mid, [0.0, 0.0])
            o[0] = max(o[0], float(np.linalg.norm(E[:3, 3]) * 1000))
            o[1] = max(o[1], math.degrees(rotation_angle(E[:3, :3])))

    cell.label.update(step=8, text="assembled: 3 s under gravity", part=None, mate=None)
    for _ in range(300):
        cell.tick()
        track()
    drift_hold = {k: list(v) for k, v in drift.items()}
    drift.clear()
    if not a.only:
        cell.label.update(text="servos powered: each joint +-15 deg and back")
        servos = list(cell.meta["servos"])
        for s in servos:
            d.eq_active[m.equality("lock_hj_" + s).id] = 0
        m.opt.disableactuator = 0
        act = {s: m.actuator("act_horn_" + s).id for s in servos}
        q0 = {s: float(d.qpos[m.jnt_qposadr[m.joint("hj_" + s).id]]) for s in servos}
        for s in servos:
            d.ctrl[act[s]] = q0[s]
        cell.tick(50)
        for s in servos:
            cell.label.update(part=s, text="servos powered: %s +-15 deg and back" % s)
            kk = int(round(2.4 / cell.tick_s))
            for i in range(kk + 1):
                d.ctrl[act[s]] = q0[s] + math.radians(15.0) * math.sin(2 * math.pi * i / kk)
                cell.tick()
                track()
            d.ctrl[act[s]] = q0[s]
            cell.tick(30)
        cell.label.update(step=9, text="done: every joint back at its fastening angle", part=None)
        cell.tick(100)
    residual = {}
    for x, y, mid in welded:
        E = _inv(rel0[(x, y)]) @ rel(x, y)
        residual[mid] = [float(np.linalg.norm(E[:3, 3]) * 1000), math.degrees(rotation_angle(E[:3, :3]))]
    zmin = cell.lowest_point()
    by = [r["placed_by"] for r in report["mates"].values()]
    report.update(score=sm.score(), states=dict(sm.state), events=sm.events, placed_by_panda=by.count("panda"),
                  unplanned_contacts_total=len(contact_log), unplanned_contacts_by_phase={ph: sum(1 for c in contact_log if c[5] == ph) for ph in set(c[5] for c in contact_log)},
                  not_placed=by.count("nobody"), drift_while_built_and_standing=drift_hold, drift_during_powered_swing=drift,
                  residual_after_swing=residual, lowest_part_z_m=zmin, sim_s=float(d.time), frames=len(cell.rec_t))
    vals = lambda dd, i: max([v[i] for v in dd.values()] + [0.0])
    report["pass_robot"] = bool(by.count("panda") == len(by) and abs(sm.score() - 1.0) < 1e-9 and vals(drift_hold, 0) < 0.05
                                and vals(drift_hold, 1) < 0.1 and vals(residual, 0) < 0.1 and vals(residual, 1) < 0.2 and zmin > -0.003)
    if a.out:
        with open(os.path.join(a.out, "panda_assembly.json"), "w") as f:
            json.dump(report, f, indent=1, default=float)
    log("[panda] score %.3f; placed by the Panda %d / %d, unplanned contacts %d, not placed %d; drift built+standing %.3f mm / %.3f deg; "
          "residual after the swing %.3f mm / %.3f deg; lowest part z %.3f m; robot PASS %s" % (
              sm.score(), by.count("panda"), len(by), len(contact_log), by.count("nobody"), vals(drift_hold, 0), vals(drift_hold, 1),
              vals(residual, 0), vals(residual, 1), zmin, report["pass_robot"]))
    if a.save_trajectory:
        np.savez_compressed(os.path.join(a.out, "trajectory.npz"), t=np.asarray(cell.rec_t, float), qpos=np.asarray(cell.rec_q, np.float32),
                            model_cache_key=np.array(cell.cache_key))
        with open(os.path.join(a.out, "trajectory_states.json"), "w") as f:
            json.dump({"cache_key": cell.cache_key, "events": sm.events, "frames": cell.rec_s}, f, default=float)
    if not a.no_video:
        from float_assembly import render
        render(a, m, d, cell.rec_t, cell.rec_q, cell.rec_s, sm.events, cell.mates, cell.loose,
               title="full-arm assembly by the Panda alone (privileged program; servos unpowered, joints free)", name="panda_assembly")
    return report
