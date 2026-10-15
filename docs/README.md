# docs/

The task form itself is documented next to the template, in
[`templates/README.md`](../templates/README.md) (the contract, file by file). The pages here
cover everything around it.

- [`architecture.md`](architecture.md) — how the engine runs a task (stages, stage
  isolation, the UID boundary), the artifact + verdict envelope, the scoring model, and the
  bridge from engine runs to the leaderboard in one diagram.
- [`security-model.md`](security-model.md) — the threat model, what stage isolation hides
  from the tested agent, what the server's `validated` badge proves and what only a
  maintainer's `verified` attestation does.
- [`submission-format.md`](submission-format.md) — the `ale-engine-run/v1` bundle written by
  `ale-export`, its `run_manifest.json`, the server-side checks, and the maintainer runbook.
- [`contributing-a-task.md`](contributing-a-task.md) — the contributor's path in detail:
  template → local gate → a pull request (opened by hand, or by the website's bot after an
  upload); metric discipline, the anchor bootstrap, what the maintainers do. The short version
  is the repository's [`CONTRIBUTING.md`](../CONTRIBUTING.md).
- [`evidence/`](evidence/) — real `ale validate` verdicts and the isolation audit.
- [`dev/`](dev/) — design records (the current tree follows
  [`dev/20260902-engine-run-submission.md`](dev/20260902-engine-run-submission.md) and
  [`dev/20260902-contribute-download-upload.md`](dev/20260902-contribute-download-upload.md);
  [`dev/registry-export.md`](dev/registry-export.md) documents `scripts/export_registry.py`).
  The pre-ALE task form and the pre-rebrand repositories are archived on GitHub
  (`ale-robotics-benchmark-private`, tag `legacy-harness-final`; `ale-robotics-benchmark`,
  tag `legacy-v0.5-final`); nothing here revives them.
- [`research/`](research/) — the agentic-robotics survey.
