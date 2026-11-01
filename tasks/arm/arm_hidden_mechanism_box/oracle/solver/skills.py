"""Scripted manipulation primitives for the hidden-mechanism box (oracle side).

All primitives work in the BOX frame given a box pose (x, y, yaw) and drive the arm through the
public action interface only (gripper-site delta targets at 10 Hz).
"""

import math

import numpy as np

DEBUG = None
TIP = 0.0095           # fingertip beyond the grip site along the approach axis (m)


def rotz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def quat_to_mat(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def rotvec(R):
    """Rotation vector of a rotation matrix."""
    c = (np.trace(R) - 1) / 2
    c = min(1.0, max(-1.0, c))
    th = math.acos(c)
    if th < 1e-9:
        return np.zeros(3)
    if th > math.pi - 1e-4:
        # near pi: use the symmetric part
        A = (R + np.eye(3)) / 2
        k = np.sqrt(np.maximum(np.diag(A), 0))
        i = int(np.argmax(k))
        k = A[:, i] / max(1e-9, math.sqrt(A[i, i]))
        return th * k / np.linalg.norm(k)
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2 * math.sin(th))
    return th * v


def axis_angle(v):
    v = np.asarray(v, dtype=float)
    th = float(np.linalg.norm(v))
    if th < 1e-12:
        return np.eye(3)
    k = v / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * K @ K


def frame(approach, opening):
    """Grip-site rotation with z = approach direction and x = opening (finger) direction."""
    z = np.asarray(approach, float)
    z = z / np.linalg.norm(z)
    x = np.asarray(opening, float)
    x = x - np.dot(x, z) * z
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    return np.stack([x, y, z], axis=1)


class Arm(object):
    """Closed-loop target tracking on top of a step function: step(action_dict) -> obs."""

    def __init__(self, step_fn, obs, budget):
        self.step_fn = step_fn
        self.obs = obs
        self.budget = budget          # remaining control steps (shared)
        self.grip = -1.0
        self.log = []

    @property
    def pos(self):
        return np.asarray(self.obs["ee_pos"], float)

    @property
    def R(self):
        return quat_to_mat(np.asarray(self.obs["ee_quat"], float))

    @property
    def force(self):
        return np.asarray(self.obs["wrench"][:3], float)

    def out_of_steps(self):
        return self.budget[0] <= 0

    def act(self, dpos, drot, grip=None, render=()):
        if grip is not None:
            self.grip = grip
        if self.budget[0] <= 0:
            raise StopIteration
        self.budget[0] -= 1
        cams = 0
        for i, n in enumerate(("left", "right", "wrist")):
            if n in render:
                cams |= 1 << i
        a = [float(v) for v in list(dpos) + list(drot)] + [float(self.grip), float(cams)]
        self.obs = self.step_fn(a)
        return self.obs

    def goto(self, p, R, grip=None, tol=0.004, rtol=0.05, max_steps=120, speed=0.03, settle=2, render=()):
        p = np.asarray(p, float)
        ok = 0
        for _ in range(max_steps):
            e = p - self.pos
            n = np.linalg.norm(e)
            dp = e if n < speed else e * (speed / n)
            dr = rotvec(R @ self.R.T)
            if np.linalg.norm(dr) > 0.15:
                dr = dr * 0.15 / np.linalg.norm(dr)
            self.act(dp, dr, grip, render)
            if n < tol and np.linalg.norm(rotvec(R @ self.R.T)) < rtol:
                ok += 1
                if ok >= settle:
                    return True
            else:
                ok = 0
        return False

    def wait(self, n, grip=None):
        for _ in range(n):
            self.act(np.zeros(3), np.zeros(3), grip)

    def push(self, direction, dist, R, fmax=40.0, speed=0.006, max_steps=200, lead=0.012):
        """Move the target along `direction` by `dist` at constant speed, keeping orientation R.
        Stops early on a large opposing force. Returns the distance actually travelled."""
        d = np.asarray(direction, float)
        d = d / np.linalg.norm(d)
        start = self.pos.copy()
        tgt = start.copy()
        stall = 0
        for _ in range(max_steps):
            tgt = tgt + d * speed
            if np.dot(tgt - start, d) > dist:
                tgt = start + d * dist
            # keep the commanded target a bounded lead ahead of the actual position
            ahead = tgt - self.pos
            if np.dot(ahead, d) > lead:
                tgt = self.pos + d * lead + (ahead - np.dot(ahead, d) * d)
            dr = rotvec(R @ self.R.T)
            self.act(tgt - self.pos, dr * 0.5)
            trav = float(np.dot(self.pos - start, d))
            if trav >= dist - 0.002:
                return trav
            if np.dot(self.force, d) > fmax:
                stall += 1
                if stall > 8:
                    return trav
        return float(np.dot(self.pos - start, d))


