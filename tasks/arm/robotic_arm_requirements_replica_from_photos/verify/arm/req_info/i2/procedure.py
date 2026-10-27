"""The brief's acceptance procedure B-14 (rung I2) on an ARBITRARY arm model with an INJECTED catalogue (public,
catalogue-free code: every catalogue, lot and price number comes from the data passed in).

    arm = ArmModel(xml=..., slots=..., stops=..., roll_zero=..., jaw_hold=..., jaw_lever_m=..., jaw_stroke_rad=...,
                   pocket_class=..., printed_moving_mass_kg=..., servo_box_m=...)
    acc = Acceptance(arm, layer)             # grid, elbow-up IK, moves (both directions), holds, mass-regressor traces
    res = acc.evaluate(bom, lot, cat, brief) # nine binary items + utilisations + currents (C4, repair, optimum)
    der = acc.derived(bom, lot, cat, brief)  # the 15 graded derived items (C3)

`cat` = catalog.Catalogue (the grader's truth or the agent's transcription), `lot` = a lot truth (lotmodel), `brief` = the
brief constants of the instance (layer.json "brief"). Grid, IK, moves and the regressor are those of the rung-I1 engine
(req_info/acceptance.py, unchanged there); the rung-I2 pass rule of a joint uses the lot's torque-speed characteristic with
its droop (lotmodel.droop_fraction): every move sample needs |tau| + J_out |qdd| <= tau_s u*(|qd| / w0, d).

Procedure (brief B-14): grid: r in 6 equal steps over [r_min, r_max], azimuth in 7 equal steps over [az_c - half,
az_c + half], z in {z_lo, (z_lo + z_max) / 2, z_max}, the approach pitch(es) of the instance, roll 0; IK elbow-up inside
the stops. Moves: for each of base_yaw, shoulder_pitch, elbow_pitch, wrist_pitch the 12 not yet selected pairs with the
largest |dq_j| (ties: lowest index pair), plus the sector-corner pairs; both directions; joint-space trapezoid with equal
thirds in time T; payload m_hi. Loads: inverse dynamics with the BOM's servo masses at the slots (point mass + box
inertia), the payload as a point mass on the TCP +z axis at the CoM offset. Pass rules: brief B-14 f-j.
"""
import hashlib
import json
import math

import numpy as np

from . import lotmodel as LM

JOINTS = ("base_yaw", "shoulder_pitch", "elbow_pitch", "wrist_pitch", "wrist_roll", "jaw")
ARM_JOINTS = JOINTS[:5]
MOVING_PITCH = ("base_yaw", "shoulder_pitch", "elbow_pitch", "wrist_pitch")
SLOT_JOINTS = JOINTS[1:]            # servos S2..S6 move with the arm (S1 sits in the fixed base)
ITEMS = JOINTS + ("psu", "bus_board", "budget")
G = 9.81
DT = 0.005


class ArmModel:
    def __init__(self, xml, assets=None, slots=None, stops=None, roll_zero=0.0, jaw_hold=0.0, jaw_lever_m=0.08,
                 jaw_stroke_rad=1.9, pocket_class=None, printed_moving_mass_kg=None, tcp_site="tcp", name="arm",
                 servo_box_m=None):
        self.xml = xml
        self.assets = dict(assets or {})
        self.slots = {j: (s[0], tuple(float(x) for x in s[1]), tuple(float(x) for x in s[2])) for j, s in (slots or {}).items()}
        self.stops = {j: (float(v[0]), float(v[1])) for j, v in (stops or {}).items()}
        self.roll_zero = float(roll_zero)
        self.jaw_hold = float(jaw_hold)
        self.jaw_lever_m = float(jaw_lever_m)
        self.jaw_stroke_rad = float(jaw_stroke_rad)
        self.pocket_class = dict(pocket_class or {j: "standard" for j in JOINTS})
        self.printed_moving_mass_kg = printed_moving_mass_kg
        self.tcp_site = tcp_site
        self.name = name
        self.servo_box_m = None if servo_box_m is None else tuple(float(x) for x in servo_box_m)

    def box_i_per_kg(self):
        """Box inertia per unit mass of a servo point mass (brief B-14 d); the box = arm_model.json servo_box_m."""
        if self.servo_box_m is None:
            raise ValueError("arm model without servo_box_m (the servo box of brief B-14 d)")
        L, W, H = self.servo_box_m
        return np.array([(W ** 2 + H ** 2) / 12.0, (L ** 2 + H ** 2) / 12.0, (L ** 2 + W ** 2) / 12.0])

    def digest(self):
        h = hashlib.sha256()
        h.update(self.xml.encode())
        for k in sorted(self.assets):
            h.update(k.encode())
            h.update(hashlib.sha256(self.assets[k]).digest())
        h.update(json.dumps([self.slots, self.stops, self.roll_zero, self.jaw_hold, self.jaw_lever_m, self.jaw_stroke_rad,
                             self.pocket_class, self.servo_box_m], sort_keys=True).encode())
        return h.hexdigest()


