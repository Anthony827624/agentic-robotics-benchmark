"""Visible practice evaluation for the spacecraft docking task."""

import argparse
import json
import os
import queue
import random
import subprocess
import sys
import threading

sys.path.insert(0, "/home/user")
from spacecraft_docking_env import FaultAdaptiveDocking  # noqa: E402

PRACTICE_SEEDS = [1907, 2909, 3911, 4931, 5953]
STEP_TIMEOUT_S = 30.0
READY_TIMEOUT_S = 120.0
REPEAT_STEPS = (25, 105, 220)


class WireError(Exception):
    pass


class Wire(object):
    def __init__(self, submission):
        self.proc = subprocess.Popen(
            ["bash", "run.sh"], cwd=submission, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, universal_newlines=True, bufsize=1
        )
        self.lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _read(self, timeout):
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            raise WireError("solver reply timeout")
        if line is None:
            raise WireError("solver closed stdout")
        return line

    def ready(self):
        for _ in range(200):
            if self._read(READY_TIMEOUT_S).strip() == "READY":
                return
        raise WireError("solver never printed READY")

    def request(self, payload):
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self._read(STEP_TIMEOUT_S).strip()
            if not line:
                continue
            try:
                reply = json.loads(line)
            except ValueError:
                raise WireError("non-JSON output on protocol stdout")
            if not isinstance(reply, dict):
                raise WireError("reply is not a JSON object")
            return reply

    def act(self, t, obs):
        reply = self.request({"type": "act", "t": t, "obs": obs})
        if "action" not in reply:
            raise WireError("solver returned no action: {}".format(reply))
        return reply["action"]

    def close(self):
        try:
            self.request({"type": "close"})
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def different(a, b):
    try:
        aa, bb = [float(v) for v in a], [float(v) for v in b]
    except Exception:
        return False
    return len(aa) != len(bb) or max(
        [abs(x - y) for x, y in zip(aa, bb)] or [0.0]
    ) > 1e-4


def episode(wire, seed, rng, stats):
    env = FaultAdaptiveDocking()
    obs = env.reset(seed)
    wire.request({"type": "reset"})
    quality = 0.0
    info = {"success": False, "failure_reason": "timeout"}
    for t in range(env.max_steps):
        if t in REPEAT_STEPS:
            changed = list(obs)
            changed[0] += rng.uniform(-0.28, 0.28)
            changed[1] += rng.uniform(-0.28, 0.28)
            import math
            yaw = math.atan2(changed[3], changed[2]) + rng.uniform(-0.18, 0.18)
            changed[2], changed[3] = math.cos(yaw), math.sin(yaw)
            if rng.random() < 0.5:
                other, action = wire.act(t, changed), wire.act(t, obs)
            else:
                action, other = wire.act(t, obs), wire.act(t, changed)
            stats["repeated"] += 1
            stats["different"] += int(different(action, other))
        else:
            action = wire.act(t, obs)
        obs, quality, done, info = env.step(action)
        if done:
            break
    return quality, info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", default=os.environ.get(
        "SUBMISSION_DIR", "/home/user/submission"))
    parser.add_argument("--episodes", type=int, default=3)
    args = parser.parse_args()
    if not os.path.isfile(os.path.join(args.submission, "run.sh")):
        print("[practice] no run.sh at {}".format(args.submission))
        return 2

    rng = random.Random(2026)
    stats = {"repeated": 0, "different": 0}
    qualities, outcomes = [], []
    wire = Wire(args.submission)
    try:
        wire.ready()
        for seed in PRACTICE_SEEDS[:max(0, min(args.episodes, len(PRACTICE_SEEDS)))]:
            quality, info = episode(wire, seed, rng, stats)
            qualities.append(quality)
            outcomes.append({
                "quality": round(quality, 4),
                "success": bool(info.get("success")),
                "reason": info.get("failure_reason", ""),
            })
    except (WireError, ValueError, TypeError) as exc:
        print("[practice] {}".format(exc))
        return 1
    finally:
        wire.close()

    if stats["repeated"] and stats["different"] == 0:
        print("[practice] WARNING: actions ignored perturbed observations", file=sys.stderr)
    mean = sum(qualities) / len(qualities) if qualities else 0.0
    print(json.dumps({
        "metric": "mean_docking_quality",
        "direction": "higher",
        "raw_value": round(mean, 5),
        "episodes": outcomes,
        "repeated_t_queries": stats["repeated"],
        "repeated_t_answers_differed": stats["different"],
        "note": "practice seeds only; this is not your score",
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