def _contact_and_push(arm, d, R, depth, fthr=1.2, search=0.02, speed_search=0.001, speed=0.0015, lead=0.010,
                      lead_search=0.004):
    """Move along unit d (holding the lateral position of the start) until the opposing force rises
    by fthr (contact), then push `depth` further, position-controlled from the contact point.
    Returns (contact_found, travelled_after_contact)."""
    d = np.asarray(d, float)
    d = d / np.linalg.norm(d)
    arm.wait(3)                               # settle: no motion transient in the force baseline
    f0 = float(np.dot(arm.force, d))          # the wrist sensor reads +d when the hand pushes along d
    start = arm.pos.copy()
    found = False
    s = 0.0
    hits = 0
    hist = []
    for _ in range(int(4 * search / speed_search) + 20):
        s = min(s + speed_search, float(np.dot(arm.pos - start, d)) + lead_search)
        tgt = start + d * s
        arm.act(tgt - arm.pos, rotvec(R @ arm.R.T) * 0.5)
        if float(np.dot(arm.force, d)) - f0 > fthr:
            hits += 1                         # two readings in a row: not a noise spike
            if hits >= 2:
                found = True
                break
        else:
            hits = 0
        hist.append(float(np.dot(arm.pos - start, d)))
        if len(hist) > 15 and hist[-1] - hist[-16] < 0.001:
            return None, 0.0                  # kinematic stall: no progress and no contact force
        if np.dot(arm.pos - start, d) > search:
            break
    pc_s = float(np.dot(arm.pos - start, d))
    s = pc_s
    hist = []
    for _ in range(300):
        s = min(s + speed, pc_s + depth, float(np.dot(arm.pos - start, d)) + lead)
        tgt = start + d * s
        arm.act(tgt - arm.pos, rotvec(R @ arm.R.T) * 0.5)
        if np.dot(arm.pos - start, d) >= pc_s + depth - 0.0003:
            break
        hist.append(float(np.dot(arm.pos - start, d)))
        if (not found and len(hist) > 15 and hist[-1] - hist[-16] < 0.001 and
                float(np.dot(arm.force, d)) - f0 < fthr):
            return None, 0.0                  # kinematic stall without any contact
    return found, float(np.dot(arm.pos - start, d)) - pc_s


class BoxFrame(object):
    def __init__(self, pose, D, W, H, table_z=0.80):
        self.x, self.y, self.yaw = pose
        self.R = rotz(self.yaw)
        self.p = np.array([self.x, self.y, table_z])
        self.D, self.W, self.H = D, W, H

    def w(self, local):
        return self.p + self.R @ np.asarray(local, float)

    def v(self, local_dir):
        return self.R @ np.asarray(local_dir, float)


def face_vectors(face):
    if face == "front":
        return np.array([-1.0, 0, 0]), np.array([0, 1.0, 0])
    if face == "left":
        return np.array([0, 1.0, 0]), np.array([1.0, 0, 0])
    return np.array([0, -1.0, 0]), np.array([1.0, 0, 0])


def mount_point(bf, face, u, v):
    if face == "front":
        return np.array([-bf.D / 2, u, v])
    if face == "left":
        return np.array([u, bf.W / 2, v])
    return np.array([u, -bf.W / 2, v])


