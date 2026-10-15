# Contribute = download the template, build locally, upload the zip

> Design record, 2026-09-02. Owner decision of 2026-08-18, reaffirmed 2026-09-02.
> Status: **retired** (2026-10-15). It was implemented on both sides (this repository: the
> template zip builder and the docs; the website: the upload endpoint, the landing credential,
> the Contribute page).
>
> **2026-10-15, second update: this design is retired.** The website became a display-only
> front end. It offers no template download and no upload channel, and its submission page
> links the project's contribution guidelines (two Google Docs, English and Chinese, linked
> from `CONTRIBUTING.md`). Contributors copy `templates/task/` from a checkout and open the
> pull request themselves. `scripts/build_template_zip.py` and its tests are removed. Read
> what follows as the record of the old path, not as a description of the tree.
>
> **2026-10-15 update.** The task taxonomy now lives in this repository: `metadata.platform` is
> the only metadata field, one of the fixed keys in `tasks/platforms.json`, and a task lands at
> `tasks/<platform>/<slug>/`. `metadata.direction` and `metadata.metric` are retired; the metric
> and its direction are declared in `verify/anchor.json` only. Where the text below says the
> template points at the website registry's keys, or that a task lands by direction, read the
> platform rule instead. The website side (download, precheck, landing bot) still implements the
> old form until its own update ships; meanwhile contributors open the pull request themselves.
> See PROGRESS.md §5.
>
> **2026-09-07 update, see §11.** The project was renamed to the Agentic Robotics Benchmark
> and this repository replaced the private one with a flat, public layout. Read the paths
> below as `tasks/`, `templates/task/` and `identity-history.json`; the download is
> `agentic-robotics-task-template.zip`. The staging area (`contrib-staging/`), the promote
> step and the public mirror are gone: the website's bot now opens a pull request that adds
> the task directly at `tasks/<direction>/<slug>/`, exactly like a pull request opened by
> hand, and the contributor sees the link. §§1–10 are kept as the record of how the path was
> designed and rehearsed.

Related: [`../contributing-a-task.md`](../contributing-a-task.md) (the contributor
narrative), [`20260902-engine-run-submission.md`](20260902-engine-run-submission.md) (the
task form and the leaderboard bridge), the repository's `CONTRIBUTING.md` (what happens
after the maintainers merge).

## 1. Decision

Contributing a task is a three-verb path: **download** the task template from the website,
**build** the task locally (gated with the local dry-run runtime, §9, or with the ALE
engine), **upload** the zipped task folder. The
server prechecks the archive and lands it in this private repository for maintainer review.
The contributor never opens a pull request and never needs access to this repository.

The chat / agentic onboarding path (the website dispatching a container that ran
`ale-onboard` for the contributor) is **removed**, not paused. It never worked on the public
server — the backend was deliberately left unwired and the deployment docs said never to
set the repository variable there — and it contradicts the decision above: the server must
not build or gate tasks on the contributor's behalf. What survives of that machinery is the
maintainer-side `ale-onboard` in `harness/`, which gates a folder a maintainer already has;
it is not shown to contributors.

## 2. The journey (this is also the Contribute page's copy)

1. Sign in (GitHub or Google; any user role).
2. Download `/task-template/ale-robotics-task-template.zip`, built from this repository's
   `ale-domain/templates/task/` at a pinned commit by `scripts/build_template_zip.py`. The
   zip holds the task folder as `ale-robotics-task-template/`, plus — **next to** the
   folder, not inside it, because the engine's top-level whitelist would fail lint on any
   extra file — `GETTING-STARTED.md` (the offline copy of these steps with the exact engine
   commands) and `TEMPLATE-SOURCE.json` (`repo`, `commit`, `generated_at`, `template_path`,
   `zip_folder`). Nothing else ships: the authoring-session capture tooling that sat beside
   the folder from 2026-09-02 (a vendored package, hook files for several agent harnesses
   and its config) was removed on 2026-09-07 by owner decision, as was the local dry-run
   runtime on 2026-09-05 (§9 is kept as history). The website shows the pinned commit
   next to the download button.
