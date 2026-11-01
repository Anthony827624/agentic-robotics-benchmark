"""rtasks: probe programs and the scoring test families (reference planner) of robocasa_object_dynamics_from_video.

Byte-identical copies are used by the hidden grader and shipped to the agent; no hidden values. All physics through
rphys.Sim. A "model" = dict(mass, com[3], I[3x3], mu) in the object body frame; `obj` is an rphys.Geometry.
"""
import numpy as np
import mujoco
import rphys as W

DT = W.DT


# ----------------------------------------------------------------------------------------------
# rotation helpers
# ----------------------------------------------------------------------------------------------
def quat_yaw(yaw):
    return np.array([np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)])


def quat_to_mat(q):
    R = np.zeros(9)
    mujoco.mju_quat2Mat(R, np.asarray(q, float))
    return R.reshape(3, 3)


def tilt_angle(q):
    """Angle between body z and world z (rad)."""
    R = quat_to_mat(q)
    return float(np.arccos(np.clip(R[2, 2], -1, 1)))


def yaw_of(q):
    R = quat_to_mat(q)
    return float(np.arctan2(R[1, 0], R[0, 0]))


def wrap(a):
    return (a + np.pi) % (2 * np.pi) - np.pi


# ----------------------------------------------------------------------------------------------
# geometry helpers on the true/visible geometry
# ----------------------------------------------------------------------------------------------
def support_radius(obj, u_body):
    """Half-extent of the collision hull along body-frame horizontal direction u (max projection)."""
    return float(np.max(obj.allv[:, :2] @ u_body[:2]))


def surface_point_along(obj, u_body, h, e=0.0):
    """Max projection along u of collision vertices in a slab around height h and lateral offset e."""
    v = np.array([-u_body[1], u_body[0]])
    V = obj.allv
    sel = (np.abs(V[:, 2] - h) < 0.012) & (np.abs(V[:, :2] @ v - e) < 0.012)
    if sel.sum() < 3:
        sel = np.abs(V[:, 2] - h) < 0.02
    return float(np.max(V[sel, :2] @ u_body[:2]))


# ----------------------------------------------------------------------------------------------
# probe programs
# ----------------------------------------------------------------------------------------------
PARK = np.array([0.0, -0.45, 0.35])


def push_program(c_xy, alpha, h, e, r_contact, stroke, v_push, v_approach=0.15, runup=0.03, amax=2.0,
                 stop_amax=None, hold_t=0.3, retract=True, t_total=None, approach=False):
    """Probe pushes along world direction alpha at height h, lateral offset e.

    c_xy: (planner's estimate of) object centre; r_contact: distance from centre to the surface along alpha.
    The tip starts runup + PROBE_R before the estimated surface, moves at v_push for runup + stroke,
    then stops (with stop_amax), holds, retracts 6 cm, rises, returns to PARK.
    """
    u = np.array([np.cos(alpha), np.sin(alpha), 0.0])
    v = np.array([-u[1], u[0], 0.0])
    c = np.array([c_xy[0], c_xy[1], 0.0])
    s0 = c + u * (-(r_contact + W.PROBE_R + runup)) + v * e + np.array([0, 0, h])
    above = s0 + np.array([0, 0, 0.12]) - u * 0.04
    if approach:
        segs = [W.hold(above, 0.1), W.seg_linear(above, s0 - u * 0.04, v_approach, 1.0),
                W.seg_linear(s0 - u * 0.04, s0, 0.1, 1.0), W.hold(s0, 0.2)]
    else:
        segs = [W.hold(s0, 0.3)]
    s1 = s0 + u * (runup + stroke)
    # accelerate inside the run-up, cruise, decelerate at stop_amax
    a = amax
    seg = _push_profile(s0, s1, v_push, a, stop_amax or a)
    segs.append(seg)
    segs.append(W.hold(s1, hold_t))
    if retract:
        s2 = s1 - u * 0.06
        segs.append(W.seg_linear(s1, s2, 0.1, 1.0))
        segs.append(W.seg_linear(s2, s2 + np.array([0, 0, 0.12]), 0.15, 1.0))
    P = W.concat(*segs)
    if t_total is not None:
        n = int(round(t_total / DT))
        if len(P) < n:
            P = np.concatenate([P, np.repeat(P[-1:], n - len(P), 0)], 0)
        else:
            P = P[:n]
    return P


