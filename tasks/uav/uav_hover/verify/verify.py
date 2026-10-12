"""Closed-loop grader for uav_hover — runs as ROOT in the verify stage.

The eval protocol our benchmark calls `closed_loop`, expressed entirely inside a
standard ALE verify stage (no custom Environment):

* The grader OWNS the HoverAviary simulator. Per run it samples fresh hidden seeds
  from OS entropy — the agent phase is already over, and the values never touch an
  agent-readable path (this file lives under root-only /opt/ale/verify).
* It spawns the agent-built solver (`/home/user/submission/run.sh`) AS THE
  UNPRIVILEGED AGENT USER via `runuser`, and drives obs->action over a stdio
  JSON-lines wire with a per-step timeout. The UID boundary is the trust boundary:
  the solver cannot read the grader's memory, environment, or /opt/ale, and cannot
  write the rewards file (iron law #1's "different container OR UID").
* Match lock: hidden perturbation probes — a perturbed observation must change the
  action (an obs-sensitive policy responds; a constant/replay stream does not).
  Probe queries are never applied to the sim.
* The authoritative metric (mean episode reward over non-aborted episodes) is
  computed HERE from the grader's own sim; nothing the solver prints is a metric
  input. Aborted episodes (crash / timeout / protocol error) count against.

Scoring: ratio = clamp(measured / anchor, 0, cap); the ALE primary reward is
clamp(ratio / full_at, 0, 1), saturating at the anchor's declared seed-variance
tolerance so a genuinely trained policy lands on exactly 1.0 — the engine only warns
(`partial_oracle`) on a non-1.0 oracle; the exact all-one rule is ALE Robotics domain
policy enforced by harness/ale_onboard.py (raw measured + ratio ride along under
metrics). The anchor is the oracle
implementation's real graded value (verify/anchor.json — absent from the sandbox
during the agent phase).
"""

import json
import os
import queue
import subprocess
import sys
import threading

import numpy as np

STAGE_DIR = os.environ.get("ALE_STAGE_DIR", os.path.dirname(os.path.abspath(__file__)))
VERDICT_PATH = os.environ["ALE_VERDICT_PATH"]
HOME = os.environ.get("ALE_HOME", "/home/user")
SUBMISSION = os.path.join(HOME, "submission")
AGENT_USER = os.path.basename(HOME) or "user"

ENV_ID = "HoverAviary"
OBS_KIND, ACT_KIND = "kin", "one_d_rpm"
MAX_PRE_READY_LINES = 200