3. Build locally: rename the folder to the task slug; fill `task.yaml` (`name` = slug,
   `metadata.platform` / `direction` / `metric`), `instruction.md`, `image/Dockerfile` +
   `image/requirements.txt`, `setup/`, `oracle/`, `verify/`; gate it — **without the
   engine** with `python3 _tools/dryrun.py check <folder>` (§9), or **with the engine**
   (`ale lint`, `ale validate`); record the measured anchor `value` and `full_at` in
   `verify/anchor.json` (`dryrun.py oracle <folder> --record-anchor`, `--repeat 5`);
   re-gate until the oracle scores exactly 1.0 (at the time a domain rule enforced by
   `ale_onboard`; since engine 90a7c1c the engine's own gate, `oracle_not_full`). The access paragraph is stated plainly: the engine is not
   needed to contribute; it is a private upstream repository whose base image needs an
   authenticated pull, so today only partner teams run the official gate themselves, and
   the maintainers run it before merging; the dry-run has no UID isolation and runs on the
   contributor's Python (unless `--docker`), so the maintainers may re-measure the anchor.
   The upload precheck still refuses an anchor whose `value` is `null` or whose `source`
   is still `TODO`, and nothing is staged or queued from an unmeasured anchor (the promote
   gate in PR CI enforces the same rule). The guide also tells extractors that drop Unix
   permissions (Windows, Python's `zipfile`) to `chmod +x` the three stage entry points,
   which `ale lint` requires.
4. Zip the task folder (the folder itself or its contents at the zip root): ≤ 50 MB total,
   ≤ 256 files, ≤ 2 MB per file, no symlinks.
5. Upload it for review. The server prechecks (§3) and lands it. The contributor sees
   **"Staged for review"** with the PR link, or **"Queued for a maintainer"** when the
   server holds no repository credential. After merge,
   `.github/workflows/promote-on-merge.yml` + `scripts/promote_contribution.py` move the
   task to `ale-domain/tasks/<direction>/<slug>/`; it enters the registry at the next
   `scripts/export_registry.py` run.

`GETTING-STARTED.md` inside the zip carries the same five steps in the same partition
(4 = zip, 5 = upload for review; what the server does with an accepted upload is its own
section), and its step 5 lists what the precheck rejects (§3).

## 3. The website API (apps/api, owned by the website repository)

`POST /api/v1/contributions/upload` — multipart `title` (3..256) + `archive` (.zip). Same
auth as every cookie-authenticated mutation (login + `x-ale-csrf`), per-user rate limit 10/h.

Pipeline: stream to a temp file under a hard byte cap → safe zip extraction (reject
absolute paths, `..`, backslashes, symlinks via `external_attr`, more than 256 entries,
any member over 2 MB, a declared uncompressed total over the cap) → strip a single shared
top-level folder → `{relpath: bytes}` → the existing precheck in
`services/contribution.py`, extended for the template shape (required files `task.yaml`,
`instruction.md`, `image/Dockerfile` or `image.ref`, `setup/run.sh`, `oracle/run.sh`,
`verify/run.sh`, `verify/anchor.json`; `task.yaml` parses and `name` matches the folder
slug and `^[a-z0-9][a-z0-9_-]*$`; `metadata.platform` / `direction` / `metric.name` /
`metric.direction` filled (no `TODO`; metric hardware-invariant); anchor `value` > 0 (or a
`metrics` map) with a `source` that is the contributor's own measurement (not `TODO`, not
a paper's figure); `full_at` in (0, scoring_cap] with the cap from
`verify/grader_config.json` (1.5 by default — the same reading as the kit,
`scripts/lint_domain.py` and `scripts/promote_contribution.py`); the Dockerfile linted
textually; no path escapes, path alphabet `[A-Za-z0-9._-/]`; slug unique; **no** genre /
paper fields; `metadata.platform`/`direction` in the slug alphabet — they become folder
names) → land via `services/github_app.py` → persist a `Contribution` row (kind `task`,
status `pr_open` or `pr_pending`; the snapshot is stored **once**, on the
`ContributionRevision` row, head = latest revision) → 201 with the precheck report and
status. The PR URL is maintainer-only (review queue); submitters see the status. Precheck
failure: 422 with the report, nothing stored. Archive violations: 413 / 422 with a plain
message. Quotas: 10 uploads/hour and at most 5 contributions under review per user; two
uploads are processed at a time per API process (503 + Retry-After when saturated).

The server does not run `ale lint` or `ale validate` (see §6).

## 4. The landing credential

`services/github_app.py` lands the folder under `contrib-staging/tasks/<slug>/` on a bot
branch `contrib/<public_id>` and opens a pull request against the configured base branch.
Every landing (first upload and revisions alike) is one git-data commit (blobs → one tree
replacing the staging folder → one commit → ref update; executable bits from the zip are
kept as mode 100755), so a half-failed landing is retried by re-landing, and a PR is opened
only when none is open for the branch. Network or GitHub failures never raise: the upload
lands `pr_pending` and the archive is kept server-side for a maintainer.
Two credential modes, both needing `ALE_GITHUB_CONTRIBUTION_REPO` (+ base branch):

- **Token mode** — `ALE_GITHUB_TOKEN` (a fine-grained PAT on this repository with
  Contents: read/write and Pull requests: read/write) is used as the bearer for the REST
  calls. This is the one variable an operator sets to enable landing.
- **GitHub App mode** — the existing `ALE_GITHUB_APP_*` JWT / installation exchange.

`docker-compose.yml` passes these through from the host `.env`; `.env.example` documents
the exact PAT permissions.

## 5. No credential: the maintainer fallback

When neither credential is configured, the validated zip is kept server-side under a
persistent volume (`contribution_store_dir`, compose volume `contribution-store`) as
`<public_id>.zip` with a JSON sidecar (title, slug, user, precheck report, sha256), the
contribution lands as `pr_pending` with the message "Queued for a maintainer — the server
has no repository credential", and `GET /api/v1/admin/contributions/{public_id}/archive`
(maintainer role) downloads it. A maintainer then stages it by hand (unzip into
`contrib-staging/tasks/<slug>/`, open the PR) and the promote bot takes over. When a
credential exists, nothing is stored.

## 6. Out of scope

- **Automatic `ale validate` on the server.** The server has neither the engine nor Docker
  nor the base image, and a validating server would be exactly the container-dispatch path
  this record removes. Correctness is vouched for by the named maintainer who re-runs
  `ale validate` before merging; the server enforces shape and limits only.
- Re-running the domain policy lint (`scripts/lint_domain.py`) on upload — it runs in this
  repository's PR CI on the staged folder instead.
- Any change to the task form, the scoring kit, or the promote bot.

## 7. What lives where

| piece | repository | path |
|---|---|---|
| template zip builder | this one | `scripts/build_template_zip.py`, `tests/test_build_template_zip.py` |
| local dry-run runtime (`_tools/dryrun.py` in the zip) | this one | `scripts/task_dryrun.py`, `tests/test_task_dryrun.py`; kit hooks in `shared/robotics_grader/stage.py` |
| contributor narrative | this one | `docs/contributing-a-task.md` |
| after merge | this one | nothing moves; `CONTRIBUTING.md` (maintainer flow), `scripts/export_registry.py` |
| upload endpoint, precheck, landing, store | website | `apps/api/app/{routers/contributions.py, services/{contribution,github_app,safe_archive}.py}` |
| Contribute page, profile statuses | website | `apps/web/src/app/contribute/`, `apps/web/src/components/contribute/ContributeFlow.tsx`, `apps/web/src/components/profile/ContributionHistory.tsx` |
| the download itself | website | `apps/web/public/task-template/agentic-robotics-task-template.zip` + `SOURCE.json` (read by `apps/web/src/lib/task-template-source.ts`: `commit` for the label, `sha256` as the integrity check for a downloaded copy) |

Refreshing the download after a template change: build from a clean tree, writing the zip
and its `SOURCE.json` in one go —

```bash
uv run python scripts/build_template_zip.py \
    --out <website>/apps/web/public/task-template/agentic-robotics-task-template.zip \
    --source-out <website>/apps/web/public/task-template/SOURCE.json
```

`SOURCE.json` = the zip's `TEMPLATE-SOURCE.json` + `sha256` of the zip as written (the
page reads both; copying `TEMPLATE-SOURCE.json` by hand would drop the hash). Then let the
website's gates run. The zip is the committed tree by construction (members and modes from
`git ls-files -s`), so a rebuild of the same commit with `--generated-at` pinned is
byte-identical.

