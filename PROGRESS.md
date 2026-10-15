# PROGRESS.md · progress log for agentic-robotics-benchmark

> **What this is:** the progress source of truth for this repository, maintained by people
> and agents alike. It records milestone state, the last stopping point, known gotchas and the
> next step. **Disk is the truth** (git + this file + `CLAUDE.md`); a session's context is a
> volatile cache. Read this file first; update it (state / next step / gotchas) and commit
> before every hand-off. Where it disagrees with the code or `git log`, the code wins: fix
> this file.
>
> Last updated: 2026-10-15 (the benchmark owns its taxonomy: platform keys in
> `tasks/platforms.json`; `metadata.direction` and `metadata.metric` retired, the metric lives in
> `verify/anchor.json`; the registry export no longer copies anything from the website).

## 0. 2026-09-07 — created by the rebrand from "ALE Robotics"

Origin: the maintainers' repository `ale-robotics-benchmark-private` at `86c2285` (its
history, including the Chinese progress log, stays there, archived) and the public mirror
`ale-robotics-benchmark` (archived, tag `legacy-v0.5-final`). This repository starts with a
fresh history and replaces both: it is public and carries every task folder whole, `oracle/`
and `verify/` included.

- **Layout flattened.** `ale-domain/tasks/` → `tasks/`, `ale-domain/templates/` →
  `templates/`, `ale-domain/identity-history.json` → `identity-history.json`,
  `ale-domain/_evidence/` → `docs/evidence/`, `ale-domain/README.md` → `tasks/README.md`.
  Verified with the engine loader: `drone_hover`'s `spec_hash` and `content_digest` are
  unchanged (a folder's location is not part of its identity), so `identity-history.json`
  still describes the tree.
- **Staging retired.** Both contribution channels (a pull request opened by hand; the
  website upload whose bot opens the same pull request) land a task directly at
  `tasks/<direction>/<slug>/`. Deleted: `contrib-staging/`, `scripts/promote_contribution.py`,
  `.github/workflows/promote-on-merge.yml`, `scripts/publish_public.py` and its tests.
  `.github/workflows/pr-checks.yml` now runs the full check set on the final path, needs no
  secret (fork pull requests run it), and its registry identity cross-check treats a slug the
  history does not know as a notice, not a failure. `scripts/lint_domain.py` gained battery 7
  (placement: `tasks/<metadata.direction>/<name>/`); `harness/ale_onboard.py` and the lint
  walk only `tasks/` and `templates/`, never the repository root (that would descend into
  `vendor/ale`). `.github/PULL_REQUEST_TEMPLATE.md` is new.
- **Renamed.** `pyproject` name `agentic-robotics-benchmark`; the download is
  `agentic-robotics-task-template.zip` with folder `agentic-robotics-task-template/`;
  `export_registry.py` release name "Agentic Robotics Benchmark" (`provenance.tasks_repo`
  follows the directory name); `GETTING-STARTED.md` rewritten for the two channels and the
  new domain `agentic-robotics-benchmark.org`; README, README.zh-CN, CLAUDE.md, CONTRIBUTING.md
  (new), docs and skills rewritten for the brand and the flat paths. Added `LICENSE`
  (Apache-2.0, from the old public mirror) and `NOTICE`.
- **Unchanged on purpose** (runtime identifiers stay until a separate decision): `ALE_*`
  environment variables, the protocol names `ale-engine-run/v1` and
  `ale-robotics-task-registry/v2`, the console scripts `ale-onboard` / `ale-export`,
  `harness/export_run.py`'s exporter and benchmark names (they travel in bundle manifests
  the server reads), the engine pin `vendor/ale` @ `90a7c1c`, and every file of
  `tasks/control/drone_hover` (its `task.yaml` and `verify/verify.py` still carry three
  comments saying "ALE Robotics domain policy"; editing them would change the task's
  `content_digest`, so they go when the task goes: owner decision, delete `drone_hover` when
  the first community task is merged).
- **Not carried over** (owner decision; they live in the archived private repository):
  the Chinese `PROGRESS.md` history, `docs/prompts/` (session hand-off briefs),
  `docs/dev/archive/` (the pre-ALE snapshot and audits), `docs/tree_diagram/` (the taxonomy
  visualisation source; the workspace repository keeps the taxonomy markdown).