def _params():
    """Grader knobs from the stage-local config (task params render into the
    instruction and are strict-checked, so grader-only knobs live here)."""
    try:
        with open(os.path.join(STAGE_DIR, "grader_config.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _fail(reason):
    """A broken verify run is a task error, not an agent zero: crash loudly."""
    print("[grader] FATAL: {}".format(reason), file=sys.stderr, flush=True)
    raise SystemExit(1)


# --------------------------------------------------------------------------- #
# solver process (agent-UID) + JSON-lines wire with per-step timeout
# --------------------------------------------------------------------------- #
class SolverAborted(Exception):
    """Solver crash / timeout / protocol violation — aborts the EPISODE."""


class SolverMissing(Exception):
    """No submission where the task said one would be — an honest zero, not a task
    error: `ale validate`'s untouched pass demands a real all-zero verdict from a
    sandbox nobody touched, and a crash here would read as a broken task."""


class Solver:
    def __init__(self, step_timeout):
        if not os.path.isfile(os.path.join(SUBMISSION, "run.sh")):
            raise SolverMissing("no submission at {}/run.sh".format(SUBMISSION))
        self.step_timeout = float(step_timeout)
        # runuser drops to the agent account; the solver inherits PATH (venv) but
        # runs with the agent's UID — it cannot read /opt/ale or this process.
        self.proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            ["runuser", "-u", AGENT_USER, "--", "bash", "run.sh"],
            cwd=SUBMISSION,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._await_ready()

    def _pump(self):
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)  # EOF

    def _readline(self, timeout):
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise SolverAborted("solver step timeout ({}s)".format(timeout))
        if line is None:
            raise SolverAborted("solver closed its stdout")
        return line

    def _await_ready(self):
        # Tolerate a bounded banner before READY (library import noise).
        for _ in range(MAX_PRE_READY_LINES):
            line = self._readline(self.step_timeout * 4).strip()
            if line == "READY":
                return
        raise SolverAborted("solver never printed READY")

    def request(self, obj):
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise SolverAborted("solver stdin closed")
        while True:
            line = self._readline(self.step_timeout).strip()
            if not line:
                continue
            try:
                reply = json.loads(line)
            except ValueError:
                raise SolverAborted("solver wrote non-JSON on the wire: {!r}".format(line[:120]))
            if isinstance(reply, dict):
                return reply
            raise SolverAborted("solver reply is not an object")

    def act(self, obs, probe=False):
        req = {"type": "act", "obs": obs}
        if probe:
            req["probe"] = True
        reply = self.request(req)
        if "action" not in reply:
            raise SolverAborted("solver error: {}".format(reply.get("error", "no action")))
        return reply["action"]

    def reset(self):
        self.request({"type": "reset"})

    def close(self):
        try:
            self.proc.stdin.write(json.dumps({"type": "close"}) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


# --------------------------------------------------------------------------- #
# the grader's own sim + the probe match lock
# --------------------------------------------------------------------------- #
def run_episodes(params, rng):
    from gym_pybullet_drones.utils.enums import ActionType, ObservationType
    import importlib
    env_cls = getattr(importlib.import_module(
        "gym_pybullet_drones.envs.{}".format(ENV_ID)), ENV_ID)
    env = env_cls(obs=ObservationType(OBS_KIND), act=ActionType(ACT_KIND))

    episodes = int(params.get("episodes", 10))
    probe_rate = float(params.get("probe_rate", 0.1))
    solver = Solver(params.get("step_timeout_s", 30))

    records, probes_total, probes_passed = [], 0, 0
    try:
        for ep in range(episodes):
            seed = int(rng.integers(2 ** 31))     # hidden; in-memory only
            rec = {"steps": 0, "reward": 0.0, "aborted": False}
            try:
                obs, _ = env.reset(seed=seed)
                solver.reset()
                cap = int(env.EPISODE_LEN_SEC * env.CTRL_FREQ) + 1
                while rec["steps"] < cap:
                    vec = np.asarray(obs, dtype=np.float32)
                    obs_msg = {"obs": vec.tolist()}
                    if rng.random() < probe_rate:
                        # MATCH LOCK: shadow-query a shifted position; a genuine
                        # hover policy's action must move. Never applied to the sim.
                        flat = vec.copy().reshape(-1)
                        for i in range(min(3, flat.shape[0])):
                            flat[i] += float(rng.uniform(-0.4, 0.4))
                        a_probe = solver.act({"obs": flat.reshape(vec.shape).tolist()},
                                             probe=True)
                        a_real = solver.act(obs_msg)
                        probes_total += 1
                        pa = np.asarray(a_probe, dtype=np.float64).ravel()
                        ra = np.asarray(a_real, dtype=np.float64).ravel()
                        if pa.shape != ra.shape or float(np.max(np.abs(pa - ra))) > 1e-4:
                            probes_passed += 1
                    else:
                        a_real = solver.act(obs_msg)
                    act = np.asarray(a_real, dtype=np.float32).reshape(env.action_space.shape)
                    obs, reward, term, trunc, _info = env.step(act)
                    rec["steps"] += 1
                    rec["reward"] += float(reward)
                    if term or trunc:
                        break
            except SolverAborted as e:
                rec["aborted"] = True
                print("[grader] episode {} aborted: {}".format(ep, e),
                      file=sys.stderr, flush=True)
            records.append(rec)
            print("[grader] episode {}: reward={:.2f} steps={} aborted={}".format(
                ep, rec["reward"], rec["steps"], rec["aborted"]),
                file=sys.stderr, flush=True)
    finally:
        solver.close()

    return records, probes_total, probes_passed


def _write_verdict(rewards, metrics):
    """One gated score under `rewards`, everything else under `metrics`.

    Every reward key must be exactly 0 on an untouched sandbox (the engine fails the
    run otherwise) and exactly 1 for the oracle (ALE Robotics domain policy, enforced
    by harness/ale_onboard.py; the engine only warns), so the raw metric, the ratio
    and the lock outcomes ride in `metrics` — persisted to the run record, never
    gated. When the engine has staged
    its `ale_verify` package (always, in a real verify stage), the same envelope must
    also go through it, because the engine cross-checks the reward file against the
    verification record.
    """
    if os.environ.get("ALE_VERIFICATION_PATH"):
        from ale_verify import CheckResult, Verification
        record = Verification()
        for name, value in sorted(rewards.items()):
            record.check(name, CheckResult(float(value), "computed by the task's grader"))
        for name, value in sorted(metrics.items()):
            record.stat(name, float(value))
        record.write()
        return
    with open(VERDICT_PATH, "w") as f:
        json.dump({"rewards": rewards, "metrics": metrics}, f)


def main():
    params = _params()
    rng = np.random.default_rng(int.from_bytes(os.urandom(8), "big"))

    with open(os.path.join(STAGE_DIR, "anchor.json")) as f:
        anchor_meta = json.load(f)
    anchor = float(anchor_meta["value"])
    # Where the reward saturates, in ratio units: the seed-variance tolerance that lets
    # a genuinely trained policy land on exactly 1.0 (domain policy: harness/ale_onboard.py;
    # the engine itself only warns on a non-1.0 oracle).
    full_at = float(anchor_meta.get("full_at", 1.0))

    try:
        records, probes_total, probes_passed = run_episodes(params, rng)
    except SolverMissing as exc:
        print("[grader] scoring 0: {}".format(exc), file=sys.stderr, flush=True)
        _write_verdict({"reward": 0.0}, {"ratio": 0.0, "anchor_recorded": 1.0})
        return

    done = [r for r in records if not r["aborted"]]
    measured = sum(r["reward"] for r in done) / len(done) if done else 0.0

    # Match lock verdict: an obs-insensitive (canned/replay) solver zeroes out.
    frac_needed = float(params.get("probe_pass_fraction", 0.5))
    lock_ok = probes_total == 0 or (probes_passed / probes_total) >= frac_needed

    cap = float(params.get("scoring_cap", 1.5))
    ratio = max(0.0, min(measured / anchor if anchor else 0.0, cap))
    if not lock_ok:
        ratio = 0.0

    reward = max(0.0, min(ratio / full_at, 1.0))
    metrics = {
        "ratio": ratio,                            # our historical score (cap 1.5)
        "mean_episode_reward": float(measured),
        "anchor_recorded": 1.0,
        "episodes_aborted": float(len(records) - len(done)),
        "probes_total": float(probes_total),
        "probes_passed": float(probes_passed),
        "match_lock_ok": 1.0 if lock_ok else 0.0,
    }
    _write_verdict({"reward": reward}, metrics)
    print("[grader] measured={:.2f} anchor={:.2f} ratio={:.3f} reward={:.3f} lock={}".format(
        measured, anchor, ratio, reward, lock_ok), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
