# CLAUDE.md · working conventions for agentic-robotics-benchmark

> **The Agentic Robotics Benchmark is an agentic-robotics benchmark: autonomous agents solve
> robotics problems inside a sealed sandbox.** Each task drops an agent into a robotics
> problem (develop a controller, plan a manipulation sequence, design a part, write
> code-as-policy, tune a planner, …) and scores **whatever artifact it leaves** in
> `/home/user/submission/` with a hidden grader on hidden seeds. The contract is
> artifact-general: `verify/` scores the artifact however the author decides, `oracle/` is the
> reference artifact that scores full. Never frame the benchmark as "running a policy"; that
> is one shape among many. There is **one kind of task**; `metadata.platform` (one of the fixed
> keys in `tasks/platforms.json`) is the taxonomy axis. The tasks run on the upstream
> `AgentsLastExam/ale` engine, pinned as a submodule at `vendor/ale`. **Never reimplement
> anything the engine does.** This repository is public. The user-visible name is always the full "Agentic Robotics Benchmark"; "ALE
> Robotics" is the name it had before 2026-09-07 and appears only where history is explained.

## Read this first

- **Authority for the task shape and quality: the upstream specs**,
  `vendor/ale/docs/specs/task-authoring.md` and `task-quality-standard.md` (public:
  https://github.com/AgentsLastExam/ale/blob/main/docs/specs/). Our docs state only the
  robotics deltas and link there; never paraphrase the specs at length.
- **Our deltas:** `templates/README.md` (the task contract, file by file: the verify
  patterns, the closed_loop wire, scoring); `tasks/README.md` is only a note on the
  `uav_hover` example. **Design records for the current tree:**
  `docs/dev/20260902-engine-run-submission.md` (task form, leaderboard bridge) and
  `docs/dev/20260902-contribute-download-upload.md` (the contribution path).
- **Docs vs code: code wins.** If a document (this file, `PROGRESS.md`, `docs/`) disagrees
  with the code or `git log`, trust the code, say so, and fix the document.
- **Everything written into the repository is English** — code, comments, docstrings, task
  files, docs, commit messages. Conversation with the owner may be in Chinese.
- Two repositories are **archived, not maintained**: `ale-robotics-benchmark-private` (the
  maintainers' repository this one grew out of; the pre-ALE task form and its harness sit at
  its tag `legacy-harness-final`) and `ale-robotics-benchmark` (the public mirror, tag
  `legacy-v0.5-final`). Do not revive them, do not cite their paths (`ale-domain/`,
  `contrib-staging/`), and do not re-introduce their vocabulary (promote, publish, mirror).

## Workflow rules (every session)

1. **Start by reading `PROGRESS.md`** (repo root): current state, last stopping point, known
   gotchas. Restate them before touching anything.
2. **Before every hand-off, update `PROGRESS.md`** (state / next step / gotchas) and commit.
3. **Disk is the truth**: git + `PROGRESS.md` + this file. Session context is a volatile cache.
4. One milestone per named session; `/clear` between milestones.
5. Large changes start in plan mode; a human approves before execution.

## The task form (there is exactly one)

```
tasks/<platform>/<slug>/
├── task.yaml         strict core/v1: spec_type, stable `name` slug, image, resources,
│                     timeouts, artifacts, metadata{platform}
├── instruction.md    the prompt — the only thing the tested agent is told
├── image/Dockerfile  sole build context; final stage FROM ghcr.io/agentslastexam/container-ubuntu22-base;
│                     py3.12 /opt/venv (installs image/requirements.txt, the ONE Python dep list)
│                     + agent-UID import self-check
├── setup/run.sh      trusted root, before the agent: stages setup/payload/ (practice grader,
│                     dev copies) into the agent home
├── oracle/run.sh     the reference implementation; `ale validate` runs it in place of the agent
└── verify/           run.sh -> verify.py (the grader), env.py, anchor.json, grader_config.json,
                      robotics_grader/ (vendored kit) — ABSENT while the agent works
```

- Identity = `task.yaml`'s `name` slug. `metadata.platform` is the only metadata field: one of
  the fixed keys in `tasks/platforms.json` (this repository owns the vocabulary; the registry
  export hands it to the website, which only displays it). A landed task sits at exactly
  `tasks/<metadata.platform>/<name>/` (`scripts/lint_domain.py` battery 7 checks the key and
  the placement; both contribution channels put the folder there directly). The metric and its
  direction live only in `verify/anchor.json` (`metric`, `direction`). There is no
  task-kind field and no evaluation-mode field: the three verify patterns (open_loop /
  closed_loop / artifact_rollout) are **authoring choices inside `verify/verify.py`**, not
  manifest fields. Do not reintroduce retired metadata keys from the archived form.
- The artifact contract (owner, 2026-09-05): the agent leaves ANY artifact in
  `/home/user/submission/` — a controller process, a plan or planner, a URDF / mesh /
  parameter design, generated code, a report; `verify/` scores it however the author
  decides; `oracle/` is simply the reference artifact (a hand-made file is fine). Wording
  everywhere says "submission" / "artifact", never "policy", where the artifact is meant
  ("code-as-policy" as a task-kind example is fine). Shapes + skeletons:
  `templates/README.md` "What a task can be" and the download's GETTING-STARTED.
- Hidden material is hidden by **stage isolation**: `verify/` and `oracle/` reach the sandbox
  only when their stage runs, `/opt/ale` is `drwx------ root:root`, and the solver runs at the
  agent's UID (`robotics_grader.Solver` / `deprivileged` via `runuser`). Proof:
  `docs/evidence/isolation-audit.md`. The repository being public changes nothing here: the
  anchor is hidden from the tested agent while it works, not from people.
- The engine reserves `config.json` inside `verify/` — grader knobs live in
  `verify/grader_config.json` and are read with `stage_config()`.
- `params:` are strict both ways: a declared parameter unused by `instruction.md` is a lint
  error.

## Gates

- **Engine gates** (from `vendor/ale`, clear `VIRTUAL_ENV`, pin the docker socket):
  `ale lint ../../tasks` and `ale lint ../../templates` (never the repository root: the
  engine would walk into `vendor/ale`), then `ale validate <task_dir>`. Validate runs the
  task twice: an **untouched** sandbox must produce an exact all-zero verdict, then the
  **oracle** runs in place of the agent and must score **exactly 1.0 on every reward key**.
  Both are the engine's own gates since 90a7c1c: `untouched_nonzero` / `oracle_not_full`,
  exit 2. Never describe the oracle rule as a warning or as "domain policy"; that framing is
  obsolete.
- `ale-onboard` classifies from the run records, not the exit status: a null-anchor run
  also fails with `oracle_not_full` and must read as `needs_anchor` (measured value
  reported), never as an error. Outcomes: `built` | `needs_anchor` | `needs_input` |
  `gave_up` (cannot be scored fairly) | `error`.
- `scripts/lint_domain.py`: hardware-invariant metric (`verify/anchor.json` names it, with a
  `direction` of higher or lower), positive metric scale, `full_at` present, visible-surface
  isolation (`setup/payload/` never reads the anchor or grader; the practice grader prints the
  raw metric only), image hygiene, anchor provenance (never a published figure), core/v1
  completeness, a `metadata.platform` from `tasks/platforms.json` and placement at
  `tasks/<platform>/<slug>/`.
- **Never run `ale validate` on `uav_hover` casually** — its oracle trains PPO for hours.
  The template validates in seconds.

## Scoring (the kit owns it; do not re-derive)

```
ratio  = clamp(measured / anchor, 0, cap=1.5)   # inverted for direction: lower
reward = clamp(ratio / full_at, 0, 1)           # the single gated reward key
```

- `verify/anchor.json`: `metric` + `direction` (higher | lower) = the task's metric, declared
  nowhere else; `value` = the reference implementation's own measured value from a
  real run (never a printed figure; null while bootstrapping — that is `needs_anchor`, not an
  error); `full_at` = the ratio at which reward saturates, sized from measured seed spread.
- Metric must be an **outcome on a positive scale**; never wall-clock time / throughput / FPS
  / frequency. A negative reward becomes a positive cost with the anchor's `direction` lower; the kit
  refuses a non-positive anchor.
- `rewards` carries only `reward`; the raw metric, capped `ratio` (the leaderboard number),
  `match_lock_ok`, `anchor_recorded`, probe counts ride in the verdict's `metrics`.

## Kit vendoring rule

`shared/robotics_grader/` is the **canonical** verify-stage kit (stdlib-only). Every task
carries a **byte-identical vendored copy** at `verify/robotics_grader/`; CI diffs each copy
against the canonical one and fails the pull request on a difference. Nothing re-vendors a
task after it lands, so the copy a maintainer validated is the copy that scores. Edit
`shared/` and copy — never edit a vendored copy in place. Self-test:
`cd shared && python3 -m robotics_grader.selftest`.

## Contributing: two channels, one pull request

- **Channel A**: the contributor forks, puts the folder at `tasks/<platform>/<slug>/` and
  opens the pull request. **Channel B**: the contributor uploads the zipped folder on the
  website's Contribute page; the server prechecks it (the engine's real `ale lint` plus the
  domain checks) and a bot opens a pull request of the same shape on a `contrib/<id>` branch,
  then shows the contributor the link. Both land in the same place and get the same CI
  (`.github/workflows/pr-checks.yml`: no secrets, so fork pull requests run it). No staging
  area, no promote step, no public mirror: the repository is public.