## 8. Rehearsal 2026-09-02

A full dress rehearsal of the path above, run by several agents in sequence and then fixed
in this repository (this section) and in the website (its own record).

**What was exercised.** Download the template zip from the Contribute page → unzip, rename
to `pendulum_swingup_control`, build a real closed_loop task from it (own simulator, own
oracle, own practice grader) → `ale lint` + `ale validate` through the pinned engine (null
anchor → measured value → anchor recorded → oracle exactly 1.0) → zip the task folder →
upload → server precheck → `contrib-staging/tasks/<slug>/` bot PR. A second agent then
solved the landed task through the engine as a real harness; a red team attacked the wire
and the grader; a maintainer promoted the task
with `scripts/promote_contribution.py`.

**What changed here (the tasks-repository share of the findings).**

- *Probe wire, scoring integrity.* The old wire marked probes (`"probe": true`) and the
  instruction said so, so a canned solver could pass the match lock by reacting only to
  flagged requests. Now every `act` request is exactly `{"type": "act", "t": N, "obs": ...}`
  with `t` the grader-owned step index (0 after each `reset`) and never a probe key; a
  probed step is asked twice with the same `t` — real and perturbed, in an order drawn
  from the hidden RNG — and only the real reply is applied (`ProbeLock.probe`,
  `Solver.act(t, obs)`, `READY_TIMEOUT_FACTOR`). The accidental `{"obs": {"obs": [...]}}`
  nesting is gone: `obs` is the observation itself. The instruction states the launch cwd,
  the READY (120 s) and per-step (30 s) deadlines, the repeated-`t` rule, that the reward
  saturates at the reference's level, and the environment inventory. The practice grader
  speaks the real wire (reader-thread deadlines, repeated-`t` queries with a perturbed
  copy, warns when the answer never changes). Kit selftest covers the wire byte for byte;
  `tests/test_template_task.py` pins the template. `ale validate` on the template with
  the measured anchor: `ok  untouched={'reward': 0.0}, oracle={'reward': 1.0}`,
  `match_lock_ok 1.0`, `full_at 0.8` unchanged.