def _push_profile(p0, p1, vmax, a_up, a_down):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    L = np.linalg.norm(p1 - p0)
    ta, td = vmax / a_up, vmax / a_down
    la, ld = 0.5 * a_up * ta ** 2, 0.5 * a_down * td ** 2
    if la + ld > L:
        vmax = np.sqrt(2 * L / (1 / a_up + 1 / a_down))
        ta, td = vmax / a_up, vmax / a_down
        la, ld = 0.5 * a_up * ta ** 2, 0.5 * a_down * td ** 2
    tc = (L - la - ld) / vmax
    T = ta + tc + td
    t = np.arange(0, T + DT, DT)
    s = np.where(t < ta, 0.5 * a_up * t ** 2,
                 np.where(t < ta + tc, la + vmax * (t - ta),
                          L - 0.5 * a_down * np.clip(T - t, 0, None) ** 2))
    s = np.clip(s, 0, L)
    return p0 + np.outer(s / L, p1 - p0)


# ----------------------------------------------------------------------------------------------
# rollouts
# ----------------------------------------------------------------------------------------------
class World:
    """Holds one compiled Sim per (object, model) and runs rollouts from exact initial states."""

    def __init__(self, obj, model, pin=None):
        self.obj = obj
        self.model = model
        self.sim = W.Sim(obj, model, model["mu"], pin=pin)
        self._rest = {}

    def rest_state(self, xy, yaw):
        key = (round(xy[0], 6), round(xy[1], 6), round(yaw, 6))
        if key not in self._rest:
            mujoco.mj_resetData(self.sim.m, self.sim.d)
            W.settle_upright(self.sim, xy, yaw, t=0.5)
            st = np.zeros(mujoco.mj_stateSize(self.sim.m, mujoco.mjtState.mjSTATE_INTEGRATION))
            mujoco.mj_getState(self.sim.m, self.sim.d, st, mujoco.mjtState.mjSTATE_INTEGRATION)
            self._rest[key] = st
        return self._rest[key]

    def surface_dist(self, xy, yaw, alpha, h, e=0.0):
        """Distance from the object's resting centre (xy) to its collision surface, approaching along world
        direction alpha at height h with lateral offset e (ray cast on the rest state)."""
        st = self.rest_state(xy, yaw)
        mujoco.mj_setState(self.sim.m, self.sim.d, st, mujoco.mjtState.mjSTATE_INTEGRATION)
        mujoco.mj_forward(self.sim.m, self.sim.d)
        u = np.array([np.cos(alpha), np.sin(alpha), 0.0])
        v = np.array([-u[1], u[0], 0.0])
        p = np.array([xy[0], xy[1], h]) + v * e - u * 0.5
        gg = np.zeros(6, np.uint8); gg[3] = 1
        gid = np.zeros(1, np.int32)
        dist = mujoco.mj_ray(self.sim.m, self.sim.d, p, u, gg, 0, -1, gid)
        if dist < 0:
            return None
        return 0.5 - dist

    def rollout(self, xy, yaw, P, record_every=5):
        st = self.rest_state(xy, yaw)
        mujoco.mj_setState(self.sim.m, self.sim.d, st, mujoco.mjtState.mjSTATE_INTEGRATION)
        # probe starts at P[0]
        self.sim.set_probe(P[0], np.zeros(3))
        poses = self.sim.run(P, record_every=record_every)
        return poses


def summarize(poses):
    tilts = np.array([tilt_angle(p[3:]) for p in poses])
    fin = poses[-1]
    return dict(final=fin, peak_tilt=float(tilts.max()), final_tilt=float(tilts[-1]),
                toppled=bool(tilts[-1] > np.radians(45)), finite=bool(np.all(np.isfinite(poses))))


