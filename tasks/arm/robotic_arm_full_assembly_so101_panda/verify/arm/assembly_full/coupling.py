"""Coupling state machine of the full-arm assembly cell (the from-scratch assembly: only the base is mounted).

One state per mate of the mating graph's `default` scope (15 mates: 6 servo seats, 3 bracket seats, 6 horn mates):

  WAITING     a mate it `requires` is not achieved yet (a bracket clamps a seated servo; a link goes on a seated
              servo's horn), so it is not evaluated
  FREE        evaluated, the part is not on its feature
  ENGAGED     the part is ON its feature (inside the capture region, larger than the fastening tolerance: the servo or
              bracket in its pocket within `capture_pos_m` across the insertion axis, up to `capture_seat_depth_m` short
              of home along it, within `capture_ang_rad`; the fork on the horn within
              `capture_pos_m` of the horn axis, `capture_ang_rad` of its direction, `capture_axial_m` axially): the
              cell holds it there with a SOFT coupling, so a part that is on stays on when it is let go. A seat is
              pulled to its design pose (the pocket locates it); a fork is pulled onto the horn axis and the horn
              face only: its rotation about the horn axis stays free (the coupling is re-anchored to the current
              rotation every tick), so the coupling carries no information about the design zero (AP-25)
  HOLDING     the fastening predicate holds (version 3's latch: within tol_pos / tol_ang, axial_tol for a horn) and the
              part is at rest (< 5 mm/s, and < 2 deg/s about the horn axis); counts `hold_ticks` consecutive ticks
  LOCKED      fastened by the cell: the soft coupling becomes the rigid weld (a seat snapped to the design; a fork at
              the rotation it rests at, the screw). The fastened joint stays FREE: the servo is unpowered during the
              assembly, its hinge turns against the gearbox's back-drive friction only, within the arm's joint limits
              (`joint_limits`, the design's collision-free box in design joint angles, mapped to the hinge through the
              fastening angle), so a push moves the arm and the arm stays where it was pushed (or sags where gravity
              beats the friction); every later target follows the arm as it is. (hold="lock", the first version's
              joint equality at the fastening angle, made the arm spring back when pushed.)
              Achieved = a seat, or a fork fastened within tol_ang of the design rotation
  LOCKED_OFF  a fork fastened more than tol_ang from the design rotation: welded (it cannot be mounted again), counts 0

Score (the stage score of this cell) = sum of the achieved mates' weights / the scope's total weight (18.0).
The predicates, the snaps and the welds are the shared `arm.assembly_cell.predicates.MateTracker`'s; this module adds
the gating, the capture and the brake, and owns the per-tick order (the tracker's own `tick` is not called).
"""

import math

import numpy as np

STATES = ("WAITING", "FREE", "ENGAGED", "HOLDING", "LOCKED", "LOCKED_OFF")


