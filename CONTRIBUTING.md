# Contributing a task

A task is one self-contained folder. Contributing one means getting a pull request merged
that adds it at `tasks/<platform>/<slug>/`. There are two ways to get that pull request open;
both are reviewed the same way and land in the same place.

## Before you start

You need the ALE engine and Docker. The engine repository is public and not on PyPI:

```bash
git clone https://github.com/AgentsLastExam/ale.git && cd ale
just bootstrap                          # needs uv and just (uv tool install rust-just)
uv run ale --help
images/build.sh container-ubuntu22      # the sandbox base image; once per machine
```

Start from the template. Either download it as a zip from
<https://agentic-robotics-benchmark.org/contribute> (the download adds a `GETTING-STARTED.md`
with every command) or copy `templates/task/` from a checkout of this repository (until the
website catches up with the 2026-10-15 change, its download is an older template: take the
checkout copy, see way two). Rename the folder to your slug and set `name` in `task.yaml` to
the same string. What each file is for, and what the robotics domain requires beyond the
engine's own specs, is in [`templates/README.md`](templates/README.md). The long form of the
whole path, including the anchor bootstrap, is
[`docs/contributing-a-task.md`](docs/contributing-a-task.md).

Build the task, then gate it from the engine checkout until both lines are green:

```bash
uv run ale lint <task_dir>
uv run ale validate <task_dir> --runs-dir <runs>     # untouched all-zero, oracle exactly 1.0
```

## Way one: open the pull request yourself

1. Fork this repository and create a branch.
2. Copy your folder to `tasks/<platform>/<slug>/`. `<platform>` is `metadata.platform`
   from your `task.yaml`, one of the keys in `tasks/platforms.json`; `<slug>` is its
   `name`. Nothing else goes in the pull request.
3. Push and open the pull request. The template asks you for the `ok` line of your
   `ale validate` run and a few confirmations.

## Way two: upload on the website

> **Until the website catches up with this repository (2026-10-15).** Its download is still an
> older template, and its upload check and bot still expect the old form (`metadata.direction`,
> `metadata.metric`, the old platform keys): an upload made from the current template is
> refused, and one made from the old download lands at `tasks/<direction>/<slug>/`, which CI
> here refuses. Until the website update ships, use way one.

1. Sign in at <https://agentic-robotics-benchmark.org/contribute> (GitHub or Google).
2. Zip the task folder alone (the folder itself, or its contents at the zip root). Limits:
   50 MB, 256 files, 2 MB per file, no symlinks.
3. Upload it with a title. The server runs the engine's `ale lint` on the archive plus the
   domain checks, refuses it with a list of problems if anything is wrong (nothing is stored),
   and otherwise opens the pull request for you on a `contrib/...` branch and shows you the
   link. "Check only" runs the same checks without uploading.
4. A revision is a new zip with the same slug, uploaded from your profile; it becomes a new
   commit on the same pull request.

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
   VIRTUAL_ENV= uv run ale lint ../../tasks/<platform>/<slug>
   VIRTUAL_ENV= DOCKER_HOST=unix:///var/run/docker.sock \
       uv run ale validate ../../tasks/<platform>/<slug> --runs-dir <runs>
   cd ../.. && uv run python scripts/lint_domain.py tasks/<platform>/<slug>
   ```

   If the measured anchor differs from `verify/anchor.json`, push a correction to the branch
   rather than asking the contributor to redo it.
3. Merge. The task is live at its path; nothing relocates or rewrites it.
4. Export the registry from a clean checkout of `main` and install it in the website
   (`scripts/export_registry.py`, then the website's `node scripts/sync-benchmark.mjs`);
   commit the updated `identity-history.json` here. For a website upload, record the merge in
   the review panel so the contributor's status reads "Landed".

## Contributing to the tooling

`harness/`, `scripts/`, `shared/`, `skills/` and `tests/` are ordinary Python; `uv sync` and
`uv run pytest -q`. Edit `shared/robotics_grader/` and copy it into every task's
`verify/robotics_grader/`, never the other way round. Everything written into this repository
is English. Where a document and the code disagree, the code wins: fix the document.
