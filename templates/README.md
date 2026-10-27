# The task template

`task/` is a complete robotics task. It passes `ale lint` and `ale validate` as shipped,
except that its anchor is null, so the oracle scores 0 until a measured value is recorded.
Copy it from a checkout of this repository, rename it, and change one thing at a time from
a state you know is green.

The folder contract is upstream's:
https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-authoring.md (the engine is
pinned at `vendor/ale`). This file says what each piece in the template is for and what to
change.

This README lives outside `task/` on purpose. The engine admits only `task.yaml`,
`instruction.md`, `image/`, `setup/`, `verify/`, `oracle/`, `tools/` and `.ale-cache` at a
task's top level, and the template must itself lint clean.

## The files

```
task/
├── task.yaml            strict core/v1; the placeholder `name` is replaced at instantiation;
│                        metadata{platform}. No admission bar in here.
├── instruction.md       the prompt. The only thing the tested agent is told.
├── image/Dockerfile     the environment: final stage FROM an official ALE base, py3.12
│   │                    /opt/venv, agent-UID import self-check
│   └── requirements.txt the one list of Python dependencies; the Dockerfile installs it
├── setup/
│   ├── run.sh           trusted root, before the agent: stages everything under payload/
│   │                    into the agent home (no file names in the script)
│   └── payload/
│       ├── template_env.py    the agent's own copy of the environment (byte-identical to
│       │                      verify/env.py; a test holds the two together)
│       └── practice_grader/   a self-test on visible seeds; prints the raw metric, never a score
├── oracle/run.sh        produces the reference artifact; `ale validate` runs it in place of
│                        the agent, as the same unprivileged user, under the same timeouts
└── verify/              the grader. Absent while the agent works; staged root-only at verify time.
    ├── run.sh             `exec python3 verify.py`
    ├── verify.py          computes the metric under the grader's own physics, writes the verdict
    ├── env.py             the authoritative environment copy the grader imports
    ├── anchor.json        the metric and its direction, the hidden denominator, the saturation point
    ├── grader_config.json grader knobs: episodes, probe_rate, step_timeout_s, scoring_cap
    └── robotics_grader/   the vendored kit (canonical copy: ../shared/; byte-equality in CI)
```

The template does not use the upstream `assets/` directories or `tools/`. Add them if your
task needs them; the authoring spec says how.

## What the engine provides at verify time

The engine stages its own `ale_verify` package into the sandbox's `python3` before
`verify/run.sh` runs. Tasks neither declare nor vendor it. It needs Python 3.12 or newer,
and the engine checks whatever interpreter answers `python3`; an older one fails every
verify stage before the grader starts. That is why the image's venv is 3.12.

Everything else the grader needs travels inside `verify/`. The vendored `robotics_grader`
package is the shared machinery: the solver wire and agent-UID spawn (`Solver`), hidden
seeds (`seed_rng`), the probe lock (`ProbeLock`), ratio scoring (`ratio_rewards`), the
honest zero (`zero_verdict`) and verdict writing (`write_verdict`, which records through
`ale_verify`; the engine cross-checks the reward file against the verification record and
rejects a mismatch).