- **Verified at creation:** `uv run pytest -q` 225 passed, 1 skipped (the website registry
  cross-check, which runs when the website checkout is next to this repository); `ale lint ../../tasks` and
  `ale lint ../../templates` ok from `vendor/ale`; `scripts/lint_domain.py .` ok;
  `robotics_grader` selftest passed; the template's vendored kit byte-identical to
  `shared/`; the workflow file parses; a clean registry export reproduces
  `identity-history.json` byte for byte and the template zip builds (22 entries); ruff reports
  the same nine pre-existing style notes as before (non-blocking in CI).

## 1. 2026-09-08 — pushed; the pending contribution re-opened here

- `main` pushed to `https://github.com/Grounded-Intelligence-Initiative/agentic-robotics-benchmark`
  after the owner signed off the privacy checklist; the first `pr-checks` run on `main` is
  green (engine lint over `tasks/` + `templates/`, identity cross-check, static checks).
- The contribution `fault_adaptive_spacecraft_docking` (old repository PR #14, uploaded on
  the website by Jiankai-Sun, revision 2) is re-opened here as https://github.com/Grounded-Intelligence-Initiative/agentic-robotics-benchmark/pull/1 on branch
  `contrib/fault_adaptive_spacecraft_docking`: commit 1 = the 20 uploaded files byte for
  byte at `tasks/control/fault_adaptive_spacecraft_docking/`; commit 2 = `verify/robotics_grader/`
  re-vendored from `shared/` (the upload carried the 2026-09-05 kit). Old PR #14 closed with
  a pointer. Still to do by a maintainer before merge: `ale validate` (the anchor was a
  dry-run measurement; the oracle runs three hours).

## 2. 2026-09-08 — website live; both channels rehearsed

- The website (`agentic-robotics-benchmark-website`, formerly `ale-robotics-website`) is live
  on `https://agentic-robotics-benchmark.org` with the two-channel Contribute page, the
  download built from this repository at `b04359f`, and the registry exported from the same
  commit; `ale-robotics.org` and its `www` 301 to the new domain.
