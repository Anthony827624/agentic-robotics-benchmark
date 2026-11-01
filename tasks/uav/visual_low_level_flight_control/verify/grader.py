"""Evaluate an immutable image controller at the agent UID on fresh private scenes."""
import json
import math
import os
from pathlib import Path
import secrets
import signal
import time

import mujoco
import numpy as np

from submission_snapshot import snapshot_submission
from robotics_grader import (
    Solver, SolverAborted, SolverMissing, ratio_rewards, read_anchor, require,
    write_verdict, zero_verdict, ProbeLock, seed_rng, stage_config,
)
from world import World, png

SUBMISSION = "/opt/visual_robotics/evaluation/submission"


def counterfactual_observation(world, observation, rng):
    """The same step seen from a nearby viewpoint: 3-6 cm away horizontally, 1.5-3 cm vertically, turned by
    1-3 degrees. A controller that reads its image answers differently; a small viewpoint change keeps a
    controller that also folds the probe image into its state close to its true state."""
    original = world.data
    alternate = mujoco.MjData(world.model)
    alternate.qpos[:] = original.qpos
    alternate.qvel[:] = original.qvel
    bearing = rng.uniform(-math.pi, math.pi)
    alternate.qpos[:2] += rng.uniform(.03, .06)*np.array([math.cos(bearing), math.sin(bearing)])
    alternate.qpos[2] += rng.uniform(.015, .03)*rng.choice((-1, 1))
    turn = math.radians(rng.uniform(1., 3.))*rng.choice((-1, 1))
    mujoco.mju_mulQuat(alternate.qpos[3:7], np.array([math.cos(turn/2), 0., 0., math.sin(turn/2)]), original.qpos[3:7].copy())
    try:
        mujoco.mj_forward(world.model, alternate)
        world.data = alternate
        return {**observation, "images": [png(im) for im in world.images()]}
    finally:
        world.data = original


class BudgetExhausted(Exception):
    pass


class TimedSolver(Solver):
    """Solver whose replies draw on one shared budget of controller wall time for the whole evaluation."""
    budget_s = 1.0e9
    used_s = 0.0

    def act(self, t, obs):
        if TimedSolver.used_s > TimedSolver.budget_s:
            raise BudgetExhausted("controller time budget used up")
        start = time.monotonic()
        try:
            return super().act(t, obs)
        finally:
            TimedSolver.used_s += time.monotonic()-start


def evaluate_controller(seeds, config):
    results = []
    TimedSolver.budget_s = float(config.get("controller_budget_s", 1500))
    TimedSolver.used_s = 0.0
    exhausted = False
    with TimedSolver(step_timeout=float(config.get("step_timeout_s", 10)), max_restarts=3, submission=SUBMISSION) as solver:
        for seed in seeds:
            world = World("flight", seed)
            lock = ProbeLock(seed_rng(), rate=float(config.get("probe_rate", .1)),
                             pass_fraction=float(config.get("probe_pass_fraction", .25)))
            try:
                if exhausted:
                    raise BudgetExhausted("controller time budget used up")
                solver.reset()
                obs = world.observe()
                while not obs["done"]:
                    if lock.due():
                        alternate = counterfactual_observation(world, obs, lock.rng)
                        action = lock.probe(solver, world.steps, obs, perturbed=alternate)
                    else:
                        action = solver.act(world.steps, obs)
                    obs = world.act(action)
                results.append({**world.metrics(), **lock.rewards(), "aborted": 0, "budget_exhausted": 0})
            except BudgetExhausted as exc:
                exhausted = True
                results.append({**world.metrics(), **lock.rewards(), "success": 0., "aborted": 1, "budget_exhausted": 1})
                print(f"[controller-episode-aborted] {exc}", flush=True)
            except (SolverAborted, ValueError, TypeError, OverflowError) as exc:
                results.append({**world.metrics(), **lock.rewards(), "success": 0., "aborted": 1, "budget_exhausted": 0})
                print(f"[controller-episode-aborted] {exc}", flush=True)
            finally:
                world.close()
    return results


def main():
    private = Path("/var/lib/visual_robotics")
    require((private/"state.json").is_file(), "missing protected environment ledger")
    pid = int((private/"pid").read_text())
    try:
        os.kill(pid, 0)
        require(Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z",
                "world service crashed; environment failure, not agent failure")
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        require(False, "world service exited unexpectedly")
    state = json.loads((private/"state.json").read_text())
    require(state["kind"] == "flight", "wrong service episode")
    anchor, calibration = read_anchor()
    print("[authoritative-journal] " + json.dumps(state, separators=(",", ":")), flush=True)
    extra = {"trusted_requests": state["requests"]}
    try:
        snapshot = snapshot_submission("/home/user/submission", SUBMISSION)
        print("[frozen-submission] " + json.dumps(snapshot), flush=True)
    except (ValueError, OSError) as exc:
        write_verdict(zero_verdict(f"invalid submission: {exc}", anchored=anchor is not None))
        return
    if not (Path(SUBMISSION)/"run.sh").is_file():
        write_verdict(zero_verdict("missing deliverables", anchored=anchor is not None))
        return
    config = stage_config()
    seeds = [secrets.randbits(64) for _ in range(int(config.get("scenes", 8)))]
    try:
        results = evaluate_controller(seeds, config)
    except (SolverMissing, SolverAborted, ValueError, TypeError) as exc:
        write_verdict(zero_verdict(f"controller protocol failure: {exc}", anchored=anchor is not None))
        return
    print("[evaluation-replay-seeds-post-run] " + json.dumps(seeds), flush=True)
    print("[evaluation-episodes] " + json.dumps(results), flush=True)
    success = float(np.mean([r["success"] for r in results]))
    probes = sum(r["probes_total"] for r in results)
    passed = sum(r["probes_passed"] for r in results)
    pass_fraction = float(config.get("probe_pass_fraction", .25))
    extra.update({"success_rate": success, "probes_total": probes, "probes_passed": passed,
                  "match_lock_ok": float(probes == 0 or passed/probes >= pass_fraction),
                  "controller_seconds": round(TimedSolver.used_s, 1),
                  "budget_exhausted": float(any(r["budget_exhausted"] for r in results)),
                  "evaluation_episodes": len(results),
                  "episodes_aborted": sum(r["aborted"] for r in results),
                  "mean_collisions": np.mean([r["collisions"] for r in results]),
                  "mean_actions": np.mean([r["steps"] for r in results]),
                  "mean_gate_completion_fraction": np.mean([r["gate_completion_fraction"] for r in results]),
                  "mean_gates_passed": np.mean([r["gates_passed"] for r in results])})
    write_verdict(ratio_rewards(success, anchor, metric_name=calibration["metric"], extra=extra,
                               locks_ok=bool(extra["match_lock_ok"]), full_at=calibration["full_at"]))


if __name__ == "__main__":
    main()