- Maintainer flow: read `verify/` and `oracle/`, check out the branch, re-run `ale lint` +
  `ale validate` from `vendor/ale`, fix the anchor on the branch if the measurement
  disagrees, merge, then export the registry (`scripts/export_registry.py`) and install it
  in the website (`node scripts/sync-benchmark.mjs`), commit `identity-history.json`, and
  record the merge in the website's review panel for an uploaded contribution.
- `scripts/build_template_zip.py`: the Contribute page's download — `templates/task/` as
  `agentic-robotics-task-template/` plus, **next to** the folder (the engine's top-level
  whitelist forbids extras inside it): `GETTING-STARTED.md` and `TEMPLATE-SOURCE.json`.
  Nothing else ships. Deterministic, refuses a dirty template tree. The guide is engine-first,
  at most 90 lines, written for humans (no em dashes, short sentences), and ends with the two
  submission channels. No local stand-in runtime and no authoring-session capture (hooks,
  vendored packages, agent-harness config) may ever ship beside the folder again (owner
  decisions 2026-09-05 and 2026-09-07). Rebuild the website's copy
  (`apps/web/public/task-template/`) plus its `SOURCE.json` from a clean commit after any
  template change.

## The bridge to the leaderboard

- `harness/export_run.py` (`ale-export`): packs `ale run` / `ale validate` episode records
  (`runs/<run_id>/<episode>/{lock,result,verification}.json` + opaque members) into an
  `ale-engine-run/v1` bundle (tar → zstd → age via `harness/bundle_crypto.py`). Derives the
  agent tuple from the locks, redacts by value pattern (`harness/redact.py`), never scores,
  never reads `verify/`. Refuses mixed harnesses (`--harness` to filter) and builtin harnesses
  (`oracle`/`nop`/`scripted`) unless `--allow-builtin` (origin `smoke`).
