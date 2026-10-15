# Contributing a task

The path: get the engine, start from the template, build the task locally, validate it, then
get a pull request open that adds your folder at `tasks/<platform>/<slug>/`. Two ways to
open that pull request, one result: open it yourself, or upload the zipped folder on the
website and let its bot open it for you. A maintainer reviews it the same way either way.
The short version is the repository's [`CONTRIBUTING.md`](../CONTRIBUTING.md); the design
record is [`dev/20260902-contribute-download-upload.md`](dev/20260902-contribute-download-upload.md).

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

You need the ALE engine and Docker. The engine repository is public; it is not on PyPI.
`uv` and `just` (`uv tool install rust-just`) drive it:

```bash
git clone https://github.com/AgentsLastExam/ale.git
cd ale
just bootstrap
uv run ale --help
images/build.sh container-ubuntu22     # the sandbox base image; build it once, it takes a while
```

The base image on ghcr.io refuses anonymous pulls, so build it locally from the checkout. If
your reference method trains, plan for a single GPU of 24 GB or less; training happens only
in the oracle phase, never during scoring. You do not need a checkout of this repository to
author a task, only to open the pull request yourself.

## 2. Get the template

> **Until the website catches up (2026-10-15).** The website's download is still built from an
> older template commit (it carries `metadata.direction` and `metadata.metric`); copy
> `templates/task/` from a checkout instead. The two sources are the same files again once the
> website's copy is rebuilt.

Two sources, same files. On the website, sign in (GitHub or Google) and download the
template from the Contribute page. Maintainers build that zip with
`uv run python scripts/build_template_zip.py --out <zip> --source-out <SOURCE.json>` from
[`templates/task/`](../templates/task/) at a pinned commit; it is the committed tree, modes
included, and unzips to:

```
agentic-robotics-task-template/   the task folder. Rename it to your slug; this is what you submit.
GETTING-STARTED.md                the guide, with the exact commands
TEMPLATE-SOURCE.json              {repo, commit, generated_at, template_path}: provenance
```

Or, with a checkout of this repository, copy `templates/task/` to wherever you work.

Rename the folder to your slug first and set `name` in `task.yaml` to the same string. If
your extractor does not keep Unix permissions (Windows, some GUI tools), run
`chmod +x setup/run.sh oracle/run.sh verify/run.sh`; `ale lint` requires the three stage
entry points to be executable.

The engine admits only `task.yaml`, `instruction.md`, `image/`, `setup/`, `verify/`,
`oracle/`, `tools/` and `.ale-cache` at a task's top level. Any stray file inside the folder
fails lint, which is why the download's helper files sit next to it. Keep notes and scratch
files outside the folder too.

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

If you have this repository, `uv run python scripts/lint_domain.py <task_dir>` runs the
domain lint the pull request CI runs (section 5, plus placement once the folder sits under
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
domain lint and the upload precheck do. The metric is declared in `verify/anchor.json` and
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

## 7. Submit

Both ways end as a pull request into this repository that adds exactly one folder,
`tasks/<platform>/<slug>/`, where `<platform>` is `metadata.platform` from your `task.yaml`,
one of the keys in `tasks/platforms.json`.

> **Until the website catches up (2026-10-15).** The upload check and bot described below are
> this repository's form. The website still runs the older one: it requires `metadata.direction`
> and `metadata.metric`, so an upload made from the current template is refused, and it lands a
> folder at `tasks/<direction>/<slug>/`, which battery 7 here refuses. Until its update ships,
> open the pull request yourself.

**Open the pull request yourself.** Fork the repository, copy your folder to that path on a
branch, push, and open the pull request. The pull request template asks for the `ok` line of
your `ale validate` run and a few confirmations. Nothing else belongs in the pull request:
no notes, no `__pycache__/`, no `.ale-cache/`, no `assets/`.

**Upload on the website.** Zip only the task folder: the folder itself, or its contents at
the zip root. Not `GETTING-STARTED.md`, not `TEMPLATE-SOURCE.json`, not `__pycache__/`, not
`.ale-cache/`. Never zip the download root; an archive holding the helper files or more than
one folder is rejected on layout. Upload it on the Contribute page with a title. Limits:
50 MB total, 256 files, 2 MB per file, no symlinks. From there the server does the rest:

1. It runs the engine's own `ale lint` on the archive and prechecks the rest, and rejects the
   upload when: the size limits or path safety are broken (a path escapes the folder or uses
   characters outside `[A-Za-z0-9._-/]`); a required file is missing (`task.yaml`,
   `instruction.md`, `image/Dockerfile` or an `image.ref`, `setup/run.sh`, `oracle/run.sh`,
   `verify/run.sh`, `verify/anchor.json`); `task.yaml` does not parse or `name` does not match
   the folder slug; `metadata.platform` is not one of the keys in `tasks/platforms.json`;
   `verify/anchor.json` names no `metric`, its `direction` is not `higher` or `lower`, the
   metric is hardware-dependent, it has no `value > 0`, or its `source` is missing,
   `TODO`, or cites a paper's number; `full_at` is outside (0, scoring_cap]
   (`verify/grader_config.json`, 1.5 by default); the Dockerfile has no `FROM`, a privileged
   flag, or a `COPY`/`ADD` of `verify/`, `oracle/` or anchor material; or the slug is already
   taken. A failure comes back as a list and nothing is stored; fix and upload again. "Check
   only" runs the same checks without uploading.
2. It commits the folder to `tasks/<platform>/<slug>/` on a branch `contrib/<id>` of this
   repository and opens the pull request, the same shape as one opened by hand, and shows
   you the link. When the server holds no repository credential the zip is kept for a
   maintainer instead ("Queued for a maintainer").

The server does not run `ale validate`; you do, and a maintainer does again before merging.
Your profile lists every upload with its status and the pull request link. To revise a task
under review, upload a new zip with the same slug; it becomes a new commit on the same pull
request. On a pull request you opened yourself, push to your branch.

## 8. What happens on the pull request

CI (`.github/workflows/pr-checks.yml`) runs `ale lint` over `tasks/` and `templates/`, the
domain lint (metric, anchor, visible surface, image hygiene, placement), the kit comparison,
the registry identity cross-check (a new slug is a notice) and the test suite. It needs no
secrets, so it runs on forks too. It does not build your image or run `ale validate`.

A maintainer then reads `verify/` and `oracle/`, re-runs `ale lint` and `ale validate` on
your branch, corrects the anchor on the branch if the measurement disagrees, and merges. The
task is live at its path from that moment, enters the registry at the maintainers' next
export, and appears on the website after the registry is installed there.

## 9. What the upload collects

The zip you upload, the title you give it, and the identity you signed in with. Nothing else.
A pull request you open yourself collects what GitHub collects.

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
