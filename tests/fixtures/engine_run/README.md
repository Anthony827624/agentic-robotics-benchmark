# `engine_run` fixtures — what `ale run` / `ale validate` write on disk

Material for `tests/test_export_run.py` (the `ale-export` bridge, design record
`docs/dev/20260902-engine-run-submission.md` §3). Two of the three episodes are **real engine
output**; the third is **synthetic** and says so below. None of it contains credentials (checked
with `grep -riE "sk-|token"`: the only hits are the `lock.gateway.limits.max_*_tokens: "unlimited"`
budget fields, which is exactly why the exporter redacts by value pattern and never by key name).

## `validate-5a31c559/` — a real `ale validate` run (copied verbatim)

Source: `/tmp/ale-audit-runs/validate-5a31c559`, produced on 2026-09-02 by engine commit
`019d0ee9f048c57dc4acf1491870268f42a0bfcd` (version 0.1.0) running the task template
(`templates/task`, task name `template_reach`) with `ale validate`. Files under the two
episode directories are byte-for-byte copies, including the members the exporter never packs
(`trace.transport.jsonl`, `artifacts/submission/`).

| episode | harness | what it exercises |
|---|---|---|
| `template_reach-untouched-29e7e3bb` | `nop` | the grader's `zero_verdict` path: no submission, `metrics = {anchor_recorded: 0, ratio: 0}`, no raw metric, no `trajectory.json` (no agent phase) |
| `template_reach-oracle-e6ab1c2a` | `oracle` | the *anchorless* path: raw metric present (`mean_goal_distance`), `match_lock_ok = 1`, but `anchor_recorded = 0` so `ratio = 0`; has `trajectory.json` and an agent phase |
| `template_reach-claude-code-1f2e3d4c` | — | **synthetic** interrupted episode: only a ledger row plus a directory holding one `trace.execution.jsonl` line, i.e. what a Ctrl-C mid-provision leaves behind (design §2) |

`validation.json` is the real one; the exporter ignores it.

`ledger.db` was **recreated** with Python's `sqlite3` using the engine's exact schema
(`vendor/ale/packages/ale-run/src/ale/run/ledger.py`: `runs(run_id, created_at, config_hash)`,
`episodes(episode_id, run_id, identity, task_id, variant, episode_path, status, current_phase,
started_at, updated_at, finished_at, rewards_json, failure_type, failure_message)` plus the
`episodes_run_identity` index). The two real rows were copied value-for-value from the real ledger;
the interrupted row was added by hand (`status = interrupted`, `failure_type = HostInterrupted`,
placeholder `identity`). The real ledger runs in WAL journal mode, this copy in the default
rollback mode; readers cannot tell the difference and the fixture stays a single file.

## `scored/template_reach-claude-code-5c0ded00/` — SYNTHETIC scored episode

There is **no real scored episode** to copy: the only registered task (`uav_hover`, formerly `drone_hover`) takes hours
per run. This directory is the oracle episode above with the following hand edits, so tests can
exercise the "a model harness completed a task and was scored" shape end to end:

- `lock.json`: `agent.harness = claude-code`, `agent.model = us.anthropic.claude-fable-5-1`,
  `agent.version = 2.1.240` (also `agent.authentication.validated_cli_version`); everything else,
  including `task.spec_hash`, `task.content_digest` and the image identities, is the template's
  real value and is **not registered anywhere** (a server would report the task as unknown).
- `result.json` / `verification.json`: `metrics = {anchor_recorded: 1.0, match_lock_ok: 1.0,
  ratio: 0.9, mean_goal_distance: 0.08411602560735186, probes_passed: 1240, probes_total: 1243,
  episodes_aborted: 0}`. The metric is lower-is-better, so `ratio = anchor / measured` with the
  oracle's real measured value `0.07570442304661668` taken as the anchor. `rewards.reward =
  clamp(ratio / full_at, 0, 1) = clamp(0.9 / 0.825, 0, 1) = 1.0` (the template's `full_at`);
  `verification.criteria[0].score` matches.
- `trajectory.json`: agent re-labelled and two fabricated agent steps appended after the real
  instruction step. `trace.execution.jsonl`: episode id substituted, otherwise the oracle's trace.
- `artifacts/submission/` and `trace.transport.jsonl` dropped (never packed).

Timestamps and phase durations are the oracle run's; they are not meaningful for this episode.
