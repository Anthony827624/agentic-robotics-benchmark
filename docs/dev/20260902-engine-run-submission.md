# Engine-native submissions: bridging `ale run` records to the leaderboard

> Design record, 2026-09-02, **v2** (v1 was reviewed adversarially from four lenses — anti-cheat,
> data model / migration, operability, upstream semantics; 44 objections were confirmed against
> the code and are folded in below). Status: **approved direction, implementation in progress.**
>
> **2026-10-15 update.** The registry export now takes `platforms` from this repository's
> `tasks/platforms.json` (no `--carry-over`, no `directions`, no `category_tree`) and reads
> `metric_name` / `metric_direction` from `verify/anchor.json`, where the metric is declared;
> `metadata.direction` and `metadata.metric` are retired. Field lists below that cite task.yaml
> metadata for direction or the metric describe the 2026-09-02 design. See PROGRESS.md §5.
>
> Owner decisions this record implements (2026-09-02):
> - **B1** — the legacy (pre-ALE) task form and its harness are *archived*, not revived. The
>   leaderboard is populated from `ale-domain/` tasks run by the upstream ALE engine.
> - **B2** — anchor visibility follows the upstream template: everything the grader needs,
>   including `verify/anchor.json`, travels *inside the task folder* and is hidden from the
>   tested agent by stage isolation (the `verify/` stage is absent while the agent works), not by
>   keeping the number secret from maintainers or from the server.
> - **B3** — "oracle scores exactly 1.0 on every reward key" is a *domain policy* enforced by
>   `harness/ale_onboard.py`; the engine only warns (`partial_oracle`). Docs say so.
> - **Narrative** — one kind of task: an agentic-robotics task (an agent develops a policy, plans,
>   writes code-as-policy, tunes a planner, …). The reproduction / open-ended genre split and the
>   faithfulness judge are retired everywhere, leaderboard included.
>
> **2026-09-07:** the project was renamed to the Agentic Robotics Benchmark and this
> repository replaced the private one with a flat, public layout. Read `ale-domain/tasks/` as
> `tasks/`, `ale-domain/templates/` as `templates/`, `ale-domain/identity-history.json` as
> `identity-history.json`. The public mirror of §6 (`publish_public.py`) is gone because
> this repository is itself public, and the contribution path is
> `20260902-contribute-download-upload.md` §11. Everything else here still describes the tree.

Related: [`../../templates/README.md`](../../templates/README.md) (task form),
[`../prompts/20260817-general-robotics-pivot.md`](../prompts/20260817-general-robotics-pivot.md)
(the pivot memo this record executes), website `docs/` for the ingest side.

## 1. Problem

The website ingests one bundle family, `ale-run-manifest/v1`, whose only producer was the legacy
`harness/run_task.py` pipeline over the legacy `tasks/<platform>/…` tree — deleted in `4b55f13`.
Nothing turns an ALE engine run (what `ale run` / `ale validate` write under
`runs/<run_id>/<episode>/`) into anything the website accepts. Separately, the server-side score
recompute needs an anchor per task and nothing seeds one, so every upload lands as `received`
(self-reported) and never reaches `validated`.

## 2. What the engine gives us (verified on engine `019d0ee`; identical at the pin `a0d3534`)

One episode directory `runs/<run_id>/<task.name>-<hex8>/`:

| file | present when | what we rely on |
|---|---|---|
| `lock.json` (RunLock) | always | `task.{name,variant,spec_hash,content_digest,source}`, `framework.{commit,version}`, `agent.{harness,model,version,family,resources_digest}`, `image.{observed_identity,prepared_identity,source}`, `resources.effective`, `sandbox`, `verification.mode`, `seed`, `config_hash`, `ale_verify.content_hash` |
| `result.json` (schema 2) | always | `episode_id`, `status`, `rewards{}` (only when `completed`), `metrics{}`, `phases[]`, `sandboxes[]`, `failure{}`, `started_at`/`finished_at` |
| `verification.json` (schema 1) | only if the verify stage produced one | `status ∈ {in_progress, completed, failed}`, `criteria[]`, `aggregates[]`, `metrics{}`, `diagnostics[]`, `failure` |
| `trajectory.json` | agent phase ran | ATIF-v1.7 agent trajectory (large text bodies are offloaded to `blobs/`) |
| `trace.execution.jsonl` | always | engine phase trace |
| `artifacts/snapshot.json` | artifacts collected | content identity of `/home/user/submission` |

