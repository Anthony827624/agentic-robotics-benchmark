# Submission format — the `ale-engine-run/v1` bundle

A leaderboard submission is a set of **engine episode records**, packed by `ale-export`
(`harness/export_run.py`) into the tar → zstd → age envelope the website decrypts. The
exporter is deliberately thin: it never scores, never reads `verify/` or the task source,
never imports the engine, and derives every manifest field from the engine's own files, so
nothing in the manifest can be typed by hand and later disagree with the members the
server re-checks. Design record: [`dev/20260902-engine-run-submission.md`](dev/20260902-engine-run-submission.md)
§3, §5, §7.

The legacy family `ale-run-manifest/v1` (produced by the archived pre-ALE harness) is still
accepted by the website; its documentation lives with that harness in the archived
`ale-robotics-benchmark-private` repository (`docs/dev/archive/legacy-openset/`). Nothing
produces it any more.

## 1. Bundle layout

```
run_manifest.json                              # written by the exporter
episodes/<episode_id>/lock.json                # engine files, value-redacted
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
      "episode_id": "uav_hover-1a2b3c4d",
      "task_name": "uav_hover", "variant": "base",
      "spec_hash": "sha256:…", "content_digest": "sha256:…",
      "status": "completed",
      "rewards": {"reward": 0.83},
      "metrics": {"ratio": 0.91, "mean_episode_reward": 430.1, "match_lock_ok": 1.0, "anchor_recorded": 1.0},
      "members": {"lock.json": "sha256:…", "result.json": "sha256:…", "verification.json": "sha256:…", "trajectory.json": "sha256:…"}
    },
    {"episode_id": "uav_hover-9f8e7d6c", "task_name": "uav_hover", "variant": "base", "status": "interrupted", "members": {}}
  ]
}
```

- `challenge` is the saved response of `POST /api/v1/challenges`, embedded verbatim (extra
  fields such as `expires_at` included); `null` → `self_hosted_offline`. `submission_id` is
  the challenge's id, or a random UUID for offline bundles.
- `agent` is **derived from the locks** (`lock.agent.{harness,model,version,family}`), never
  typed by hand, and must be identical across all packed episodes.
- `members` lists exactly the members that were packed, with the sha256 of the **stored**
  bytes. Stored JSON members are re-serialised after redaction (`indent=2`, sorted keys), so
  hash what is in the bundle, never the engine's original file.
- `rewards` is present only when the engine wrote rewards (`status == completed`); `metrics`
  is always present (possibly `{}`).
- `interrupted` entries come from the ledger (a non-terminal row with no `result.json`, e.g.
  a Ctrl-C mid-episode); they carry no members and are shown, never scored.

## 2. Exporter usage

```
ale-export RUN_DIR_OR_EPISODE_DIR... --out <name>.ale-engine-run.tar.zst.age \
           [--public-key age1…|env:VAR|path] [--challenge-file challenge.json] \
           [--harness claude-code] [--allow-builtin] [--plaintext]
```

| flag | meaning |
|---|---|
| `paths` | each is an episode dir (`lock.json` + `result.json`) or a run dir, enumerated through `ledger.db` opened **read-only** (`file:…?mode=ro`); `validation.json` is ignored |
| `--out` | bundle path; the receipt lands next to it as `<out>.receipt.json` |
| `--public-key` | age recipient: literal `age1…`, `env:VAR`, or a file path; default `$ALE_SUBMISSION_PUBLIC_KEY`. Fetch the leaderboard's key from `GET /api/v1/submission-key` |
| `--challenge-file` | saved `POST /api/v1/challenges` response (must carry non-empty `submission_id`, `challenge_token`, `nonce`) |
| `--harness` | keep only episodes run by this harness — a bundle carries one agent; a mixed run dir without it is refused |
| `--allow-builtin` | permit `oracle` / `nop` / `scripted` episodes; stamps `submission_origin: smoke` (never scored) |
| `--plaintext` | write the bare tar.zst (tests and ingest integration only); the receipt records `encryption: none` and null `encrypted_sha256`, so it is never evidence |

Exit codes: `0` ok; `2` usage error or a refusal the design mandates (mixed agents, builtin
harness without `--allow-builtin`, unusable path or challenge file, duplicate episode ids,
missing public key); `1` a record on disk is malformed (a `completed` episode without
`verification.json`, an opaque member that does not parse) or a required member exceeds the
size cap.

Policies the exporter applies:

- **Redaction** is value-pattern only (`harness/redact.py`) over string leaves of every JSON
  member and every trace line — never key-name based, which would corrupt
  `lock.gateway.limits`. `lock.task.source.path` is rewritten to `"<local>"`.
- **Size**: optional members (`trajectory.json`, `trace.execution.jsonl`,
  `artifacts/snapshot.json`) over **32 MiB** are omitted and listed under
  `omitted_members` in the receipt; a required member over the limit aborts.