class CouplingStateMachine(object):
    def __init__(self, model, data, tracker, capture_pos_m=0.004, capture_ang_rad=math.radians(8.0), capture_axial_m=0.004,
                 release_factor=2.0, soft_solref=(0.04, 1.0), brake_N_m=2.94, insertion_axes=None, capture_seat_depth_m=0.010,
                 hold="limits", joint_limits=None):
        import mujoco
        self.mj = mujoco
        self.m, self.d, self.tk = model, data, tracker
        self.cap_pos, self.cap_ang, self.cap_axial = float(capture_pos_m), float(capture_ang_rad), float(capture_axial_m)
        self.release_factor = float(release_factor)
        self.soft_solref = [float(v) for v in soft_solref]
        self.brake = float(brake_N_m)
        self.hold_mode = hold
        self.joint_limits = dict(joint_limits or {})      # design joint name -> (lower, upper) rad
        self.limits_set = {}                              # servo -> (lo, hi) hinge range set at the fastening
        # a servo or bracket on its way into its pocket is captured up to `capture_seat_depth_m` short of home along its
        # insertion axis (the pocket guides it), within `capture_pos_m` across it
        self.axes = {k: np.asarray(v, float) / np.linalg.norm(v) for k, v in (insertion_axes or {}).items()}
        self.seat_depth = float(capture_seat_depth_m)
        self.hard_solref = {k: model.eq_solref[e].copy() for k, e in tracker.weld_id.items()}
        self.state = {mm["id"]: "WAITING" for mm in tracker.mates}
        self.hold = {mm["id"]: 0 for mm in tracker.mates}
        self.captured = {}                 # mate id -> weld keys used for the soft coupling
        self.events = []                   # (sim time, text)
        self.total_w = sum(mm["weight"] for mm in tracker.mates)

    # ------------------------------------------------------------------ helpers
    def score(self):
        return sum(mm["weight"] for mm in self.tk.mates if self.tk.state[mm["id"]]["achieved"]) / self.total_w

    def _requires_ok(self, mm):
        return all(self.tk.state[r]["achieved"] for r in mm.get("requires", []))

    def _set_soft(self, key, on):
        e = self.tk.weld_id[key]
        self.m.eq_solref[e] = self.soft_solref if on else self.hard_solref[key]

    def _release(self, mid):
        for key in self.captured.pop(mid, []):
            self.d.eq_active[self.tk.weld_id[key]] = 0
            self._set_soft(key, False)

    def _capture_seat(self, mm, r):
        key = (mm["part_a"], r["servo"])
        for k in self.captured.get(mm["id"], []):
            if k != key:
                self._release(mm["id"])
                break
        self._set_soft(key, True)
        self.tk._snap_seat(self.d, mm, r["servo"])               # activates the weld at the design relative pose
        self.captured[mm["id"]] = [key]

    def _capture_horn(self, mm, r):
        from arm.assembly_cell.predicates import _inv, _rotz, body_pose
        design_id, phys, T_h, T_lb, F_b = r["first"]
        T_target = T_h @ _rotz(float(r["yaw_res"])) @ _inv(F_b)  # on the axis and the horn face, at the CURRENT rotation
        keys = []
        for _d, p in r["servos"]:
            key = ("horn_" + p, mm["part_b"])
            self._set_soft(key, True)
            T_hb = body_pose(self.d, self.tk.body["horn_" + p])
            self.tk._activate(self.d, key, _inv(T_hb) @ T_target)
            keys.append(key)
        self.captured[mm["id"]] = keys

    def _brake(self, phys):
        """The fastened horn's servo holds its joint: the joint equality `lock_hj_<servo>` at the current angle when the
        cell has one (rigid), else the hinge's friction raised to the servo's holding torque."""
        try:
            e = self.m.equality("lock_hj_" + phys).id
        except KeyError:
            e = None
        j = self.m.joint("hj_" + phys).id
        if e is not None:
            self.m.eq_data[e, :] = 0.0
            self.m.eq_data[e, 0] = self.d.qpos[self.m.jnt_qposadr[j]]
            self.d.eq_active[e] = 1
        else:
            self.m.dof_frictionloss[self.m.jnt_dofadr[j]] = self.brake

    def _limit(self, mm, phys, drive):
        """The fastened joint's limits on the servo's hinge: theta = theta_snap + sign * q - psi_h (the tracker's servo
        drive), q in the design's joint limits; the range always contains the hinge's current angle."""
        lim = self.joint_limits.get(mm.get("joint"))
        if lim is None:
            return
        j = self.m.joint("hj_" + phys).id
        th = [drive["theta_snap"] + drive["sign"] * q - drive["psi_h"] for q in lim]
        cur = float(self.d.qpos[self.m.jnt_qposadr[j]])
        lo, hi = min(min(th), cur), max(max(th), cur)
        self.m.jnt_range[j] = [lo, hi]
        self.m.jnt_limited[j] = 1
        self.limits_set[phys] = (lo, hi)

    def _eval(self, mm):
        return self.tk._eval_seat(self.d, mm) if mm["kind"] in ("sleeve_seat", "part_seat") else self.tk._eval_horn(self.d, mm)

    def seat_offsets(self, mm, r):
        """(axial, lateral) m of a seat's part from home along / across the insertion axis (axial < 0: short of home)"""
        from arm.assembly_cell.predicates import body_pose
        ax = self.axes.get(mm["id"])
        T_a = body_pose(self.d, self.tk.body[mm["part_a"]])
        T_b = body_pose(self.d, self.tk.body[r["servo"]]) @ mm["F_b"]
        dw = T_b[:3, 3] - (T_a @ mm["F_a"])[:3, 3]
        if ax is None:
            return 0.0, float(np.linalg.norm(dw))
        ins = T_a[:3, :3] @ ax
        axial = float(dw @ ins)
        return axial, float(np.linalg.norm(dw - axial * ins))

    def _inside(self, mm, r, factor=1.0):
        if r is None:
            return False
        if mm["kind"] in ("sleeve_seat", "part_seat"):
            if r["ang_err"] > self.cap_ang * factor:
                return False
            if self.axes.get(mm["id"]) is None:
                return r["pos_err"] <= self.cap_pos * factor
            axial, lateral = self.seat_offsets(mm, r)
            return lateral <= self.cap_pos * factor and -self.seat_depth * factor <= axial <= self.cap_pos * factor
        return r["pos_err"] <= self.cap_pos * factor and r["ang_err"] <= self.cap_ang * factor and abs(r["axial"]) <= self.cap_axial * factor

    # ------------------------------------------------------------------ per control tick
    def tick(self, sim_time):
        tk = self.tk
        for mm in tk.mates:
            mid = mm["id"]
            st = tk.state[mid]
            if st["fastened"]:
                self.state[mid] = "LOCKED" if st["achieved"] else "LOCKED_OFF"
                continue
            if not self._requires_ok(mm):
                self.state[mid] = "WAITING"
                self.hold[mid] = 0
                continue
            r = self._eval(mm)
            was = mid in self.captured
            if self._inside(mm, r, self.release_factor if was else 1.0):
                if mm["kind"] in ("sleeve_seat", "part_seat"):
                    self._capture_seat(mm, r)
                else:
                    self._capture_horn(mm, r)
                if not was:
                    self.events.append((sim_time, "ENGAGED %s" % mid))
            elif was:
                self._release(mid)
                self.events.append((sim_time, "released from capture %s" % mid))
            if mid not in self.captured:
                self.state[mid] = "FREE"
                self.hold[mid] = 0
                continue
            still = r["holds"] and r.get("rel_speed", 1.0) < tk.rel_speed_max and r.get("rel_ang_speed", 0.0) < tk.rest_ang_speed_max
            if not still:
                self.state[mid] = "ENGAGED"
                self.hold[mid] = 0
                continue
            self.hold[mid] += 1
            self.state[mid] = "HOLDING"
            if self.hold[mid] < tk.hold_ticks:
                continue
            self._lock(mm, r, sim_time)

    def _lock(self, mm, r, sim_time):
        """The tracker's latch (fasten, snap, record) with the soft coupling turned rigid."""
        tk = self.tk
        st = tk.state[mm["id"]]
        st["fastened"] = True
        st["achieved"] = bool(r.get("design_yaw_ok", True)) if mm["kind"] == "horn_mate" else True
        st["t_s"] = float(sim_time)
        st["pos_err_mm"] = r["pos_err"] * 1000.0
        st["ang_err_deg"] = math.degrees(r["ang_err"])
        keys = self.captured.pop(mm["id"], [])
        if mm["kind"] in ("sleeve_seat", "part_seat"):
            st["servo"] = r["servo"]
            if r["servo"] in tk.meta["servos"]:
                tk.servo_used[r["servo"]] = mm["id"]
                tk.seat_servo[mm["id"]] = r["servo"]
            tk._snap_seat(self.d, mm, r["servo"])
        else:
            st["axial_err_mm"] = r["axial"] * 1000.0
            st["yaw_err_deg"] = math.degrees(r["yaw_res"])
            st["servos"] = [phys for _d, phys in r["servos"]]
            st["servo"] = st["servos"][0]
            angles = {phys: float(self.d.qpos[self.m.jnt_qposadr[tk.horn_joint[phys]]]) for _d, phys in r["servos"]}
            st["horn_angles_at_snap_rad"] = angles
            st["joint_zero_offset_rad"] = tk._joint_zero_offset(mm, r, angles)
            st["servo_drive"] = tk._servo_drive(mm, r, angles)
            tk._snap_horn(self.d, mm, r)
            for phys in st["servos"]:
                if self.hold_mode == "lock":
                    self._brake(phys)
                else:
                    self._limit(mm, phys, st["servo_drive"][phys])
        for key in keys:
            self._set_soft(key, False)
        self.state[mm["id"]] = "LOCKED" if st["achieved"] else "LOCKED_OFF"
        text = ("LOCKED %s" % mm["id"]) if st["achieved"] else ("LOCKED_OFF %s (rotation %.1f deg from the design)" % (mm["id"], st["yaw_err_deg"]))
        self.events.append((sim_time, text))
        tk.events.append(("seated:" if st["achieved"] else "fastened_off_design:") + mm["id"])