- *Re-vendoring at promote.* `scripts/promote_contribution.py` now writes the canonical
  `shared/robotics_grader/` over the landed task's `verify/robotics_grader/` (and says so);
  `--check` notes a differing staged copy. `.github/workflows/pr-checks.yml` diffs staged
  copies too and **fails the job** on a difference: promote would land something other than
  what the maintainer validated, so the copy must be reconciled (one `cp -r`) and
  re-validated before merge.
- *Template hygiene.* `task.yaml` lists the registry's platform keys verbatim and points
  at `directions[]`; no contributor-facing file names `harness/ale_onboard.py`;
  `setup/run.sh` stages everything under `payload/` without naming files;
  `verify/env.py` and `setup/payload/template_env.py` are byte-identical under test.
- *Lint.* `scripts/lint_domain.py` matches hardware-metric words as whole tokens
  (`rms_error`, `rmse`, `items_total`, `timesteps_to_goal` pass; `planning_time_ms`,
  `control_frequency_hz`, `fps` still fail). The copies of that regex in
  `harness/ale_onboard.py` and the website precheck are still substring-based (hand-off).
- *GETTING-STARTED / contributing-a-task.md.* Exact engine invocation including
  `cd vendor/ale`, `VIRTUAL_ENV=`, `DOCKER_HOST`, `--runs-dir`; the one-liner that reads the
  measured metric off `<runs>/validate-*/<slug>-oracle-*/result.json` and where the grader's
  stderr went (`trace.execution.jsonl`, verify-phase `command_finished` event); zip ONLY the
  task folder (the download root is rejected on layout); `scripts/lint_domain.py` as the
  maintainers' extra battery; payload renames need no script edit; the probe wording.

