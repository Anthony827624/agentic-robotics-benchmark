"""alelab plugin: the real hidden boxes (same robot environment as the agent's dev sim, box_env.py,
with the hidden box models built from the root-only instance file by mech.py).

Instance file (setup/ref/boxes.json): {box id: box config}. Lab instances = the scored boxes.
Every trial starts from a fresh 64-bit seed drawn from OS entropy (box placement jitter and
sensor noise). Episodes never end early (the reply does not reveal when a box is fully open);
replies carry the observation only (images, proprioception, wrench), never box state.
"""

import secrets

import box_env as E
import mech

_U8 = "u8[*,*,3]"
OBS_SCHEMA = {
    "t": "int",
    "box": "str",
    "q": "f64[7]",
    "dq": "f64[7]",
    "ee_pos": "f64[3]",
    "ee_quat": "f64[4]",
    "gripper": "float",
    "wrench": "f64[6]",
    "T_world_wrist": "f64[4,4]",
    "left": _U8,
    "right": _U8,
    "wrist": _U8,
}
MESSAGES = {
    "action": "action must be a list of 8 finite numbers (6 motion values, grip, camera mask 0-7)",
}


def _schema(ids):
    s = dict(OBS_SCHEMA)
    s["box"] = "enum:" + "|".join(ids)
    return {
        "info": {"control_dt": "float", "max_steps": "int"},
        "reset": s,
        "step": s,
    }


class _Episode(object):
    def __init__(self, env):
        self.env = env

    def step(self, action):
        obs, done, _info = self.env.step(action)
        return obs, bool(done)


class BoxLab(object):
    def __init__(self, instances, api):
        self.api = api
        self.specs = {}
        for bid, cfg in sorted(instances.items()):
            if bid.startswith("_"):
                continue
            box = mech.Box(cfg)
            self.specs[bid] = {"xml": box.xml(), "meta": box.meta()}
        self.SCHEMA = _schema(sorted(self.specs))
        self.MESSAGES = MESSAGES

    def instance_ids(self):
        return sorted(self.specs)

    def info(self):
        return {"control_dt": E.CONTROL_DT, "max_steps": E.MAX_STEPS}

    def validate_action(self, action):
        # depends only on the request and public constants (never on the instance or hidden state)
        try:
            E.parse_action(action)
        except E.ActionError:
            raise self.api.BadRequest("action")
        return [float(v) for v in action]

    def reset(self, instance):
        env = E.BoxEnv(self.specs[instance], box_id=instance, early_end=False)
        obs = env.reset(secrets.randbits(64))
        return _Episode(env), obs


def make_plugin(instances, api):
    return BoxLab(instances, api)