# ----------------------------------------------------------------------------------------------
# generic experiment runner on a World (supports the stop bar)
# ----------------------------------------------------------------------------------------------
def run_exp(world, xy, yaw, P, stop=None, record_every=5):
    st = world.rest_state(xy, yaw) if stop is None else world.rest_state_stop(xy, yaw, stop)
    sim = world.sim
    mujoco.mj_setState(sim.m, sim.d, st, mujoco.mjtState.mjSTATE_INTEGRATION)
    if stop is None:
        sim.park_stop()
    else:
        sim.set_stop(stop[0], stop[1])
    sim.set_probe(P[0], np.zeros(3))
    return sim.run(P, record_every=record_every)


def _rest_state_stop(self, xy, yaw, stop):
    key = ("stop", round(xy[0], 6), round(xy[1], 6), round(yaw, 6), round(stop[0][0], 6), round(stop[0][1], 6),
           round(stop[1], 6))
    if key not in self._rest:
        mujoco.mj_resetData(self.sim.m, self.sim.d)
        self.sim.set_stop(stop[0], stop[1])
        q = quat_yaw(yaw)
        self.sim.set_object([xy[0], xy[1], 0.0005], q)
        traj = np.repeat(np.array([0.0, -0.6, 0.3])[None], int(0.5 / DT), 0)
        self.sim.run(traj, record_every=10 ** 9)
        st = np.zeros(mujoco.mj_stateSize(self.sim.m, mujoco.mjtState.mjSTATE_INTEGRATION))
        mujoco.mj_getState(self.sim.m, self.sim.d, st, mujoco.mjtState.mjSTATE_INTEGRATION)
        self._rest[key] = st
    return self._rest[key]


World.rest_state_stop = _rest_state_stop


def stop_for(world, xy, yaw, alpha, gap=0.002):
    """Stop-bar placement just beyond the far side of the object (over the bar's height) along alpha."""
    rb = max([world.surface_dist(xy, yaw, alpha + np.pi, hh, ee) or 0.0
              for hh in (0.001, 0.004, 0.007, 0.010, 0.013) for ee in (-0.02, -0.01, 0.0, 0.01, 0.02)])
    u = np.array([np.cos(alpha), np.sin(alpha)])
    return (np.asarray(xy) + u * (rb + gap + 0.005), alpha)


# ----------------------------------------------------------------------------------------------
# Test families (planner on the SUBMITTED model; scoring in the GT world)
# ----------------------------------------------------------------------------------------------
TIP_V = 0.05          # quasi-static push speed (m/s)
TIP_T = 7.0
ROCK_T = 4.0
PUSH_T = 4.0


def contact_height(world, xy, yaw, alpha, hfrac):
    """Highest probe height <= hfrac*H where the approach ray hits the object (true geometry)."""
    H = world.obj.extent[2]
    h = hfrac * H
    while h > 0.01:
        r = world.surface_dist(xy, yaw, alpha, h)
        if r is not None:
            return h, r
        h -= 0.005
    return 0.01, world.surface_dist(xy, yaw, alpha, 0.01)


def tip_program(world, xy, yaw, alpha, hfrac, stroke):
    h, r = contact_height(world, xy, yaw, alpha, hfrac)
    P = push_program(xy, alpha, h, 0.0, r, stroke, TIP_V, runup=0.01, hold_t=0.5)
    return np.concatenate([P, np.repeat(P[-1:], int(1.5 / DT), 0)], 0)


def toppled_of(poses):
    return tilt_angle(poses[-1][3:]) > np.radians(45)


def bisect_boundary(fn_topples, lo, hi, iters=12):
    """Smallest action that topples, assuming monotone. Returns (boundary, ok)."""
    if fn_topples(lo):
        return lo, False
    if not fn_topples(hi):
        return hi, False
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if fn_topples(mid):
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi), True


def scan_boundary(fn_topples, lo, hi, ratio=1.025, refine=6):
    """Smallest action that topples: geometric scan from lo, then local bisection. Returns (boundary, ok)."""
    prev = None
    x = lo
    while x <= hi * 1.0001:
        if fn_topples(x):
            if prev is None:
                return x, False
            a, b = prev, x
            for _ in range(refine):
                m = 0.5 * (a + b)
                if fn_topples(m):
                    b = m
                else:
                    a = m
            return b, True
        prev = x
        x *= ratio
    return hi, False