**Known environment blocker (dev machine, not the pipeline).** `ufw` on the host drops
traffic from Docker's bridge networks to ports bound on the host. The engine's model
gateway and egress proxy bind `0.0.0.0` on an *ephemeral* port (`Gateway(port=0)`,
`EgressProxy(port=0)`) and a sandbox reaches them at its bridge's gateway address, so with
the firewall up every harness run fails to reach its model while `ale validate` still
passes — the oracle path never dials the gateway. The fix needs root, so it is a
suggestion, untested here (`sudo` prompts for a password in the agent session):

```bash
# Docker's default address pool is 172.16.0.0/12 (docker0 = 172.17.0.0/16; the engine's
# per-episode ale-net-* bridges and ale-agent-net were 172.19-172.20.0.0/16 on this machine).
# The port is ephemeral, so scope the rule by source subnet, not by port.
sudo ufw allow in from 172.16.0.0/12 to any proto tcp \
    comment 'ALE engine gateway/proxy: docker sandbox bridges -> host'
sudo ufw reload && sudo ufw status numbered
# verify from a sandbox: the bridge gateway is `ip route | awk '/default/ {print $3}'`
# inside the container; a TCP connect to <gateway>:<port printed by the engine> must succeed.
# To tighten: pin Docker's pool ("default-address-pools" in /etc/docker/daemon.json) and
# narrow the rule to that subnet.
```

## 9. Local dry-run runtime (2026-09-05)

**Why.** §2 step 3 and §6 left a gap: the engine and its base image are private, so a
contributor without partner access could author a task but never see a verdict, record an
anchor or size `full_at` — and the precheck refuses an unmeasured anchor. The gap is
closed without putting the engine anywhere new: a task folder is self-contained, and the
kit already reads every path it needs from the environment (`ALE_HOME`, `ALE_STAGE_DIR`,
`ALE_VERDICT_PATH`, `ALE_VERIFICATION_PATH`).

**What.** `scripts/task_dryrun.py` (stdlib-only, Python 3.10+, bash; no import from this
repository) ships in the download as `_tools/dryrun.py`. It drives the
task's own `setup/run.sh`, `oracle/run.sh` and `verify/run.sh` in the engine's order with
the engine's environment contract and reads back the verdict the grader wrote — the same
grader code, never a re-implementation of scoring:

- `untouched <task_dir>` — a fresh agent home with an EMPTY `submission/`; setup, then
  verify; every reward key must be exactly 0.
