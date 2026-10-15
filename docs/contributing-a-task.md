# Contributing a task

The path: get the engine, start from the template, build the task locally, validate it,
measure its difficulty with a frontier model, then open a pull request that adds your folder
at `tasks/<platform>/<name>/`. Sign-up, roles, the task stages (expert validation, formal
benchmarking) and how contributions are credited are in the project's contribution guidelines
([English](https://docs.google.com/document/d/1s1E3ymIBLujWf2l7rhu4g9Nl4orBfooK0aDWOdum7-Y/view),
[中文](https://docs.google.com/document/d/1gX8v-w-idIPLjzoWgRskT0llE7cKXqg9sVLB0cWfZk0/view));
the short version of this page is the repository's [`CONTRIBUTING.md`](../CONTRIBUTING.md).

What a task is and the bar it has to clear are the engine's own specs. Read them once:

- Task authoring: https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-authoring.md
- Task quality standard: https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-quality-standard.md

What the robotics domain adds, the file-by-file tour of the template and the "Turning it
into your task" checklist are in [`templates/README.md`](../templates/README.md). Where this
page disagrees with it, it wins.

A robotics task here is an agentic problem an autonomous agent solves inside a sealed
sandbox. It is handed an `instruction.md` and an environment image, works however it likes
(train a controller, write a planner, design a part, generate code, tune parameters), and
leaves any artifact in `/home/user/submission/`. A hidden grader scores that artifact on
fresh hidden seeds. Your job is to author the whole package so that it validates: an
untouched sandbox scores an exact zero and the reference artifact scores an exact one.

## 1. Prerequisites

You need Docker and the ALE engine at the commit this repository pins, the `vendor/ale`
submodule; upstream `main` moves on, and a task is gated on the pinned commit. `uv` and
`just` (`uv tool install rust-just`) drive it. Clone this repository with its submodule (or
run `git submodule update --init vendor/ale` in an existing clone) and run every `ale` command
from `vendor/ale`:

```bash
git clone --recurse-submodules https://github.com/Grounded-Intelligence-Initiative/agentic-robotics-benchmark.git
cd agentic-robotics-benchmark/vendor/ale
just bootstrap
uv run ale --help
images/build.sh container-ubuntu22     # the sandbox base image; build it once, it takes a while
```

The base image on ghcr.io refuses anonymous pulls, so build it locally from the checkout. If
your reference method trains, plan for a single GPU of 24 GB or less; training happens only
in the oracle phase, never during scoring.

## 2. Get the template

Copy [`templates/task/`](../templates/task/) from your checkout to wherever you work. Its
name in the project's sheet, the folder name and `name` in `task.yaml` are one string that
stays the task's identity for good: preferably a short `snake_case` name (lowercase letters,
digits, underscores; the lint accepts `^[a-z0-9][a-z0-9_-]*$`).

Rename the folder to that name first and set `name` in `task.yaml` to the same string. If
your copy does not keep Unix permissions (Windows, some GUI tools), run
`chmod +x setup/run.sh oracle/run.sh verify/run.sh`; `ale lint` requires the three stage
entry points to be executable.

The engine admits only `task.yaml`, `instruction.md`, `image/`, `setup/`, `verify/`,
`oracle/`, `tools/` and `.ale-cache` at a task's top level. Any stray file inside the folder
fails lint, so keep notes and scratch files outside it.

The template validates as shipped, except that its anchor is null (step 6). Start from that
state and change one thing at a time.

## 3. The build loop

Work through "Turning it into your task" in [`templates/README.md`](../templates/README.md).
The short form:

1. Name: the folder and `task.yaml`'s `name` are the same slug (`^[a-z0-9][a-z0-9_-]*$`).
2. Image: `image/Dockerfile` is your environment, final stage `FROM` an official ALE base;
   Python packages go in `image/requirements.txt`. Keep the py3.12 `/opt/venv` and the
   agent-UID import self-check. A prebuilt stack can declare `image.ref` instead.
3. Environment: `verify/env.py` is the authoritative copy the grader imports;
   `setup/payload/template_env.py` is the dev copy the agent gets.
4. Oracle: `oracle/run.sh` leaves the reference artifact in `/home/user/submission/`, by
   running the real method or by copying in a hand-made file.
5. Grader: `verify/verify.py` scores your artifact. Do not touch `verify/robotics_grader/`;
   CI compares it byte for byte with the canonical `shared/robotics_grader/` and fails the
   pull request on a difference. An absent or unusable submission is `zero_verdict`, never a
   crash.
6. Instruction: the deliverable and the grading protocol. For a controller on the wire, keep
   the paragraph about the step index `t`: a step may be queried more than once with the same
   `t`, answer each request from the observation it carries, and repeated queries do not
   advance the episode. Nothing on the wire marks a probe.
7. Anchor: section 6 below.

After each change, from the engine checkout (`<task_dir>` is your folder, `<runs>` a scratch
directory):

```bash
uv run ale lint <task_dir>
uv run ale validate <task_dir> --runs-dir <runs>
```

`ale validate` runs the task twice. Untouched: nobody touches the sandbox, and `verify/`
must write a real verdict of exactly 0 on every reward key (`untouched_nonzero` otherwise).
A crash fails it too, which is why the grader treats an absent artifact as `zero_verdict(...)`.
Oracle: `oracle/run.sh` runs in place of the agent, and `verify/` must score exactly 1 on
every reward key (`oracle_not_full` otherwise).

Nothing you print to stdout is a scoring input. The grader computes the metric under its own
physics and writes the verdict through a channel your solution cannot pre-create or overwrite.

From the repository root, `uv run python scripts/lint_domain.py <task_dir>` runs the domain
lint the pull request CI runs (section 5, plus placement once the folder sits under
`tasks/`).

## 4. What the agent sees, and what it must not

| Agent sees (during its phase) | Hidden (staged only when its stage runs) |
|---|---|
| `instruction.md`, the built image, `setup/payload/` (dev env, practice grader) | `verify/` (grader, `env.py`, `anchor.json`), `oracle/`, and `task.yaml` itself |

There is no "hide this" flag. A stage's folder is absent from the sandbox until that stage
runs, and `/opt/ale` is root-only while the solver runs unprivileged. The one thing you
police yourself: the practice grader in `setup/payload/` prints the raw metric only, never a
score, a ratio or the anchor, and it never imports `verify/`.

The repository is public, so a person can read every anchor. That is fine: the benchmark
measures what the agent does inside a blocked-network sandbox that does not contain
`verify/`, and the agent is what is being measured.

## 5. Metric discipline

Two mistakes silently corrupt a task. Neither `ale lint` nor `ale validate` checks them; the
domain lint does, on every pull request. The metric is declared in `verify/anchor.json` and
nowhere else: `metric` is the key your grader reports, `direction` is `higher` or `lower`.

- The metric is hardware-invariant. Never score wall-clock time, throughput or FPS; they
  depend on the machine. Use outcome metrics (success rate, collision rate, SPL, ATE, episode
  reward). If the natural headline of your problem is a time, convert it to a pass/fail
  real-time threshold.
- The metric is on a positive scale. A negative anchor inverts the ratio, so a better result
  scores lower. Report a negative reward as a positive cost with the anchor's `direction` set
  to `lower`.

## 6. The anchor bootstrap

The scoring denominator is the reference implementation's own measured value, never a figure
printed elsewhere. You cannot know it until you run, so:

1. Leave `verify/anchor.json`'s `value` as `null` and run `ale validate`. It prints `FAIL`
   and the run's `validation.json` names the reason, `oracle_not_full`; that is expected.
   The oracle record carries the measured value:

   ```bash
   python3 -c 'import glob,json; [print(p, json.load(open(p))["metrics"]) for p in
     sorted(glob.glob("<runs>/validate-*/<slug>-oracle-*/result.json"))]'
   ```

   The metric is the key that is not bookkeeping (`ratio`, `match_lock_ok`,
   `anchor_recorded`, `probes_*`, `episodes_aborted` are). The grader's own stderr is not
   printed by `ale validate`; it is in the same folder's `trace.execution.jsonl`, in the
   verify-phase `command_finished` event.
2. Write the value into `anchor.json` as `value`, and say where it came from in `source`.
3. Run the oracle several more times, look at the spread of `ratio`, and set `full_at` (the
   ratio at which the reward saturates to 1.0) to the worst ratio an honest on-reference run
   lands on. Too high and an honest oracle fails its own gate by luck; too low and a mediocre
   solution reads as perfect. The template's own spread and method are in
   `templates/README.md`.
4. Repeat until `ale validate` prints `ok  ... untouched={'reward': 0.0}, oracle={'reward': 1.0}`.

## 7. Measure the difficulty

`ale validate` proves the task runs and scores honestly; it says nothing about difficulty.
Run frontier models on it with the engine's `ale run` (for example
`uv run ale run <task_dir> --agent claude-code --model <model> --runs-dir <runs>`) and read
the verdicts. The task's score is the verdict's `reward` (0 to 1; the capped `ratio` rides
along in `metrics`), and current frontier models should stay roughly below 0.5 under the
agreed configuration and run budget. When a model saturates the task, raise the
engineering or perception challenge and test again; do not suppress scores with arbitrary
penalties, and keep the task completable by a human expert under the stated conditions.
Before reading a failed run as a model limitation, fix what is not the task: environment
failures, missing dependencies, unclear instructions. Keep the run instructions and the
results for the pull request.

Two defects of the `claude-code` harness at the pinned engine commit lower scores without
any error. Check for both before trusting a run:

- A model stream that is silent for 300 s is cut off, and the episode ends as
  `API Error: The operation timed out`. For evaluation runs, add
  `ENV BUN_CONFIG_HTTP_IDLE_TIMEOUT=1800 CLAUDE_STREAM_IDLE_TIMEOUT_MS=1800000` to a copy of
  the task's `image/Dockerfile` (the variables only reach the harness).
- The harness sets the agent's `PATH` to its own directories plus `/usr/local/bin:/usr/bin:/bin`,
  so `/opt/venv/bin` is gone and the agent's `python3` lacks the venv's packages. Say in
  `instruction.md` that the venv's interpreter is `/opt/venv/bin/python3`, and check the
  agent's transcript for `ModuleNotFoundError`.

## 8. Submit

Open a pull request into this repository that adds exactly one folder,
`tasks/<platform>/<name>/`, where `<platform>` is `metadata.platform` from your `task.yaml`
(one of the keys in `tasks/platforms.json`) and `<name>` its `name`. Push the branch to this
repository (the project initiator grants write access after you sign up and send your GitHub
ID) or to a fork. The pull request
template asks for the `ok` line of your `ale validate` run, your frontier-model results and a
few confirmations. Nothing else belongs in the pull request: no notes, no `__pycache__/`, no
`.ale-cache/`, no `assets/`. To revise the task under review, push to the same branch.

## 9. What happens on the pull request

CI (`.github/workflows/pr-checks.yml`) runs `ale lint` over `tasks/` and `templates/`, the
domain lint (metric, anchor, visible surface, image hygiene, placement), the kit comparison,
the registry identity cross-check (a new slug is a notice) and the test suite. It needs no
secrets, so it runs on forks too. It does not build your image or run `ale validate`.

A maintainer then reads `verify/` and `oracle/`, re-runs `ale lint` and `ale validate` on
your branch, corrects the anchor on the branch if the measurement disagrees, and merges. The
task is live at its path from that moment and enters the registry at the maintainers' next
export; the website displays the registry once that export is installed there. Expert
validation works from a task version (the pull request's branch and commit); the guidelines
describe that stage and the formal benchmarking runs.

## Common pitfalls

- A venv the agent account cannot traverse fails only at solve time, as a misleading
  `ModuleNotFoundError`. The agent UID must be able to traverse (not merely read) everything
  the venv points at; keep the Dockerfile's agent-UID import self-check.
- `params:` are strict both ways. A parameter you declare but never use in `instruction.md`
  is a lint error. Grader knobs go in `verify/grader_config.json`, not `params`.
- `ale validate` does not print a stage's stderr. It is in `trace.execution.jsonl`.
- Do not edit run outputs to make a task pass. Author the inputs; the verdict is computed.
- The folder name, `name` in `task.yaml` and the `tasks/<platform>/` parent must agree; the
  domain lint refuses a pull request where they do not.