Run level: `ledger.db` (`runs`, `episodes` tables — the only enumeration of episodes that also
lists interrupted ones); `validation.json` for `ale validate` only.

**Statuses.** `result.status` is `completed | agent_error | env_error | task_error | timeout |
budget_exceeded | cancelled | refused`. Only `completed` carries `rewards`; the engine keeps
failures out of its own score aggregates. A Ctrl-C mid-episode leaves a directory with neither
`result.json` nor `lock.json` (the ledger row stays non-terminal).

**Identities.** `spec_hash = content_hash(TaskSpec.model_dump())` — the *rendered* spec:
`task.yaml` fields **plus the rendered `instruction.md`, `metadata`, `variant`, and every engine
schema default**. Editing one character of `instruction.md`, adding a metadata key, or an engine
release that adds a defaulted field all change it. `content_digest` is a path-independent tree
digest of **every regular file** in the task folder (exec bit included; only `*/assets` and
`.ale-cache` excluded) — a stray `__pycache__` or `chmod` changes it, and `oracle/` is inside it.
Both come from the engine's own loader (`ale.run.tasksets.manifest.load_tasks`); we never
re-implement them. We carry an *identity history* (§4) so that honest edits do not orphan runs.

**Harness names.** `lock.agent.harness ∈ {claude-code, codex-cli, grok-build, openclaw-cli,
computer-use, oracle, nop, scripted}`; `oracle`/`nop`/`scripted` are model-free authoring
harnesses (`model: ""`). The engine's *reportability* (`RunLock.require_reportable`) only passes
for registry-sourced tasks, and the CLI resolves local paths only, so no run is reportable in the
engine's sense today; we bind identity ourselves (§5).

**Reverify.** `ale reverify` (upstream `019d0ee`) re-runs `verify/` on a retained sandbox: the
new episode copies the source trajectory, has no `agent` phase in `result.phases`, and has an id
ending in `-reverify-<6hex>`. The server must not count it as a fresh trial (§5).

## 3. Shape of the bridge

**One bundle family, two trust levels.** A thin exporter packs engine episode records into the
same tar → zstd → age envelope the website already decrypts. Who ran the episodes and who
attests to them decides the trust level, not the format (§7).

### 3.1 Bundle layout — `ale-engine-run/v1`

```
run_manifest.json                              # written by the exporter
episodes/<episode_id>/lock.json                # engine files, value-redacted (§3.2)
episodes/<episode_id>/result.json
episodes/<episode_id>/verification.json        # required iff result.status == completed
episodes/<episode_id>/trajectory.json          # optional, OPAQUE on the server
episodes/<episode_id>/trace.execution.jsonl    # optional, OPAQUE
episodes/<episode_id>/artifacts/snapshot.json  # optional, OPAQUE
```

`run_manifest.json`:

```json
{
  "manifest_version": "ale-engine-run/v1",
  "exporter": {"name": "ale-robotics-export", "version": "0.5.0"},
  "benchmark": {"name": "ale-robotics", "version": "v0.5"},
  "submission_id": "…",
  "submission_origin": "challenge_issued" | "self_hosted_offline" | "smoke",
  "challenge": {"submission_id": "…", "challenge_token": "…", "nonce": "…"} | null,
  "agent": {"harness": "claude-code", "model": "us.anthropic.claude-…", "version": "2.1.240", "family": "autonomous"},
  "engine": {"commit": "a0d3534…", "version": "0.1.0", "ale_verify_content_hash": "sha256:…"},
  "created_at": "2026-09-02T…Z",
  "episodes": [
    {
      "episode_id": "drone_hover-1a2b3c4d",
      "task_name": "drone_hover", "variant": "base",
      "spec_hash": "sha256:…", "content_digest": "sha256:…",
      "status": "completed",
      "rewards": {"reward": 0.83},
      "metrics": {"ratio": 0.91, "mean_episode_reward": 430.1, "match_lock_ok": 1.0, "anchor_recorded": 1.0},
      "members": {"lock.json": "sha256:…", "result.json": "sha256:…", "verification.json": "sha256:…", "trajectory.json": "sha256:…"}
    },
    {"episode_id": "drone_hover-9f8e7d6c", "task_name": "drone_hover", "variant": "base", "status": "interrupted", "members": {}}
  ]
}
```

- `challenge` is the saved response of `POST /api/v1/challenges`, embedded verbatim (same field
  names as the legacy exporter and the website docs); `null` → `self_hosted_offline`.
- `agent` is **derived from the locks**, never typed by hand (§3.2).
- `members` lists exactly the members that were packed, with the sha256 of the *stored* bytes.
- `interrupted` entries come from the ledger (non-terminal row, no `result.json`); they carry no
  members and are shown, not scored.

### 3.2 Exporter (this repo)

`harness/export_run.py`, console script `ale-export`:

```
ale-export RUN_DIR_OR_EPISODE_DIR... --out <name>.ale-engine-run.tar.zst.age \
           [--public-key age1…|env:VAR|path] [--challenge-file challenge.json] \
           [--harness claude-code] [--allow-builtin]
```

- Each path is an episode dir (`lock.json` + `result.json`) or a run dir. A run dir is enumerated
  through `ledger.db` opened **read-only** (`file:…?mode=ro`, never via the engine's `Ledger`
  class, which mutates rows on open); non-terminal rows (`queued` / `running` / `interrupted`)
  without `result.json` become `interrupted` entries; a terminal row without `result.json` is
  an error (a damaged run dir — the engine writes `result.json` before recording a terminal
  status).
  `validation.json` is ignored.
- `agent` = the `lock.agent.{harness,model,version,family}` tuple, which must be identical across
  all packed episodes; `--harness X` filters a mixed run dir down to one harness; otherwise mixed
  runs are refused. Episodes whose harness is `oracle`/`nop`/`scripted` are refused unless
  `--allow-builtin`, which stamps `submission_origin: "smoke"` (the server never scores those,
  §5). There is **no** `--agent-model` override.
- Redaction is **value-pattern only** (`redact.redact_str` over string leaves of lock / result /
  verification / trajectory / snapshot; never the legacy key-name redaction, which corrupts the
  token-limit fields in `lock.gateway.limits`). `lock.task.source.path` is rewritten to the
  literal `"<local>"`. Hashes in `members` are computed after redaction, on stored bytes.
- Size policy is the exporter's: optional members (`trajectory.json`, `trace.execution.jsonl`,
  `artifacts/snapshot.json`) larger than **32 MiB** are omitted and listed under
  `omitted_members` in the receipt; a required member (`lock`, `result`, `verification`) over the
  limit aborts the export. `members` is built from what was actually packed.
- Reuses the packaging core moved out of the legacy `submission.py` into `harness/bundle_crypto.py`
  (`resolve_public_key`, `tar_zst`, `encrypt_and_write`, `decrypt_bundle`); writes
  `<out>.receipt.json` (member hashes, compressed/encrypted sha256, recipient key, omissions).
- Never scores, never reads `verify/` or the task source; it packs what the engine wrote.

### 3.3 Server ingest (website)