- `oracle <task_dir> [--record-anchor] [--repeat N]` — the oracle is staged into the home
  as `.ale-oracle/` (as the engine's `oracle_dir`) and run between setup and verify; prints
  the raw metric (`metadata.metric.name`, else the anchor's `metric`, else the first
  non-bookkeeping key), ratio, reward, probes, aborts. `--record-anchor` writes the measured
  value (median over `--repeat N`) into `verify/anchor.json` when `value` is null and, if
  `source` is empty/`TODO`, sets it to "dry-run oracle measurement on <date>; re-measured
  by the maintainers with the engine before merge"; `--repeat N` prints the raw and ratio
  min/median/max and a `full_at` suggestion (worst ratio rounded down to 0.05).
- `grade <task_dir> <submission_dir>` — copies the folder, whatever it holds, to
  `$ALE_HOME/submission` and grades it with the task's own `verify/`. The runtime makes no
  assumption about the submission's shape: a controller behind `run.sh`, a plan file, a
  URDF, generated code — the grader decides what counts (an empty folder is the untouched
  pass and says so). Named `policy` until 2026-09-05; that spelling is accepted as a hidden
  alias (not in `--help`) so older notes still run.
- `check <task_dir>` — untouched then oracle; exit 0 only when untouched is all-zero and
  the oracle exactly 1.0 on every key; one-screen summary ("ready to zip and upload" or
  what is missing).

Run roots live under `<task_dir>/../.dryrun/<run-id>/` (or `--workdir`; cleaned unless
`--keep`; `.dryrun/` is gitignored here); the Python
is `--python <exe>` or a venv at `<task_dir>/../.dryrun/venv` created from
`image/requirements.txt` (uv when present, else pip). `--docker` builds the task image
(`ale-dryrun/<slug>`) when `docker` and the Dockerfile's base image are present locally and
runs the passes inside one container — setup/verify as root, the solver as `user`, stage
copies under a root-only `/opt/ale-dryrun/` — otherwise it says why it fell back to host
mode. The overall `--timeout` (default 3600 s) is the runner's only deadline; per-step
deadlines stay the kit's business. Exit codes: 0 pass, 1 fail, 2 usage.

**Kit changes (`shared/robotics_grader/stage.py`, vendored byte-identically).**
`deprivileged()` runs argv directly when `os.geteuid() != 0` — the UID boundary exists in
the engine's sandbox; a non-root dry-run has nothing to drop, and the root path is
unchanged. `write_verdict()` with `ALE_VERIFICATION_PATH` set but no importable
`ale_verify` still fails as a task error — unless `ALE_DRYRUN=1`, in which case it writes a
plain verification record (`{"status": "completed", "criteria": [{"name", "score",
"source": "check"}...], "metrics": {...}, "dryrun": true}`) there and the verdict envelope
as usual. Both paths are covered by the kit selftest.

**Template.** `image/requirements.txt` is the one list of Python dependencies; the
Dockerfile `COPY`s it and `uv pip install -r`s it (engine lint and `ale validate` stay
green: 2026-09-05 run `WARN template_reach: untouched={'reward': 0.0}, oracle={'reward':
0.0}`, the expected null-anchor result, measured `mean_goal_distance` 0.0796).

**Measured on the dev box (2026-09-05).** Host mode: the whole 60-episode oracle verify of
the template takes about 0.8 s; `check` on a fresh unzip (venv creation with uv included)
under 10 s; `--docker check` 6.5 s including the image build. Host-mode oracle ratios over
3 runs against a dry-run-recorded anchor: 0.86 / 0.89 / 0.96 (spread x1.12), in line with
the template's engine-measured band (~0.86 worst over 12 runs, `full_at 0.8`).

**Wording (owner critique, 2026-09-05).** The runtime and every contributor-facing text
around it speak of a *submission* or an *artifact*, never of "running a policy": the
engine's contract is artifact-general (the agent leaves ANY artifact in
`/home/user/submission/`; `verify/` scores it however the author decides; `oracle/` is the
reference artifact — a hand-made file is fine), and many robotics tasks (design, TAMP,
code-as-policy, pure agentic work) have no learned policy in them. `GETTING-STARTED.md`
opens with "What a task can be": the four shapes — (a) a controller / agent process on the
stdio wire, (b) a plan or planner executed by verify, (c) a design artifact verify loads and
measures, (d) any program or produced file with a deterministic check — plus two ≤ 15-line
verify skeletons built only from the kit's real helpers (`stage_config`, `read_anchor`,
`ratio_rewards`, `zero_verdict`, `write_verdict`, `seed_rng`, `deprivileged`). The guide
is capped at 175 lines (`tests/test_build_template_zip.py`) and carries no "policy" at all.

