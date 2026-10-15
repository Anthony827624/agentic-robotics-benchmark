"""The grader. Runs as ROOT in the verify stage, after the agent is gone.

This is the closed_loop pattern: the grader owns the simulator and drives the agent's
policy step by step. The three properties that make the resulting number trustworthy:

* `verify/` is absent from the sandbox while the agent works, so nothing here — this file,
  the anchor, the config — was ever readable by the agent.
* The solver runs at the AGENT's UID (`robotics_grader.Solver` spawns it through
  `runuser`), so the agent's code cannot read this process, its environment, or /opt/ale,
  and cannot write the verdict.
* Seeds are drawn from OS entropy inside this process and never touch disk, and the metric
  is computed from this process's own simulator state. Nothing the solver prints is an
  input to the score.

To turn this into your task: replace `TemplateReach` with your environment (imported from
the image), and adjust `run_episodes` to your task's stepping. Leave the structure alone —
the abort handling, the probe lock, and the scoring are the parts that must not vary.

The wire is keyed by the step index `t`, which this loop owns. A probed step is asked
twice with the same `t` (`lock.probe`) — real and perturbed, in a hidden order — and only
the real reply reaches the simulator; nothing on the wire marks the probe.

For the other two patterns:

* **open_loop** — delete the `Solver`. The agent's problems and answers already exist on
  disk from the agent phase; read its answers, re-simulate them under your own dynamics,
  and reject any answer that does not match the state it claims to start from.
* **artifact_rollout** — delete the `Solver` too. Load the agent's frozen artifact inside
  `robotics_grader.deprivileged` (deserializing as root hands the grader over), then roll
  it out yourself on hidden seeds. No wire means no probe lock is needed.
"""

import math
import os
import sys

# Sibling files — env.py and the vendored robotics_grader/ package — import from this
# stage folder. The canonical robotics_grader lives at the repo root (shared/); the copy
# here is byte-identical, CI-checked, and travels with the task, because the engine
# stages exactly this folder and nothing else.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env import TemplateReach  # noqa: E402
from robotics_grader import (  # noqa: E402
    ProbeLock,
    Solver,
    SolverAborted,
    SolverMissing,
    ratio_rewards,
    read_anchor,
    seed_rng,
    stage_config,
    write_verdict,
    zero_verdict,
)


def run_episodes(config, rng):
    """Drive the agent's solver through every scored episode.

    Returns the mean per-step distance to the goal — a COST, on a positive scale, where 0
    is perfect. The environment's own reward is the negative of that distance, and scoring
    the negative directly would invert the ratio: with a negative anchor a better policy
    scores lower, silently. Report the cost and declare `direction: lower` instead. The kit
    refuses a non-positive anchor for exactly this reason.

    An episode that aborts (solver crash, stalled step, protocol violation, malformed
    action) is charged what standing still would cost: its starting distance to the goal,
    for every step. Dropping it instead would let a weak policy abort the episodes it would
    do badly in and average only the ones it does well in; charged like this, aborting can
    never beat trying.
    """
    episodes = int(config.get("episodes", 10))
    lock = ProbeLock(
        rng,
        rate=config.get("probe_rate", 0.1),
        pass_fraction=config.get("probe_pass_fraction", 0.5),
        dims=2,  # perturb the position components of [x, y, vx, vy, gx, gy]
    )

    env = TemplateReach()
    costs, aborted = [], 0
    # Raises SolverMissing (no run.sh) or SolverAborted (it never comes up); main() scores both 0.
    solver = Solver(step_timeout=config.get("step_timeout_s", 30))
    try:
        for episode in range(episodes):
            # Hidden, in memory only. 64 bits: the agent holds a copy of this simulator, and
            # a 31-bit seed can be brute-forced from the first observation. (Here the seed
            # decides only what that observation already shows; in a task with hidden
            # parameters it decides everything.)
            seed = rng.seed(bits=64)
            total, steps = 0.0, 0
            obs = env.reset(seed)
            standing_still = math.hypot(obs[0] - obs[4], obs[1] - obs[5])
            try:
                solver.reset()
                for t in range(env.max_steps):
                    if lock.due():
                        # Ask this step twice with the same t — the real observation and
                        # a perturbed copy, in a hidden order — and apply only the real
                        # reply; the perturbed one is compared and thrown away. On the
                        # wire the probe is indistinguishable from any repeated query.
                        action = lock.probe(solver, t, obs)
                    else:
                        action = solver.act(t, obs)
                    obs, reward, done = env.step(_two_numbers(action))
                    total += -reward     # reward is the negative distance; accumulate cost
                    steps += 1
                    if done:
                        break
            except SolverAborted as exc:
                aborted += 1
                costs.append(standing_still)
                print("[grader] episode {} aborted ({}); charged {:.4f}".format(
                    episode, exc, standing_still), file=sys.stderr, flush=True)
                continue
            cost = total / steps if steps else float("inf")
            costs.append(cost)
            print("[grader] episode {}: mean_distance={:.4f}".format(episode, cost),
                  file=sys.stderr, flush=True)
    finally:
        solver.close()

    return costs, aborted, lock


def _two_numbers(action):
    """The solver's action as two finite floats, or a protocol error (the episode aborts;
    a malformed reply must never crash the grader into a task error)."""
    try:
        ax, ay = (float(value) for value in action)
    except (TypeError, ValueError, OverflowError):
        raise SolverAborted("malformed action: {!r}".format(action)[:160])
    if not (math.isfinite(ax) and math.isfinite(ay)):
        raise SolverAborted("non-finite action")
    return [ax, ay]


def main():
    config = stage_config()
    rng = seed_rng()
    anchor, anchor_meta = read_anchor()

    try:
        costs, aborted, lock = run_episodes(config, rng)
    except (SolverMissing, SolverAborted) as exc:
        # An untouched sandbox is a real, gradeable outcome: `ale validate` fails the run
        # unless it yields an all-zero verdict, so a missing submission scores 0 instead
        # of crashing. So does a submission that never comes up (no READY, or it exits
        # at once): `Solver(...)` raises SolverAborted before the first episode.
        # A crash here would read as a broken task, which this is not.
        write_verdict(zero_verdict(reason=str(exc), anchored=anchor is not None))
        return

    # No usable episode is the worst outcome, not a free zero-cost win — and on a
    # lower-is-better metric a measured 0 would otherwise score full marks.
    measured = sum(costs) / len(costs) if costs else float("inf")

    verdict = ratio_rewards(
        measured,
        anchor,
        cap=config.get("scoring_cap", 1.5),
        higher_is_better=anchor_meta.get("direction", "higher") == "higher",
        metric_name="mean_goal_distance",
        locks_ok=lock.ok,
        extra=dict(lock.rewards(), episodes_aborted=aborted),
        # Where the reward saturates to 1.0, in ratio units: the tolerance that lets an
        # honest oracle land on exactly 1.0 under fresh seeds. The engine fails validation
        # (`oracle_not_full`) when any oracle reward is below 1.0.
        full_at=anchor_meta.get("full_at", 1.0),
    )
    write_verdict(verdict)
    print("[grader] measured={:.3f} anchor={} ratio={:.3f} reward={:.3f} "
          "lock={} aborted={}".format(measured, anchor, verdict["metrics"]["ratio"],
                                      verdict["rewards"]["reward"], lock.ok, aborted),
          file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
