# Architecture — how a task runs and what the repository publishes

> Audience: maintainers and contributors. The task contract is
> [`templates/README.md`](../templates/README.md); the trust boundary is
> [`security-model.md`](security-model.md); the design record is
> [`dev/20260902-engine-run-submission.md`](dev/20260902-engine-run-submission.md) (dated: its
> leaderboard bridge is retired).
> The engine's own normative specs live in `vendor/ale/docs/specs/` (`task-authoring.md`,
> `standard-environment.md`, `verification.md`). Where this page and the engine disagree,
> the engine wins.

## 1. What the benchmark measures

The Agentic Robotics Benchmark is an agentic-robotics benchmark built on the upstream ALE
engine. Each task drops an autonomous agent into a sandboxed robotics problem — develop a
controller, plan a manipulation sequence, write code-as-policy, tune a planner — and scores
the artifact it leaves behind
with a hidden grader on hidden seeds. The agent's deliverable is whatever the task's
`instruction.md` asks for (a solver behind `run.sh`, answer files, a frozen checkpoint)
under a declared artifact path, normally `/home/user/submission/`.

Nothing here is a harness of our own. The **upstream ALE engine** (submodule `vendor/ale`)
builds the sandbox, runs the agent, captures the artifact, and runs the task's grader; this
repository owns the **tasks**, the **authoring gate** and the **task registry** it
publishes.

## 2. The task folder

```
tasks/<platform>/<slug>/
├── task.yaml         strict core/v1 manifest — spec_type, stable `name`, image, resources,
│                     timeouts, artifacts, metadata{platform}
├── instruction.md    the prompt — the only thing the tested agent is told
├── image/Dockerfile  the environment; sole build context, final stage FROM an official ALE base
├── setup/run.sh      trusted root, before the agent; stages setup/payload/ into the agent home
├── oracle/run.sh     the reference implementation; `ale validate` runs it in place of the agent
└── verify/           run.sh -> verify.py (the grader), env.py (authoritative environment),
                      anchor.json, grader_config.json, robotics_grader/ (vendored kit)
```

Identity is the manifest's `name` slug. Everything the grader needs — including the anchor —
travels **inside the folder**. The three verify patterns (open_loop, closed_loop,
artifact_rollout) are authoring choices inside `verify/verify.py`, not manifest fields.

## 3. Engine stages and stage isolation

One episode, as the engine runs it (`vendor/ale/docs/specs/standard-environment.md`):

```
provision   build image/ (or pull image.ref); start ONE sandbox; /opt/ale is drwx------ root:root
   │
setup       root, egress open, cwd = /opt/ale/setup: stages setup/payload/ into /home/user
   │        (practice grader, dev copies). verify/ and oracle/ are NOT in the sandbox.
   │
agent       the harness (claude-code, codex-cli, …) works as the image's agent account
   │        under network.mode block, within timeouts.agent. It sees instruction.md,
   │        the image, /home/user. It leaves the declared artifact.
   │        [ale validate: oracle/run.sh runs here instead, as the SAME unprivileged user;
   │         oracle/ is uploaded only for that episode]
   │
evidence    trajectory.json (agent transcript, ATIF), artifacts/snapshot.json (content
   │        identity of the artifact path), trace.execution.jsonl
   │
verify      verify/ is uploaded NOW to /opt/ale/verify; ale_verify is staged into the
   │        image's python3 (>= 3.12); run.sh -> verify.py runs as root, cwd = verify/.
   │        The grader owns the simulator, spawns the agent's solver AT THE AGENT'S UID
   │        (robotics_grader.Solver / deprivileged via runuser), draws seeds from OS
   │        entropy in memory, computes the metric from its own state, writes the verdict.
   │
teardown    sandbox destroyed by default (retention is a run policy)
```

**Stage isolation** is the whole hiding mechanism: a stage folder reaches the sandbox only
when its stage runs, so `verify/` (grader, anchor, config) and `oracle/` are simply absent
while the agent works. Two more barriers hold even for what does exist: `/opt/ale` is
root-only, and the solver runs at the agent's UID (Yama `ptrace_scope` blocks tracing across
UIDs), so the agent's code cannot read the grader's memory or environment and cannot write
the verdict path (`ALE_VERDICT_PATH`, inside the root-only tree). Real-run proof:
[`evidence/isolation-audit.md`](evidence/isolation-audit.md).

The engine's untouched/oracle **double pass** (`ale validate`) runs this protocol twice with
fresh sandboxes: untouched must complete with a non-empty all-zero reward map, and every
oracle reward must equal one. Either miss fails validation (`untouched_nonzero`,
`oracle_not_full`; exit 2). `harness/ale_onboard.py` reads the same records back and tells
a null anchor (`needs_anchor`, the measured value reported) apart from a broken oracle.

## 4. The verdict envelope and the episode record

`robotics_grader.write_verdict({"rewards": {...}, "metrics": {...}})` records through the
engine's `ale_verify` (`Verification.check` per reward key, `stat` per metric, `write()`),
which atomically writes the reward map and `verification.json`; the engine cross-checks the
two and rejects a mismatch.

- `rewards` — only the gated key, `reward ∈ [0, 1]`. Under validate every key here must be
  exactly 0 (untouched) and exactly 1 (oracle).
- `metrics` — everything worth keeping but never gated: the raw metric (e.g.
  `mean_docking_quality`), the capped `ratio` (reported alongside the reward), `match_lock_ok`,
  `anchor_recorded`, probe and abort counts.

