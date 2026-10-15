---
name: onboard-artifact-rollout
description: >-
  Build an ARTIFACT_ROLLOUT task for the Agentic Robotics Benchmark (on the ALE engine): the agent leaves a FROZEN artifact — a
  checkpoint, a config bundle, a ROS package — and the verify stage loads it inside
  `robotics_grader.deprivileged` and rolls it out itself on hidden seeds. For batched/vectorized
  sims and whole-launch stacks that cannot answer a per-step wire. Loaded by onboard-task with
  its spec payload. Drives image/Dockerfile → oracle (train, then freeze) → verify/verify.py on
  the vendored robotics_grader package → practice grader → the anchor from a REAL run → a green
  `ale validate`.
---

# onboard-artifact-rollout — build an artifact_rollout task

Loaded by **onboard-task** with `{task, platform, metric, image, task_dir}`. Ask
only what this pattern needs, then drive the build.

The shape: the agent's work product is a **frozen artifact** under a path the task declared in
`artifacts:`. The grader loads it and runs every episode itself, in-process and at full speed.
Choose this pattern when a per-step wire is the wrong shape rather than merely inconvenient: a
vectorized simulator that steps a thousand environments at once would spend all its time on JSON
round-trips, and a whole ROS launch owns its own clock and node graph and cannot be driven one
observation at a time from outside.

There is **no wire, so no probe lock is needed** — the lock is structural. A closed_loop solver
can lie about reacting to its input because it answers questions one at a time; an artifact
answers nothing. It is a static blob, and the grader's rollout is the only thing that ever runs
it. There is nothing to inject into.

Template: `templates/task/` — its `verify/verify.py` is the closed_loop pattern, and
its module docstring says exactly what to delete for this one. Shared machinery: the vendored
`verify/robotics_grader/` package (canonical copy at `shared/robotics_grader/`, byte-equality
enforced in CI). Audited trust boundary: `docs/evidence/isolation-audit.md`.

## The one hard rule: load it deprivileged

**Load the artifact inside `robotics_grader.deprivileged`, in a subprocess at the agent's UID.**
`verify/run.sh` runs as root. `torch.load`, `pickle`, `yaml.load`, a ROS launch file and a
plugin path all execute arbitrary code chosen by the agent — so deserializing an
agent-produced artifact as root hands the grader over completely: the anchor, the seeds in
memory, `$ALE_VERDICT_PATH`, everything. `deprivileged(argv, **kwargs)` is a `subprocess.Popen`
through `runuser -u <agent user> --`, and Yama's default `ptrace_scope` keeps the boundary
standing even though both processes share one sandbox.

That constrains the design: the untrusted deserialization and whatever it produces live on the
far side of a process boundary, and what crosses back is data your grader parses defensively.
Two arrangements both work — run the whole rollout in the deprivileged child and return
per-episode records on its stdout, or load in the child and pass tensors/actions back over a
pipe while root keeps the simulator. Pick one deliberately, and if root keeps the simulator,
remember that everything crossing the pipe is untrusted input.

The corollary, which upstream's own layout enforces: `verify/` is absent from the sandbox during
the agent phase, so nothing under it — the grader, the anchor, `grader_config.json` — was ever readable.
That timing *is* the hiding mechanism; there is no assertion to write.

## Mode-specific questions

1. **What exactly is the artifact, and at what literal path?** A `.pt`/`.onnx` checkpoint, a
   parameter YAML, a costmap plus a planner config, a colcon-built ROS package. It must be
   **load-and-run**: anything that trains at load time is a contract violation, because a scoring
   run that trains is no longer measuring the artifact it was handed.
2. **How does the grader load it?** The exact call, the exact expected shape, and what happens
   when it is missing, truncated, or the wrong architecture — that is a zero for the run, not a
   task error.
3. **What is one episode?** Reset semantics, the step budget, termination, and what counts as
   success. If the sim is batched, say how many environments run at once and how a batch maps
   onto episodes.
4. **What is a hidden "world"?** A terrain seed, a map, an initial state, a scene. `seed_rng()`
   samples these fresh from OS entropy inside the grader process; the values never touch disk and
   never come from anything the agent could have observed.
5. **How long does the oracle need to train?** Training happens in `oracle/run.sh` **during the
   agent phase**, inside `timeouts.agent`, and `ale validate` really pays it. `timeouts.verify`
   covers only load plus rollout — the separation is what makes a scoring run cheap and
   comparable, and it is the same budget a real agent gets.

## Build steps