- `scripts/export_registry.py`: `ale-robotics-task-registry/v2` from the engine's own loader
  (`spec_hash`, `content_digest`), metric/direction/anchor/full_at/cap from `verify/`,
  `platforms` from `tasks/platforms.json` (no directions, no carry-over from the website); merges
  `identity-history.json`; **refuses a dirty tree**. Runs inside the engine venv (re-execs
  itself if needed). The protocol names (`ale-engine-run/v1`, `ale-robotics-task-registry/v2`)
  and the `ALE_*` environment variables are runtime identifiers, not brand text: they did not
  change with the rename and must not be edited casually.
- Server side: `validated` is a **consistency badge** (recompute from the registry anchor);
  `verified` is a maintainer attestation after an organisation re-run. See
  `docs/security-model.md` and `docs/submission-format.md`.

## Commands

```bash
uv sync
cd vendor/ale && VIRTUAL_ENV= uv run ale lint ../../tasks && VIRTUAL_ENV= uv run ale lint ../../templates
cd vendor/ale && VIRTUAL_ENV= DOCKER_HOST=unix:///var/run/docker.sock \
    uv run ale validate <task_dir> --runs-dir /tmp/ale-runs
uv run ale-onboard <spec>.yaml --json
uv run ale-onboard --gate-only tasks/<platform>/<slug> --json
uv run python scripts/lint_domain.py .
cd shared && python3 -m robotics_grader.selftest
uv run ale-export <runs>/<run_id> --out <name>.ale-engine-run.tar.zst.age --public-key age1...
cd vendor/ale && VIRTUAL_ENV= uv run python ../../scripts/export_registry.py --tasks ../../tasks \
    --history ../../identity-history.json --out registry.json
#   then, in the website: node scripts/sync-benchmark.mjs --manifest registry.json
uv run python scripts/build_template_zip.py --out agentic-robotics-task-template.zip --source-out SOURCE.json
uv run pytest -q
```