The engine persists one episode directory `runs/<run_id>/<task.name>-<hex8>/`:

| file | what it carries |
|---|---|
| `lock.json` | `task.{name,variant,spec_hash,content_digest}`, `framework.{commit,version}`, `agent.{harness,model,version,family}`, `image.*`, `resources.effective`, `ale_verify.content_hash` |
| `result.json` | `episode_id`, `status` (`completed`, `agent_error`, `timeout`, …), `rewards{}` (only when completed), `metrics{}`, `phases[]`, `failure{}` |
| `verification.json` | the `ale_verify` record: criteria, stats, aggregates, diagnostics |
| `trajectory.json`, `trace.execution.jsonl`, `artifacts/snapshot.json` | agent transcript, engine trace, artifact identity |

Run level: `ledger.db` (the only enumeration that also lists interrupted episodes) and, for
`ale validate`, `validation.json`.

**Task identity** is the engine's: `spec_hash` hashes the rendered spec (manifest fields +
`instruction.md` + metadata + engine defaults); `content_digest` is a tree digest of every
regular file in the folder (`oracle/` included, exec bits included). Editing one character
of `instruction.md` changes `spec_hash`; a stray `__pycache__` changes `content_digest`. We
never re-implement either — `scripts/export_registry.py` reads both from the engine loader
and keeps past pairs in `identity-history.json` at the repository root.

## 5. Scoring

```
ratio  = clamp(measured / anchor, 0, cap)     # anchor / measured for direction: lower; cap = 1.5
reward = clamp(ratio / full_at, 0, 1)         # the gated key
```

- `anchor` (`verify/anchor.json` `value`) is the reference implementation's own measured
  value from a real run — never a printed figure from elsewhere. It starts null: the first
  run measures, reports the value in `metrics`, and scores 0 (`needs_anchor`).
- `full_at` is where the reward saturates, in ratio units — the worst ratio a genuinely
  on-reference run lands on under fresh hidden seeds, sized from a measured spread.
- `cap` (`verify/grader_config.json` `scoring_cap`, default 1.5) bounds how much beating the
  reference can dominate an aggregate.
- Metrics are outcome metrics on a positive scale, hardware-invariant by policy
  (`scripts/lint_domain.py`); a non-positive anchor is refused by the kit because it would
  invert the ordering.

The canonical kit is `shared/robotics_grader/` (`Solver`, `ProbeLock`, `seed_rng`,
`ratio_rewards`, `zero_verdict`, `write_verdict`, `deprivileged`); every task vendors a
byte-identical copy under `verify/robotics_grader/` because the engine stages exactly the
`verify/` folder and nothing else.

## 6. What the repository publishes: the task registry

```
 tasks/<platform>/<slug>/                      (this repo, committed, clean tree)
     │
     ├─ ale lint / ale validate  ──►  harness/ale_onboard.py  ──►  built | needs_anchor | …
     │
     ├─ ale run <task> --agent <harness> --model <model>  ──►  runs/<run_id>/<episode>/…
     │        (the difficulty test and the formal benchmarking runs; the engine's own records)
     │
     └─ scripts/export_registry.py  (engine loader: spec_hash, content_digest;
              verify/anchor.json: metric, direction, anchor, full_at; grader_config: cap;
              tasks/platforms.json: platforms; identity-history)
              ──►  registry.json  (ale-robotics-task-registry/v2,
                                   contract: schemas/task-registry.schema.json)
              ──►  the website installs it and displays the tasks
```

The repository is public: every task folder is here whole, `oracle/` and `verify/` included,
and there is no separate mirror. The website is a display-only front end (owner decision
2026-10-15): it shows the tasks from the registry and links the contribution guidelines. It
takes no uploads and no run or score submissions, has no leaderboard, and nothing here reads
from it. A result is the engine's own episode record (`lock.json`, `result.json`,
`verification.json`) for a task identity the registry names.

## 7. Repository components

```
vendor/ale/                  the engine (submodule, pinned) — never reimplemented here
tasks/  templates/task/      the tasks and the one template (flat layout); identity-history.json at the root
tasks/platforms.json         the platform vocabulary (metadata.platform keys and labels)
shared/robotics_grader/      canonical verify-stage kit (stdlib-only)
harness/ale_onboard.py       ale-onboard: instantiate + gate (lint -> validate -> outcome)
scripts/export_registry.py   registry v2 (engine loader identity; dirty-tree refusal)
scripts/lint_domain.py       domain lint (what `ale lint` cannot know)
schemas/                     the registry's JSON Schema (owned here; the export is tested against it)
skills/onboard-*/            agent-facing onboarding skills
tests/                       pytest
```

## 8. Operational notes

- Host-side tooling runs on uv (`pyproject.toml`, py3.11). The engine has its own venv under
  `vendor/ale`; run engine commands from there with `VIRTUAL_ENV=` cleared and
  `DOCKER_HOST=unix:///var/run/docker.sock` pinned.
- Task images: py3.12 `/opt/venv` with a world-traversable interpreter
  (`UV_PYTHON_INSTALL_DIR=/opt/uv-python`) and an agent-UID import self-check at the end of
  the Dockerfile. The engine refuses `python3 < 3.12` at verify time.
- `network.mode: block` during the agent phase means any lazy first-use download must be
  baked into the image.
- `ale validate` does not keep a stage's stderr; write what the grader says into a collected
  artifact if you need to read it afterwards.
- The template and the worked example each validate in well under a minute; the template is
  the place to test machinery.
