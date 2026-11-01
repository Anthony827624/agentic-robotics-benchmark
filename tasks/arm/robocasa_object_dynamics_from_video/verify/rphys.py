"""rphys: physics world of robocasa_object_dynamics_from_video (MuJoCo 3.16).

Byte-identical copies are used by the hidden grader and shipped to the agent. It holds no hidden values.

Conventions: world frame = counter top at z = 0, z up. An object is given as convex collision pieces (list of (N,3)
vertex arrays in the object's body frame; MuJoCo uses the convex hull of each piece), plus an explicit inertial
(mass, COM, inertia tensor about the COM, body axes) and the object-counter sliding friction mu.

Typical use (replaying a clip with your own object model):

    import numpy as np, rphys
    geom = rphys.Geometry([piece0_vertices, piece1_vertices, ...])          # your convex pieces, body frame
    model = dict(mass=m, com=np.array([...]), I=np.array([[...]]))          # body frame
    sim = rphys.Sim(geom, model, mu)
    sim.set_object(pos, quat)                    # free-joint pose (MuJoCo quaternion w, x, y, z)
    clip = json.load(open(".../clip.json"))
    if clip["stop_bar"]: sim.set_stop_yaw(clip["stop_bar"]["center_xy"], clip["stop_bar"]["long_axis_yaw"])
    else: sim.park_stop()
    P = np.load(".../probe_500hz.npy")
    sim.set_probe(P[0], np.zeros(3))
    poses = sim.run(P, record_every=1)           # (M, 7) object poses, one per 2 ms step
"""
import numpy as np
import mujoco

DT = 0.002          # physics timestep (s)
PROBE_R = 0.008     # probe ball radius (m)
PROBE_MU = 0.4      # probe-object sliding friction
G = 9.81


def point_cloud_props(pts, w):
    """(mass=sum w, com, inertia about com) of weighted points."""
    m = w.sum()
    c = (w[:, None] * pts).sum(0) / m
    r = pts - c
    I = np.eye(3) * (w * (r * r).sum(1)).sum() - (w[:, None, None] * r[:, :, None] * r[:, None, :]).sum(0)
    return m, c, I


def combine(parts):
    """parts: list of (m, com, I_about_com). Returns combined (m, com, I_about_com)."""
    M = sum(p[0] for p in parts)
    C = sum(p[0] * p[1] for p in parts) / M
    I = np.zeros((3, 3))
    for m, c, Ic in parts:
        r = c - C
        I += Ic + m * (np.dot(r, r) * np.eye(3) - np.outer(r, r))
    return M, C, I


# ----------------------------------------------------------------------------------------------
# Scene (physics)
# ----------------------------------------------------------------------------------------------
def inline_collision_xml(pieces, prefix="o_"):
    """Collision geometry from convex-piece vertex arrays (object body frame); MuJoCo uses each piece's hull."""
    assets, geoms = [], []
    for j, pc in enumerate(pieces):
        v = " ".join("%.9g" % x for x in np.asarray(pc, float).ravel())
        assets.append('<mesh name="%scol%d" vertex="%s"/>' % (prefix, j, v))
        geoms.append('<geom name="%scol%d" type="mesh" mesh="%scol%d" group="3" rgba="0.8 0.3 0.3 0.4" mass="0" '
                     'condim="3" friction="0.1 0.005 0.0001"/>' % (prefix, j, prefix, j))
    return "\n".join(assets), "\n".join(geoms)


class Geometry:
    """Object collision geometry given as convex pieces (list of (N,3) arrays, object body frame)."""

    def __init__(self, pieces):
        self.pieces = [np.asarray(p, float) for p in pieces]
        self.allv = np.concatenate(self.pieces, 0)
        self.extent = self.allv.max(0) - self.allv.min(0)


def scene_xml(obj, inertial, mu, extra_world="", obj_visual=False, pin=None):
    """Physics scene: counter plane, probe (3 slide joints), object (freejoint) with explicit inertial.

    inertial: dict(mass, com[3], I[3x3]) in the normalized body frame.
    mu: object-counter sliding friction.
    pin: None or dict(r, h, pos[2]) -> a vertical cylinder pin standing on the counter.
    """
    a, g = inline_collision_xml(obj.pieces)
    I = np.asarray(inertial["I"])
    full = "%.9g %.9g %.9g %.9g %.9g %.9g" % (I[0, 0], I[1, 1], I[2, 2], I[0, 1], I[0, 2], I[1, 2])
    c = inertial["com"]
    pin_xml = ""
    if pin is not None:
        pin_xml = ('<geom name="pin" type="cylinder" size="%g %g" pos="%g %g %g" priority="3" friction="1.0 0.005 0.0001" '
                   'solref="0.004 1" solimp="0.95 0.99 0.001" rgba="0.3 0.3 0.3 1"/>' % (pin["r"], pin["h"] / 2, pin["pos"][0], pin["pos"][1], pin["h"] / 2))
    xml = f"""<mujoco model="rscene">
  <compiler inertiafromgeom="false" angle="radian"/>
  <option timestep="{DT}" integrator="implicitfast" cone="elliptic" impratio="1">
    <flag multiccd="enable"/>
  </option>
  <asset>{a}</asset>
  <worldbody>
    <geom name="counter" type="plane" size="2 2 0.1" priority="2" friction="{mu:.6g} 0.005 0.0001" condim="3" rgba="0.6 0.6 0.6 1"/>
    {pin_xml}
    <body name="probe" pos="0 0 0">
      <joint name="px" type="slide" axis="1 0 0" damping="0"/>
      <joint name="py" type="slide" axis="0 1 0" damping="0"/>
      <joint name="pz" type="slide" axis="0 0 1" damping="0"/>
      <inertial pos="0 0 0" mass="50" diaginertia="1 1 1"/>
      <geom name="probe_tip" type="sphere" size="{PROBE_R}" priority="4" friction="{PROBE_MU} 0.005 0.0001" condim="3" rgba="0.9 0.5 0.1 1" contype="1" conaffinity="1"/>
    </body>
    <body name="obj" pos="0 0 0">
      <freejoint name="obj_free"/>
      <inertial pos="{c[0]:.9g} {c[1]:.9g} {c[2]:.9g}" mass="{inertial['mass']:.9g}" fullinertia="{full}"/>
      {g}
    </body>
    <body name="stop" mocap="true" pos="0 3 0.005">
      <geom name="stop_bar" type="box" size="0.12 0.005 0.005" priority="3" friction="1.0 0.005 0.0001" condim="3" rgba="0.25 0.25 0.28 1"/>
    </body>
    {extra_world}
  </worldbody>
</mujoco>"""
    return xml


