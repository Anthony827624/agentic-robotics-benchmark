# Security model — what is hidden from the agent, and what a verdict proves

This page states what the benchmark defends against, how, and — just as carefully — what
it does not prove: the **grading trust boundary**, which keeps the grader's number
authoritative against a hostile agent inside the sandbox. The mechanics are the engine's
(`vendor/ale/docs/specs/standard-environment.md`) plus the verify-stage kit
(`shared/robotics_grader/`).

Results are the engine's own episode records. The website is a display-only front end: it
takes no uploads and no run or score submissions (2026-10-15), so the submission trust
levels this page used to describe (`validated` / `verified` badges, the bundle exporter) are
retired with the upload path.

## 1. Assets and adversaries

Assets: (1) **result integrity** — the score is what the grader computed, not what the agent
claims; (2) **hidden-protocol secrecy during the run** — the per-episode seeds, the grader,
the anchor and the oracle are unreachable from inside the sandbox while the agent works;
(3) **credential secrecy** — the model credentials stay on the host's gateway, never in the
sandbox or a task folder.

The adversary is the **tested agent**: arbitrary code running for hours as the sandbox's
agent account, and again — through the solver it leaves behind — while the grader runs. It
may try to inflate its metric, echo canned output, pre-create or overwrite the verdict, read
the grader, or predict the hidden seeds. We do **not** defend against a compromised host or a
malicious operator: a result is only as trustworthy as whoever ran the episodes (the
guidelines' Agent Benchmarking role says who runs formal evaluations and what they record).

## 2. What stage isolation hides

Anti-cheat is **architectural isolation, not detection**. The mechanism is the engine's
stage protocol; the task only has to respect it.

| barrier | what it does | who enforces it |
|---|---|---|
| **Timing** | `verify/` (grader, `env.py`, `anchor.json`, `grader_config.json`) and `oracle/` are uploaded to the sandbox only when their stage runs. During the agent phase they do not exist anywhere in the sandbox. | engine (`standard-environment.md`: "`verify/` is also absent throughout this phase") |
| **Root-only staging root** | `/opt/ale` is `drwx------ root:root`; even paths that do exist there are untraversable for the agent account. | engine |
| **UID boundary** | `verify/run.sh` runs as root; the agent's solver is spawned **at the agent's UID** (`robotics_grader.Solver`, `deprivileged`, via `runuser`). The solver cannot read the grader's memory or environment, and Yama's `ptrace_scope` blocks tracing across UIDs. Artifacts the agent produced (checkpoints, pickles) are deserialized deprivileged too. | kit |
| **Verdict path** | The reward map is written to `ALE_VERDICT_PATH`, inside the root-only tree, through `ale_verify`'s atomic write; the agent can neither pre-create nor overwrite it. The engine cross-checks the reward file against `verification.json`. | engine + kit |
| **Seeds in memory** | The grader draws hidden seeds from OS entropy inside its own process (`seed_rng`); they never touch disk. The practice grader the agent gets uses different, visible seeds and prints the raw metric only. | kit + `scripts/lint_domain.py` |
| **No self-reported numbers** | The metric is computed from the grader's own simulator state. Nothing the solver prints is parsed as a score; the wire carries observations and actions only. | kit |
| **Network** | `network.mode: block` during the agent phase; the agent cannot fetch anything, including this repository. | engine |
| **Image hygiene** | The task image may not `COPY` `verify/`, `oracle/`, the anchor or the manifest — an image layer is readable by anyone who can run the image. | `scripts/lint_domain.py` |

Real-run evidence for the first four rows: [`evidence/isolation-audit.md`](evidence/isolation-audit.md)
(`/opt/ale` unreadable, `verify/` absent, a fake verdict refused, the kit untouchable).

On top of isolation sit the **match locks**, which are detection for canned output:
closed_loop graders fire hidden perturbation probes (`ProbeLock`) and require the action to
respond, so a constant, replayed or observation-blind policy scores zero
(`match_lock_ok = 0`); open_loop graders re-simulate every answer under their own dynamics
and reject one that does not start where it claims; artifact_rollout is structurally immune
(the grader is the only thing that ever runs the artifact).

**What "hidden" means, precisely.** The anchor and the oracle are hidden from the *tested
agent during its run*, by stage isolation. They are **not** secrets from maintainers or from
the public: this repository is public and every task folder is in it
whole, `verify/anchor.json` included (the engine's loader refuses a folder without
`oracle/run.sh`, and a stub would change the task's identity). A human can read the anchor;
the agent in the sandbox cannot, and the agent is what is being measured.

## 3. What a verdict proves

The grader runs as root inside the sandbox after the agent phase, owns its simulator, draws
its seeds from OS entropy through a keyed hash (`robotics_grader.seeds`: no output predicts
another, so the probe perturbations a solver sees reveal nothing about later seeds), and
writes the verdict itself. The engine records the episode (`lock.json` with the task
identity, the engine commit and the `ale_verify` content hash; `result.json`;
`verification.json`). A result therefore says: this agent's artifact, scored by this
grader at this task identity, on seeds nobody saw in advance.

The registry (`scripts/export_registry.py`) publishes each task's identity history, so a
result can be matched to the exact committed tree it ran against.

## 4. Honest limits

- **The host.** An episode record is a recording made on whoever's machine ran it, not proof
  that it happened under controlled conditions. Trust the runs whose operator you trust.
- **Network during verify (an engine issue, not yet fixed upstream).** The engine opens egress
  before the verify stage, and a closed_loop task runs the agent's solver during that stage.
  The solver can therefore reach the internet while it is being scored, and download anything
  public, this repository's `oracle/` included. The hidden seeds never leave the grader's
  memory, but a solver that fetches the reference controller can play it. Until the engine
  keeps the verify stage offline for the solver, check formal runs' submissions and traces
  for network use.
- **The probe lock is not a proof of honesty.** It catches a policy that ignores its input;
  a solver that notices a repeated `t` can vary its answer on purpose. Isolation, not the
  lock, keeps a score honest.
- **Public anchors.** Because the repository is public and complete, the target value is
  public knowledge outside the sandbox. The agent phase runs in a blocked-network sandbox that
  does not contain it; the benchmark does not measure what a human who read the repository
  could type into an instruction.
- **A simulator the agent holds.** When a task gives the agent a copy of its simulator, the
  episode seed is the only secret: seed it with 64 bits (`rng.seed(bits=64)`) and draw every
  hidden value through a generator whose outputs reveal nothing (a keyed hash), or the first
  observations give the seed away.

## 5. Constraints the tooling must preserve

- The task image never contains `verify/`, `oracle/`, the anchor, or the manifest.
- `setup/payload/` never reads the anchor or the grader; the practice grader prints the raw
  metric only, never a ratio or score.
- `rewards` in the verdict carries only the gated `reward`; raw values ride in `metrics`.
- Credentials live in the engine's `.env` on the host, never in a task folder.
- Run outputs (`runs/`) are git-ignored.