## Discipline

- **Task data vs framework code**: `tasks/` and `templates/` are data; `harness/`, `scripts/`,
  `shared/`, `skills/` are code. Author the inputs; never hand-edit run outputs to make a gate
  pass.
- Onboarding goes through `skills/onboard-task/SKILL.md` (router → `onboard-open-loop` /
  `onboard-closed-loop` / `onboard-artifact-rollout`); `tests/test_skills.py` asserts the
  skills carry no retired vocabulary.
- Anti-cheat is **architectural isolation, not detection**: the grader is unreachable by the
  agent, seeds live only in grader memory, nothing the agent prints is a scoring input. The
  probe lock (closed_loop: the same step `t` is queried twice, true and perturbed observation
  in random order with no marker on the wire, only the true reply is applied) and
  re-simulation (open_loop) sit on top of that; they do not replace it.
- No secrets in the repo (`.env`, tokens, keys). No hardware-dependent scored metric. No
  100 MB+ blobs in git.
- `tasks/uav/uav_hover` (renamed from `tasks/control/drone_hover` on 2026-10-12) is a dummy
  task that frontier models complete easily. It stays only to show what a task's file
  structure looks like (owner decision 2026-10-12; it replaces "delete it with the first
  merged community task"). Its `task.yaml` and `verify/verify.py` still carry three comments
  from the old brand name ("ALE Robotics domain policy").

## Gotchas (details in PROGRESS.md)

- `uv venv --python 3.12` puts its managed interpreter under `/root/.local/share/uv`, which
  the agent account cannot traverse → the venv silently fails for the solver UID with a
  misleading `ModuleNotFoundError`. Set `UV_PYTHON_INSTALL_DIR=/opt/uv-python` and keep the
  Dockerfile's agent-UID import self-check. **Rule: the agent UID must traverse (not only
  read) everything the venv points at.**
- The engine stages `ale_verify` into whatever answers `python3` and refuses < 3.12.
- `ale validate` does not keep a stage's stderr — write what the grader says into a
  collected artifact.
- A null anchor is not an error; it is `needs_anchor` with the measured value. The engine
  prints `FAIL <task>: untouched={...}, oracle={'reward': 0.0}` (exit 2) and names the
  code, `oracle_not_full`, in the run's `validation.json`; read the records.
- When auditing isolation, mount permissions must match a real run, otherwise you measure
  your own audit tool.
- Two docker contexts on the dev machine → always `DOCKER_HOST=unix:///var/run/docker.sock`.
- Judge a background command by its log tail and container state, not the notification's
  exit code. Kill long runs by `pgrep`-selected pid, never a broad `pkill -f`.
- `export_registry.py` refuses a dirty tree on purpose: `content_digest` covers every regular
  file, including ignored `__pycache__/`. `--allow-dirty` is for throwaway exports only: it
  stamps `provenance.source_commit` `<sha>-dirty` + `dirty: true` and never writes the
  canonical `identity-history.json`.
