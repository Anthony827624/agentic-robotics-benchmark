"""Verify-stage basics: where things are, who runs them, and how a run ends.

The framework tells a stage script three things through the environment, and this module
is the only place that reads them, so a task never hardcodes a path the framework owns.
"""

import json
import os
import subprocess
import sys

__all__ = [
    "agent_user",
    "deprivileged",
    "fail",
    "require",
    "stage_dir",
    "verdict_path",
    "write_verdict",
]


def stage_dir():
    """This stage's folder inside the sandbox (root-only, under the framework's /opt/ale).

    Assets that must not exist while the agent works — the anchor, grader config, hidden
    reference data — live here, because a stage folder is copied in when its stage runs.
    """
    return os.environ.get("ALE_STAGE_DIR", os.path.dirname(os.path.abspath(sys.argv[0])))


def verdict_path():
    """Where rewards are written. The agent's UID cannot pre-create or overwrite it."""
    return os.environ["ALE_VERDICT_PATH"]


def agent_home():
    """The unprivileged account's home — the workspace, per the image's declared user."""
    return os.environ.get("ALE_HOME", "/home/user")


def agent_user():
    """The account the agent ran as, derived from its home.

    The image declares it (`ale.user`) and the framework passes the home; taking the leaf
    of the home keeps the two consistent without a second source of truth.
    """
    return os.path.basename(agent_home().rstrip("/")) or "user"


def fail(reason):
    """End the run as a TASK ERROR, not as a zero.

    The distinction is load-bearing: a zero says the agent did not solve the task, and a
    task error says the task is broken. Conflating them lets a defective grader read as a
    stream of legitimately failing agents.
    """
    print("[grader] TASK ERROR: {}".format(reason), file=sys.stderr, flush=True)
    raise SystemExit(1)


def require(condition, reason):
    """`fail` unless the condition holds — for the grader's own preconditions."""
    if not condition:
        fail(reason)


def write_verdict(verdict, path=None):
    """Write the verdict envelope the framework reads back.

    `verdict` is `{"rewards": {...}, "metrics": {...}}`. The split is load-bearing under
    `ale validate`: every key in `rewards` must be exactly 0 on an untouched sandbox
    (`untouched_nonzero` fails the run otherwise) and exactly 1 for the oracle
    (`oracle_not_full` fails it otherwise), so `rewards` carries only the gated score —
    normally the single key `reward`, in [0, 1] — and everything worth keeping but not
    gating (the raw metric, the capped ratio, lock outcomes, abort counts) goes in
    `metrics`, which the engine persists to the run record and never judges.

    When the engine's own `ale_verify` package is present (it stages it into the sandbox's
    `python3` at verify time), the same envelope is also recorded through it, because the
    engine cross-checks the reward file against the verification record and rejects a
    mismatch. Outside a sandbox — the kit's selftest, a practice run — the plain JSON
    envelope is enough.
    """
    rewards = verdict.get("rewards") or {}
    metrics = verdict.get("metrics") or {}
    require("reward" in rewards, "rewards map has no primary 'reward' key")
    require(all(0.0 <= float(v) <= 1.0 for v in rewards.values()),
            "every gated reward must lie in [0, 1]; report raw values under metrics")

    if os.environ.get("ALE_VERIFICATION_PATH"):
        try:
            from ale_verify import CheckResult, Verification
        except ImportError:
            fail("ALE_VERIFICATION_PATH is set but ale_verify is not importable — the "
                 "image's python3 is older than the engine's 3.12 floor, or the stage "
                 "is running outside the engine")
        record = Verification()
        for name, value in sorted(rewards.items()):
            record.check(name, CheckResult(float(value), "computed by the task's grader"))
        for name, value in sorted(metrics.items()):
            record.stat(name, float(value))
        record.write()   # writes ALE_VERIFICATION_PATH and ALE_VERDICT_PATH atomically
        return

    with open(path or verdict_path(), "w") as handle:
        json.dump({"rewards": {k: float(v) for k, v in rewards.items()},
                   "metrics": {k: float(v) for k, v in metrics.items()}}, handle)


def deprivileged(argv, **kwargs):
    """Run `argv` as the agent account instead of as root.

    Two callers need this and both are security-critical:

    * a live solver the agent wrote (closed_loop) — at root it could read the grader's
      memory, environment and `/opt/ale`;
    * deserializing an artifact the agent produced (artifact_rollout) — `torch.load` and
      `pickle` execute arbitrary code, so doing it as root hands over the grader.

    Yama's default `ptrace_scope` blocks tracing across UIDs, so the boundary holds even
    though both processes share a sandbox.
    """
    return subprocess.Popen(  # noqa: S603 — caller-fixed argv, no shell
        ["runuser", "-u", agent_user(), "--"] + list(argv), **kwargs
    )