class Skills(object):
    PITCH = math.radians(10.0)     # approach pitched down from horizontal (keeps the forearm high)
    FLIP = False                    # gripper turned 180 deg about the approach axis
    REHOME_ALWAYS = True            # every move to a new part starts from the start posture

    def __init__(self, arm, bf, fk=None):
        self.arm = arm
        self.bf = bf
        self.fk = fk
        # the episode's start pose (HOME): a well-conditioned posture to return to
        self.home_p = arm.pos.copy()
        self.home_R = arm.R.copy()

    # public Panda joint limits (robosuite model)
    Q_LO = np.array([-2.8973, -1.7628, -2.8973, -3.0718, -2.8973, -0.0175, -2.8973])
    Q_HI = np.array([2.8973, 1.7628, 2.8973, -0.0698, 2.8973, 3.7525, 2.8973])

    def contorted(self):
        """True when the arm sits at a shoulder / upper-arm / flange joint limit (a posture from which
        the operational-space controller cannot reach low front targets)."""
        q = np.asarray(self.arm.obs["q"], float)
        m = np.minimum(q - self.Q_LO, self.Q_HI - q)
        return bool(min(m[0], m[1], m[2], m[6]) < 0.12)

    def rehome(self):
        """Back to the start pose (lifting first), letting the controller's null-space pull the
        joints back to a neutral posture."""
        up = self.arm.pos.copy()
        up[2] = max(up[2], self.bf.p[2] + self.bf.H + 0.10)
        self.arm.goto(up, self.arm.R, tol=0.02, max_steps=30)
        if self.fk is not None:
            # follow the grip-site poses of a straight joint-space path to the start posture, so the
            # arm leaves whatever wrist / elbow branch it is in
            q0 = np.asarray(self.arm.obs["q"], float)
            n_k = 20
            for k in range(1, n_k + 1):
                p, R = self.fk(q0 + (self.fk.home_q - q0) * k / n_k)
                self.arm.goto(p, R, tol=0.01, rtol=0.1, max_steps=3, settle=1)
            p, R = self.fk(self.fk.home_q)
            self.arm.goto(p, R, tol=0.008, rtol=0.06, max_steps=30)
            self.arm.wait(4)
            return
        self.arm.goto(self.home_p, self.home_R, tol=0.01, rtol=0.08, max_steps=80)
        self.unwind()
        self.arm.wait(8)

    def unwind(self, j7_home=math.pi / 4):
        """A wound-up flange (joint 7 far from its start value) is turned back by rotating the
        gripper about its own axis in small steps; the gripper is symmetric, so the start orientation
        turned by 180 degrees is an equally good target. Joint readings come from the observation."""
        q7 = float(self.arm.obs["q"][6])
        if abs(q7 - j7_home) < 0.6:
            return
        # rotate about the gripper axis (site z) towards the start value of joint 7
        for _ in range(60):
            q7 = float(self.arm.obs["q"][6])
            err = j7_home - q7
            if abs(err) < 0.3:
                break
            # joint 7 turns the flange about site z; a positive joint motion = positive rotation about z
            step = max(-0.15, min(0.15, err))
            Rz = self.arm.R @ axis_angle(np.array([0.0, 0.0, step]))
            self.arm.act(self.home_p - self.arm.pos, rotvec(Rz @ self.arm.R.T))
        # new start orientation for later rehomes
        self.home_R = self.arm.R.copy()

    SIDE_PITCH = math.radians(30.0)

    def side_pitch(self, n):
        """Steeper approach for a side face that turns away from the robot (yawed box)."""
        away = float(self.bf.v(n)[0])          # world x component of the outward normal
        return self.SIDE_PITCH + max(0.0, min(1.0, away / 0.4)) * math.radians(25.0)

    def face_R(self, n, o, pitch=None):
        ph = self.PITCH if pitch is None else pitch
        if pitch is None and abs(n[1]) > 0.5:
            ph = self.side_pitch(n)
        a = self.bf.v(-n) * math.cos(ph) + np.array([0, 0, -1.0]) * math.sin(ph)
        # opening axis horizontal with the forearm above the hand (site y axis pointing down)
        oo = np.cross(a, np.array([0, 0, 1.0]))
        if self.FLIP:
            oo = -oo
        return frame(a, oo), a

    def site_for_tip(self, tip_world, a):
        return tip_world - a * TIP

    def retreat(self, face, local, dist=0.10):
        n, o = face_vectors(face)
        R, a = self.face_R(n, o)
        p = self.bf.w(local + n * dist)
        self.arm.goto(p, R, tol=0.01, max_steps=60)

    def via(self, face, local_from, local_to):
        """Move between two stand-off points of the same face (stay in front of it)."""
        n, o = face_vectors(face)
        R, a = self.face_R(n, o)
        self.arm.goto(self.bf.w(local_to), R, tol=0.008, max_steps=80)

    def approach(self, face, local_pt, standoff, grip=1.0):
        n, o = face_vectors(face)
        R, a = self.face_R(n, o)
        far = self.site_for_tip(self.bf.w(local_pt + n * (standoff + 0.08)), a)
        near = self.site_for_tip(self.bf.w(local_pt + n * standoff), a)
        # go to a safe point first: above the box height if coming from elsewhere
        cur = self.arm.pos
        safe_z = self.bf.p[2] + self.bf.H + 0.10
        last = getattr(self, "last_face", None)
        if np.linalg.norm(cur - far) > 0.05 and (self.REHOME_ALWAYS or self.contorted() or
                                                 (last is not None and last != face)):
            # switching faces: go through the start posture, so the wrist takes the same branch as
            # from a fresh start (a direct re-orientation can wind the wrist into its joint limits)
            self.rehome()
            cur = self.arm.pos
        self.last_face = face
        if np.linalg.norm(cur - far) > 0.05:
            up = cur.copy()
            up[2] = max(up[2], safe_z)
            self.arm.goto(up, self.arm.R, grip=grip, tol=0.02, max_steps=40)
            ang = np.linalg.norm(rotvec(R @ self.arm.R.T))
            if ang > math.radians(50.0):
                # re-orient through a neutral gripper-down pose above the box (keeps the wrist away
                # from its limits when switching between faces)
                Rd = frame(np.array([0, 0, -1.0]), self.bf.v([0, 1.0, 0]))
                mid = 0.5 * (up + np.array([far[0], far[1], safe_z]))
                mid[2] = safe_z + 0.05
                self.arm.goto(mid, Rd, grip=grip, tol=0.03, rtol=0.1, max_steps=60)
            above = far.copy()
            above[2] = max(far[2], safe_z)
            self.arm.goto(above, R, grip=grip, tol=0.015, max_steps=100)
        self.arm.goto(far, R, grip=grip, tol=0.006, max_steps=80)
        self.arm.goto(near, R, grip=grip, tol=0.003, max_steps=80)
        if np.linalg.norm(self.arm.pos - near) > 0.01 and not getattr(self, "_reapproach", False):
            # the stand-off point was not reached (the arm ran into a joint limit on the way):
            # back off, return to the start posture and approach once more
            self._reapproach = True
            try:
                self.arm.goto(self.arm.pos + self.bf.v(n) * 0.05, self.arm.R, grip=grip, tol=0.01, max_steps=20)
                self.rehome()
                return self.approach(face, local_pt, standoff, grip)
            finally:
                self._reapproach = False
        return R

    def press(self, face, u, v, travel=0.010):
        n, _ = face_vectors(face)
        mp = mount_point(self.bf, face, u, v)
        ph = self.side_pitch(n) if abs(n[1]) > 0.5 else self.PITCH
        mp = mp + np.array([0.0, 0.0, self.PRESS_DZ * math.sin(ph)])
        R = self.approach(face, mp, 0.020)
        moved = self.arm.push(self.bf.v(-n), 0.004 + travel + 0.010, R, fmax=35.0)
        self.arm.wait(3)
        self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
        return moved

    PRESS_DZ = -0.015               # aim lower on pitched presses: the fingers meet the cap face-on, not its rim

    def press_to(self, face, u, v, depth):
        """Press a button to a given depth (from first contact with its cap)."""
        n, _ = face_vectors(face)
        mp = mount_point(self.bf, face, u, v)
        ph = self.side_pitch(n) if abs(n[1]) > 0.5 else self.PITCH
        mp = mp + np.array([0.0, 0.0, self.PRESS_DZ * math.sin(ph)])
        for _attempt in range(2):
            R = self.approach(face, mp, 0.022 + 0.02 * math.sin(ph))
            self.arm.wait(2)
            found, moved = _contact_and_push(self.arm, self.bf.v(-n), R, depth, search=0.03, lead=0.005)
            if found is not None:
                break
            self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
            self.rehome()
        self.arm.wait(2)
        self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
        return (found, round(moved, 4))

    def slide_bolt(self, u, v, direction, travel):
        """Push the bolt knob sideways (front face) by `travel` in box-y direction `direction`."""
        n, o = face_vectors("front")
        mp = mount_point(self.bf, "front", u, v)
        side = np.array([0, -direction * 0.037, 0])           # start beside the knob
        R = self.approach("front", mp + side, 0.018)
        moved = self.arm.push(self.bf.v([0, direction, 0]), travel + 0.02, R, fmax=35.0, speed=0.005)
        self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
        return moved

    def slide_bolt_to(self, u, v, direction, q):
        """Slide the bolt to displacement q (from first contact with its pin)."""
        n, o = face_vectors("front")
        mp = mount_point(self.bf, "front", u, v)
        side = np.array([0, -direction * 0.040, 0])          # start clear of the pin (yawed boxes)
        for _attempt in range(2):
            R = self.approach("front", mp + side, 0.014)
            self.arm.wait(2)
            found, moved = _contact_and_push(self.arm, self.bf.v([0, direction, 0]), R, q, search=0.03, lead=0.005)
            if found is not None:
                break
            self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
            self.rehome()                                     # stalled at a joint limit: new posture
        self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
        return (found, round(moved, 4))

    def press_slide_bolt(self, u, v, direction, travel):
        """Latched bolt: press the pin into the face and, while holding it in, slide it sideways."""
        n, o = face_vectors("front")
        mp = mount_point(self.bf, "front", u, v)
        R = self.approach("front", mp, 0.034)
        self.arm.wait(2)
        found, moved = _contact_and_push(self.arm, self.bf.v(-n), R, 0.0065, search=0.015)
        din = self.bf.v(-n)
        dside = self.bf.v([0, direction, 0])
        pc = self.arm.pos.copy()
        tgt = pc + din * 0.003
        start = pc.copy()
        for _ in range(120):
            tgt = tgt + dside * 0.003
            if np.dot(tgt - start, dside) > travel + 0.008:
                tgt = start + din * 0.003 + dside * (travel + 0.008)
            # keep pressing: inward target 3 mm beyond the current depth
            cur = self.arm.pos
            want = tgt.copy()
            want = want - din * np.dot(want - cur, din) + din * (np.dot(cur - start, din) + 0.003)
            self.arm.act(want - cur, rotvec(R @ self.arm.R.T) * 0.5)
            if np.dot(self.arm.pos - start, dside) >= travel + 0.004:
                break
        self.arm.wait(2)
        self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
        return (found, round(float(np.dot(self.arm.pos - start, dside)), 4))

    def turn_knob(self, u, v, sense, r_push=0.022, lead=0.004, final_deg=96.0, end=None, retry=True):
        """Push one end of the knob's lever around its axis with the closed fingertip block up to
        `final_deg` (sense = +1 / -1 about the inward axis +x_box; the lever is vertical at 0).
        end = +1 pushes the upper end, -1 the lower end (default: lower end when the knob sits within
        5 cm of the box top). The lever's friction gives a tangential wrist force while the fingertip
        pushes it; if that force vanishes early (the fingertip slipped off the lever, e.g. after a
        depth error of the pose estimate) a full turn is resumed from a conservative angle with a
        deeper fingertip and then with the other lever end."""
        if end is None:
            end = -1 if v > self.bf.H - 0.05 else 1
        cmd, slipped = self._turn_once(u, v, sense, r_push, lead, final_deg, end, 0.016, 0.0)
        if not retry:
            return True
        for e_, depth in ((end, 0.014), (-end, 0.016), (-end, 0.019)):
            if not slipped:
                return True
            a0 = max(0.0, cmd - 25.0)
            cmd, slipped = self._turn_once(u, v, sense, r_push, lead, final_deg, e_, depth, a0)
        return not slipped

    def _turn_once(self, u, v, sense, r_push, lead, final_deg, end, depth, a0):
        """One pass along the lever's arc from a0. Returns (commanded angle at which the lever was
        last felt, slipped) where slipped = the tangential force vanished before 80 degrees."""
        n, o = face_vectors("front")
        c = mount_point(self.bf, "front", u, v)
        R, a = self.face_R(n, o)

        def pt(theta, extra):
            th = sense * theta
            p = end * np.array([0.0, -r_push * math.sin(th), r_push * math.cos(th)])
            t = end * np.array([0.0, -math.cos(th), -math.sin(th)]) * sense      # motion direction of that end
            half = abs(t[1]) * 0.027 + abs(t[2]) * 0.011 + 0.004 + extra    # block support + lever half
            return c + p - t * half

        def tdir(theta):
            th = sense * theta
            return self.bf.v(end * np.array([0.0, -math.cos(th), -math.sin(th)]) * sense)

        self.approach("front", pt(math.radians(a0), 0.006 if a0 > 0 else 0.004), depth)
        self.arm.wait(2)
        f0 = self.arm.force.copy()              # hand weight and sensor bias, no contact
        felt, touched, lost, slipped = a0, False, 0, False
        nk = max(1, int(math.ceil((final_deg - a0) / 2.0)))
        for k in range(1, nk + 1):
            th = math.radians(min(final_deg, a0 + 2.0 * k))
            tgt = self.site_for_tip(self.bf.w(pt(th, -lead) + n * depth), a)
            self.arm.goto(tgt, R, tol=0.003, max_steps=4, settle=1, speed=0.01)
            ft = float(np.dot(self.arm.force - f0, tdir(th)))
            if DEBUG is not None:
                DEBUG("turn end=%d depth=%.3f cmd=%.0f ft=%.1f" % (end, depth, math.degrees(th), ft))
            if ft > 2.0:
                touched, lost, felt = True, 0, math.degrees(th)
            elif touched:
                lost += 1
                if lost >= 5 and felt < 80.0:
                    slipped = True
                    break
        if not touched and a0 < 60.0:
            slipped = True
        for k in range(1, 4):
            th = math.radians(max(0.0, felt - 3.0 * k))
            tgt = self.site_for_tip(self.bf.w(pt(th, 0.006) + n * depth), a)
            self.arm.goto(tgt, R, tol=0.003, max_steps=4, settle=1, speed=0.01)
        self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
        return felt, slipped

    def turn_knob_to(self, u, v, sense, target_deg, r_push=0.022, depth=0.016, end=None, lag_deg=8.0):
        """Turn a knob to `target_deg` (a window centre) without relying on the fingertip model: touch
        the lever at its start angle (0) by a slow tangential approach until the lever's friction is
        felt, then rotate that measured contact configuration rigidly about the knob axis by the target
        angle (plus a small compliance lag)."""
        if end is None:
            end = -1 if v > self.bf.H - 0.05 else 1
        n, o = face_vectors("front")
        c = mount_point(self.bf, "front", u, v)
        R, a = self.face_R(n, o)
        t0 = end * np.array([0.0, -1.0, 0.0]) * sense                 # motion of that lever end at 0 deg
        p_end = c + end * np.array([0.0, 0.0, r_push])
        start = p_end - t0 * (0.027 + 0.004 + 0.010)                  # 10 mm clear of the lever side
        self.approach("front", start, depth)
        self.arm.wait(4)
        f0 = self.arm.force.copy()
        d = self.bf.v(t0)
        p0 = self.arm.pos.copy()
        tip_c = None
        hits = 0
        s_ = 0.0
        for _ in range(90):
            s_ = min(s_ + 0.0015, float(np.dot(self.arm.pos - p0, d)) + 0.006)
            tgt = p0 + d * s_
            self.arm.act(tgt - self.arm.pos, rotvec(R @ self.arm.R.T) * 0.5)
            if float(np.dot(self.arm.force - f0, d)) > 2.0:
                hits += 1
                if hits >= 2:
                    tip_c = self.bf.R.T @ (self.arm.pos + a * TIP - self.bf.p)
                    break
            else:
                hits = 0
            if np.dot(self.arm.pos - p0, d) > 0.05:
                break
        if tip_c is None:
            self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
            return False
        ax = np.array([1.0, 0.0, 0.0]) * sense                       # unlock rotation axis (box frame)
        rel = tip_c - c

        def rot(theta):
            ct, st = math.cos(theta), math.sin(theta)
            return c + rel * ct + np.cross(ax, rel) * st + ax * np.dot(ax, rel) * (1 - ct)

        goal = target_deg + lag_deg
        ang = 0.0
        while ang < goal:
            ang = min(goal, ang + 2.0)
            tgt = self.site_for_tip(self.bf.w(rot(math.radians(ang)) + self.bf.R.T @ (d * 0.0) ), a)
            self.arm.goto(tgt, R, tol=0.002, max_steps=4, settle=1, speed=0.01)
        self.arm.wait(3)
        # back off along the arc a little, then away from the face
        tgt = self.site_for_tip(self.bf.w(rot(math.radians(max(0.0, goal - 8.0)))), a)
        self.arm.goto(tgt, R, tol=0.003, max_steps=6, settle=1, speed=0.01)
        self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
        return True

    def panel_up(self, u, v, h, travel):
        n, o = face_vectors("front")
        mp = mount_point(self.bf, "front", u, v)
        rib = mp + np.array([0, 0, -h / 2 - 0.015])
        R = self.approach("front", rib, 0.029)
        moved = self.arm.push(self.bf.v([0, 0, 1]), travel + 0.03, R, fmax=35.0, speed=0.005)
        self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
        return moved

    def panel_side(self, u, v, w, direction, travel):
        n, o = face_vectors("front")
        mp = mount_point(self.bf, "front", u, v)
        rib = mp + np.array([0, -direction * (w / 2 + 0.033), 0])
        R = self.approach("front", rib, 0.029)
        moved = self.arm.push(self.bf.v([0, direction, 0]), travel + 0.03, R, fmax=35.0, speed=0.005)
        self.arm.push(self.bf.v(n), 0.06, R, fmax=1e9, speed=0.01)
        return moved

    def open_lid(self, lip_u=0.0, lid_t=0.012):
        bf = self.bf
        n, o = face_vectors("front")
        hinge = np.array([bf.D / 2, 0.0, bf.H])
        lip = np.array([-bf.D / 2 - 0.016, lip_u, bf.H])       # under the lip, near its tip
        R, a = self.face_R(n, o, pitch=0.0)
        start = lip + np.array([0, 0, -0.016])
        old = self.PITCH
        self.PITCH = 0.0
        self.approach("front", start, 0.0)
        self.PITCH = old
        # follow the arc of the lip, staying slightly below the lid's underside
        for k in range(0, 60):
            th = math.radians(min(105.0, 2.0 * k))
            Ry = np.array([[math.cos(th), 0, math.sin(th)], [0, 1, 0], [-math.sin(th), 0, math.cos(th)]])
            rel = lip - hinge
            p = hinge + Ry @ rel + Ry @ np.array([0, 0, -0.006])
            # tip at p: site is TIP behind along approach (+x box)
            tgt = self.site_for_tip(bf.w(p), a)
            self.arm.goto(tgt, R, tol=0.005, max_steps=5, settle=1, speed=0.02)
        self.arm.wait(5)
        return True

    def pull_drawer(self, u, v, travel, standoff=0.045):
        """Pinch the vertical handle bar with a horizontal approach and pull the drawer out."""
        n, o = face_vectors("front")
        bf = self.bf
        bar = np.array([-bf.D / 2 - standoff, u, v])
        old = self.PITCH
        self.PITCH = 0.0
        R = self.approach("front", bar + np.array([0.012, 0, 0]), 0.0, grip=-1.0)
        self.PITCH = old
        self.arm.wait(12, grip=1.0)
        moved = self.arm.push(bf.v([-1, 0, 0]), travel + 0.01, R, fmax=60.0, speed=0.006)
        self.arm.wait(8, grip=-1.0)
        self.arm.push(bf.v([-1, 0, 0]), 0.05, R, fmax=1e9, speed=0.01)
        return moved