class Sim:
    def __init__(self, obj, inertial, mu, pin=None):
        self.obj = obj
        self.m = mujoco.MjModel.from_xml_string(scene_xml(obj, inertial, mu, pin=pin))
        self.d = mujoco.MjData(self.m)
        self.qadr = self.m.jnt_qposadr[self.m.joint("obj_free").id]
        self.vadr = self.m.jnt_dofadr[self.m.joint("obj_free").id]
        self.padr = [self.m.jnt_qposadr[self.m.joint(n).id] for n in ("px", "py", "pz")]
        self.pvadr = [self.m.jnt_dofadr[self.m.joint(n).id] for n in ("px", "py", "pz")]

    def set_object(self, pos, quat):
        self.d.qpos[self.qadr:self.qadr + 3] = pos
        self.d.qpos[self.qadr + 3:self.qadr + 7] = quat
        self.d.qvel[self.vadr:self.vadr + 6] = 0

    def set_probe(self, p, v):
        for i in range(3):
            self.d.qpos[self.padr[i]] = p[i]
            self.d.qvel[self.pvadr[i]] = v[i]

    def set_stop(self, xy, alpha):
        """Place the stop bar (long axis perpendicular to world direction alpha) with its centre at xy."""
        self.d.mocap_pos[0] = [xy[0], xy[1], 0.005]
        a = alpha + np.pi / 2  # bar long axis (local x) perpendicular to alpha
        self.d.mocap_quat[0] = [np.cos(a / 2 - np.pi / 4 + np.pi / 4), 0, 0, np.sin(a / 2 - np.pi / 4 + np.pi / 4)]

    def set_stop_yaw(self, center_xy, long_axis_yaw):
        """Place the stop bar from clip.json values (centre and yaw of the bar's long axis)."""
        self.set_stop(center_xy, long_axis_yaw - np.pi / 2)

    def park_stop(self):
        self.d.mocap_pos[0] = [0, 3, 0.005]
        self.d.mocap_quat[0] = [1, 0, 0, 0]

    def obj_pose(self):
        return self.d.qpos[self.qadr:self.qadr + 7].copy()

    def run(self, probe_traj, record_every=1):
        """probe_traj: (T,3) positions at DT spacing. Returns object poses (T//record_every, 7)."""
        P = probe_traj
        V = np.gradient(P, DT, axis=0)
        out = []
        for k in range(len(P)):
            self.set_probe(P[k], V[k])
            mujoco.mj_step(self.m, self.d)
            if k % record_every == 0:
                out.append(self.obj_pose())
        return np.array(out)


def settle_upright(sim, xy, yaw, t=0.6):
    """Place object upright at xy/yaw resting on the counter and let it settle (probe parked far away)."""
    q = np.array([np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)])
    sim.set_object([xy[0], xy[1], 0.0005], q)
    park = np.array([0.0, -0.6, 0.3])
    traj = np.repeat(park[None], int(t / DT), 0)
    sim.run(traj, record_every=10 ** 9)
    return sim.obj_pose()


# ----------------------------------------------------------------------------------------------
# Probe motion primitives (positions at DT)
# ----------------------------------------------------------------------------------------------
def seg_linear(p0, p1, vmax, amax=2.0):
    """Trapezoidal-velocity straight segment from p0 to p1."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    L = np.linalg.norm(p1 - p0)
    if L < 1e-9:
        return p0[None].copy()
    ta = vmax / amax
    if amax * ta * ta > L:
        ta = np.sqrt(L / amax)
        vmax = amax * ta
    tc = (L - amax * ta * ta) / vmax
    T = 2 * ta + tc
    t = np.arange(0, T + DT, DT)
    s = np.where(t < ta, 0.5 * amax * t ** 2,
                 np.where(t < ta + tc, 0.5 * amax * ta ** 2 + vmax * (t - ta),
                          L - 0.5 * amax * np.clip(T - t, 0, None) ** 2))
    s = np.clip(s, 0, L)
    return p0 + np.outer(s / L, p1 - p0)


def hold(p, t):
    return np.repeat(np.asarray(p, float)[None], max(1, int(round(t / DT))), 0)


def concat(*segs):
    return np.concatenate(segs, 0)

