"""Privileged reference controller for arm_hidden_mechanism_box (stdio wire, kit protocol).

It knows every hidden box's mechanism (boxes.json, shipped from oracle/ref/) and estimates the
box pose from the two fixed cameras at t = 0; then it drives scripted primitives through the
stages in order. The plan runs in a worker thread; each wire query is answered from the plan's
current absolute target and the queried observation, so repeated queries of the same step are
answered consistently (and differ when the observation differs).
"""

import base64
import json
import math
import os
import queue
import sys
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import oracle_plan  # noqa: E402
import perceive  # noqa: E402
import skills as S  # noqa: E402

BOXES = json.load(open(os.path.join(HERE, "boxes.json")))
T_WALL = 0.012
MAX_DPOS, MAX_DROT = 0.03, 0.2


def log(msg):
    print("[oracle] " + msg, file=sys.stderr, flush=True)


def decode(img):
    if not isinstance(img, dict) or not img.get("shape") or 0 in img["shape"]:
        return None
    return np.frombuffer(base64.b64decode(img["b64"]), np.uint8).reshape(img["shape"])


class Stop(Exception):
    pass


class WireArm(S.Arm):
    """Arm whose act() publishes an absolute target intent and waits for the next observation."""

    def __init__(self, obs, budget, obs_q, intent_q):
        S.Arm.__init__(self, None, obs, budget)
        self.obs_q = obs_q
        self.intent_q = intent_q

    def act(self, dpos, drot, grip=None, render=()):
        if grip is not None:
            self.grip = grip
        if self.budget[0] <= 0:
            raise StopIteration
        self.budget[0] -= 1
        tgt = self.pos + np.asarray(dpos, float)
        Rt = S.axis_angle(np.asarray(drot, float)) @ self.R
        self.intent_q.put((tgt, Rt, float(self.grip)))
        o = self.obs_q.get()
        if o is None:
            raise Stop()
        self.obs = o
        return o


def plan_worker(obs0, obs_q, intent_q):
    try:
        cfg = BOXES[obs0["box"]]
        sh = cfg["shell"]
        lid = cfg.get("final", "lid") == "lid"
        z = 0.80 + sh["H"] + (T_WALL if lid else 0.0)
        lip = (sh.get("lip_u", 0.0), sh.get("lip_w", 0.09)) if lid else None
        D, W = (sh["D"] + 0.004, sh["W"] + 0.004) if lid else (sh["D"], sh["W"])
        pose, diag = perceive.estimate_pose({"left": decode(obs0["left"]), "right": decode(obs0["right"])}, D, W, z, lip)
        log("box {} pose {} {}".format(obs0["box"], np.round(pose, 4).tolist(), diag))
        arm = WireArm(obs0, [100000], obs_q, intent_q)
        oracle_plan.run_plan(arm, cfg, pose, log=log)
    except (Stop, StopIteration):
        return
    except Exception as exc:  # noqa: BLE001 - the plan failing must not kill the wire
        log("plan error: {}: {}".format(type(exc).__name__, exc))
    intent_q.put("done")


def quat_to_mat(q):
    return S.quat_to_mat(np.asarray(q, float))


class Controller(object):
    def __init__(self):
        self.thread = None
        self.obs_q = None
        self.intent_q = None
        self.last_t = -1
        self.intent = None
        self.finished = False

    def reset(self):
        if self.obs_q is not None:
            self.obs_q.put(None)
        self.thread = None
        self.last_t = -1
        self.intent = None
        self.finished = False

    def _next_intent(self):
        it = self.intent_q.get()
        if it == "done":
            self.finished = True
            return None
        return it

    def act(self, t, obs):
        if t != self.last_t:
            if self.thread is None:
                self.obs_q, self.intent_q = queue.Queue(), queue.Queue()
                self.thread = threading.Thread(target=plan_worker, args=(obs, self.obs_q, self.intent_q), daemon=True)
                self.thread.start()
                it = self._next_intent()
            elif self.finished:
                it = None
            else:
                self.obs_q.put(obs)
                it = self._next_intent()
            if it is not None:
                self.intent = it
            self.last_t = t
        pos = np.asarray(obs["ee_pos"], float)
        R = quat_to_mat(obs["ee_quat"])
        if self.intent is None:
            return [0.0] * 6 + [-1.0, 0.0]
        tgt, Rt, grip = self.intent
        dp = tgt - pos
        n = np.linalg.norm(dp)
        if n > MAX_DPOS:
            dp = dp * (MAX_DPOS / n)
        dr = S.rotvec(Rt @ R.T)
        n = np.linalg.norm(dr)
        if n > MAX_DROT:
            dr = dr * (MAX_DROT / n)
        return [float(v) for v in dp] + [float(v) for v in dr] + [grip, 0.0]


def main():
    ctl = Controller()
    print("READY", flush=True)
    for line in sys.stdin:
        msg = json.loads(line)
        if msg["type"] == "reset":
            ctl.reset()
            print(json.dumps({"ok": True}), flush=True)
        elif msg["type"] == "act":
            print(json.dumps({"action": ctl.act(msg["t"], msg["obs"])}), flush=True)
        elif msg["type"] == "close":
            print(json.dumps({"ok": True}), flush=True)
            break


if __name__ == "__main__":
    main()
