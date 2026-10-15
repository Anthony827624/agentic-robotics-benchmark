# Exporting the task registry (`ale-robotics-task-registry/v2`)

`scripts/export_registry.py` turns the committed `tasks/` tree into `registry.json`, the
document this repository publishes and the website displays. Its contract is
`schemas/task-registry.schema.json`, owned here; the tests hold the export to it, so change
both in one commit. Task identity — `spec_hash` and `content_digest` — comes from the pinned
engine's own loader, so a run's `lock.json` can be matched against exactly what the engine
computes; nothing here re-implements them.

## Run it

From the repository root, with a clean tree (every file under each task folder committed,
nothing ignored lying around — `content_digest` covers every regular file):

```bash
cd vendor/ale && VIRTUAL_ENV= uv run python ../../scripts/export_registry.py \
    --tasks ../../tasks \
    --history ../../identity-history.json \
    --out /tmp/registry.json
```

`uv run python scripts/export_registry.py ...` from this repo's own venv also works: the
script notices `ale` is not importable and re-executes itself via `uv run --project vendor/ale`.

Then commit `identity-history.json` here. The website installs the export with its own sync
script, validating it against this repository's schema; it only displays what it is given.
The document carries the benchmark's own taxonomy: `platforms` is `tasks/platforms.json`
verbatim and there are no directions.

| flag | meaning |
|---|---|
| `--tasks` | task collection root (default `tasks/`) |
| `--history` | identity history to merge and write back (default `identity-history.json`; with `--allow-dirty` the default file is read but never written) |
| `--release-name` / `--release-version` | release block (defaults `Agentic Robotics Benchmark` / `v0.5`) |
| `--allow-dirty` | skip the clean-tree check — throwaway exports and tests only. The document is stamped `provenance.source_commit: <sha>-dirty` and `provenance.dirty: true`, and the canonical history file is never written (pass `--history <elsewhere>` for a throwaway copy) |
| `--out` | output path (2-space JSON, sorted keys, trailing newline) |

## Where each field comes from

- `slug`, `platform` (refused unless it is a key of `tasks/platforms.json`), `resources`,
  `timeouts`: `task.yaml` via the loader.
- `metric_name`, `metric_direction`, `metric_label`: `verify/anchor.json` keys `metric` and
  `direction` (`higher|lower` becomes `higher_is_better|lower_is_better`; the label is the
  metric name with underscores as spaces).
- `platforms` (top level): `tasks/platforms.json`, `[{key, label}]` in file order.
- `title`: first `# ` heading of `instruction.md`, else the title-cased slug.
- `anchor`, `anchor_full_at` (default 1.0, must lie in `(0, score_cap]`), `anchor_source`,
  `anchor_image_identity`: `verify/anchor.json` keys `value`, `full_at`, `source`,
  `image_identity`. A `null` value is the authoring state and is passed through.
- `score_cap`: `verify/grader_config.json` `scoring_cap`, default 1.5.
- `identities`: newest first; `[0]` is the loader's current pair, older entries come from
  the history file.
- `release.aggregation_policy.task_set`: every exported (active) slug. Only the `base`
  variant is exported.
- `provenance.engine`: `vendor/ale` commit, `ale-run` package version, and the
  `ale_verify` content hash the engine stamps into every `lock.json`.

## The identity history

`identity-history.json` is a flat, newest-first list of
`{"slug", "spec_hash", "content_digest", "since"}` records. It lives outside every task
folder on purpose: inside one it would feed the very digest it records. On each export, if
either hash of a task differs from its newest record, the new pair is prepended with
`since = generated_at`; a pair appears at most once (a revert drops the older duplicate so the
current identity is always `[0]`), and nothing else is removed automatically. When a change is
genuinely breaking (new grader semantics, a new anchor), prune the stale records by hand so that
results recorded against the old task no longer match a registered identity. The file is
validated on load (a JSON list of objects with string `slug`, `spec_hash`, `content_digest`,
`since`, the hashes `sha256:<64 hex>` and `since` an ISO-8601 UTC timestamp, as the schema
requires); a typo made while pruning is reported as `error: ...`, not a traceback.

CI (`engine-lint` job) re-exports the tree into throwaway paths, validates the document against
`schemas/task-registry.schema.json`, and checks that every task the history knows has
`identities[0]` equal to its first committed record, so a tree that drifted from its registered
identity is caught before results are recorded against it. A slug the history does not know yet
is a new task arriving by pull request: CI prints a notice, and the maintainer's export after the
merge records it. `provenance.tasks_repo` is the `origin` remote's `owner/name`: export from a
clone whose `origin` is the canonical repository, not a fork.