Names the engine reserves inside the verify stage folder; never ship your own: `config.json`
(this is why the grader's knobs live in `grader_config.json`), `params.json`,
`parameters.json`, `instruction.md`, `trajectory.json`, `agent-judge.jsonl`,
`rewards.json`, `verification.json`.

## The closed_loop wire

`Solver` speaks one JSON object per line to the agent's `run.sh`, launched as `bash run.sh`
with cwd `/home/user/submission` at the agent's UID. `READY` must arrive within
`READY_TIMEOUT_FACTOR` (4) step deadlines of launch, 120 s with the template's
`step_timeout_s: 30`, and every reply within one step deadline:

```
{"type": "reset"}                        -> {"ok": true}
{"type": "act", "t": 0, "obs": <json>}   -> {"action": <json>}
{"type": "close"}                        -> {"ok": true}, then exit
```

`t` is the step index inside the episode, owned by the grader's loop and restarting at 0
after each `reset`. Nothing on the wire marks a probe. `ProbeLock.probe(solver, t, obs)`
asks a probed step twice with the same `t`, the real observation and a perturbed copy in a
hidden order, applies only the real reply and compares the two. The instruction tells the
agent exactly this, so an honest solver needs no special case. The lock catches a policy
that ignores its input; it is not a proof of honesty (a solver can notice a repeated `t`
and vary its answer on purpose), and isolation, not the lock, keeps the score honest. The
practice grader speaks the same wire, deadlines and repeated-`t` queries included.

After an aborted episode (a missed deadline, a crash, a malformed or missing action) the
next `reset` stops the solver's whole process group and launches `run.sh` again, which must
print `READY` again: a late reply left on the wire must not answer the next request. After
`max_restarts` (3) restarts, or a restart that fails, every remaining episode aborts at
once. Say so in the instruction, and charge an aborted episode a real cost (the template
charges what standing still would cost), never drop it: a dropped episode lets a weak
policy abort its bad starts and average only its good ones.

## How validation gates

`ale validate` runs the task twice.

1. Untouched: nobody touches the sandbox. `verify/` must write a real verdict of exactly 0
   on every reward key; `untouched_nonzero` fails the run otherwise. A crash fails it too,
   which is why the grader catches `SolverMissing` and writes `zero_verdict(...)`.
2. Oracle: `oracle/run.sh` runs in place of the agent. `verify/` must then score exactly 1
   on every reward key; `oracle_not_full` fails the run otherwise.

There is no per-task bar to declare. Seed-to-seed variance is absorbed in scoring:

```
ratio  = clamp(measured / anchor, 0, cap)     # inverted for lower-is-better; cap = 1.5
reward = clamp(ratio / full_at, 0, 1)         # the single gated reward key
```

`anchor` (`verify/anchor.json` `value`) is the reference implementation's own measured
value from a real `ale validate` run, never a published number, and never on an
agent-readable path. It starts null: the first run measures, reports the value in the
oracle record's `metrics`, scores 0 and fails with `oracle_not_full`. That is the
bootstrap, not a broken task.

`full_at` (same file) is where the reward saturates to 1.0, in ratio units: the worst ratio
an honest on-reference run lands on under fresh hidden seeds. Size it from a measured
spread. This template measured a 1.16x spread over 12 independent 60-episode runs (1.43x at
10 episodes, which made the gate a coin flip), so the worst honest ratio is about 0.86 and
`full_at: 0.8` clears it with margin. Too high and an honest oracle fails its own gate by
luck; too low and a mediocre solution reads as perfect.

Everything that is not the gated reward (the raw metric, the capped `ratio`, probe and
abort counts) goes in the verdict's `metrics`, which the engine records in `result.json`
and never gates on.

## What a task can be

The agent leaves any artifact in `/home/user/submission/`; `verify/` scores it however the
author decides, on hidden seeds, after the agent is gone; `oracle/` is the reference
artifact that scores full. Four shapes that fit:

- a controller or agent process queried over the stdio wire (what this template ships);
- a plan or planner that verify executes in a simulator (success rate, path cost);
- a design artifact (URDF, mesh, parameters, generated code) that verify loads and measures;
- any program or produced file with a deterministic check.

Two minimal `verify.py` skeletons for the non-wire shapes. `ratio_rewards` turns one
measured value into the verdict, `zero_verdict` is the honest zero an absent or unusable
submission earns, `write_verdict` hands the verdict to the engine. A planner program runs
through `deprivileged([...])`, never as root.

```python
# read a file and score: a design artifact, or any produced file with a deterministic check
import os
from robotics_grader import ratio_rewards, read_anchor, stage_config, write_verdict, zero_verdict
config, (anchor, meta) = stage_config(), read_anchor()          # grader_config.json, anchor.json
path = os.path.join(os.environ.get("ALE_HOME", "/home/user"), "submission", "arm.urdf")
if not os.path.isfile(path):                                     # absent or unusable: an honest 0
    write_verdict(zero_verdict(reason="no arm.urdf", anchored=anchor is not None)); raise SystemExit
measured = reachable_volume(path)                                # your check, in your simulator
write_verdict(ratio_rewards(measured, anchor, cap=config.get("scoring_cap", 1.5), metric_name="reachable_volume",
                            higher_is_better=meta.get("direction", "higher") == "higher", full_at=meta.get("full_at", 1.0)))
```

```python
# run a plan on hidden seeds and score
import json, os
from robotics_grader import ratio_rewards, read_anchor, seed_rng, stage_config, write_verdict, zero_verdict
config, rng, (anchor, meta) = stage_config(), seed_rng(), read_anchor()
path = os.path.join(os.environ.get("ALE_HOME", "/home/user"), "submission", "plan.json")
if not os.path.isfile(path):
    write_verdict(zero_verdict(reason="no plan.json", anchored=anchor is not None)); raise SystemExit
episodes = int(config.get("episodes", 20))
succeeded = sum(execute(json.load(open(path)), seed=rng.seed()) for _ in range(episodes))  # your simulator
write_verdict(ratio_rewards(succeeded / episodes, anchor, cap=config.get("scoring_cap", 1.5), higher_is_better=True,
                            metric_name="success_rate", full_at=meta.get("full_at", 1.0)))
```

## The three verify patterns

One template; the pattern is an authoring choice inside `verify.py`, not a manifest field.

| Pattern | The agent produces | verify.py does |
|---|---|---|
| closed_loop (as shipped) | a controller process behind a stdio wire | owns the simulator, steps the controller at the agent's UID by step index `t`, asks probed steps twice with the same `t` (real and perturbed, hidden order; nothing on the wire marks the probe) |
| open_loop | answers, plans or designs on disk from the agent phase | reads them and re-simulates or measures under its own dynamics; rejects answers that do not start where they claim |
| artifact_rollout | a frozen checkpoint, a planner program, generated code | loads or runs it inside `deprivileged(...)` (deserializing or executing as root hands the grader over), rolls it out on hidden seeds |

## Turning it into your task

1. Name. `task.yaml`'s `name` must be a collection-unique slug (`^[a-z0-9][a-z0-9_-]*$`).
   Do it first: it is the Task Name registered in the project's sheet, and once the task sits
   under `tasks/` the domain lint checks that the folder carries the same name. `metadata.platform`
   is one of the benchmark's platforms (`tasks/platforms.json`, listed in the manifest's
   comment) and names the `tasks/<platform>/` folder the task lands in. A new key needs a
   maintainer.