def tip_boundary(world, xy, yaw, alpha, hfrac):
    stop = stop_for(world, xy, yaw, alpha)
    f = lambda s: toppled_of(run_exp(world, xy, yaw, tip_program(world, xy, yaw, alpha, hfrac, s), stop))
    return scan_boundary(f, 0.004, 0.16)


A_ROBOT = 15.0  # probe acceleration / braking limit (m/s^2) for fast pushes


def fast_runup(v):
    return max(0.02, v * v / (2 * A_ROBOT) + 0.01)


def rock_program(world, xy, yaw, alpha, hfrac, v):
    h, r = contact_height(world, xy, yaw, alpha, hfrac)
    return push_program(xy, alpha, h, 0.0, r, 0.010 + v * v / (2 * A_ROBOT), v, runup=fast_runup(v), amax=A_ROBOT,
                        stop_amax=A_ROBOT, hold_t=0.0, t_total=ROCK_T)


def rock_boundary(world, xy, yaw, alpha, hfrac):
    stop = stop_for(world, xy, yaw, alpha)
    f = lambda v: toppled_of(run_exp(world, xy, yaw, rock_program(world, xy, yaw, alpha, hfrac, v), stop))
    return scan_boundary(f, 0.08, 1.6)


def push_program_slide(world, xy, yaw, alpha, hfrac, e, v):
    h = hfrac  # absolute probe-centre height for slides
    r = world.surface_dist(xy, yaw, alpha, h, e)
    if r is None:
        r = world.surface_dist(xy, yaw, alpha, h, 0.0)
    return push_program(xy, alpha, h, e, r, 0.02 + v * v / (2 * A_ROBOT), v, runup=fast_runup(v), amax=A_ROBOT,
                        stop_amax=A_ROBOT, hold_t=0.0, t_total=PUSH_T)


def push_outcome(world, xy, yaw, alpha, hfrac, e, v):
    poses = run_exp(world, xy, yaw, push_program_slide(world, xy, yaw, alpha, hfrac, e, v))
    fin = poses[-1]
    d = fin[:2] - np.asarray(xy)
    u = np.array([np.cos(alpha), np.sin(alpha)])
    return dict(dist=float(d @ u), lat=float(d @ np.array([-u[1], u[0]])), dyaw=float(wrap(yaw_of(fin[3:]) - yaw)),
                toppled=toppled_of(poses), pos=fin[:2].copy())


def margin_score(a_star, a_gt_boundary, toppled, lo=0.80, full=0.90):
    if toppled:
        return 0.0
    r = a_star / a_gt_boundary
    return float(np.clip((r - lo) / (full - lo), 0, 1))


PLAN_MARGIN = 0.93


def test_tip(sub_w, gt_w, xy, yaw, alpha, hfrac, gt_boundary=None):
    sc_pred, ok = tip_boundary(sub_w, xy, yaw, alpha, hfrac)
    s_star = PLAN_MARGIN * sc_pred
    if gt_boundary is None:
        sc_gt, ok_gt = tip_boundary(gt_w, xy, yaw, alpha, hfrac)
    else:
        sc_gt, ok_gt = gt_boundary, True
    stop = stop_for(gt_w, xy, yaw, alpha)
    top = toppled_of(run_exp(gt_w, xy, yaw, tip_program(gt_w, xy, yaw, alpha, hfrac, s_star), stop))
    return margin_score(s_star, sc_gt, top), dict(sc_pred=sc_pred, sc_gt=sc_gt, s_star=s_star, toppled=top, ok_gt=ok_gt)


def test_rock(sub_w, gt_w, xy, yaw, alpha, hfrac, gt_boundary=None):
    vc_pred, ok = rock_boundary(sub_w, xy, yaw, alpha, hfrac)
    v_star = PLAN_MARGIN * vc_pred
    if gt_boundary is None:
        vc_gt, ok_gt = rock_boundary(gt_w, xy, yaw, alpha, hfrac)
    else:
        vc_gt, ok_gt = gt_boundary, True
    stop = stop_for(gt_w, xy, yaw, alpha)
    top = toppled_of(run_exp(gt_w, xy, yaw, rock_program(gt_w, xy, yaw, alpha, hfrac, v_star), stop))
    return margin_score(v_star, vc_gt, top), dict(vc_pred=vc_pred, vc_gt=vc_gt, v_star=v_star, toppled=top, ok_gt=ok_gt)


