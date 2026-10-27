"""Collision-free Panda motions in the full-arm cell (privileged: the planner sees the cell's exact state).

`Planner(cell)` keeps its own copy of the cell's model (the Panda's geoms grown by `clearance_m`, so a contact closer
than the clearance counts) and checks a Panda posture, with the held part carried rigidly by the hand, against
everything else in the CURRENT scene: the bench, the fixture, the arm built so far (at its joints' current angles),
the parts still on the bench. `free(q0, q1)` checks the joint-space segment; `plan(q0, q1)` returns a straight segment
when it is free, else an RRT-Connect path (joint limits, shortcut smoothing); `line_free(qs)` checks a list of
postures (a Cartesian path).
"""
import math

import numpy as np


class Planner(object):
    def __init__(self, cell, clearance_m=0.008, held_clearance_m=0.004, seed=0):
        import copy
        import mujoco
        self.mj, self.cell = mujoco, cell
        self.m = copy.deepcopy(cell.m)
        self.d = mujoco.MjData(self.m)
        self.clear = float(clearance_m)
        self.held_clear = float(held_clearance_m)     # the part in the hand keeps this far from everything else
        m = self.m
        self.panda = set(b for b in range(m.nbody) if m.body(b).name.startswith("panda/"))
        self.fingers = set(b for b in self.panda if "finger" in m.body(b).name)
        # only the Panda (and the part it holds) against the rest is checked: collision bits Panda/held = (2, 1), the rest
        # = (1, 2), so the parts touching each other in the built arm cost no contacts (the arena)
        self.col = [g for g in range(m.ngeom) if m.geom_contype[g] or m.geom_conaffinity[g]]
        for g in self.col:
            if m.geom_bodyid[g] in self.panda:
                m.geom_margin[g] = self.clear
                m.geom_contype[g], m.geom_conaffinity[g] = 2, 1
            else:
                m.geom_contype[g], m.geom_conaffinity[g] = 1, 2
        self.held_geoms = []
        self.pj = [m.joint("panda/joint%d" % (i + 1)).id for i in range(7)]
        self.qadr = np.array([m.jnt_qposadr[j] for j in self.pj])
        self.lo = np.array([m.jnt_range[j][0] for j in self.pj]) + 0.02
        self.hi = np.array([m.jnt_range[j][1] for j in self.pj]) - 0.02
        self.tcp = m.site("tcp").id
        self.rng = np.random.default_rng(seed)
        self.held = None                    # (part name, G = part pose in the tcp frame)
        self.allow = set()                  # body names contacts with which are allowed (the part being grasped)
        self.n_checks = 0

    # ------------------------------------------------------------------ the scene
    def sync(self, held=None, allow=(), poses=None, width=None):
        """take the cell's current state (every body, the finger opening) and what the hand carries; `poses` {part:
        T} overrides where parts are (a hypothetical scene), `width` the finger opening"""
        from arm.assembly_cell.predicates import quat_wxyz_from_mat
        c = self.cell
        self.d.qpos[:] = c.d.qpos
        for name, T in (poses or {}).items():
            a = self.m.jnt_qposadr[self.m.joint("free_" + name).id]
            self.d.qpos[a:a + 3] = T[:3, 3]
            self.d.qpos[a + 3:a + 7] = quat_wxyz_from_mat(T[:3, :3])
        if width is not None:
            for fq in c.ctrl.finger_q:
                self.d.qpos[fq] = width / 2.0
        m = self.m
        for g in self.held_geoms:
            m.geom_contype[g], m.geom_conaffinity[g] = 1, 2
        self.held_geoms = []
        for g in getattr(self, "held_margin", []):
            m.geom_margin[g] = 0.0
        self.held_margin = []
        if held is not None:
            hb = m.body(held[0]).id
            self.held_geoms = [g for g in self.col if m.geom_bodyid[g] == hb]
            for g in self.held_geoms:
                m.geom_contype[g], m.geom_conaffinity[g] = 2, 1
                m.geom_margin[g] = self.held_clear
            self.held_margin = list(self.held_geoms)
        self.held = held
        self.allow = set(m.body(n).id for n in allow)
        if held is not None:
            self.held_id = m.body(held[0]).id
            self.held_adr = m.jnt_qposadr[m.joint("free_" + held[0]).id]

    def _place_held(self):
        from arm.assembly_cell.predicates import quat_wxyz_from_mat
        T = np.eye(4)
        T[:3, :3] = self.d.site_xmat[self.tcp].reshape(3, 3)
        T[:3, 3] = self.d.site_xpos[self.tcp]
        P = T @ self.held[1]
        a = self.held_adr
        self.d.qpos[a:a + 3] = P[:3, 3]
        self.d.qpos[a + 3:a + 7] = quat_wxyz_from_mat(P[:3, :3])

    def hits(self, q, tol_m=0.0005, finger_clear=0.002, link_clear=None, held_clear=None):
        """[(Panda or held body, other body, distance m)] of the contacts closer than the clearance (the held part:
        closer than tol_m) at posture q"""
        mj, m, d = self.mj, self.m, self.d
        self.n_checks += 1
        d.qpos[self.qadr] = q
        mj.mj_kinematics(m, d)
        if self.held is not None:
            self._place_held()
            mj.mj_kinematics(m, d)
        mj.mj_collision(m, d)
        out = []
        hid = self.held_id if self.held is not None else -1
        for i in range(d.ncon):
            c = d.contact[i]
            b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            s1, s2 = (b1 in self.panda or b1 == hid), (b2 in self.panda or b2 == hid)
            if s1 == s2:
                continue
            mine, other = (b1, b2) if s1 else (b2, b1)
            if other in self.allow:
                continue
            if mine == hid:
                if c.dist < (self.held_clear if held_clear is None else held_clear) - tol_m:
                    out.append((m.body(mine).name, m.body(other).name, float(c.dist)))
            elif c.dist < 0.0 or (c.dist < (self.clear if link_clear is None else link_clear) and mine not in self.fingers):
                out.append((m.body(mine).name, m.body(other).name, float(c.dist)))
            elif mine in self.fingers and c.dist < finger_clear:
                out.append((m.body(mine).name, m.body(other).name, float(c.dist)))
        return out

    def ok(self, q):
        return not self.hits(q)

    # ------------------------------------------------------------------ segments and paths
    def free(self, q0, q1, step=0.005):
        q0, q1 = np.asarray(q0, float), np.asarray(q1, float)
        n = max(1, int(math.ceil(float(np.max(np.abs(q1 - q0))) / step)))
        return all(self.ok(q0 + (q1 - q0) * k / n) for k in range(1, n + 1))

    def line_free(self, qs, moving_away_ok=True):
        """every posture free; with moving_away_ok a contact already there at the first posture is tolerated while it
        does not get closer (backing out of a tight spot)"""
        if not qs:
            return True
        h0 = {(h[0], h[1]): h[2] for h in self.hits(qs[0])} if moving_away_ok else {}
        for q in qs:
            for a, b, dist in self.hits(q):
                if (a, b) in h0 and dist >= h0[(a, b)] - 0.0005:
                    continue
                return False
        return True

    def plan(self, q0, q1, max_iter=4000, step=0.25):
        """[q0, ..., q1] collision-free (straight if possible, else RRT-Connect + shortcuts); None if not found"""
        q0, q1 = np.asarray(q0, float), np.asarray(q1, float)
        if not self.ok(q1):
            return None
        if self.free(q0, q1):
            return [q0, q1]
        ta, tb = [(q0, -1)], [(q1, -1)]

        def nearest(t, q):
            return int(np.argmin([np.linalg.norm(x[0] - q) for x in t]))

        def extend(t, q):
            i = nearest(t, q)
            v = q - t[i][0]
            dist = float(np.linalg.norm(v))
            qn = q if dist <= step else t[i][0] + v / dist * step
            if not self.free(t[i][0], qn):
                return None
            t.append((qn, i))
            return len(t) - 1

        def chain(t, i):
            out = []
            while i >= 0:
                out.append(t[i][0])
                i = t[i][1]
            return out

        swapped = False
        for it in range(max_iter):
            qr = self.lo + (self.hi - self.lo) * self.rng.random(7) if it % 5 else (tb[-1][0] if not swapped else q1)
            ia = extend(ta, qr)
            if ia is None:
                ta, tb, swapped = tb, ta, not swapped
                continue
            # connect tb towards the new node
            qa = ta[ia][0]
            while True:
                ib = extend(tb, qa)
                if ib is None:
                    break
                if np.linalg.norm(tb[ib][0] - qa) < 1e-9:
                    pa, pb = chain(ta, ia)[::-1], chain(tb, ib)
                    path = pa + pb[1:]
                    if swapped:
                        path = path[::-1]
                    return self.shortcut(path)
            ta, tb, swapped = tb, ta, not swapped
        return None

    def shortcut(self, path, rounds=60):
        path = [np.asarray(p, float) for p in path]
        for _ in range(rounds):
            if len(path) <= 2:
                break
            i, j = sorted(self.rng.choice(len(path), 2, replace=False))
            if j - i < 2:
                continue
            if self.free(path[i], path[j]):
                path = path[:i + 1] + path[j:]
        return path