`safe_archive.safe_extract_tar` gains a prefix rule, active whenever `allow_run_bundle` is set:
`^episodes/[A-Za-z0-9_.-]+/(lock|result|verification|trajectory)\.json$`,
`…/trace\.execution\.jsonl$`, `…/artifacts/snapshot\.json$` (existing per-member caps apply).
`process_bundle` parses `run_manifest.json` **once** and branches on its `manifest_version`
prefix: `ale-engine-run/` → `services/engine_bundle.process(...)`; `ale-run-manifest/` → the
existing legacy path. `detect_family` returns `"engine"` for the new prefix.

`engine_bundle` parses only `run_manifest.json`, `lock.json`, `result.json`, `verification.json`;
the three opaque members are hashed for `engine:member_hashes` and never `json.loads`-ed.

## 4. Registry v2: the engine is the source of task identity

`scripts/export_registry.py` (this repo) runs the pinned engine's loader
(`cd vendor/ale && uv run python -m …`, same `ENGINE_DIR` as `ale_onboard`) over
`tasks/` and emits `ale-robotics-task-registry/v2`:

```json
{
  "schema_version": "ale-robotics-task-registry/v2",
  "provenance": {"source": "engine_task_loader", "tasks_repo": "agentic-robotics-benchmark",
                 "source_commit": "…", "generated_at": "…",
                 "engine": {"commits": ["a0d3534…"], "version": "0.1.0", "ale_verify_content_hash": "sha256:…"}},
  "release": {"name": "Agentic Robotics Benchmark", "version": "v0.5", "status": "active",
              "aggregation_policy": {"type": "macro_mean", "task_set": ["drone_hover"], "score_cap": 1.5, "missing_task_policy": "zero"}},
  "tasks": [{
      "slug": "drone_hover", "title": "Drone hover: learn a hovering policy for a Crazyflie in gym-pybullet-drones",
      "platform": "uav", "direction": "control",
      "metric_name": "mean_episode_reward", "metric_label": "Mean episode reward",
      "metric_direction": "higher_is_better", "hardware_invariant": true,
      "identities": [{"spec_hash": "sha256:…", "content_digest": "sha256:…", "since": "…"}],
      "anchor": 472.07, "anchor_full_at": 0.825, "anchor_source": "oracle real run …",
      "anchor_image_identity": null, "score_cap": 1.5,
      "resources": {"cpus": 4, "memory_mb": 8192, "gpus": 0}, "timeouts": {"setup": 120, "agent": 10800, "verify": 1800},
      "status": "active"
  }],
  "platforms": [...], "directions": [...], "category_tree": {...}
}
```

Field sources, per task:
- `slug`/`platform`/`direction`/`metric_*`/`hardware_invariant`/`resources`/`timeouts` — `task.yaml`
  (`name`, `metadata.{platform,direction,metric}`, `resources`, `timeouts`).
- `title` — first `# heading` of `instruction.md`, else title-cased slug.
- `anchor`, `anchor_full_at` (default **1.0**, must lie in `(0, cap]` exactly like the kit),
  `anchor_source`, optional `anchor_image_identity` — `verify/anchor.json` (B2).
- `score_cap` — `verify/grader_config.json` `scoring_cap`, default 1.5 (the only cap source; the
  server reads it from `metadata_json`, not a literal).
- `identities[0]` — the loader's `spec_hash`/`content_digest` for the current tree; older entries
  are carried from `identity-history.json` (outside every task folder so it does not
  feed the digest). The exporter appends the previous identity automatically when either hash
  changes; a maintainer prunes entries when a change is genuinely breaking (new grader semantics,
  new anchor). Only `variant == "base"` is registered; variant runs are unknown identities.
- The exporter **refuses a dirty tree**: `git status --porcelain --ignored -- <task>` must be
  empty for every task, and `provenance.source_commit` is recorded.
- `genre`, `mode`, `paper_*` are gone (§8). `platforms`/`directions`/`category_tree` are carried
  over from the current registry verbatim (their old source — legacy folder scaffolds — is gone;
  moving the taxonomy source to `docs/tree_diagram/data` is a follow-up).

