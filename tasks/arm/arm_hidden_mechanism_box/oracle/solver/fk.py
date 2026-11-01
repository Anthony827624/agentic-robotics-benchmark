"""Forward kinematics of the public robot model (box_env.py's Panda in the same scene, without a box):
grip-site pose for given arm joint angles. Used to return to the start posture along a joint-space
path (the operational-space controller alone cannot leave a flipped wrist branch)."""

import numpy as np


class FK(object):
    def __init__(self):
        import mujoco
        import box_env as E
        self.mj = mujoco
        self.home_q = np.asarray(E.HOME_Q, float)
        self.m = E.build_model({"xml": '<body name="box"/>', "meta": {}}, (0.04, 0.0, 0.0))
        self.d = mujoco.MjData(self.m)
        self.jq = [int(self.m.joint("r_joint%d" % i).qposadr[0]) for i in range(1, 8)]
        self.site = self.m.site("r_g_grip_site").id

    def __call__(self, q):
        self.d.qpos[self.jq] = np.asarray(q, float)
        self.mj.mj_kinematics(self.m, self.d)
        return self.d.site_xpos[self.site].copy(), self.d.site_xmat[self.site].reshape(3, 3).copy()


_FK = []


def get():
    if not _FK:
        try:
            _FK.append(FK())
        except Exception:          # no model available: callers fall back to Cartesian homing
            _FK.append(None)
    return _FK[0]
