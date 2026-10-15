"""Practice grader for uav_hover (AGENT-VISIBLE self-test).

Runs a short local rollout of your submission's solver against HoverAviary on
FIXED, non-hidden seeds and prints the raw mean_episode_reward it observes. This
is a smoke test that your solver speaks the protocol and hovers — it does NOT
tell you your official score: it uses visible seeds, reports only the raw
metric value (no reference denominator, no ratio, no score), and imports nothing
from the hidden grading side. The official grader samples fresh hidden seeds and
computes the authoritative metric in an isolated container after you finish.

Usage (in this session, after your submission/ is ready):
    python3 practice_grader/grade.py [--episodes 3] [--submission /home/user/submission]
"""

import argparse
import json
import os
import subprocess
import sys

import numpy as np

# Visible practice seeds (deliberately NOT the hidden eval seeds).
PRACTICE_SEEDS = [11, 22, 33, 44, 55]


def _rollout(env_cls, ObservationType, ActionType, proc, seed):
    env = env_cls(obs=ObservationType("kin"), act=ActionType("one_d_rpm"))
    obs, _ = env.reset(seed=int(seed))
    _send(proc, {"type": "reset"})
    _recv(proc)
    total = 0.0
    max_steps = int(env.EPISODE_LEN_SEC * env.CTRL_FREQ) + 1
    for _ in range(max_steps):
        _send(proc, {"type": "act", "obs": {"obs": np.asarray(obs, np.float32).tolist()}})
        reply = _recv(proc)
        if "action" not in reply:
            raise RuntimeError("solver error: {}".format(reply))
        act = np.asarray(reply["action"], np.float32).reshape(env.action_space.shape)
        obs, reward, term, trunc, _ = env.step(act)
        total += float(reward)
        if term or trunc:
            break
    return total


def _send(proc, obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def _recv(proc):
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("solver closed the connection")
    return json.loads(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submission", default=os.environ.get("SUBMISSION_DIR",
                                                           "/home/user/submission"))
    ap.add_argument("--episodes", type=int, default=3)
    args = ap.parse_args()

    from gym_pybullet_drones.envs.HoverAviary import HoverAviary
    from gym_pybullet_drones.utils.enums import ActionType, ObservationType

    run_sh = os.path.join(args.submission, "run.sh")
    if not os.path.isfile(run_sh):
        print("[practice] no submission/run.sh at {}".format(args.submission))
        return 2

    proc = subprocess.Popen(["bash", "run.sh"], cwd=args.submission,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True)
    try:
        # wait for READY
        while True:
            line = proc.stdout.readline()
            if not line:
                print("[practice] solver exited before READY")
                return 1
            if line.strip() == "READY":
                break
        rewards = []
        for s in PRACTICE_SEEDS[:args.episodes]:
            rewards.append(_rollout(HoverAviary, ObservationType, ActionType, proc, s))
        _send(proc, {"type": "close"})
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    mean = sum(rewards) / len(rewards) if rewards else 0.0
    print(json.dumps({"metric": "mean_episode_reward",
                      "raw_value": round(mean, 3),
                      "per_episode": [round(r, 3) for r in rewards],
                      "note": "practice seeds only; NOT your official score"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