Website side:
- `sync-benchmark.mjs` keeps only `--manifest`; for a v2 manifest it **replaces** `tasks` and the
  release block (legacy tasks leave `registry.json`), carrying only `platforms`/`directions`/
  `category_tree` from the current file. The folder-scan mode is deleted.
- v2 JSON schema: `modes` and per-task `mode`/`genre`/`metric_label`/`paper_*` no longer required;
  `mode` nullable; `provenance.source` gains `engine_task_loader`; new properties `identities`,
  `anchor*`, `score_cap`, `resources`, `timeouts`, `hardware_invariant`. `validate-registry.mjs`
  guards `registry.modes ?? []`, checks `task.mode` only when present, and enforces
  *every `active` task ∈ `task_set`*. `check:schema-enums` keeps working (reads only
  `platforms[]`/`directions[]` keys). `@ale/contracts` types follow (`mode?: Mode | null`,
  `Genre` removed, `identities`/`anchor*` added).
- `seed.py`: `Task.task_config_hash = identities[0].spec_hash`; `metadata_json = {anchor,
  anchor_full_at, anchor_source, anchor_image_identity, score_cap, identities, metric_label,
  hardware_invariant, resources, timeouts}` (**flat scalars** — the router reads `anchor` as a
  number); tasks in the DB but absent from the registry are set `status = "deprecated"` (rows are
  never deleted; results may reference them); `BenchmarkRelease.engine_json = provenance.engine`.
  When the stored `aggregation_policy` differs from the registry's, every submission of the
  release is re-aggregated so stored numbers follow the policy. `seed_demo` derives its tasks
  from the registry (or is dropped) — it must not crash the container entrypoint.

## 5. Server-side validation and scoring of an engine bundle

### 5.1 Checks

Reject ids live in `engine_bundle.ENGINE_REJECT_IDS` and are OR-ed into the router's reject set;
**a `rejected` bundle writes no `TaskResult` rows.**

| check id | rule | on failure |
|---|---|---|
| `engine:member_hashes` | sha256 of every stored member == `run_manifest.episodes[].members`; no member outside the manifest | **reject** |
| `engine:manifest_consistent` | per episode `task_name/variant/spec_hash/content_digest/status/rewards/metrics` == `lock.json`/`result.json`; every `lock.agent.{harness,model,version}` == `run_manifest.agent` | **reject** |
| `engine:verification_consistent` | if `result.status == completed`: `verification.json` present, `status == completed`, `result.rewards == {name: score for criteria ∪ aggregates}`, `result.metrics == verification.metrics`. Otherwise: `rewards` absent, `failure` present, and `verification.json` absent or `status != completed` | **reject** |
| `engine:scorable_harness` | `lock.agent.harness ∉ {oracle, nop, scripted}` | not scored; status capped at `received` ("authoring harness, not a model run") |
| `engine:agent_phase_present` | `result.phases` contains `phase == "agent"` and `episode_id` does not match `-reverify-[0-9a-f]{6}$` | challenge path: **reject**; direct (maintainer) path: flagged `regrade`, replaces its source episode's trial (§5.3) |
| `engine:known_task` | `lock.task.name` is an **active** task of the release (`_load_task_contexts` filters on status) | `received` (unknown task: not scored) |
| `engine:task_identity` | `(spec_hash, content_digest)` ∈ `identities` → pass (info `task_previous_version` if not the newest). Else: spec matches a known identity but content differs → warning `task_content_drift` → `received`; content matches but spec differs → warning `engine_schema_drift` → `received`; neither → **reject** `task_unknown_version` | as stated |
| `engine:engine_known` | `lock.framework.commit ∈ release.engine_json.commits`; `"unknown"` → distinct warning `engine_commit_unresolved` | warning (expected for external self-hosted runs) |
| `engine:benchmark_version` | `_version_matches(run_manifest.benchmark.version, release.version)` | warning |
| `engine:score_matches_recompute` | see §5.2 | **reject** |
| `engine:reward_consistent` | `rewards.reward == clamp(expected_ratio / anchor_full_at, 0, 1)` within 1e-4 | **reject** |
| `challenge_*` | as today (`_verify_challenge`, embedded challenge block) | as today |
| `engine:network_blocked`, `engine:sandbox_destroyed`, `engine:image_matches_anchor_run` | informational (`resources.effective.network_mode == "block"`, `sandboxes[].outcome`, `image.observed_identity == anchor_image_identity` when recorded) | info |

