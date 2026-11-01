"""rarm: the robot's visual model (Franka Panda from the robosuite 1.5.2 package, closed gripper, probe rod with
the 16 mm ball), posed by the per-frame joint angles in clipNN/arm_q.npy. Useful e.g. to render robot masks with the
cameras of data/cameras.json. The arm never touches objects; only the ball (rphys probe) does.
"""
import os
import re
import json
import numpy as np
import importlib.util
import mujoco

# robosuite is installed in the image only for its MJCF/mesh assets (not imported: no dependencies installed)
RS = os.path.join(importlib.util.find_spec("robosuite").submodule_search_locations[0], "models", "assets")
ROD_L = 0.11
ROD_TILT = np.radians(35)
ROBOT_BASE = np.array([0.0, -0.62, 0.0])
BALL_R = 0.008


def _abs_files(xml, base):
    return re.sub(r'file="([^"]+)"', lambda m: 'file="%s"' % (m.group(1) if os.path.isabs(m.group(1)) else os.path.join(base, m.group(1))), xml)


def arm_xml_parts(prefix="r_", with_visual=True):
    """Return (asset_xml, body_xml) for the Panda arm + gripper (closed) + probe rod + ball, base at ROBOT_BASE."""
    rx = open(os.path.join(RS, "robots/panda/robot.xml")).read()
    gx = open(os.path.join(RS, "grippers/panda_gripper.xml")).read()
    rx = _abs_files(rx, os.path.join(RS, "robots/panda"))
    gx = _abs_files(gx, os.path.join(RS, "grippers"))
    ra = re.search(r"<asset>(.*?)</asset>", rx, re.S).group(1)
    ga = re.search(r"<asset>(.*?)</asset>", gx, re.S).group(1)
    rb = re.search(r"<worldbody>(.*)</worldbody>", rx, re.S).group(1)
    gb = re.search(r"<worldbody>(.*)</worldbody>", gx, re.S).group(1)
    # drop collision geoms, cameras, sites, inertials, joints in gripper (closed fingers)
    def clean(s):
        s = re.sub(r'<geom[^>]*group="0"[^>]*/>', "", s)
        s = re.sub(r'<geom type="mesh" group="0"[^>]*/>', "", s)
        s = re.sub(r"<camera[^>]*/>", "", s)
        s = re.sub(r"<site[^>]*/>", "", s)
        return s
    rb, gb = clean(rb), clean(gb)
    gb = re.sub(r"<joint[^>]*/>", "", gb)
    # close the fingers: finger bodies at pos 0 0 0.0524 already (joint value 0 = closed)
    rod = ('<body name="%sprobe_rod" pos="0 0 0.097">'
           '<geom type="cylinder" size="0.005 %g" pos="0 0 %g" rgba="0.15 0.15 0.15 1" contype="0" conaffinity="0" group="1"/>'
           '<geom name="%sball" type="sphere" size="%g" pos="0 0 %g" rgba="0.95 0.45 0.1 1" contype="0" conaffinity="0" group="1"/>'
           '<site name="%stip" pos="0 0 %g" size="0.002"/></body>') % (prefix, ROD_L / 2, ROD_L / 2 - 0.01, prefix, BALL_R, ROD_L, prefix, ROD_L)
    gb = gb.replace('<body name="eef"', rod + '<body name="eef"', 1)
    rb = rb.replace("<!-- to add gripper -->", gb)
    # prefix names
    def pre(s):
        s = re.sub(r'name="([^"]+)"', lambda m: 'name="%s%s"' % ("" if m.group(1).startswith(prefix) else prefix, m.group(1)), s)
        s = re.sub(r'(mesh|material|joint)="([^"]+)"', lambda m: '%s="%s%s"' % (m.group(1), "" if m.group(2).startswith(prefix) else prefix, m.group(2)), s)
        return s
    assets = pre(ra + ga)
    body = pre(rb)
    body = body.replace('<body name="%sbase" pos="0 0 0">' % prefix,
                        '<body name="%sbase" pos="%g %g %g" quat="0.7071068 0 0 0.7071068">' % (prefix, *ROBOT_BASE), 1)
    # remove actuator-free leftovers: inertial tags are fine
    return assets, body


ARM_JOINTS = ["r_joint%d" % i for i in range(1, 8)]
Q_HOME = np.array([0.0, 0.3, 0.0, -2.0, 0.0, 2.3, 0.785])




ARM_JOINTS = ["r_joint%d" % i for i in range(1, 8)]


def arm_model(cameras_json=None):
    """MjModel of the arm (+ optional cameras from cameras.json, OpenCV extrinsics converted to MuJoCo cameras)."""
    a, b = arm_xml_parts()
    cams = ""
    if cameras_json is not None:
        cj = json.load(open(cameras_json))["cameras"]
        for c in cj:
            R_w2c = np.array(c["R_w2c"])
            pos = np.array(c["position_world"])
            R_cv_in_world = R_w2c.T
            R_mj = R_cv_in_world @ np.diag([1, -1, -1])
            q = np.zeros(4)
            mujoco.mju_mat2Quat(q, R_mj.ravel())
            fy = c["K"][1][1]
            fovy = np.degrees(2 * np.arctan(0.5 * c["height"] / fy))
            cams += '<camera name="%s" pos="%.9g %.9g %.9g" quat="%.9g %.9g %.9g %.9g" fovy="%.9g"/>' % (
                c["name"], *pos, *q, fovy)
    xml = ('<mujoco><compiler angle="radian"/><visual><global offwidth="1280" offheight="960"/></visual>'
           '<asset>%s</asset><worldbody>%s%s</worldbody></mujoco>' % (a, cams, b))
    return mujoco.MjModel.from_xml_string(xml)


def set_arm(m, d, q):
    for i, n in enumerate(ARM_JOINTS):
        d.qpos[m.jnt_qposadr[m.joint(n).id]] = q[i]
    mujoco.mj_kinematics(m, d)
    mujoco.mj_camlight(m, d)


def ball_position(m, d):
    """World position of the ball centre (equals the probe position used by the physics)."""
    return d.site_xpos[m.site("r_tip").id].copy()
