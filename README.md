# Agentic Robotics Benchmark

**A benchmark of agentic robotics tasks for autonomous agents.** Each task drops an autonomous
agent into a sandboxed robotics problem (develop a controller, plan a manipulation sequence,
design a part or a mechanism, write code-as-policy, tune a planner, …) and scores **whatever
artifact it leaves** in `/home/user/submission/` — a controller process, a plan or planner, a
URDF / mesh / parameter design, generated code, a report — with a **hidden grader on hidden
seeds**. `verify/` scores the artifact however the task author decides; `oracle/` is the
reference artifact that scores full. There is one kind of task; `metadata.platform`
(one of the fixed keys in [`tasks/platforms.json`](tasks/platforms.json)) is the taxonomy axis.

The tasks run on the upstream **ALE engine**
([AgentsLastExam/ale](https://github.com/AgentsLastExam/ale)), pinned as the submodule at
`vendor/ale`; nothing here reimplements it. Upstream's
[task-authoring.md](https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-authoring.md)
and
[task-quality-standard.md](https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-quality-standard.md)
are the authority on the task format and the quality bar; this repository documents only what
the robotics domain adds. The website, <https://agentic-robotics-benchmark.org>, displays
the tasks; it takes no uploads and no submissions.

This repository is public and complete: every task folder is here whole, `oracle/` and
`verify/` included. The anchor is hidden from the *tested agent* by the engine's stage
isolation while it works, not from people.

*[中文说明](README.zh-CN.md)*

## One task = one folder

A task is a self-contained `core/v1` folder that the engine runs in three stages
(`setup` → `agent` → `verify`); `oracle/` stands in for the agent under `ale validate`.
What the robotics domain adds (the three verify patterns, scoring, the vendored kit, the
probe wire) is in [`templates/README.md`](templates/README.md), next to the file-by-file
tour of the template.

```
tasks/<platform>/<slug>/
├── task.yaml         the manifest (strict core/v1); invisible to the agent
├── instruction.md    the prompt — the only thing the tested agent is told
├── image/Dockerfile  the environment; final stage FROM an official ALE base
├── setup/            trusted root, before the agent: stages setup/payload/ into the agent home
├── oracle/run.sh     produces the reference artifact; `ale validate` runs it in place of the agent
└── verify/           the grader (verify.py), anchor.json, grader_config.json, and the
                      vendored robotics_grader/ package; ABSENT while the agent works
```

What keeps the grader and the anchor away from the agent is **timing, not a flag**: a
stage's folder reaches the sandbox only when that stage runs, `/opt/ale` is root-only, and
the agent's solver runs at the agent's UID. Nothing the agent prints is ever a scoring
input; the grader computes the metric from its own simulator state.

## Scoring

```
ratio  = clamp(measured / anchor, 0, cap)     # inverted for lower-is-better metrics; cap = 1.5
reward = clamp(ratio / full_at, 0, 1)         # the single gated reward key
```

The **anchor** is the reference implementation's own measured value from a real run —
never a printed figure from elsewhere — and `full_at` is the ratio at which the reward
saturates, sized from the metric's measured seed-to-seed spread. `reward` (0 to 1) is the
task's score and the engine's gated signal; the capped `ratio` rides along in the verdict. Metrics are always outcome
metrics on a positive scale, never wall-clock time or throughput.

`ale validate` runs every task twice. An untouched sandbox must produce an exact all-zero
verdict, and the oracle must then score exactly 1.0 on every reward key. Either miss fails
validation (`untouched_nonzero`, `oracle_not_full`; exit 2). A null anchor fails the same
way by design; `harness/ale_onboard.py` reads the run records and reports it as
`needs_anchor` with the measured value.

## Contributing a task

A contribution is a pull request into this repository that adds one task folder at
`tasks/<platform>/<name>/`, where `<name>` is the manifest's `name` (the task's registered
name) and `<platform>` its `metadata.platform`. The project's contribution guidelines cover
sign-up, roles, the task stages and credit:
[English](https://docs.google.com/document/d/1s1E3ymIBLujWf2l7rhu4g9Nl4orBfooK0aDWOdum7-Y/view) and [中文](https://docs.google.com/document/d/1gX8v-w-idIPLjzoWgRskT0llE7cKXqg9sVLB0cWfZk0/view).

Build the task from `templates/task/`, gate it with `ale lint` and `ale validate`, measure its
difficulty with frontier models (`ale run`; the target is a score roughly below 0.5), then
open the pull request. CI runs `ale lint`, the domain lint and the kit comparison on it; a
maintainer re-runs `ale validate`, merges, and exports the registry. The short version is
[`CONTRIBUTING.md`](CONTRIBUTING.md); the whole path, from the template to a merged task,
is [`docs/contributing-a-task.md`](docs/contributing-a-task.md).

## Layout

```
vendor/ale/                     upstream engine (submodule, pinned commit) — do not reimplement it
tasks/<platform>/<slug>/        the tasks (today: space/fault_adaptive_spacecraft_docking)
tasks/platforms.json            the platform vocabulary: the `metadata.platform` keys and their labels
tasks/README.md                 a note: which task is the worked example
templates/task/                 ONE template, gates green as shipped (null anchor by design)
templates/README.md             the task contract, file by file (source of truth for the task form)
identity-history.json           past (spec_hash, content_digest) pairs per task (registry v2)
shared/robotics_grader/         canonical verify-stage kit; each task vendors a byte-identical copy
harness/
  ale_onboard.py                `ale-onboard`: instantiate the template + run the real gates
scripts/
  export_registry.py            registry v2 from the engine's own loader (task identity)
  lint_domain.py                domain lint (what `ale lint` cannot know), placement included
schemas/task-registry.schema.json   the registry's contract (owned here; the export is tested against it)
skills/onboard-*/SKILL.md       agent-facing onboarding skills (router + one per verify pattern)
tests/                          pytest suite
docs/                           architecture, security model, contributing,
                                evidence/ (real verdicts, isolation audit), dev/ (design records)
.github/workflows/pr-checks.yml the checks every pull request gets
```

## Commands

```bash
uv sync                                                    # host-side tooling (py3.11)

# the engine's own gates, from the pinned submodule (clear VIRTUAL_ENV, pin the docker socket)
cd vendor/ale && VIRTUAL_ENV= uv run ale lint ../../tasks && VIRTUAL_ENV= uv run ale lint ../../templates
cd vendor/ale && VIRTUAL_ENV= DOCKER_HOST=unix:///var/run/docker.sock \
    uv run ale validate ../../tasks/<platform>/<slug> --runs-dir /tmp/ale-runs

# authoring gate: lint -> validate (untouched-zero / oracle-one) -> honest outcome
uv run ale-onboard <spec>.yaml --json                                      # instantiate + gate
uv run ale-onboard --gate-only tasks/<platform>/<slug> --json
#   built | needs_anchor (measured value reported) | needs_input | gave_up | error

# domain lint (platform and placement, metric discipline, anchor provenance, visible surface, image hygiene)
# + the shared kit's self-test (no docker)
uv run python scripts/lint_domain.py .
cd shared && python3 -m robotics_grader.selftest

# run a task with a real agent (the difficulty test; the verdicts are in <runs>)
cd vendor/ale && VIRTUAL_ENV= DOCKER_HOST=unix:///var/run/docker.sock \
    uv run ale run ../../tasks/<platform>/<slug> --agent claude-code --model <model> --runs-dir <runs>

# the task registry (task identity from the engine loader; refuses a dirty tree; contract in
# schemas/task-registry.schema.json). platforms come from tasks/platforms.json; the website
# installs this export with its own sync script and only displays it
cd vendor/ale && VIRTUAL_ENV= uv run python ../../scripts/export_registry.py \
    --tasks ../../tasks --history ../../identity-history.json --out registry.json

uv run pytest -q
```

The template and the worked example each validate in well under a minute.

## Access

Running a task needs the **ALE engine** and the **ALE base image**. The engine repository
([AgentsLastExam/ale](https://github.com/AgentsLastExam/ale)) is public; it is not on PyPI
and has no license file yet, so reference it, never vendor its code into this
repository. Install per its README (`git clone`, `just bootstrap`, `uv run ale --help`;
needs `uv`, `just`, Docker). The base image `ghcr.io/agentslastexam/container-ubuntu22-base`
refuses anonymous pulls; `images/build.sh container-ubuntu22` in the engine checkout builds it
locally in a few minutes. A contributor runs `ale lint` and `ale validate` themselves before
opening a pull request; a maintainer re-runs them before merging.

## Docs

- [`docs/architecture.md`](docs/architecture.md) — engine stages, stage isolation, the UID
  boundary, the verdict envelope, and the registry export.
- [`docs/security-model.md`](docs/security-model.md) — what stage isolation hides from the
  tested agent, and what a verdict can and cannot prove.
- [`docs/contributing-a-task.md`](docs/contributing-a-task.md) — the contributor's path from
  the template to a merged task.
- [`docs/dev/`](docs/dev/) — dated design records; their 2026-10-15 notes say what no longer
  holds (the website upload channel and the leaderboard bridge are retired).

## History

This repository was created on 2026-09-07 with a fresh history when the project was renamed
from "ALE Robotics" to the Agentic Robotics Benchmark. It replaces two archived repositories:
`ale-robotics-benchmark-private` (the maintainers' task repository; the pre-ALE task form and
its harness are at its tag `legacy-harness-final`) and `ale-robotics-benchmark` (the public
mirror, tag `legacy-v0.5-final`). Nothing here revives either.

## Conventions

Everything written into this repository — code, comments, docs, task files — is **English**;
it is read by tested agents and benchmark users worldwide. Where a document and the code
disagree, the code wins: say so and fix the document.

## License

Apache-2.0 (see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE)). The upstream engine under
`vendor/ale` is a separate project with its own terms.