1. **`image/Dockerfile`.** Start from the template's: final stage directly FROM the official
   ALE base, py3.12 `/opt/venv` with a world-traversable interpreter, and the agent-UID import
   self-check at the end (a venv whose interpreter the agent account cannot traverse is the
   most common image failure). Add the simulator or the ROS stack at a pinned commit, with the
   reference implementation's own recipe stripped in the *same* layer that clones it. Bake in
   every asset the rollout needs — maps, meshes, terrains — because the agent phase runs
   sealed. A heavy ROS/Gazebo/Isaac stack is usually the reason a task is artifact_rollout in
   the first place.
2. **`oracle/run.sh`** — the reference implementation, as it is. Train here, then write the
   frozen artifact to the same literal path `instruction.md` gives the agent. It runs as the
   same unprivileged account the agent does, so an artifact your oracle can produce is one an
   agent can produce. Its real measured score becomes the anchor.
3. **`verify/verify.py`** — a plain script, no base class, no hooks. Delete the `Solver` and the
   `ProbeLock`. Import from `robotics_grader`: `deprivileged` for the load, `seed_rng()` for the
   hidden worlds, `stage_config()` for knobs, `read_anchor()`, `ratio_rewards(...)`,
   `write_verdict(...)`, and `require(...)`/`fail(...)` **only** for your own preconditions — the
   anchor file, the simulator import. A missing or unloadable artifact is a legitimate zero and
   must not go through `fail`, which ends the run as a task error and would make a broken
   submission read as a broken task. Leave the vendored `verify/robotics_grader/` untouched.
4. **`verify/grader_config.json`** — episode/world count, batch size, the load and rollout
   deadlines (`config.json` is overwritten by the engine at verify time),
   `scoring_cap`. Not `params:`: those render into the prompt and are strict both ways, so a
   declared parameter the prompt never mentions is a lint error.
5. **`setup/payload/practice_grader/grade.py`** — `setup/payload/` is the visible-material
   channel; `setup/run.sh` stages it into the agent's home. It loads and rolls out the artifact
   on *visible* worlds and prints the raw metric only: no anchor, no ratio, no score, no import
   from `verify/`. It is also where the agent finds out its checkpoint format actually loads,
   which is worth more here than in any other pattern.
6. **`verify/anchor.json`** — leave `value` null, and make sure the metric is on a positive scale
   (the package fails the run on a non-positive anchor, because the ratio would order every
   future score backwards; a negative reward sum becomes a positive cost with the anchor's
   `direction` set to `lower`). Gate once, take the measured value off the verdict, record it, gate again. Never a
   published figure — the anchor is the reference implementation's own measured value. Name
   the metric something that is not one of the framework's bookkeeping keys (`reward`,
   `ratio`, `anchor_recorded`, `match_lock_ok`, `probes_total`, `probes_passed`,
   `episodes_aborted`), or the gate cannot tell it apart from its own.
7. **The gate.**
   ```bash
   uv run python -m harness.ale_onboard --gate-only tasks/<platform>/<task> --json
   uv run python scripts/lint_domain.py
   ```
   `needs_anchor` on the first pass is expected and carries the measurement (the engine
   reports that run as `oracle_not_full`; `harness/ale_onboard.py` reads the records). `built`
   means the double pass held: untouched all-zero (a missing artifact must earn
   `zero_verdict(...)`, not a crash) and oracle all-one, both the engine's own gates
   (`untouched_nonzero`, `oracle_not_full`; size the anchor's `full_at` from measured spread).
   If validation dies on its deadline and your oracle trains, raise `timeouts.agent` and
   `--validate-timeout` together.
8. **Prove a bad artifact scores 0.** Re-gate with the artifact deleted, then with it truncated
   to garbage. Both must come back as a zero-reward verdict, not as `needs_input` from a crashed
   verify stage — the difference between "the agent failed" and "the task is broken" is the one
   distinction a grader must never blur.

## Give-up rules

The router's shared aborts apply, and here the **unhostable checkpoint is by far the most common
one**: this pattern's whole premise is a large frozen blob, and a 100 MB+ checkpoint with no
stable public URL and pinned sha256, and no feasible retrain inside `oracle/run.sh`, is a stop.
Never commit such a blob to git and never bake one into the image — fetching it at build time
from a stable URL with a pinned sha256 is the only acceptable route. Say what would unblock you
("give me a public URL plus sha256", "confirm the training fits in N GPU-hours") and leave the
contribution unmerged. Never fabricate an anchor or a green run.