No `required_task_set_hash` is carried or checked: per-episode task identity binds strictly more
tightly than a slug-list hash; `task_coverage` is computed from the tasks actually scored.

### 5.2 Recompute — an exact port of `robotics_grader.scoring.ratio_rewards`

For a `completed` episode of a known task, with `measured = metrics.get(Task.metric_name)`:

```
if metrics.get("anchor_recorded") == 0.0:      -> episode is "anchorless": not scored, status ≤ received (grader ran without an anchor)
elif measured is None:                          -> valid only if metrics.ratio == 0 and rewards.reward == 0 (zero_verdict / SolverMissing); scores 0
elif metrics.get("match_lock_ok", 1.0) != 1.0:  -> expected_ratio = 0
else:
    raw = measured / anchor                     (higher_is_better)
        = anchor / measured if measured > 0 else 0.0   (lower_is_better)
    expected_ratio = clamp(raw, 0, score_cap)
check |expected_ratio - metrics.ratio| ≤ 1e-4  else reject
```

`anchor`, `anchor_full_at`, `score_cap` come from `Task.metadata_json`. A missing `match_lock_ok`
key means the task has no lock gate (artifact-style graders) — it is *not* a failure.

### 5.3 Trials, attribution and per-task rows

- `completed` → one trial, score = `expected_ratio` (the ungated leaderboard number; `reward` is
  the gated training signal and is only checked for consistency).
