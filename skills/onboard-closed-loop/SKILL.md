---
name: onboard-closed-loop
description: >-
  Build an CLOSED_LOOP task for the Agentic Robotics Benchmark (on the ALE engine): the grader owns the simulator and steps the agent's
  policy over a stdio JSON-lines wire, with hidden perturbation probes as the match lock.
  Loaded by onboard-task with its spec payload. Drives image/Dockerfile → oracle (train + a
  self-contained submission) → verify/verify.py on the vendored robotics_grader package →
  practice grader → the anchor from a REAL run → a green `ale validate`.
---

# onboard-closed-loop — build a closed_loop task

Loaded by **onboard-task** with `{task, platform, metric, image, task_dir}`. Ask
only what this pattern needs, then drive the build.

The shape: the grader **owns** the simulator. It steps the agent's policy one observation at a
time, computes the metric from its own state, and never treats anything the solver prints as a
scoring input. The solver runs at the **agent's UID** — `verify/run.sh` is root, so spawning
the solver directly would hand the agent the grader's privileges. `robotics_grader.Solver` does
the drop through `runuser`; from there `/opt/ale`, the verdict file and the grader's memory are
all out of reach (see `docs/evidence/isolation-audit.md` for the audited proof).

Worked example: `tasks/space/fault_adaptive_spacecraft_docking` — a real closed_loop task
(a fault-adaptive docking controller for a six-thruster spacecraft) that validates green
on the shared kit. It also shows what a task that hands the agent its simulator must do:
64-bit seeds and a keyed-hash generator, so the source gives nothing away. Template:
`templates/task/`, whose `verify/verify.py` is already this pattern.

## Mode-specific questions

1. **What is one observation?** A state vector, an image, a point cloud? If the reference
   method defines its own observation (point clouds, cropped RGB), **the grader must generate
   it** — camera placement, cropping, downsampling, farthest-point sampling — and the oracle's
   anchor must be trained against that same grader-side pipeline. Otherwise the policy is
   silently evaluated out of distribution and the anchor measures the mismatch instead of the
   method.
2. **What is one action?** It has to be JSON-serializable, so a dict of arrays or a flat list.
3. **What is one episode?** Reset semantics, termination, the step budget, and what counts as
   success.
4. **What perturbation should the probe apply?** The lock asks the policy twice for the same
   step `t` — once with the true observation, once with a hidden perturbation, in random order
   and with no marker on the wire — and requires the action to move. Only the reply to the true
   observation is applied. Pick a perturbation a real policy must respond to (shift the position
   components, move the goal, nudge the obstacle) and set `dims` in `ProbeLock` to cover exactly
   those leading components. A constant, a replay and an obs-blind policy must all fail it.
5. **How long does the oracle need to train?** That is `timeouts.agent`, and `ale validate`
   really pays it: an oracle that trains a policy (PPO, say) makes every validate that slow.

## The solver contract (what the agent must build)

`/home/user/submission/run.sh` starts a long-lived process speaking one JSON object per line:

```
                                     <- READY
{"type": "reset"}                    -> {"ok": true}
{"type": "act", "t": 0, "obs": ...}  -> {"action": [...]}
{"type": "act", "t": 1, "obs": ...}  -> {"action": [...]}   # a step may be queried more than
{"type": "act", "t": 1, "obs": ...}  -> {"action": [...]}   # once with the same t; answer from
                                                            # the obs carried; t does not advance
{"type": "close"}                    -> {"ok": true}, then exit
```

Write this into `instruction.md` verbatim, and warn that **stdout is the wire**: a library
banner during model load corrupts the handshake. The package tolerates a bounded banner before
`READY` (200 lines) and nothing after it, so diagnostics go to stderr.

At grading time the solver only **loads** its checkpoint. Training belongs to the agent phase,
under the agent's own budget — that separation is why a scoring run is cheap and comparable.

## Build steps

1. **`image/Dockerfile`.** Start from the template's: final stage directly FROM the official
   ALE base, py3.12 `/opt/venv` with a world-traversable interpreter, and the agent-UID import
   self-check at the end. Add the simulator at a pinned commit, and strip the reference
   implementation's own recipe in the *same* layer that clones it — an image that ships the
   exact training script hands the agent the answer.
2. **`oracle/run.sh`** — the reference implementation, as it is. Train inside `timeouts.agent`,
   then write a **self-contained** `/home/user/submission/` (checkpoint + solver + `run.sh`).
   Self-contained matters: `verify` drives that folder, not your oracle directory.
3. **`verify/verify.py`** — start from the template and change the physics only. Keep:
   * `Solver(step_timeout=…)` for the UID drop and the per-step deadline;
   * the `SolverAborted` handler — an aborted episode is dropped from the metric and counted,
     so a solver that answers one episode in ten cannot average its way to a score;
   * `ProbeLock`, and the rule that a probe reply is compared and **thrown away**, never applied
     to the simulator;
   * `seed_rng()` — hidden seeds from OS entropy, in memory only, never written anywhere;
   * `ratio_rewards(...)` + `write_verdict(...)` for scoring.
   Leave the vendored `verify/robotics_grader/` untouched — CI diffs it against the canonical
   copy at `shared/robotics_grader/`.
4. **`verify/grader_config.json`** — episodes, `probe_rate`, `probe_pass_fraction`,
   `step_timeout_s`, `scoring_cap`. Not `params:`: those render into the prompt and are strict
   both ways. Not `config.json` either: the engine overwrites that name with its own judge
   settings at verify time.
5. **`setup/payload/practice_grader/grade.py`** — the agent's self-test on visible seeds;
   `setup/run.sh` stages `payload/` into the agent home. Raw metric only: no anchor, no ratio,
   no score, and no import from `verify/`.
6. **`verify/anchor.json`** — leave `value` null. Gate once, take the measured value off the
   verdict, record it, gate again. Never a published figure: it came from other hardware,
   other seeds, and usually a kinder evaluation — the anchor is the reference implementation's
   own measured value.
7. **The gate.**
   ```bash
   uv run python -m harness.ale_onboard --gate-only tasks/<platform>/<task> --json
   uv run python scripts/lint_domain.py
   ```
   `needs_anchor` on the first pass is expected (the engine reports it as `oracle_not_full`;
   `harness/ale_onboard.py` reads the measured value off the records). `built` means the
   double pass held: an untouched sandbox scored an exact all-zero and the oracle an exact
   all-one — the engine fails validation otherwise (`untouched_nonzero`, `oracle_not_full`).
   That demands two things of your grader: catch a missing submission (`SolverMissing`) and
   write `zero_verdict(...)` instead of crashing, and size the anchor's `full_at` from the
   metric's measured seed-to-seed spread so an honest oracle saturates to 1.0.
8. **Prove the lock bites.** Replace the oracle's solver with one that returns a constant,
   re-gate, and confirm `match_lock_ok: 0.0` and `reward: 0.0` — with the wire working. A lock
   nobody has seen fail is a lock nobody has tested. (Ours: 204 probes fired, 0 passed, reward
   0 — recorded in `docs/evidence/isolation-audit.md`.)

## Give-up rules

The router's shared rules apply. The one that bites hardest here: closed_loop usually needs a
**trained checkpoint**, and if it can neither be fetched from a stable public URL with a pinned
sha256 nor retrained inside the oracle's budget, stop and say so. Never commit a 100 MB+ blob,
never bake one into the image, and never fabricate an anchor or a green run.