- Dress rehearsal with a template-derived task `rehearsal_reach_20260908` (uav/control;
  anchor `0.0813` measured by `ale validate` on the maintainer's machine, oracle exactly 1.0):
  channel A (pull request #2, opened by hand) and channel B (pull request #3, opened by the
  website's bot after the upload precheck) produced byte-identical trees at
  `tasks/control/rehearsal_reach_20260908/` (20 files, same modes, nothing outside the
  folder), and `pr-checks.yml` passed on both. Both were closed without merging and their
  branches deleted, as planned.
- Website follow-ups found by the rehearsal and fixed the same day: the `www` alias of the
  new domain (Caddyfile `WWW_DOMAIN`), and a 500 on `POST /api/v1/challenges` caused by the
  rename creating a second release row (the website's seed now keys the release by version
  and merges duplicates).

## 3. 2026-09-08 — the paper-onboarding skill link removed

- `.claude/skills/onboard_paper` was a symlink to `skill/`, the paper-reproduction
  onboarding skill of the project's first months; its target had been deleted long before
  the rebrand copied the dangling link over. Removed (owner decision: the benchmark is not
  about reproducing papers, so no replacement). The repository has no `.claude/` directory
  now; the agent-facing skills are the ones under `skills/onboard-*/`, routed by
  `skills/onboard-task/SKILL.md`. `docs/research/agentic-robotics-survey.md` pointed at
  the old `skills/SKILL.md`; it now points at the router.

## 4. 2026-10-12 — `drone_hover` is now `tasks/uav/uav_hover`; tasks are placed by platform

- Owner decisions: `tasks/control/drone_hover` becomes `tasks/uav/uav_hover`
  (`name: uav_hover`), a dummy task that frontier models complete easily, kept only to show
  what a task's file structure looks like. This replaces "delete it with the first merged
  community task". `tasks/README.md` is now one paragraph saying so: there is no open-loop /
  closed-loop distinction any more.
- A landed task sits at `tasks/<metadata.platform>/<name>/` (was
  `tasks/<metadata.direction>/<name>/`). Changed with it: `scripts/lint_domain.py` battery 7,
  the instantiate target of `harness/ale_onboard.py`, the template download's getting-started
  text (`scripts/build_template_zip.py`), the comment in `templates/task/task.yaml`, the pull
  request template, the comments in `pr-checks.yml`, README, README.zh-CN, CONTRIBUTING,
  CLAUDE.md, docs and skills. The tests now refuse a direction-named folder.
- Identity: the new `name` gives the task a new `spec_hash` and `content_digest`, so the
  registry identity cross-check reports `uav_hover` as a new task; `identity-history.json`
  keeps the `drone_hover` record. `image/` is untouched, so the image identity (and an image
  already built from it) carries over; its Dockerfile still says `drone-hover` in two log
  lines and one comment.
- Verified locally (the `pr-checks.yml` steps): `ale lint ../../tasks` and
  `../../templates` ok; registry export + identity cross-check ok (`uav_hover` new);
  `robotics_grader` selftest and the vendored-kit diff ok; `uv run pytest -q` all passed with
  the engine venv present (the template-zip test, which refuses a dirty tree, passed on a
  clean throwaway commit); `scripts/lint_domain.py .` ok; ruff reports the same nine
  pre-existing notes.

## 5. 2026-10-15 — the benchmark owns its taxonomy; `direction` and `metric` metadata retired

- Owner decisions: this repository is self-sustaining, and the website is only a front end
  that displays what this repository exports. The platform vocabulary is
  `tasks/platforms.json`, fixed keys `arm` (robotic arm), `hand` (dexterous hand),
  `quadruped`, `humanoid`, `uav`, `driving` (autonomous driving), `underwater` (UUV/AUV),
  `space` (space robot), `soft`, `surgical`, `swarm`, `subsystems`. `metadata.direction` is
  removed: platform is the only taxonomy field. `metadata.metric` is removed: upstream ALE
  does not define it (its metadata is "optional descriptive values; no runtime behavior"), and
  the metric and its direction are declared in `verify/anchor.json` only, where the grader
  reads them.
- `scripts/lint_domain.py`: battery 7 refuses a `metadata.platform` outside the vocabulary;
  the metric checks (named, not hardware-dependent, `direction` higher or lower) read
  `verify/anchor.json`; the two "task.yaml and anchor.json agree" checks are gone; stale
  `direction` / `metric` metadata is ignored, like `genre` / `paper`.
- `scripts/export_registry.py`: `platforms` comes from `tasks/platforms.json`; a task whose
  platform is not a key is refused; `metric_name` / `metric_direction` / `metric_label` come
  from `verify/anchor.json`; no per-task `direction`, no `directions`, no `category_tree`, no
  `hardware_invariant` (a self-declaration nothing checked; the lint's token rule is what
  enforces invariance); `--carry-over` is removed; `tasks/platforms.json` is held to the same
  clean-tree rule as the task folders, since it goes into the document. `schema_version` stays
  `ale-robotics-task-registry/v2` on purpose: the website's sync treats any other version as
  a legacy merge and its seed refuses unknown versions, whereas a v2 document without
  `direction` fails the website's current schema closed (nothing is written).
- `harness/ale_onboard.py`: the spec's `platform` must be a key (the `misc` fallback is gone),
  and the spec's metric name and direction are written into the copied `verify/anchor.json`
  (a name without a direction is `higher`, never the template's `lower`). The gates check that
  the grader reports the anchor's metric: an oracle record without it is `needs_input` (the
  anchor's name is what the export publishes and the server's recompute looks up). An anchor
  still at `TODO` names no metric, and `needs_anchor` then asks for the name with the value.
- Template: `metadata: {platform: TODO}` with the key list; the anchor's `metric` is `TODO`
  (the template grader's `mean_goal_distance` is an example, not a default);
  `templates/README.md`, the template-zip guide, README, README.zh-CN, CONTRIBUTING,
  CLAUDE.md, docs and skills follow; the two 2026-09-02 design records carry a dated note. The
  pointers that called `tasks/README.md` the task contract now point at `templates/README.md`,
  and the upload docs carry an interim note (the website still runs the old form: open the
  pull request yourself). The test that read the sibling website checkout is deleted (the
  dependency pointed the wrong way).
- `uav_hover`: metadata reduced to `platform: uav`; its anchor gains `"direction": "higher"`
  (its grader already hard-codes higher), so its `spec_hash` and `content_digest` change again
  (still not in `identity-history.json`; CI reports it as a new task).
- Verified locally (the `pr-checks.yml` steps): `ale lint ../../tasks` and `../../templates` ok
  (`tasks/platforms.json` is invisible to the engine); registry export ok (`platforms` = the 12
  keys; no `direction`, `directions` or `category_tree`) and identity cross-check ok (`uav_hover`
  new); `robotics_grader` selftest and the vendored-kit diff ok; `uv run pytest -q` all passed
  (the template-zip test on a clean throwaway commit); `scripts/lint_domain.py .` ok; ruff the
  same nine pre-existing notes.
- Still bound to the website (not changed here, candidates for a later pass): website policy
  copied into this repository's text (upload URL, size limits, the precheck rule list); the
  JSON Schemas of the registry and of `ale-engine-run/v1` exist only in the website
  (`packages/contracts/schemas`); `provenance.tasks_repo` is the clone's directory name; the
  `episode_id` pattern in `harness/export_run.py` mirrors the server's.

## Open items

- Website, one pull request merged as a single commit (its deploy does not wait for CI):
  registry schema and types without `directions`, `category_tree` and per-task `direction`;
  the sync takes `platforms` from the export; a migration dropping `tasks.direction` and the
  seed that writes it; the upload precheck checks `metadata.platform` against the installed
  `platforms` and the metric on `verify/anchor.json` (no `metadata.direction` /
  `metadata.metric`); the bot lands uploads at `tasks/<metadata.platform>/<slug>/`; pages
  without direction facets and badges, the run command built from the platform; the
  hardware-invariant badge (the export no longer carries `hardware_invariant`);
  `validate-registry.mjs`, `sync-benchmark.mjs` and the generated schema enums
  (`generate-schema-enums.mjs`) without directions; the Contribute page copy, the upload
  contract docs (`docs/contribution-flow*.md`, `docs/data-model.md`) and the `--carry-over`
  mentions in its README and `docs/content-sync.md`; tests; then install a fresh export and
  rebuild the template download. Until then an upload made from the new template is refused
  by the website's precheck, one made from the old download lands at `tasks/<direction>/<slug>/`
  and fails battery 7, and installing a new export fails closed.
- `ale validate` `uav_hover` (the oracle trains PPO for hours), then export the registry and
  commit `identity-history.json`; installing it in the website waits for the website pull
  request above.
- Pull request #1 (`fault_adaptive_spacecraft_docking`, at
  `tasks/spacecraft/fault_adaptive_spacecraft_docking/` since 5000d45) follows this change on
  its branch: merge `main`, move the folder to `tasks/space/fault_adaptive_spacecraft_docking/`,
  reduce its metadata to `{platform: space}` (its anchor already declares the metric and
  direction). Merge or reject it after `ale validate`.

## Gotchas

- `export_registry.py` refuses a dirty tree on purpose: `content_digest` covers every
  regular file, ignored `__pycache__/` included. Export from a clean commit; `--allow-dirty`
  is for throwaway exports and never writes the canonical `identity-history.json`.
- Never run `ale validate` on `uav_hover` casually: its oracle trains PPO for hours. The
  template validates in seconds.
- Engine commands run from `vendor/ale` with `VIRTUAL_ENV=` cleared and
  `DOCKER_HOST=unix:///var/run/docker.sock` pinned (two docker contexts on the dev machine).
  `uvx ruff`, not `uv run ruff` (ruff is not a project dependency).
- `uv venv --python 3.12` puts its managed interpreter under `/root/.local/share/uv`, which
  the agent account cannot traverse; task images set `UV_PYTHON_INSTALL_DIR=/opt/uv-python`
  and keep the Dockerfile's agent-UID import self-check. The agent UID must traverse (not
  only read) everything the venv points at.
- The engine stages `ale_verify` into whatever answers `python3` and refuses < 3.12.
- `ale validate` does not keep a stage's stderr; it is in the episode's
  `trace.execution.jsonl`.
- A null anchor is not an error: the engine fails the run with `oracle_not_full` and
  `harness/ale_onboard.py` reads that as `needs_anchor` with the measured value.
- The template's `GETTING-STARTED.md` is an inline constant in
  `scripts/build_template_zip.py` (capped at 90 lines by its test); edit it there and rebuild
  the website's `apps/web/public/task-template/` copy plus `SOURCE.json`.