**Known limits.** No UID isolation on the host (the grader and the submission's code run as
the same user), so the dry-run is a functional gate, not a security one; `ale lint` is not
replicated (the engine's static rules run at review); the template's `setup/run.sh` does
`chown -R "$AGENT_USER:$AGENT_USER"`, which needs a group named after the current user on
the host (the Linux user-private-group default; macOS users may need `--docker` or a local
edit to `chown -R "$AGENT_USER:"`). Windows needs WSL (bash).


## 10. 2026-09-06: the engine is public; the local dry-run is gone

AgentsLastExam/ale became public on 2026-09-06 (no license file yet; not on PyPI). Contributors
now install the engine itself (`git clone … && just bootstrap`), build the base image once with
`images/build.sh container-ubuntu22`, and run `ale lint` / `ale validate` — so §9's dry-run
runtime was removed the same day, together with the kit's dry-run-only branches. Engine main
(90a7c1c, our new `vendor/ale` pin) fails validation unless every oracle reward equals one
(`oracle_not_full`); the anchor bootstrap loop is unchanged because `result.json` still carries
the measured metric. The upstream specs `docs/specs/task-quality-standard.md` and
`docs/specs/task-authoring.md` are the authority for task shape; our docs keep only the
robotics deltas. The server precheck runs the real `ale lint` (website change of the same day).

## 11. 2026-09-07: one public repository, two channels, no staging

The project became the **Agentic Robotics Benchmark** and the private task repository plus
the public mirror were replaced by this single public repository with a fresh history and a
flat layout (`tasks/<direction>/<slug>/`, `templates/task/`, `identity-history.json`,
`docs/evidence/`). Consequences for the contribution path:

- **Two channels, one result.** A contributor either opens the pull request by hand (fork,
  copy the folder to `tasks/<direction>/<slug>/`, push, open) or uploads the zipped folder
  on the website; the server prechecks it as before and its bot opens a pull request of the
  same shape on a `contrib/<id>` branch, committing the folder at the final path (the
  direction comes from `metadata.direction`, which the precheck already holds to the
  registry's keys and the slug alphabet). The contributor now sees the pull request link:
  the repository is public, so there is nothing to hide.
- **Retired:** `contrib-staging/`, `scripts/promote_contribution.py`,
  `.github/workflows/promote-on-merge.yml`, `scripts/publish_public.py` and their tests.
  `scripts/lint_domain.py` gained a placement battery (folder name == `name`, parent folder
  == `metadata.direction`, exactly two levels under `tasks/`), which is what used to be
  promote's `--check`. Nothing re-vendors the grader kit after merge; CI fails a pull request
  whose `verify/robotics_grader/` differs from `shared/`.
- **CI** (`.github/workflows/pr-checks.yml`) runs the same set on every pull request, fork or
  bot: `ale lint` over `tasks/` and `templates/`, the domain lint, the kit comparison, the
  registry identity cross-check (a slug the history does not know is a notice, not a
  failure, so a new task's pull request goes green) and the tests. No secrets.
- **Maintainer flow:** read `verify/` and `oracle/`, re-run `ale validate` on the branch,
  correct the anchor there if needed, merge, export the registry, install it in the website,
  commit `identity-history.json`, record the merge in the website's review panel for an
  uploaded contribution.
- **Download:** `agentic-robotics-task-template.zip` unzips to
  `agentic-robotics-task-template/` + `GETTING-STARTED.md` + `TEMPLATE-SOURCE.json`; the
  guide (≤ 90 lines) ends with the two channels and the new domain
  `agentic-robotics-benchmark.org`.