- `agent_error`, `timeout`, `budget_exceeded`, `refused` → one trial scoring 0 (the agent's fault).
- `env_error`, `task_error`, `cancelled`, `interrupted` → **not a trial**; warning
  `episode_infrastructure_failure` (visible, never scored, never counted).
- A regrade (`-reverify-`) replaces its source episode (id minus the suffix) when both are present;
  the latest regrade wins.
- Per task: `measured_metric` = mean raw metric over scored trials, `normalized_score` = mean
  `expected_ratio`, `num_trials`, `stderr` over ratios, `grader_result_hash` = sha256 of the
  concatenated `verification.json` members. Bundle summary uses `missing_task_policy: exclude`
  (a bundle describes itself); the leaderboard aggregate always reads the **release** policy
  (`zero`, anti-cherry-pick) — every leaderboard-level call reads it, the per-call fallbacks are
  deleted (closes A4).
- Agent/model rows: `agent = {"name": lock.agent.harness, "version": lock.agent.version}`,
  `model = {"provider": _infer_provider(model) or lock.gateway.dialect, "name": model,
  "display_name": model}`; empty model → `None` (only builtin harnesses, never scored).
- `Submission.integrity_root` = sha256 over the sorted `members` map; `manifest_sha256` = sha256 of
  `run_manifest.json`; `trajectory_event_count` = number of episodes with a packed
  `trajectory.json`; `validation_report_json.bundle_family = "engine"`.

### 5.4 Status

Any reject check → `rejected` (no rows). All checks pass, challenge verified, anchors present,
scorable harness → `validated`. Otherwise `received`. Only a maintainer sets `verified` (§7).
An upload onto a row that is already `verified` is refused with **409** ("revoke, re-upload,
re-verify") — the badge never covers cells the organisation did not attest to; rows are
upserted only after the status transition succeeds.

### 5.5 What this proves and what it does not

The recompute catches: anchor / `full_at` / cap drift between the run's grader and the registry
(`task_identity`, `score_matches_recompute`), edits to any single member (`member_hashes`,
`manifest_consistent`, `verification_consistent`), arithmetic bugs in ported graders
(`reward_consistent`), relabelled authoring runs (`scorable_harness`, `agent_phase_present`), and
cherry-picked task subsets (release policy `zero`). It cannot catch a *consistent* rewrite of
every file: the members are plain JSON and the solver/verify image is a local build whose
identity is recorded but bound to no reference. `validated` is therefore a **consistency badge**;
authenticity comes only from a maintainer attestation after an organisation re-run (§7). The
docs say so in exactly these words.

## 6. Legacy archival (B1) and what "public" means

- **Private repo**: tag `legacy-harness-final`, then delete together (they reference only each
  other): `harness/{run_task,run_suite,grading,scoring,faithfulness,finalizer,recorders,verify,
  cost,onboard,manifest,submission}.py`, `harness/{agents,sandbox,gateway}/`, `grader_protocol/`,
  `skills/{SKILL.md,scaffold.py,templates/,specs/}`, `scripts/{promote_public,build_template_bundle,
  fetch_papers,lint_dockerfiles,lint_grader,preflight_task,regrade_submission}.py`, their tests,
  their CI steps, the `ale-run`/`ale-suite` console scripts, and the `grader_protocol` wheel
  package. Kept: `ale_onboard.py`, `trajectory.py` (canonical bytes + sha256), `integrity.py`,
  `redact.py`, and the packaging core as `bundle_crypto.py`.
- **Public repo `ale-robotics-benchmark`**: tag `legacy-v0.5-final`, then replace the tree with a
  README written from scratch (new narrative, archive pointer, the access gate below; no link to
  the dead `/docs/task-template` page) plus `tasks/<direction>/<task>/` mirrored **in full,
  `oracle/` included**, by `scripts/publish_public.py`. Rationale: the engine's loader refuses a
  folder without `oracle/run.sh`, so a mirror without it can be neither linted nor run; a stub
  oracle would change `content_digest` and cap every external run at `received`; upstream hides
  `oracle/` from the agent by stage isolation exactly as it hides `verify/`; and the legacy public
  repo already published `example/` (the reference solution) for all 12 tasks. `publish_public.py`
  runs `ale lint` on the produced mirror and asserts the mirror's `content_digest` equals the
  registry's before anything is pushed. `--withhold oracle` exists but yields a reference-only
  mirror and is documented as such.
- **The access gate, stated plainly in the public README and the website docs**: running a task
  needs the ALE engine (upstream repo is private, not on PyPI) and the ALE base image
  (`ghcr.io/agentslastexam/container-ubuntu22-base`, authenticated pull or local
  `images/build.sh`). Today both are available only to partner teams granted access by
  AgentsLastExam; the public mirror is reference material for everyone else, and the challenge
  flow serves partners. Follow-ups (§10): an installable engine distribution, public base images.
- **Website**: the `ale-run-manifest/*` ingest path stays (tested, harmless) and is documented as
  legacy; `GET /submission-key` advertises both families; the genre split goes (§8).

## 7. `verified`, and the maintainer runbook (B4)

`verified` is a **maintainer attestation**, not an automatic outcome of any upload path. It is
recorded as an append-only `VerificationEvent` with actor, `method` (`official_rerun` for an
organisation re-run; `trusted_runner` / `tee_attestation` remain for future infrastructure),
`reason`, and `evidence_hash` = the sha256 of the re-run's exported bundle (its
`<out>.receipt.json` `encrypted_sha256`), so the attestation points at concrete evidence. A
maintainer may verify an *external* submission after reproducing it; nothing binds `verified` to
the direct-upload path. Only `maintainer`/`admin` roles can transition to `verified`/`revoked`;
the system actor never can (tested today).

Runbook (until a maintainer API token exists, uploads and transitions go through the browser —
the API is cookie + CSRF only):

1. Run the task from the **pinned** engine (so `framework.commit` matches the registry):
   `cd vendor/ale && VIRTUAL_ENV= DOCKER_HOST=unix:///var/run/docker.sock uv run ale run ../../tasks/<direction>/<slug> --agent claude-code --runs-dir <runs>` (the agent's API key comes from the engine's `.env`; see `harness/ale_onboard.py` for the same environment).
2. Export: `uv run ale-export <runs>/<run_id> --out <name>.ale-engine-run.tar.zst.age --public-key "$(curl -s https://agentic-robotics-benchmark.org/api/v1/submission-key | jq -r .public_key)"`.
3. Upload at `/submit/direct` (maintainer page; pick the release). Without a bound challenge
   the bundle lands `received` (the same engine-record checks run; `validated` is only reached
   through the challenge flow, §5.4).
4. Review the validation report in the admin queue; transition to `verified` with
   `method = official_rerun` and `evidence_hash` from step 2's receipt. New cells later? Revoke,
   re-upload, re-verify (§5.4).

External partners follow the same steps 1–2 with a challenge (`POST /api/v1/challenges`, save the
response as `--challenge-file`) and upload at `/submit`; they reach `validated` at most, and a
maintainer may verify them after an organisation re-run.

## 8. Narrative

Public story: *The Agentic Robotics Benchmark is an agentic-robotics
benchmark. Each task drops an autonomous agent into a sandboxed robotics problem (develop a
control policy, plan a manipulation sequence, write code-as-policy, tune a planner, …) and scores
the artifact it leaves behind with a hidden grader on hidden seeds.*

Decision (a) on `metadata.genre`: it is **deleted** from the template, from `drone_hover`, from
`lint_domain.py`, `ale_onboard.py`, and from the website contribution precheck
(`services/contribution.py` — the paper block becomes optional metadata that nothing requires).
Consequences implemented: `Genre` and `Mode` enums, the `genre` column and CHECK,
`reproduction_score`/`open_ended_score`, the leaderboard category tabs, the faithfulness docs
page, the "paper" fields and the reproduction framing in landing copy, docs, both READMEs,
template comments, `drone_hover/instruction.md` (its "## Faithfulness" section) — all removed.
`direction` stays the taxonomy axis and can grow agentic sub-genres without a schema change.
**Because these edits change `drone_hover`'s `spec_hash` and `content_digest`, the registry is
exported after they land, and the identity history starts there.**

## 9. Website data model and migration (one revision)

- `tasks`: drop `ck_task_genre` then `genre`; `mode` → nullable (keep `ck_task_mode`; NULL passes);
  `task_config_hash` holds `spec_hash`. `downgrade()` first runs
  `UPDATE tasks SET mode='closed_loop' WHERE mode IS NULL`, then restores NOT NULL and re-adds
  `genre` with its default — CI runs `downgrade -1` **after** seeding and must stay green.
- `submissions`: drop `reproduction_score`, `open_ended_score`.
- `benchmark_releases`: add `engine_json` (JSON, nullable).
- Code: `TaskContext` gains `full_at`, `score_cap` (from `metadata_json`), `status`;
  `recompute_submission_aggregate` loses the genre split; `leaderboard.py` drops category tabs and
  filters/labels `per_task` by `Task.status`; `schemas/api.py` follows; `seed_demo` follows §4.
- Tests: `test_incremental_submission` (second task via a synthetic active row), leaderboard
  category assertions, `test_migrations` round-trip with a seeded NULL `mode`, new
  `test_engine_bundle.py` with fixtures taken from the real audit run (the untouched
  `zero_verdict` episode included), a router test that a corrupted member lands `rejected` with
  zero rows, and a 409 test for uploads onto `verified` rows.

## 10. Out of scope (tracked separately)

- Porting `drone_hover/verify` onto the vendored `robotics_grader` kit (multi-hour re-validate);
  bumping `vendor/ale` to `019d0ee` (record both commits in `engine.commits` when it happens).
- A maintainer API token for scripted `/direct` uploads and transitions.
- An installable engine distribution and anonymously pullable base images (upstream).
- Making `docs/tree_diagram/data` the source of the website taxonomy tree.
