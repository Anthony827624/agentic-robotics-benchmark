---
name: onboard-task
description: >-
  Interactive ROUTER for turning (a robotics problem + a reference implementation that
  solves it) into a runnable Agentic Robotics Benchmark task (on the ALE engine). Ask the contributor a few questions, choose
  the verify pattern, instantiate the template, then drive the build to a green
  `ale validate`. Owns the shared GIVE-UP rules and the admission gate. Use this when a
  contributor wants to add / onboard a new task.
---

# onboard-task — interactive task-onboarding router

You are guiding a contributor from *(a robotics problem + a reference implementation that
solves it)* to a task the upstream ALE engine accepts. The Agentic Robotics Benchmark runs
its tasks on `AgentsLastExam/ale`: each task drops an autonomous agent into a sandboxed
robotics problem (develop a controller, plan a manipulation sequence, write code-as-policy,
tune a planner, …) and scores the artifact it leaves behind with a hidden grader on hidden
seeds. There is one kind of task; `metadata.platform` (one of `tasks/platforms.json`) is the
taxonomy axis. The folder shape and the
manifest are upstream's, and the pinned engine lives at `vendor/ale`.

Read first: `templates/README.md` (the task contract and what each template file is for —
the source of truth for the layout) and the worked example `tasks/uav/uav_hover` — a real
closed_loop task that validates green.

## What you are producing

```
tasks/<platform>/<task>/
├── task.yaml         the manifest (core/v1) — invisible to the agent
├── instruction.md    the prompt — the only thing the agent is told
├── image/Dockerfile  the environment; sole build context, final stage FROM an official ALE base
├── setup/
│   ├── run.sh        trusted root, before the agent: stages payload/ into the agent home
│   └── payload/      agent-visible material (practice grader, dev copies)
├── oracle/run.sh     the reference implementation; `ale validate` runs it in place of the agent
└── verify/           the grader (verify.py), the anchor, grader_config.json, and the
                      vendored robotics_grader/ package; ABSENT while the agent works
```

Every task owns its image. There is no shared image tree and no repository kit: the
Dockerfile is the whole environment, and the shared verify machinery travels inside
`verify/robotics_grader/` (canonical copy at `shared/robotics_grader/`, byte-equality in CI).

## Interaction contract

Work as a wizard: ask, wait, confirm, proceed. Ask only what you cannot infer from what the
contributor gave you; infer the rest and show it back for confirmation. Never silently
assume, and never interrogate.

### Step 0 — gather

1. **The problem.** What the agent is dropped into, in one sentence: the simulator, the robot,
   the objective, and what artifact the agent leaves behind (a live policy, plans on disk, a
   frozen checkpoint). This becomes the first paragraph of `instruction.md`.
2. **A reference implementation + a PINNED commit** — the code `oracle/run.sh` will run. No
   runnable reference is a give-up condition, because the anchor is the reference
   implementation's own measured value and there is nothing to measure without it.
3. **Headline metric.** It must be an outcome, hardware-invariant, and **on a positive scale**
   — success_rate, reward, SPL, ATE, collision_rate. Not ms / FPS / throughput / frequency:
   those measure the machine. If the natural headline is a time, recast it as a pass/fail
   real-time threshold. If the natural metric is negative (a reward that is minus a distance),
   report the positive cost and set the anchor's `direction` to `lower` — a negative anchor inverts the ratio
   and the kit refuses it.
4. **Large assets** — weights, datasets, maps. Fetchable from a stable public URL with a
   pinned sha256 at build time, or retrainable in the oracle? If neither, give up.

### Step 1 — the image (do this before writing anything)

Start from the template's `image/Dockerfile` and keep its two load-bearing idioms:

* the final stage derives **directly** from an official ALE base
  (`ghcr.io/agentslastexam/container-ubuntu22-base`) — lint enforces the FROM;
* a **py3.12 `/opt/venv`** whose interpreter every account can traverse
  (`UV_PYTHON_INSTALL_DIR=/opt/uv-python`), because the engine stages its `ale_verify`
  package into whatever answers `python3` and refuses anything older — and an
  **agent-UID import self-check** at the end. The single most common failure is a venv the
  agent account cannot traverse, which surfaces at solve time as a misleading
  `ModuleNotFoundError`.

Install the simulator and every stable dependency at pinned versions; strip the reference
implementation's own recipe in the *same* layer that clones it — an image that ships the
exact training script hands the agent the answer. A prebuilt stack can instead declare
`image: {kind: container, ref: ...}` and delete the Dockerfile.

### Step 2 — choose the verify pattern (an authoring choice, not a task.yaml field)

There is one template. The pattern only decides what `verify/verify.py` does:

| Pattern | Use when | verify/verify.py |
|---|---|---|
| **open_loop** | a planner or one-shot solver: the agent produces whole plans offline | read the agent's answers, re-simulate them under your own dynamics, reject any answer that does not start from the state it claims |
| **closed_loop** | a reactive policy: the action depends on the current observation | `Solver` + `ProbeLock` — the grader owns the sim and steps the agent's policy |
| **artifact_rollout** | a frozen checkpoint/config the grader runs itself (batched sim, a whole ROS launch) | load the artifact inside `deprivileged`, roll out on hidden seeds; no wire, so no probe lock |

Load the matching mode skill — `onboard-open-loop`, `onboard-closed-loop`,
`onboard-artifact-rollout` — with `{task, platform, metric, image, task_dir}`.

### Step 3 — instantiate

```bash
uv run python -m harness.ale_onboard <spec>.yaml --no-gate
```

The spec is small: `task`, `platform` (one of `tasks/platforms.json`), `metric: {name,
direction}` (written into `verify/anchor.json`), and an optional prebuilt `image` ref. It fills
those and nothing else — the environment, the
oracle and the grader's physics are yours to write, and inventing them is the one failure
this pipeline exists to prevent.

### Step 4 — the gate

```bash
uv run python -m harness.ale_onboard --gate-only tasks/<platform>/<task> --json
```

That runs `ale lint`, then `ale validate` from the pinned engine. Validate runs the task
TWICE — an untouched sandbox, then the oracle in place of the agent — and the gate reads the
run records and returns one of:

* **`built`** — untouched all-zero and oracle all-one. Done. Both are the engine's own
  gates: `ale validate` fails with `untouched_nonzero` or `oracle_not_full` (exit 2)
  otherwise. Still never read the engine's exit status as the verdict: a null-anchor run
  fails with `oracle_not_full` on purpose, and only the run records tell it apart from a
  broken oracle, which is what `harness/ale_onboard.py` reads.
* **`needs_anchor`** — the anchor is still null; the result carries the **measured value**.
  Write it into `verify/anchor.json` and re-gate. This is the normal second-to-last step, not
  a failure.
  ⚠ **Before you accept that number, check how much your metric moves between runs.** The
  anchor comes from ONE run, and the next run draws fresh hidden seeds — so if the spread is
  wide, an honest oracle misses 1.0 by luck. Run the gate a few times and compare;
  raise `verify/grader_config.json` episodes until the spread is small, and set the anchor's
  `full_at` (the ratio where the reward saturates to 1.0) below the worst realistic ratio.
  (The template measured 1.43x spread at 10 episodes and 1.16x at 60, which is why it ships
  60 episodes and `full_at: 0.8`.)
* **`needs_input`** — lint rejected it, no verdict came back, the untouched pass did not
  score an honest zero (a grader that crashes on a missing submission instead of writing
  `zero_verdict(...)`), or the oracle could not reach 1.0. The detail says which.
* **`gave_up`** — the task cannot be scored fairly. Say why, and what would unblock it.

Also run the domain's own lints, which enforce the policies upstream's generic linter does not
know about: `uv run python scripts/lint_domain.py`.

## GIVE-UP rules (an honest abort beats a broken task)

Stop and say exactly what is missing when any of these hold:

* **A required blob cannot be hosted** — no stable public URL to fetch at build time, and no
  feasible retrain in the oracle. Never commit a 100 MB+ checkpoint, and never bake one into
  the image.
* **No runnable reference implementation**, or it does not run at the pinned commit.
* **A licence forbids** baking a required asset into the image and no synthetic fixture
  substitutes. EULA-encumbered payloads (Isaac Gym, Isaac Sim, CARLA assets) go behind a
  build-time acceptance ARG in the task's own Dockerfile, never in anything published.
* **The metric is inherently hardware-dependent** and cannot be recast.

On abort, say why and what would unblock it ("give me a public URL + sha256 for the
checkpoint", "point me at a fork that runs"). The contribution stays unmerged.

**Never fabricate an anchor, a measurement, or a green run.** `built` comes from a real
verdict or not at all.

## Trust boundary (never violate)

* The anchor and the grader are hidden from the tested agent by **timing**: `verify/` reaches
  the sandbox only when the verify stage runs. Nothing in `setup/payload/` or
  `instruction.md` may restate the anchor, and the practice grader reports the raw metric
  only — never a ratio, a score, or the reference value.
* Do not print the anchor number into a contributor-facing summary.
* The repository is public and a task folder travels whole, `verify/` and `oracle/`
  included: the anchor is hidden from the tested agent by stage timing, not from people.
  What must never happen is the anchor reaching `instruction.md` or `setup/payload/`.