def _mujoco():
    import mujoco
    return mujoco


def build_dynamics(arm, com_offset):
    """Compile the arm with one massless servo body per slot and a payload body; zero armature / damping / friction.
    Returns (model, data, slot_body_ids {joint: id}, payload_body_id, ids)."""
    mj = _mujoco()
    spec = mj.MjSpec.from_string(arm.xml, assets=arm.assets) if arm.assets else mj.MjSpec.from_string(arm.xml)
    spec.option.gravity = [0.0, 0.0, -G]
    for j in spec.joints:
        j.armature = 0.0
        j.damping = [0.0, 0.0, 0.0]
        j.frictionloss = 0.0
    for a in list(spec.actuators):
        spec.delete(a)
    for e in list(spec.equalities):
        spec.delete(e)
    for g in spec.geoms:            # dynamics only: no contacts
        g.contype = 0
        g.conaffinity = 0
    for j, (body, pos, quat) in arm.slots.items():
        b = spec.body(body)
        if b is None:
            raise ValueError("slot body %r of %s not in the model" % (body, j))
        sb = b.add_body()
        sb.name = "reqslot_" + j
        sb.pos = list(pos)
        sb.quat = list(quat)
        sb.mass = 1.0
        sb.inertia = list(arm.box_i_per_kg())
        sb.explicitinertial = True
    site = spec.site(arm.tcp_site)
    if site is None:
        raise ValueError("no site %r" % arm.tcp_site)
    parent = site.parent
    pb = parent.add_body()
    pb.name = "reqpayload"
    # payload CoM on the TCP +z axis at com_offset: site pose in its body
    q = np.array(site.quat, float)
    q = q / np.linalg.norm(q)
    R = np.zeros(9)
    mj.mju_quat2Mat(R, q)
    zc = R.reshape(3, 3)[:, 2]
    pb.pos = (np.array(site.pos, float) + com_offset * zc).tolist()
    pb.mass = 1.0
    pb.inertia = [1e-9, 1e-9, 1e-9]
    pb.explicitinertial = True
    model = spec.compile()
    data = mj.MjData(model)
    ids = {n: mj.mj_name2id(model, mj.mjtObj.mjOBJ_JOINT, n) for n in JOINTS}
    for n, i in ids.items():
        if i < 0:
            raise ValueError("joint %s missing" % n)
    slot_ids = {j: mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, "reqslot_" + j) for j in arm.slots}
    pay = mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, "reqpayload")
    return model, data, slot_ids, pay, ids


def grid_targets(layer):
    """The 252 TCP targets [(x, y, z, pitch_rad)] in grid order (r, az, z, pitch); plus index helpers."""
    r = np.linspace(layer["r_min"], layer["r_max"], 6)
    az = np.linspace(layer["az_c"] - layer["az_half"], layer["az_c"] + layer["az_half"], 7)
    z = np.array([layer["z_lo"], 0.5 * (layer["z_lo"] + layer["z_max"]), layer["z_max"]])
    pitch = np.radians(layer.get("pitches_deg", [-90.0, -45.0]))
    out = []
    idx = {}
    for i, rr in enumerate(r):
        for k, aa in enumerate(az):
            for m, zz in enumerate(z):
                for p, pp in enumerate(pitch):
                    idx[(i, k, m, p)] = len(out)
                    out.append((-rr * math.sin(aa), rr * math.cos(aa), float(zz), float(pp)))
    return out, idx