def plan_push_speed(w, xy, yaw, alpha, h, e, d_target, vlo=0.1, vhi=1.3, n=37):
    """Reference push planner on a model: scan speeds, keep non-toppling outcomes, take the speed whose predicted
    along-track distance is closest to the target, then refine by bisection on the bracketing interval."""
    vs = np.linspace(vlo, vhi, n)
    outs = [push_outcome(w, xy, yaw, alpha, h, e, v) for v in vs]
    best, bi = None, None
    for i, o in enumerate(outs):
        if o["toppled"]:
            continue
        err = abs(o["dist"] - d_target)
        if best is None or err < best:
            best, bi = err, i
    if bi is None:
        return vlo
    # refine on a neighbour interval that brackets the target, if any
    for j in (bi - 1, bi):
        if j < 0 or j + 1 >= n:
            continue
        a, b = outs[j], outs[j + 1]
        if a["toppled"] or b["toppled"]:
            continue
        if (a["dist"] - d_target) * (b["dist"] - d_target) <= 0:
            lo, hi = vs[j], vs[j + 1]
            flo = a["dist"] - d_target
            for _ in range(10):
                mid = 0.5 * (lo + hi)
                fm = push_outcome(w, xy, yaw, alpha, h, e, mid)["dist"] - d_target
                if fm * flo <= 0:
                    hi = mid
                else:
                    lo, flo = mid, fm
            return 0.5 * (lo + hi)
    return float(vs[bi])


PUSH_TOL_D = 0.010
PUSH_TOL_YAW = np.radians(5)


def test_push(sub_w, gt_w, xy, yaw, alpha, hfrac, e, d_target):
    """Planner picks push speed v so the submitted model slides the object d_target along alpha;
    executed in GT. Score = 0.5 q(distance) + 0.5 q(yaw prediction)."""
    v_star = plan_push_speed(sub_w, xy, yaw, alpha, hfrac, e, d_target)
    pred = push_outcome(sub_w, xy, yaw, alpha, hfrac, e, v_star)
    real = push_outcome(gt_w, xy, yaw, alpha, hfrac, e, v_star)
    if real["toppled"]:
        return 0.0, dict(v=v_star, pred=pred, real=real)
    ed = abs(real["dist"] - d_target)
    el = abs(real["lat"] - pred["lat"])
    ey = abs(wrap(real["dyaw"] - pred["dyaw"]))
    qd = float(np.clip(2 - ed / PUSH_TOL_D, 0, 1))
    ql = float(np.clip(2 - el / PUSH_TOL_D, 0, 1))
    qy = float(np.clip(2 - ey / PUSH_TOL_YAW, 0, 1))
    return min(qd, ql, qy), dict(v=v_star, ed=ed, el=el, ey=np.degrees(ey), pred=pred, real=real)


PIN_RADII = (0.003, 0.005, 0.007, 0.010)


def pin_balanced(obj, model, com_xy_sub, yaw, r, t=3.0):
    """GT world: object upright on a pin of radius r at the origin, positioned so the SUBMITTED COM xy
    (body frame) is above the pin axis. Returns True if it stays (tilt < 15 deg) for t seconds."""
    pin = dict(r=r, h=0.04, pos=(0.0, 0.0))
    sim = W.Sim(obj, model, model["mu"], pin=pin)
    q = quat_yaw(yaw)
    R = quat_to_mat(q)
    off = R[:2, :2] @ np.asarray(com_xy_sub)
    sim.set_object([-off[0], -off[1], 0.04 + 0.0003], q)
    sim.park_stop()
    traj = np.repeat(np.array([0.0, -0.6, 0.3])[None], int(t / DT), 0)
    poses = sim.run(traj, record_every=25)
    tilts = [tilt_angle(p[3:]) for p in poses]
    return bool(max(tilts) < np.radians(15) and poses[-1][2] > 0.02)


def test_pin(obj, gt_model, sub_model, yaw):
    res = [pin_balanced(obj, gt_model, sub_model["com"][:2], yaw, r) for r in PIN_RADII]
    return float(np.mean(res)), dict(res=res)
