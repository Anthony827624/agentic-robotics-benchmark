---
name: onboard-open-loop
description: >-
  Build an OPEN_LOOP task for the Agentic Robotics Benchmark (on the ALE engine): the agent produces whole answers offline (plans,
  trajectories, one-shot solutions) and the verify stage RE-SIMULATES every one of them under
  its own dynamics, rejecting any answer that does not start where it claims, teleports between
  states, or breaks the declared constraints. Loaded by onboard-task with its spec payload.
  Drives image/Dockerfile → oracle → verify/verify.py on the vendored robotics_grader package →
  practice grader → the anchor from a REAL run → a green `ale validate`.
---

# onboard-open-loop — build an open_loop task

Loaded by **onboard-task** with `{task, platform, metric, image, task_dir}`. Ask
only what this pattern needs, then drive the build.

The shape: the agent's work product is **answers**, not a policy. By the time the verify stage
runs they are already on disk, under a path the task declared in `artifacts:` — the agent wrote
them during its own phase, and `verify/` was not in the sandbox while it did. So there is no
solver process, no wire, and no `ProbeLock`. What `verify/verify.py` does instead is arithmetic
on those answers **under its own dynamics**, plus a structural check that they are answers to
the questions that were asked.

Recomputation is the whole pattern. A number the agent printed next to its answer is not
evidence, it is a claim by the party being measured — and a plan file is the easiest artifact in
robotics to write without solving anything. Read the raw answer, step your own model, let your
own state produce the metric.

Template: `templates/task/` — its `verify/verify.py` is the closed_loop pattern, and
its module docstring says exactly what to delete for this one. Shared machinery: the vendored
`verify/robotics_grader/` package (canonical copy at `shared/robotics_grader/`, byte-equality
enforced in CI). Audited trust boundary: `docs/evidence/isolation-audit.md`.

## Mode-specific questions

1. **What is one problem instance?** A start and a goal, an obstacle field, an initial state, a
   scene. Say it precisely enough that two people would generate the same thing.
2. **Who owns the authoritative copy of the instances?** Two arrangements work, and both rest on
   the agent being unable to move the goalposts:
   * **Grader-owned, with a visible copy.** The instances the metric is computed over live in
     `verify/` (or are generated there from a fixed definition), and `setup/payload/` carries
     the copy the agent works from. If the agent edits its copy it simply answers the wrong
     questions — the same argument the template makes for `verify/env.py` versus
     `setup/payload/template_env.py`.
   * **Handed out per run by `setup/run.sh`.** Runs before the agent, as root — so `chown`
     whatever the agent must read or rewrite, or your own oracle hits the wall first. The
     generator's per-run seed must stay inside `/opt/ale`, which is `drwx------ root:root` and
     untraversable for the agent's UID, and `verify/verify.py` re-derives from it the exact
     instances that were handed out. A seed on an agent-stattable path turns "solve the task"
     into "memorize the evaluation".
3. **What is one answer, byte for byte, and at exactly which path?** Upstream renders
   `instruction.md` once and checks placeholders strictly both ways, so write the paths
   literally; the task chose them and the image is fixed.
4. **How do you re-simulate one answer?** Integrator, timestep, collision model, tolerances. The
   dynamics the grader uses must live in `verify/`, imported from there or from the image —
   never from a file in the agent's workspace.
5. **What does a missing or unparseable answer score?** Zero for that instance, counted in the
   denominator. Dropping it lets an agent that answered three of fifty problems average its way
   to a good score. And score it as a zero, not with `require`/`fail`: those end the run as a
   *task error*, which says your task is broken rather than that the agent failed.

## The match lock is structural — spell it out

There is no probe to fire here, so the lock is a set of checks on the answer itself. At minimum
your grader rejects an answer that:

* **does not start from the state it claims** (start-echo: the classic no-op answer is the
  problem's own initial state, echoed back and dressed as a solution);
* **teleports** — two consecutive states further apart than the dynamics permit in one step,
  which is how a "path" that was interpolated rather than planned gives itself away;
* **violates the declared constraints** — leaves the arena, sweeps through an obstacle,
  exceeds an actuation or velocity bound.

Reject per answer, inside the metric: that instance is a failure. Reserve
`ratio_rewards(..., locks_ok=False)` — which zeroes the whole run — for a run-level finding, the
case where the number was not produced by solving the task at all (every answer identical, every
answer a start-echo). Pass the outcome along in `extra=` including a `match_lock_ok` key, since
the gate reads that key back and reports it.

Say all of this in `instruction.md` too. The lock exists to make fabrication worthless, not to
ambush a contributor working in good faith, and an honest solver loses nothing by being told the
rules.

## Build steps

1. **`image/Dockerfile`.** Start from the template's: final stage directly FROM the official
   ALE base, py3.12 `/opt/venv` with a world-traversable interpreter, and the agent-UID import
   self-check at the end. Add the simulator or planning library at a pinned commit, with the
   reference implementation's own recipe stripped in the *same* layer that clones it.
2. **`oracle/run.sh`** — the reference implementation, as it is, run in place of the agent by
   `ale validate` as the same unprivileged account and inside `timeouts.agent`. It leaves its
   answers exactly where `instruction.md` tells the agent to leave them; its real measured
   score becomes the anchor.
3. **`verify/verify.py`** — a plain script, no base class, no hooks. Import from
   `robotics_grader`: `seed_rng()` for anything sampled at verify time (OS entropy, in memory
   only), `stage_config()` for knobs, `read_anchor()`, `ratio_rewards(...)`, `write_verdict(...)`,
   and `require(...)` **only** for your own preconditions — the anchor file, the simulator
   import. Read each answer, re-simulate it, apply the lock, aggregate. Leave the vendored
   `verify/robotics_grader/` untouched.
4. **`verify/grader_config.json`** — instance count, tolerances, step bounds, `scoring_cap`
   (`config.json` is overwritten by the engine at verify time). Not
   `params:`: those render into the prompt and are strict both ways, so a declared parameter the
   prompt never mentions is a lint error.
5. **`setup/payload/practice_grader/grade.py`** — `setup/payload/` is the visible-material
   channel; `setup/run.sh` stages it into the agent's home. The practice grader re-simulates on
   *visible* instances and prints the raw metric only: no anchor, no ratio, no score, no import
   from `verify/`. It should apply the same structural checks, so the agent learns the rules
   while working instead of at scoring time.
6. **`verify/anchor.json`** — leave `value` null, and make sure the metric is on a positive scale
   (the package fails the run on a non-positive anchor, because the ratio would order every
   future score backwards). Gate once, take the measured value off the verdict, record it, gate
   again. Never a published figure: other hardware, other seeds, usually a kinder evaluation —
   the anchor is the reference implementation's own measured value.
   Name the metric something that is not one of the framework's bookkeeping keys (`reward`,
   `ratio`, `anchor_recorded`, `match_lock_ok`, `probes_total`, `probes_passed`,
   `episodes_aborted`), or the gate cannot tell it apart from its own.
7. **The gate.**
   ```bash
   uv run python -m harness.ale_onboard --gate-only tasks/<platform>/<task> --json
   uv run python scripts/lint_domain.py
   ```
   `needs_anchor` on the first pass is expected and carries the measurement (the engine
   reports that run as `oracle_not_full`; `harness/ale_onboard.py` reads the records). `built`
   means the double pass held: untouched all-zero and oracle all-one, both the engine's own
   gates (`untouched_nonzero`, `oracle_not_full`). Catch the missing-answers case with
   `zero_verdict(...)` and size the anchor's `full_at` from measured spread.
8. **Prove the lock bites.** Replace the oracle's answers with the cheapest fake your format
   admits — the start state echoed back, or a straight line from start to goal through the
   obstacles — re-gate, and confirm it scores 0. A lock nobody has watched fail is a lock nobody
   has tested.

## Give-up rules

The router's shared aborts apply. The one specific to this pattern: **if the metric cannot be
recomputed without trusting the agent's own numbers, open_loop does not apply.** That happens
when the reference measurement is only defined by the code that produced it, or when the
dynamics are non-deterministic enough that re-simulating an answer diverges from the run that
produced it — then a step-feasibility check has no threshold to use. Move the task to
closed_loop or artifact_rollout, where the grader owns the rollout, or say honestly that it
cannot be scored fairly. Never fabricate an anchor or a green run.