def corner_pairs(idx):
    pairs = []
    n_p = 1 + max(k[3] for k in idx)
    for p in range(n_p):
        c = lambda i, k, m: idx[(i, k, m, p)]  # noqa: E731
        # 4 space diagonals of the corner box (r, az, z)
        pairs += [(c(0, 0, 0), c(5, 6, 2)), (c(0, 6, 0), c(5, 0, 2)), (c(5, 0, 0), c(0, 6, 2)), (c(5, 6, 0), c(0, 0, 2))]
        # 4 azimuth edges
        pairs += [(c(0, 0, 0), c(0, 6, 0)), (c(5, 0, 0), c(5, 6, 0)), (c(0, 0, 2), c(0, 6, 2)), (c(5, 0, 2), c(5, 6, 2))]
    return pairs


_EYE4 = np.eye(4)


def _wrap(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def trapezoid(q0, q1, T, dt=DT):
    n = int(round(T / dt))
    t = np.arange(n + 1) * (T / n)
    d = q1 - q0
    ta = T / 3.0
    vmax = 1.5 / T
    acc = vmax / ta
    s = np.where(t < ta, 0.5 * acc * t ** 2, np.where(t < 2 * ta, 0.5 * acc * ta ** 2 + vmax * (t - ta),
                                                         1.0 - 0.5 * acc * (T - t) ** 2))
    sd = np.where(t < ta, acc * t, np.where(t < 2 * ta, vmax, acc * (T - t)))
    sdd = np.where(t < ta, acc, np.where(t < 2 * ta, 0.0, -acc))
    return q0 + np.outer(s, d), np.outer(sd, d), np.outer(sdd, d), t


class Acceptance:
    """Grid, IK, moves and the regressor traces of one (arm, layer). Heavy work happens once in __init__."""

    def __init__(self, arm, layer, ik_cache=None):
        self.arm = arm
        self.layer = dict(layer)
        self.model, self.data, self.slot_ids, self.pay_id, self.jid = build_dynamics(arm, layer.get("com_offset", 0.025))
        mj = _mujoco()
        self.qadr = np.array([self.model.jnt_qposadr[self.jid[n]] for n in JOINTS])
        self.dadr = np.array([self.model.jnt_dofadr[self.jid[n]] for n in JOINTS])
        self.site = mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_SITE, arm.tcp_site)
        self._jp = np.zeros((3, self.model.nv))
        self._jr = np.zeros((3, self.model.nv))
        self.targets, self.gidx = grid_targets(self.layer)
        self.ik = ik_cache if ik_cache is not None else self.solve_grid()
        self.q_grid = np.array([s["q"] for s in self.ik])
        self.pairs = self.select_moves()
        self._build_samples()
        self._regress()

    @classmethod
    def ik_only(cls, arm, com_offset=0.025):
        """An object with the model and the IK solver only (reach maps, quick feasibility probes)."""
        mj = _mujoco()
        self = cls.__new__(cls)
        self.arm = arm
        self.layer = {}
        self.model, self.data, self.slot_ids, self.pay_id, self.jid = build_dynamics(arm, com_offset)
        self.qadr = np.array([self.model.jnt_qposadr[self.jid[n]] for n in JOINTS])
        self.dadr = np.array([self.model.jnt_dofadr[self.jid[n]] for n in JOINTS])
        self.site = mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_SITE, arm.tcp_site)
        self._jp = np.zeros((3, self.model.nv))
        self._jr = np.zeros((3, self.model.nv))
        return self

    def solve_targets(self, targets, seeds=None, n_seeds=24):
        seeds = seeds or self._seed_guesses(n_seeds)
        out = []
        prev = None
        for t in targets:
            guesses = ([np.array(prev["q"][:4])] if prev is not None and prev["ok"] and prev["elbow_up"] else []) + list(seeds)
            s = self._solve_one(t, guesses)
            out.append(s)
            prev = s
        return out

    # ------------------------------------------------------------------ kinematics
    def _set_q(self, q6, qd6=None, qdd6=None):
        d = self.data
        d.qpos[:] = 0.0
        d.qvel[:] = 0.0
        d.qacc[:] = 0.0
        d.qpos[self.qadr] = q6
        if qd6 is not None:
            d.qvel[self.dadr] = qd6
        if qdd6 is not None:
            d.qacc[self.dadr] = qdd6

    def fk(self, q6):
        mj = _mujoco()
        self._set_q(q6)
        mj.mj_kinematics(self.model, self.data)
        p = self.data.site_xpos[self.site].copy()
        R = self.data.site_xmat[self.site].reshape(3, 3).copy()
        return p, R

    def joint_points(self, q6):
        mj = _mujoco()
        self._set_q(q6)
        mj.mj_kinematics(self.model, self.data)
        m, d = self.model, self.data
        pts = {}
        for n in JOINTS:                                 # the joint's point in world: body pose x jnt_pos
            j = self.jid[n]
            b = m.jnt_bodyid[j]
            pts[n] = d.xpos[b] + d.xmat[b].reshape(3, 3) @ m.jnt_pos[j]
        return pts, {n: d.xaxis[self.jid[n]].copy() for n in JOINTS}

    def _full_q(self, q4):
        return np.array([q4[0], q4[1], q4[2], q4[3], self.arm.roll_zero, self.arm.jaw_hold])

    def _res_jac(self, q4, target):
        """Residual (4) and its analytic Jacobian (4 x 4) for the 4 IK joints (roll / jaw fixed)."""
        mj = _mujoco()
        x, y, z, pitch = target
        d = self.data
        d.qpos[self.qadr[:4]] = q4
        d.qpos[self.qadr[4]] = self.arm.roll_zero
        d.qpos[self.qadr[5]] = self.arm.jaw_hold
        mj.mj_kinematics(self.model, self.data)
        mj.mj_comPos(self.model, self.data)
        p = self.data.site_xpos[self.site]
        R = self.data.site_xmat[self.site].reshape(3, 3)
        zt = R[:, 2].copy()
        jp, jr = self._jp, self._jr
        mj.mj_jacSite(self.model, self.data, jp, jr, self.site)
        cols = self.dadr[:4]
        Jp = jp[:, cols]
        Jr = jr[:, cols]
        dzt = np.empty((3, 4))                             # d zt / dq_i = w_i x zt
        dzt[0] = Jr[1] * zt[2] - Jr[2] * zt[1]
        dzt[1] = Jr[2] * zt[0] - Jr[0] * zt[2]
        dzt[2] = Jr[0] * zt[1] - Jr[1] * zt[0]
        rn = math.hypot(x, y)
        yaw_dir = np.array([x / rn, y / rn, 0.0]) if rn > 1e-9 else np.array([0.0, 1.0, 0.0])   # outward = the target's radial
        h = np.array([zt[0], zt[1], 0.0])
        hn = float(np.linalg.norm(h))
        sgn = 1.0 if np.dot(h, yaw_dir) >= 0 else -1.0
        a = zt[2]
        b = sgn * hn
        ang = math.atan2(a, b)
        da = dzt[2]
        db = sgn * (h[0] * dzt[0] + h[1] * dzt[1]) / max(hn, 1e-12)
        dang = (b * da - a * db) / max(a * a + b * b, 1e-12)
        r = np.array([(p[0] - x) * 100.0, (p[1] - y) * 100.0, (p[2] - z) * 100.0, 0.5 * _wrap(ang - pitch)])
        J = np.vstack([Jp * 100.0, 0.5 * dang[None, :]])
        return r, J

    def _residual(self, q4, target):
        return self._res_jac(q4, target)[0]

    def _lm(self, q, target, lo, hi, iters=60, early=True):
        lam = 1e-3
        r, J = self._res_jac(q, target)
        f = float(r @ r)
        for _ in range(iters):
            if f < 1e-18:
                break
            A = J.T @ J
            g = J.T @ r
            M = A + lam * (np.diag(np.diag(A)) + 1e-9 * _EYE4)
            step = -np.linalg.solve(M, g)
            qn = np.clip(q + step, lo, hi)
            rn, Jn = self._res_jac(qn, target)
            fn = float(rn @ rn)
            if fn < f:
                q, r, J, f = qn, rn, Jn, fn
                lam = max(lam * 0.3, 1e-9)
            else:
                lam *= 10.0
                if lam > 1e8:
                    break
            if early and _ > 15 and f > 1e-2:          # hopeless start (a bound-stuck local minimum): give up early
                break
        return q, r

    def elbow_up(self, q6):
        pts, _ = self.joint_points(q6)
        s, e, w = pts["shoulder_pitch"], pts["elbow_pitch"], pts["wrist_pitch"]
        d = w - s
        dn = d / max(np.linalg.norm(d), 1e-12)
        v = (e - s) - np.dot(e - s, dn) * dn
        up = np.array([0.0, 0.0, 1.0]) - dn[2] * dn
        return float(np.dot(v, up)) > 0.0

    def _solve_one(self, target, guesses):
        lo = np.array([self.arm.stops[n][0] for n in JOINTS[:4]])
        hi = np.array([self.arm.stops[n][1] for n in JOINTS[:4]])
        best = None
        for g in guesses:
            g = np.clip(np.asarray(g, float), lo, hi)
            q, res = self._lm(g, target, lo, hi)
            pos_err = float(np.linalg.norm(res[:3]) / 100.0)
            ang_err = float(abs(res[3]) / 0.5)
            ok = pos_err < 1e-6 and ang_err < 1e-6
            q6 = self._full_q(q)
            eu = self.elbow_up(q6) if ok else False
            key = (0 if (ok and eu) else 1 if ok else 2, pos_err + ang_err)
            if best is None or key < best[0]:
                best = (key, q6, ok, eu, pos_err, ang_err)
            if ok and eu:
                break
        _, q6, ok, eu, pe, ae = best
        margins = [min(q6[i] - self.arm.stops[n][0], self.arm.stops[n][1] - q6[i]) for i, n in enumerate(JOINTS[:4])]
        return {"q": [float(v) for v in q6], "ok": bool(ok), "elbow_up": bool(eu), "pos_err_m": pe, "ang_err_rad": ae,
                "stop_margin_rad": float(min(margins))}

    def _seed_guesses(self, n=24):
        lo = np.array([self.arm.stops[n_][0] for n_ in JOINTS[:4]])
        hi = np.array([self.arm.stops[n_][1] for n_ in JOINTS[:4]])
        out = []
        # Halton-like deterministic points inside the stops
        primes = (2, 3, 5, 7)
        for i in range(1, n + 1):
            u = []
            for p in primes:
                f, r, k = 1.0, 0.0, i
                while k > 0:
                    f /= p
                    r += f * (k % p)
                    k //= p
                u.append(r)
            out.append(lo + (hi - lo) * np.array(u))
        return out

    def solve_grid(self):
        seeds = self._seed_guesses()
        sol = [None] * len(self.targets)
        order = sorted(range(len(self.targets)), key=lambda i: i)
        prev = {}
        for i in order:
            t = self.targets[i]
            key = i // 2  # same (r, az, z) other pitch shares the neighbour list
            guesses = []
            for j in (i - 2, i - 1, i - 6, i - 42):
                if 0 <= j < i and sol[j] is not None and sol[j]["ok"] and sol[j]["elbow_up"]:
                    guesses.append(np.array(sol[j]["q"][:4]))
            if not guesses:
                guesses = seeds
            s = self._solve_one(t, guesses)
            if not (s["ok"] and s["elbow_up"]) and guesses is not seeds:
                s = self._solve_one(t, seeds)
            sol[i] = s
            prev[key] = s
        return sol

    def grid_ok(self, margin_rad=0.0):
        return all(s["ok"] and s["elbow_up"] and s["stop_margin_rad"] >= margin_rad - 1e-12 for s in self.ik)

    # ------------------------------------------------------------------ moves
    def select_moves(self):
        Q = self.q_grid
        n = len(Q)
        iu, ju = np.triu_indices(n, 1)
        chosen = []
        seen = set()
        for jn in MOVING_PITCH:
            k = JOINTS.index(jn)
            d = np.abs(Q[iu, k] - Q[ju, k])
            order = np.lexsort((ju, iu, -d))       # largest |dq|, ties: lowest (i, j)
            cnt = 0
            for o in order:
                pr = (int(iu[o]), int(ju[o]))
                if pr in seen:
                    continue
                seen.add(pr)
                chosen.append(pr)
                cnt += 1
                if cnt == 12:
                    break
        for a, b in corner_pairs(self.gidx):
            pr = (min(a, b), max(a, b))
            if pr in seen:
                continue
            seen.add(pr)
            chosen.append(pr)
        # direction both ways
        return chosen

    def _build_samples(self):
        T = float(self.layer["T"])
        Qs, Vs, As, mid, t_all = [], [], [], [], []
        for m, (a, b) in enumerate(self.pairs):
            for direction, (i0, i1) in enumerate(((a, b), (b, a))):
                q0, q1 = self.q_grid[i0], self.q_grid[i1]
                Q, V, A, t = trapezoid(q0, q1, T)
                Qs.append(Q)
                Vs.append(V)
                As.append(A)
                mid += [2 * m + direction] * len(Q)
        self.mv_q = np.concatenate(Qs)
        self.mv_qd = np.concatenate(Vs)
        self.mv_qdd = np.concatenate(As)
        self.mv_id = np.array(mid)
        self.n_moves = 2 * len(self.pairs)

    def _inverse(self, Q, V, A):
        mj = _mujoco()
        out = np.zeros((len(Q), 6))
        for i in range(len(Q)):
            self._set_q(Q[i], V[i] if V is not None else None, A[i] if A is not None else None)
            mj.mj_inverse(self.model, self.data)
            out[i] = self.data.qfrc_inverse[self.dadr]
        return out

    def _regress(self):
        """tau = tau0 + sum_k m_k b_k + m_p b_p for moves and holds (exact: RNE is linear in the inertial parameters)."""
        m = self.model
        base_mass = {}
        for j, bid in self.slot_ids.items():
            base_mass[j] = (m.body_mass[bid], m.body_inertia[bid].copy())
        pm = (m.body_mass[self.pay_id], m.body_inertia[self.pay_id].copy())

        def set_all(zero_except=None, which=None):
            for j, bid in self.slot_ids.items():
                if which == j:
                    m.body_mass[bid] = 1.0
                    m.body_inertia[bid] = self.arm.box_i_per_kg()
                else:
                    m.body_mass[bid] = 0.0
                    m.body_inertia[bid] = 0.0
            if which == "payload":
                m.body_mass[self.pay_id] = 1.0
                m.body_inertia[self.pay_id] = 1e-9
            else:
                m.body_mass[self.pay_id] = 0.0
                m.body_inertia[self.pay_id] = 0.0

        Qh = self.q_grid
        terms = [None] + list(self.slot_ids) + ["payload"]
        mv, hd = {}, {}
        for w in terms:
            set_all(which=w)
            mv[w] = self._inverse(self.mv_q, self.mv_qd, self.mv_qdd)
            hd[w] = self._inverse(Qh, None, None)
        for j, bid in self.slot_ids.items():
            m.body_mass[bid], m.body_inertia[bid] = base_mass[j]
        m.body_mass[self.pay_id], m.body_inertia[self.pay_id] = pm
        self.mv0 = mv[None]
        self.hd0 = hd[None]
        self.mvb = {w: mv[w] - mv[None] for w in terms[1:]}
        self.hdb = {w: hd[w] - hd[None] for w in terms[1:]}
        self.az_max = self._tcp_vertical_acc()

    def _tcp_vertical_acc(self):
        mj = _mujoco()
        h = 1e-4
        T = float(self.layer["T"])
        amax = 0.0
        jac = np.zeros((3, self.model.nv))
        for (a, b) in self.pairs:
            q0, q1 = self.q_grid[a], self.q_grid[b]
            Q, V, A, t = trapezoid(q0, q1, T)
            for i in range(len(t)):
                vz = []
                for s in (-1.0, 1.0):
                    tt = min(max(t[i] + s * h, 0.0), T)
                    # analytic trapezoid at tt
                    ta = T / 3.0
                    vmax = 1.5 / T
                    acc = vmax / ta
                    if tt < ta:
                        ss, sd = 0.5 * acc * tt ** 2, acc * tt
                    elif tt < 2 * ta:
                        ss, sd = 0.5 * acc * ta ** 2 + vmax * (tt - ta), vmax
                    else:
                        ss, sd = 1.0 - 0.5 * acc * (T - tt) ** 2, acc * (T - tt)
                    q = q0 + ss * (q1 - q0)
                    qd = sd * (q1 - q0)
                    self._set_q(q)
                    mj.mj_kinematics(self.model, self.data)
                    mj.mj_comPos(self.model, self.data)
                    mj.mj_jacSite(self.model, self.data, jac, None, self.site)
                    vz.append(float(jac[2, self.dadr] @ qd))
                dtt = (min(t[i] + h, T) - max(t[i] - h, 0.0))
                if dtt > 0:
                    amax = max(amax, abs((vz[1] - vz[0]) / dtt))
        return amax

    # ------------------------------------------------------------------ loads for a BOM
    def loads(self, masses, payload_kg):
        """(moves (N, 6), holds (252, 6), holds at the proof load) load torques for servo masses {joint: kg}."""
        mv = self.mv0.copy()
        hd = self.hd0.copy()
        for j, mk in masses.items():
            if j in self.mvb:
                mv += mk * self.mvb[j]
                hd += mk * self.hdb[j]
        mvp = mv + payload_kg * self.mvb["payload"]
        hdp = hd + payload_kg * self.hdb["payload"]
        proof = hd + self.layer["proof"] * payload_kg * self.hdb["payload"]
        return mvp, hdp, proof

    def grip_torque(self):
        m_hi = float(self.layer["m_hi"])
        F = m_hi * (G + self.az_max) * 2.0 / (2.0 * 0.5)
        return F * self.arm.jaw_lever_m

    def per_move_mean_sq(self, x):
        """mean over the samples of each move of x^2 -> (n_moves, 6)."""
        out = np.zeros((self.n_moves, x.shape[1]))
        cnt = np.bincount(self.mv_id, minlength=self.n_moves).astype(float)
        for c in range(x.shape[1]):
            out[:, c] = np.bincount(self.mv_id, weights=x[:, c] ** 2, minlength=self.n_moves) / cnt
        return out

    def duty_rms(self, mv, hd):
        T, H = float(self.layer["T"]), float(self.layer["H"])
        s_move = T / (T + H)
        return np.sqrt(s_move * self.per_move_mean_sq(mv).max(axis=0) + (1.0 - s_move) * (hd ** 2).max(axis=0))

    def joint_util(self, j, cap, mv, hd, proof):
        """Worst utilisation of a capability at arm joint `j` given the load traces (> 1 fails)."""
        k = JOINTS.index(j)
        tau = np.abs(mv[:, k])
        qd = np.abs(self.mv_qd[:, k])
        qdd = self.mv_qdd[:, k]
        need = tau + cap["J_out"] * np.abs(qdd)
        x = qd / max(cap["w0"], 1e-12)
        frac = LM.droop_fraction_array(x, cap["droop"])
        env = cap["tau_s"] * frac
        with np.errstate(divide="ignore", invalid="ignore"):
            u_move = np.where(env > 1e-12, need / np.maximum(env, 1e-12), np.inf)
        u_move = float(np.max(u_move)) if len(u_move) else 0.0
        u_hold = float(np.max(np.abs(hd[:, k]))) / cap["tau_s"]
        u_proof = float(np.max(np.abs(proof[:, k]))) / cap["tau_s"]
        motor = mv[:, k] + cap["J_out"] * qdd
        T, H = float(self.layer["T"]), float(self.layer["H"])
        s_move = T / (T + H)
        cnt = np.bincount(self.mv_id, minlength=self.n_moves).astype(float)
        msq = np.bincount(self.mv_id, weights=motor ** 2, minlength=self.n_moves) / cnt
        rms = math.sqrt(s_move * float(msq.max()) + (1 - s_move) * float(np.max(hd[:, k] ** 2)))
        u_rms = rms / cap["tau_c"]
        return {"move": u_move, "hold": u_hold, "proof": u_proof, "rms": u_rms,
                "worst": max(u_move, u_hold, u_proof, u_rms)}

    def gripper_util(self, cap):
        need = self.grip_torque()
        u_hold = need / cap["tau_c"]
        u_close = (self.arm.jaw_stroke_rad / float(self.layer.get("jaw_close_s", 0.5))) / cap["w0"]
        return {"hold": u_hold, "close": u_close, "worst": max(u_hold, u_close)}

    def currents(self, caps, mv):
        """Summed bus current over the move samples (A) for the capabilities {joint: cap}."""
        tot = np.zeros(len(mv))
        for j in JOINTS:
            cap = caps[j]
            slope = (cap["i_stall"] - cap["i0"]) / cap["tau_s"]
            if j == "jaw":
                tot += cap["i0"] + self.grip_torque() * slope
            else:
                k = JOINTS.index(j)
                tot += cap["i0"] + np.abs(mv[:, k] + cap["J_out"] * self.mv_qdd[:, k]) * slope
        return tot

    def caps_for(self, bom, lot, cat, brief):
        bus = float(bom["bus_voltage_v"])
        return {j: LM.unit_capability(bom["servos"][j], bus, lot, cat, brief) for j in JOINTS}

    def evaluate(self, bom, lot, cat, brief, caps=None):
        """bom = {"bus_voltage_v", "servos": {joint: sku_id}, "boards": [ids], "psu": id} -> result dict."""
        bus = float(bom["bus_voltage_v"])
        servos = bom["servos"]
        caps = caps or self.caps_for(bom, lot, cat, brief)
        masses = {j: caps[j]["mass_kg"] for j in SLOT_JOINTS}
        mv, hd, proof = self.loads(masses, float(self.layer["m_hi"]))
        items, util = {}, {}
        for j in JOINTS:
            sku = cat.si(servos[j])
            fits = sku["body_class"] == self.arm.pocket_class.get(j, "standard")
            volt = cat.servo_ok_on_bus(servos[j], bus)
            u = self.gripper_util(caps[j]) if j == "jaw" else self.joint_util(j, caps[j], mv, hd, proof)
            util[j] = u
            items[j] = bool(fits and volt and u["worst"] <= 1.0 + 1e-12)
            u["fits"] = fits
            u["voltage_ok"] = volt
        cur = self.currents(caps, mv)
        peak_i = float(cur.max())
        psu = cat.get(bom["psu"]) if bom.get("psu") and cat.has(bom["psu"]) and cat.role(bom["psu"]) == "psu" else {}
        all_volt = all(cat.servo_ok_on_bus(servos[j], bus) for j in JOINTS)
        items["psu"] = bool(psu and abs(float(psu.get("output_v", -1)) - bus) < 1e-9 and all_volt
                            and float(psu.get("rated_current_a", 0)) >= peak_i - 1e-12)
        protocols = sorted({cat.si(servos[j])["protocol"] for j in JOINTS})
        items["bus_board"] = bool(cat.boards_cover(bom.get("boards", []), protocols, bus))
        cost = cat.bom_cost(bom)
        items["budget"] = bool(cost <= float(self.layer["ceiling"]) + 1e-9)
        return {"items": items, "util": util, "peak_current_a": peak_i, "cost": cost, "caps": caps,
                "pass_fraction": sum(items[i] for i in ITEMS) / len(ITEMS)}

    def derived(self, bom, lot, cat, brief):
        """The 15 graded derived items of brief B-16 for the BOM installed on this arm (the lot's weakest expected unit)."""
        caps = self.caps_for(bom, lot, cat, brief)
        masses = {j: caps[j]["mass_kg"] for j in SLOT_JOINTS}
        mv, hd, proof = self.loads(masses, float(self.layer["m_hi"]))
        out = {}
        for j in MOVING_PITCH:
            k = JOINTS.index(j)
            out["joint_peak_torque:" + j] = float(max(np.abs(mv[:, k]).max(), np.abs(hd[:, k]).max()))
            out["joint_peak_speed:" + j] = float(np.abs(self.mv_qd[:, k]).max())
        rms = self.duty_rms(mv, hd)
        for j in MOVING_PITCH:
            out["joint_rms_torque:" + j] = float(rms[JOINTS.index(j)])
        out["grip_torque"] = float(self.grip_torque())
        out["supply_peak_current"] = float(self.currents(caps, mv).max())
        mm = float(self.arm.printed_moving_mass_kg or 0.0) + sum(masses[j] for j in SLOT_JOINTS)
        out["moving_mass"] = mm
        return out


def bom_from_json(bj):
    """bom.json (bom-info/2) -> the internal BOM dict (raises on a structural problem)."""
    servos, boards, psu = {}, [], None
    for line in bj["parts"]:
        role = line.get("role")
        if role == "servo":
            servos[line["joint"]] = line["part_id"]
        elif role == "bus_board":
            boards += [line["part_id"]] * int(line.get("count", 1))
        elif role == "psu":
            psu = line["part_id"]
    return {"bus_voltage_v": float(bj["bus_voltage_v"]), "servos": servos, "boards": boards, "psu": psu}


def bom_to_json(bom, cat):
    parts = [{"part_id": bom["servos"][j], "count": 1, "role": "servo", "joint": j} for j in JOINTS]
    parts += [{"part_id": b, "count": 1, "role": "bus_board"} for b in bom.get("boards", [])]
    parts.append({"part_id": bom["psu"], "count": 1, "role": "psu"})
    return {"format": "bom-info/2", "bus_voltage_v": float(bom["bus_voltage_v"]), "parts": parts,
            "total_cost_usd": cat.bom_cost(bom)}
