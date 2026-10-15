# Contributing a task

A task is one self-contained folder. Contributing one means getting a pull request merged
that adds it at `tasks/<platform>/<name>/`. The project's contribution guidelines, with
sign-up, the three roles (development, validation, benchmarking), the task stages and how
contributions are credited, are two Google Docs with the same content:
[English](https://docs.google.com/document/d/1s1E3ymIBLujWf2l7rhu4g9Nl4orBfooK0aDWOdum7-Y/view)
and [中文](https://docs.google.com/document/d/1gX8v-w-idIPLjzoWgRskT0llE7cKXqg9sVLB0cWfZk0/view).
This page covers what happens in this repository.

## Before you start

1. Sign up in the project's coordination sheet (linked from the guidelines) and tell the
   project initiator your GitHub ID; they give you write access to this repository (the sheet
   alone does not).
2. Register the task in the sheet. Its Task Name is the task's permanent `name`, identical in
   the sheet, in the folder name and in `name` in `task.yaml`: preferably a short `snake_case`
   string (lowercase letters, digits, underscores; the lint accepts `^[a-z0-9][a-z0-9_-]*$`).
   A retired task keeps its name; never reuse one.
3. Pick its platform, one of the keys in [`tasks/platforms.json`](tasks/platforms.json):
   `arm`, `hand`, `quadruped`, `humanoid`, `uav`, `driving`, `underwater`, `space`, `soft`,
   `surgical`, `swarm`, `subsystems`. A task that fits none needs a maintainer first.

You need Docker and the ALE engine at the commit this repository pins: the `vendor/ale`
submodule (upstream `main` moves on; a task is gated on the pinned commit). Clone with the
submodule (or run `git submodule update --init vendor/ale` in an existing clone):

```bash
git clone --recurse-submodules https://github.com/Grounded-Intelligence-Initiative/agentic-robotics-benchmark.git
cd agentic-robotics-benchmark/vendor/ale  # the pinned engine; run every `ale` command from here
just bootstrap                          # needs uv and just (uv tool install rust-just)
uv run ale --help
images/build.sh container-ubuntu22      # the sandbox base image; once per machine
```

## Build and test it

Copy `templates/task/` from a checkout of this repository, rename the folder to the task's
name and set `name` in `task.yaml` to the same string. What each file is for, and what the
robotics domain requires beyond the engine's own specs, is in
[`templates/README.md`](templates/README.md). The long form of the whole path, including the
anchor bootstrap, is [`docs/contributing-a-task.md`](docs/contributing-a-task.md).

Gate it from `vendor/ale` until both lines are green:

```bash
uv run ale lint <task_dir>
uv run ale validate <task_dir> --runs-dir <runs>     # untouched all-zero, oracle exactly 1.0
```

Validation shows that the task runs; it says nothing about difficulty. Measure that with a
frontier model (`uv run ale run <task_dir> --agent <harness> --model <model>`) and iterate:
the task's score is the verdict's `reward` (0 to 1), and current frontier models should stay
roughly below 0.5 under the agreed configuration and budget. Raise the engineering or
perception challenge rather than adding arbitrary penalties, and fix environment failures,
missing dependencies and unclear instructions before reading a failed run as a model
limitation. The pinned `claude-code` harness has two such defects (a model stream silent for
300 s is cut off; the agent's `PATH` drops `/opt/venv/bin`); see
[`docs/contributing-a-task.md`](docs/contributing-a-task.md) §7. Keep the run instructions
and the results of both.

## Open the pull request

1. Push a branch (to this repository, or to a fork) that adds exactly one folder,
   `tasks/<platform>/<name>/`, where `<platform>` is `metadata.platform` in your
   `task.yaml` and `<name>` its `name`. Nothing else goes in the pull request.
2. Open the pull request. Its template asks for the `ok` line of your `ale validate` run,
   your frontier-model results and a few confirmations.
3. Once CI passes, hand the task version (the branch and its commit) to the task's validator,
   as the guidelines describe.

## What CI checks on every pull request

`.github/workflows/pr-checks.yml`, no secrets needed, so it runs on forks too:

- the pinned engine's `ale lint` over `tasks/` and `templates/`;
- the domain lint (`scripts/lint_domain.py`): a hardware-invariant metric on a positive scale
  (`metric` and `direction` in `verify/anchor.json`), an anchor with a positive `value`, a
  `full_at` inside the scoring cap and a real `source`, nothing under `setup/payload/` that
  reaches the grader, image hygiene, a `metadata.platform` from `tasks/platforms.json`, and the
  placement of the folder at `tasks/<metadata.platform>/<name>/`;
- `verify/robotics_grader/` byte-identical to `shared/robotics_grader/` (copy the canonical
  kit over yours and re-validate if this fails);
- the registry identity cross-check for tasks the registry already knows (a new slug is a
  notice, not a failure);
- the pytest suite.

CI does not build images or run `ale validate`; that stays with you before the pull request
and with a maintainer before the merge.

## What the maintainers do

1. Read `verify/` and `oracle/`. This is contributor code, and `verify/` computes every
   future score.
2. Check out the branch and re-run the gate from `vendor/ale`:

   ```bash
   VIRTUAL_ENV= uv run ale lint ../../tasks/<platform>/<name>
   VIRTUAL_ENV= DOCKER_HOST=unix:///var/run/docker.sock \
       uv run ale validate ../../tasks/<platform>/<name> --runs-dir <runs>
   cd ../.. && uv run python scripts/lint_domain.py tasks/<platform>/<name>
   ```

   If the measured anchor differs from `verify/anchor.json`, push a correction to the branch
   rather than asking the contributor to redo it.
3. Merge. The task is live at its path; nothing relocates or rewrites it.
4. Export the registry from a clean checkout of `main` (`scripts/export_registry.py`) and
   commit the updated `identity-history.json`. The website installs that export and only
   displays it.

## Contributing to the tooling

`harness/`, `scripts/`, `shared/`, `skills/` and `tests/` are ordinary Python; `uv sync` and
`uv run pytest -q`. Edit `shared/robotics_grader/` and copy it into every task's
`verify/robotics_grader/`, never the other way round. Everything written into this repository
is English. Where a document and the code disagree, the code wins: fix the document.