- **Smoke bundles from a validate run**: `ale validate` always mixes the `nop` and `oracle`
  harnesses, so exporting one needs `--harness oracle --allow-builtin`.

The receipt (`<out>.receipt.json`) carries `bundle_schema`, `output`, `encryption`,
`members`, `plaintext_member_hashes`, `compressed_sha256`/`compressed_bytes`,
`encrypted_sha256`/`encrypted_bytes`, `recipient_public_key`, `challenge_bound`,
`submission_id`, `submission_origin`, `agent`, `episodes[{episode_id, status}]`,
`omitted_members`, `warnings`, and its own `receipt_hash`. **`encrypted_sha256` is the
`evidence_hash` a maintainer records when attesting `verified`.**

## 3. What the server does with it

The website extracts only the whitelisted member paths, parses `run_manifest.json`,
`lock.json`, `result.json` and `verification.json`, and hashes the three opaque members
without parsing them. Then, per bundle:

| check | on failure |
|---|---|
| every stored member's sha256 == `members`; no member outside the manifest | **reject** |
| per episode `task_name/variant/spec_hash/content_digest/status/rewards/metrics` == `lock.json`/`result.json`; every `lock.agent` == `run_manifest.agent` | **reject** |
| `completed` ⇒ `verification.json` present and `completed`, rewards == criteria scores, metrics == verification metrics; otherwise no rewards and a `failure` block | **reject** |
| harness ∉ {`oracle`, `nop`, `scripted`} | not scored, capped at `received` |
| an `agent` phase exists and the id is not a `-reverify-` regrade | challenge path: **reject**; maintainer path: regrade replaces its source episode |
| `lock.task.name` is an active task; `(spec_hash, content_digest)` is a known identity | unknown → `received`; spec known but content differs → `task_content_drift` (`received`); neither → **reject** `task_unknown_version` |
| `lock.framework.commit` ∈ the release's engine commits; benchmark version matches | warning |
| recomputed `ratio` from the registry anchor/`full_at`/cap equals `metrics.ratio` (±1e-4); `rewards.reward == clamp(ratio / full_at, 0, 1)` | **reject** |
| challenge token / nonce (challenge path) | as before |

Recompute (an exact port of `robotics_grader.scoring.ratio_rewards`): `anchor_recorded == 0`
→ anchorless, not scored; metric absent → valid only as a zero verdict; `match_lock_ok != 1`
→ ratio 0; otherwise `clamp(measured / anchor, 0, cap)` (inverted for lower-is-better).

Trials: `completed` → one trial scored at `ratio`; `agent_error` / `timeout` /
`budget_exceeded` / `refused` → one trial scoring 0; `env_error` / `task_error` /
`cancelled` / `interrupted` → not a trial (`episode_infrastructure_failure`, visible, never
counted). The leaderboard aggregate uses the release's `missing_task_policy: zero`
(anti-cherry-pick). Statuses and what they prove: [`security-model.md`](security-model.md) §4.

## 4. Maintainer runbook

`verified` is a maintainer attestation, never an automatic outcome. Until a maintainer API
token exists, uploads and transitions go through the browser.

1. **Run the task from the pinned engine** (so `framework.commit` matches the registry):
   ```bash
   cd vendor/ale && VIRTUAL_ENV= DOCKER_HOST=unix:///var/run/docker.sock \
       uv run ale run ../../tasks/<platform>/<slug> --agent claude-code --runs-dir <runs>
   ```
   The agent's API key comes from the engine's `.env`; `harness/ale_onboard.py` uses the same
   environment. Never commit or print it.
2. **Export**:
   ```bash
   uv run ale-export <runs>/<run_id> --out <name>.ale-engine-run.tar.zst.age \
       --public-key "$(curl -s https://agentic-robotics-benchmark.org/api/v1/submission-key | jq -r .public_key)"
   ```
   Keep `<name>.ale-engine-run.tar.zst.age.receipt.json`.
3. **Upload** at `/submit/direct` (maintainer page; pick the release). The bundle lands
   `received` or `validated`.
4. **Review** the validation report in the admin queue and transition to `verified` with
   `method = official_rerun` and `evidence_hash` = the receipt's `encrypted_sha256`. New
   cells later? Revoke, re-upload, re-verify (a `verified` row refuses uploads with 409).

External partners follow steps 1–2 with a challenge (`POST /api/v1/challenges`, save the
response as `--challenge-file`) and upload at `/submit`; they reach `validated` at most, and a
maintainer may verify them after an organisation re-run.

## 5. Fixtures

`tests/fixtures/engine_run/` holds a real `ale validate` run of the task template (the
untouched `zero_verdict` episode and the anchorless oracle episode, copied verbatim) plus one
**synthetic** scored episode derived from the oracle one; its README says exactly what was
edited. There is no real scored `uav_hover` episode in the tree because one run takes hours.