2. Image. Make `image/Dockerfile` your environment: the simulator and every stable
   dependency, final stage FROM an official ALE base; Python packages go in
   `image/requirements.txt`. Keep the py3.12 `/opt/venv` with a world-traversable
   interpreter and the agent-UID import self-check at the end. A prebuilt stack can instead
   declare `image: {kind: container, ref: ...}` and delete the Dockerfile. Large agent-visible
   files (photo sets, scans, meshes) are fetched at build time from the benchmark's Hugging
   Face dataset, not committed: [`docs/task-assets.md`](../docs/task-assets.md).
3. Environment. Replace `verify/env.py` with your simulator, imported from the image, and
   `setup/payload/template_env.py` with whatever dev material the agent gets. While both
   files exist they must stay byte-identical (`tests/test_template_task.py`). `setup/run.sh`
   stages every payload file without naming it, so renames need no script edit.
4. Oracle. `oracle/run.sh` leaves the reference artifact in `/home/user/submission/`: run
   the real reference method (train here if the method trains, inside the agent-phase
   budget), or copy in a hand-made file. Whatever scores full under your `verify/`.
5. Grader. Make `verify/verify.py` score your artifact. The shipped file drives a controller
   over the wire (adapt `run_episodes` to your physics); for a design file, read it and
   measure; for a plan, execute it on hidden seeds. Keep what must not vary: an absent or
   unusable submission is `zero_verdict`, never a crash; the abort handling and the probe
   lock when there is a wire; the kit's scoring. Leave the vendored `robotics_grader/`
   untouched. CI diffs it against the canonical copy and fails the pull request on a difference.
6. Instruction. Describe the deliverable (what to leave in `/home/user/submission/`) and the
   grading protocol. The anchor is what stays hidden, not the protocol.
7. Anchor. Set `metric` (the metric key your grader reports) and `direction` (`higher` or
   `lower`). Leave `value` null and run `ale validate`. It fails (`validation.json` says
   `oracle_not_full`); read the measured value off the oracle record
   (`<runs>/validate-*/<slug>-oracle-*/result.json`, key `metrics`), write it in, run
   several more times to see the spread, set `full_at`, and repeat until validate prints
   `ok  ... untouched={'reward': 0.0}, oracle={'reward': 1.0}`.

The domain lint (`scripts/lint_domain.py`) enforces what the engine cannot know:
hardware-invariant metrics, a positive metric scale, `full_at` presence, agent-visible-surface
isolation, image hygiene, anchor provenance, a registered platform with its
`tasks/<platform>/` placement, and the cheap static core/v1 completeness checks.
