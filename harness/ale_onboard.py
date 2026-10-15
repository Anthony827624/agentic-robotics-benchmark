"""ale-onboard — build an ALE-shaped robotics task and gate it on the real engine.

The orchestrator behind the onboarding skills, rebuilt on upstream ALE. It is deliberately
a thin, importable core (`onboard(...)` returns a structured `OnboardResult`) so an agent
and a contributor's CLI drive exactly the same code.

What it does:

1. checks feasibility from the spec alone, and gives up honestly if the task cannot be
   scored fairly (a hardware-dependent metric, an unknown metric direction);
2. instantiates `templates/task/` into `tasks/<platform>/<task>/`, filling in
   what the spec knows and leaving TODOs where a human or an agent must still write physics;
3. runs the REAL gates — `ale lint` on the task collections, then `ale validate` on the task (its
   untouched-zero / oracle-one double pass), both from the pinned engine in `vendor/ale`;
4. reports one of four honest outcomes.

What it never does: invent a score, invent an anchor, or call something `built` that a real
`ale validate` did not produce. `built` requires the engine's double pass to hold: an
untouched sandbox scored exactly 0 on every reward key and the oracle exactly 1 on every
reward key. The engine enforces both (`untouched_nonzero`, `oracle_not_full`; exit 2).
This tool still classifies from the run records, not from the exit status, because the
bootstrap case fails the engine's gate on purpose:

A task whose anchor is still null runs, measures, and scores 0 by design, so the engine
reports `oracle_not_full`. Here that comes back as `needs_anchor` carrying the measured
value, which is the one number the author needs and cannot get any other way.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The repository root is the domain root: it holds the task collections `tasks/` and
#: `templates/` side by side (flat layout since the 2026-09 rebrand).
DOMAIN_ROOT = REPO_ROOT
TEMPLATE_DIR = REPO_ROOT / "templates" / "task"
#: The benchmark's platform vocabulary (also read by scripts/lint_domain.py and the export).
PLATFORMS_FILE = REPO_ROOT / "tasks" / "platforms.json"
ENGINE_DIR = REPO_ROOT / "vendor" / "ale"
#: The task collections the engine lints and the name-collision scan walks. Never the
#: repository root itself: that would descend into vendor/ale and the venvs.
COLLECTION_DIRS = ("tasks", "templates")

#: A metric naming a wall-clock quantity measures the machine, not the method — two
#: contributors on different hardware would get different scores for identical work.
# Whole-token rule, mirrored from scripts/lint_domain.py (kept in sync by tests): a metric
# named after wall-clock or throughput measures the machine, not the method. Tokens are the
# alphabetic runs of the name after splitting camelCase, so `rms_error` and `timesteps`
# pass while `planning_time_ms` and `controlFrequencyHz` do not.
_HW_TOKENS = frozenset((
    "time", "runtime", "walltime", "wallclock", "latency",
    "ms", "msec", "millis", "milliseconds",
    "fps", "throughput", "frequency", "freq", "hz", "khz", "mhz", "ghz",
))
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ALPHA_RUN = re.compile(r"[A-Za-z]+")


def hw_metric_token(name: str) -> str | None:
    """The first hardware-dependent token in a metric name, or None when it has none."""
    for token in _ALPHA_RUN.findall(_CAMEL_BOUNDARY.sub("_", name)):
        if token.lower() in _HW_TOKENS:
            return token
    return None

#: Upstream's TaskId shape (ale.core.ids._SLUG). The stable `name:` in task.yaml must
#: match it, and the engine rejects a collection with two tasks of the same name — so
#: both are checked here, before a container is ever started.
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: The placeholder name the template ships with; instantiate() must replace it.
_TEMPLATE_NAME = "template_reach"


@dataclass
class OnboardResult:
    """The outcome of one onboarding attempt, JSON-serializable for the web layer."""

    status: str  # "built" | "needs_anchor" | "needs_input" | "gave_up" | "error"
    task: str
    task_dir: str | None = None
    reward: float | None = None
    ratio: float | None = None
    measured: float | None = None
    metric: str | None = None
    anchor_recorded: bool = False
    match_lock_ok: bool | None = None
    untouched_zero: bool | None = None
    engine_failures: list[str] = field(default_factory=list)
    giveup_reason: str | None = None
    detail: str | None = None
    next_steps: list[str] = field(default_factory=list)
    log_tail: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# give-up detection — an honest abort beats a task that cannot be scored
# --------------------------------------------------------------------------- #
def check_feasibility(spec: dict[str, Any]) -> str | None:
    """Return a give-up reason if the spec cannot become a fair task, else None.

    Spec-level checks only. Deeper infeasibility — a reference implementation that does
    not run, a checkpoint too large to host, a licence that forbids redistribution —
    surfaces during the build and is reported from there. Cheap refusals belong here so
    nobody spends an hour on a task that was never scoreable. Spec keys this tool does
    not know (the retired `genre` / `paper` block among them) are ignored, not refused.
    """
    metric = spec.get("metric") or {}
    name = str(metric.get("name", ""))
    if hw_metric_token(name):
        return (
            f"metric {name!r} is hardware-dependent: it measures the machine, not the "
            f"method, so two contributors would score differently for identical work. "
            f"Recast it as a pass/fail real-time threshold, or pick an outcome metric "
            f"(success_rate / reward / SPL / ATE / collision_rate)."
        )
    if str(metric.get("direction", "higher")) not in ("higher", "lower"):
        return f"metric direction {metric.get('direction')!r} must be 'higher' or 'lower'."
    return None


# --------------------------------------------------------------------------- #
# instantiate the template
# --------------------------------------------------------------------------- #
def _substitute(text: str, spec: dict[str, Any]) -> str:
    """Fill the template manifest's TODOs with what the spec knows.

    Only matters of record are filled: the task's name, its platform and a prebuilt image
    (the metric and its direction go into verify/anchor.json, see `_record_metric`). Physics,
    the environment layer and the grader body are left as they are, because inventing them is
    exactly the failure mode this pipeline exists to prevent — an agent that "finishes" a task
    by writing a plausible grader has produced a task that scores nothing.
    """
    pairs = [
        ("name: {}".format(_TEMPLATE_NAME), "name: {}".format(spec["task"])),
        ("platform: TODO", "platform: {}".format(spec.get("platform", "TODO"))),
    ]
    if spec.get("image"):
        # A fully-qualified prebuilt image ref. The engine gives a local image/Dockerfile
        # priority over a ref, so instantiate() removes the template's Dockerfile when a
        # ref is declared (see there).
        pairs.append(("image: {kind: container}",
                      "image: {{kind: container, ref: {}}}".format(_quote(spec["image"]))))
    for old, new in pairs:
        text = text.replace(old, new)
    return text


def platform_keys() -> tuple[str, ...]:
    """The benchmark's platform keys (tasks/platforms.json), in file order."""
    return tuple(str(entry["key"]) for entry in json.loads(PLATFORMS_FILE.read_text(encoding="utf-8")))


def _record_metric(anchor_path: Path, spec: dict[str, Any]) -> None:
    """Write the spec's metric name and direction into the copied verify/anchor.json.

    The anchor is the one place a task declares its metric: the grader reads `direction`
    there, and the registry export publishes both. The contributor's
    grader must report that metric. `value` stays null: only a real `ale validate` fills it.
    """
    metric = spec.get("metric") or {}
    if not metric.get("name") and not metric.get("direction"):
        return
    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    if metric.get("name"):
        # A named metric without a direction is higher-is-better, as check_feasibility reads
        # it; never inherit the template's `lower`, which belongs to its cost metric.
        anchor["metric"] = str(metric["name"])
        anchor["direction"] = str(metric.get("direction", "higher"))
    else:
        anchor["direction"] = str(metric["direction"])
    anchor_path.write_text(json.dumps(anchor, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _quote(value: Any) -> str:
    """YAML-safe scalar: quote anything that could be read as structure."""
    if value in (None, ""):
        return "TODO"
    text = str(value)
    if any(ch in text for ch in ':#{}[]&*!|>%@`"\'') or text.strip() != text:
        return json.dumps(text)
    return text


def _name_collision(repo: Path, task: str, target: Path) -> Path | None:
    """The task.yaml of a DIFFERENT folder already claiming this stable name, if any.

    The engine refuses a collection with two tasks of one name, and the template itself
    is discovered like any task — so the scan covers every collection, templates included.
    """
    import yaml

    for manifest in _manifests(repo):
        if manifest.parent == target:
            continue
        try:
            existing = yaml.safe_load(manifest.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(existing, dict) and str(existing.get("name", "")) == task:
            return manifest.parent
    return None


def collection_roots(repo: Path) -> list[Path]:
    """The task collections under ``repo`` (``tasks/`` and ``templates/``), or ``repo`` itself
    when it is a bare collection with neither (a throwaway test tree, a single folder)."""
    roots = [repo / name for name in COLLECTION_DIRS if (repo / name).is_dir()]
    return roots or [repo]


def _manifests(repo: Path) -> list[Path]:
    manifests: list[Path] = []
    for root in collection_roots(repo):
        manifests += sorted(root.rglob("task.yaml"))
    return manifests


def instantiate(spec: dict[str, Any], *, repo: Path, force: bool = False) -> Path:
    """Copy the template into `<repo>/tasks/<platform>/<task>/` and fill in what the spec knows.

    The spec's `task` becomes the manifest's stable `name`, so it must be a valid slug
    and unique across every task.yaml in the repo — checked here, because the engine's
    own rejection would otherwise surface only at gate time with a cryptic message. Its
    `platform` must be one of the benchmark's platforms (tasks/platforms.json): it names the
    folder the task lands in.
    """
    task = str(spec["task"])
    if not _SLUG.match(task):
        raise ValueError(
            f"task name {task!r} is not a valid slug (^[a-z0-9][a-z0-9_-]*$)"
        )
    platform = str(spec.get("platform") or "")
    if platform not in (keys := platform_keys()):
        raise ValueError(
            f"platform {platform!r} is not one of the benchmark's platforms "
            f"({' | '.join(keys)}; tasks/platforms.json)"
        )
    target = repo / "tasks" / platform / task
    if (claimed := _name_collision(repo, task, target)) is not None:
        raise ValueError(f"task name {task!r} is already claimed by {claimed}")
    if target.exists():
        if not force:
            raise FileExistsError(
                f"{target} already exists; pass force=True to overwrite it"
            )
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE_DIR, target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".ale-cache"))

    manifest = target / "task.yaml"
    manifest.write_text(_substitute(manifest.read_text(), spec))
    _record_metric(target / "verify" / "anchor.json", spec)
    if spec.get("image"):
        # The manifest now declares a prebuilt ref, and a local Dockerfile would win
        # over it — remove the template's so the declared image is the one that runs.
        dockerfile = target / "image" / "Dockerfile"
        if dockerfile.is_file():
            dockerfile.unlink()
    # copytree preserves modes, but a template copied through a zip or a Windows checkout may not have.
    for entry in ("setup/run.sh", "verify/run.sh", "oracle/run.sh"):
        path = target / entry
        if path.is_file():
            path.chmod(path.stat().st_mode | 0o111)
    return target


# --------------------------------------------------------------------------- #
# the real gates
# --------------------------------------------------------------------------- #
def _engine(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run one `ale` subcommand from the pinned engine.

    `VIRTUAL_ENV` is cleared because this harness runs inside its own venv and uv would
    otherwise refuse to use the engine's; the docker socket is pinned because this machine
    has two contexts and a build that lands on the other daemon is invisible to the run.
    """
    env = dict(os.environ)
    env["VIRTUAL_ENV"] = ""
    env.setdefault("DOCKER_HOST", "unix:///var/run/docker.sock")
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["uv", "run", "ale", *argv],
        cwd=str(ENGINE_DIR), env=env, capture_output=True, text=True, timeout=timeout,
    )


def lint(repo: Path, *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """The cheap gate: everything answerable from the files alone.

    Runs `ale lint` over each task collection under ``repo`` (``tasks/``, ``templates/``)
    and stops at the first failing one; the repository root is never linted as a whole,
    because the engine would walk into ``vendor/ale`` and the venvs."""
    result: subprocess.CompletedProcess[str] | None = None
    for root in collection_roots(repo):
        result = _engine(["lint", str(root)], timeout=timeout)
        if result.returncode != 0:
            return result
    assert result is not None
    return result


@dataclass
class ValidatePasses:
    """The two episodes one `ale validate` runs per task, read from its run records.

    `untouched` is the NopHarness pass (nobody touched the sandbox; every reward must be
    exactly 0) and `oracle` is the OracleHarness pass (the reference solution ran; every
    reward must be exactly 1). The engine fails validation on either miss
    (`untouched_nonzero`, `oracle_not_full`); `engine_failures` carries those codes as
    read from the run's `validation.json`. `metrics` are the oracle pass's diagnostics —
    the raw metric, the capped ratio, the lock outcomes — which the engine records but
    never gates on.
    """

    untouched: dict[str, float] | None = None
    oracle: dict[str, float] | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    untouched_failure: str | None = None
    oracle_failure: str | None = None
    engine_failures: list[str] = field(default_factory=list)


def validate(task_dir: Path, *, runs_dir: Path, timeout: int = 14400
             ) -> tuple[subprocess.CompletedProcess[str] | None, ValidatePasses | None]:
    """The real run: `ale validate`'s untouched + oracle double pass on the pinned engine.

    The engine's exit status is not the verdict: it exits 2 on any validation failure,
    including the intended null-anchor bootstrap run (`oracle_not_full`), so `run_gates`
    classifies from the run records instead. Returns `(process, passes)`. `passes` is None when the run produced no records at all
    — a broken image, a crash before any episode — which is a real outcome and is reported
    as such, never smoothed into a zero.
    """
    try:
        proc = _engine(["validate", str(task_dir), "--runs-dir", str(runs_dir)],
                       timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, None
    return proc, _read_passes(runs_dir)


def _read_passes(runs_dir: Path) -> ValidatePasses | None:
    """Read both validation episodes out of the newest validate run directory.

    The verdict comes from the framework's own `result.json` records, not from anything
    the task printed: a number a task reports about itself is not evidence.
    """
    runs = sorted(runs_dir.glob("validate-*"), key=lambda p: p.stat().st_mtime,
                  reverse=True)
    if not runs:
        return None
    passes = ValidatePasses()
    passes.engine_failures = _read_failure_codes(runs[0] / "validation.json")
    found = False
    for episode in runs[0].iterdir():
        record_path = episode / "result.json"
        if not record_path.is_file():
            continue
        try:
            record = json.loads(record_path.read_text())
        except ValueError:
            continue
        rewards = record.get("rewards")
        rewards = ({k: float(v) for k, v in rewards.items()}
                   if isinstance(rewards, dict) else None)
        failure = (record.get("failure") or {}).get("message")
        if "-untouched-" in episode.name:
            passes.untouched, passes.untouched_failure, found = rewards, failure, True
        elif "-oracle-" in episode.name:
            passes.oracle, passes.oracle_failure, found = rewards, failure, True
            metrics = record.get("metrics")
            if isinstance(metrics, dict):
                passes.metrics = {k: float(v) for k, v in metrics.items()}
    return passes if found else None


def _read_failure_codes(validation_path: Path) -> list[str]:
    """The engine's failure codes for the run (`validation.json`, `tasks[].failures[].code`).

    Diagnostic only: `oracle_not_full` is the expected reading of a null-anchor run, so
    a code never decides the outcome by itself; the episode records do.
    """
    if not validation_path.is_file():
        return []
    try:
        document = json.loads(validation_path.read_text())
    except ValueError:
        return []
    codes: list[str] = []
    for entry in document.get("tasks") or []:
        for notice in (entry.get("failures") if isinstance(entry, dict) else None) or []:
            code = notice.get("code") if isinstance(notice, dict) else None
            if code:
                codes.append(str(code))
    return codes


# --------------------------------------------------------------------------- #
# orchestrator
# --------------------------------------------------------------------------- #
#: What follows a green gate: validation proves the task runs, not that it is hard.
_DIFFICULTY_STEP = (
    "measure difficulty: run frontier models with `ale run <task_dir> --agent <harness> "
    "--model <model>` and read each verdict's `reward`; the target is roughly below 0.5. "
    "Raise the engineering or perception challenge if a model saturates it, and rule out the "
    "pinned claude-code harness's two defects first (docs/contributing-a-task.md section 7)."
)
_NEXT_STEPS = [
    "Write image/Dockerfile: the simulator and every stable dependency, final stage FROM "
    "an official ALE base, python3 >= 3.12 world-traversable — or declare a prebuilt "
    "`image: {kind: container, ref: ...}` and delete image/Dockerfile.",
    "Replace the template's environment with your simulator, imported from the image.",
    "Write oracle/run.sh: the reference method, trained/run inside the agent's own budget, "
    "leaving a self-contained /home/user/submission/.",
    "Adapt verify/verify.py to your physics, keeping the abort handling, the match lock and "
    "the scoring untouched; its `metric_name=` must be verify/anchor.json's `metric`.",
    "Record the anchor: run `ale validate`, take the measured metric off the oracle pass's "
    "result.json, write it into verify/anchor.json (with a `full_at` sized from the "
    "metric's measured seed-to-seed spread), re-run — `ale validate` fails with "
    "`oracle_not_full` until the oracle lands on reward exactly 1.0.",
]


def onboard(spec: dict[str, Any] | Path, *, repo: Path | None = None, force: bool = False,
            gate: bool = True, runs_dir: Path | None = None,
            validate_timeout: int = 14400) -> OnboardResult:
    """Instantiate a task from a spec and run the real gates.

    `gate=False` stops after instantiation — for a caller that wants the folder to hand to
    an agent, without paying for a container.
    """
    if isinstance(spec, Path):
        import yaml

        try:
            spec = yaml.safe_load(spec.read_text()) or {}
        except (OSError, Exception) as exc:  # noqa: BLE001 — a bad spec file is an outcome
            return OnboardResult(status="error", task="?",
                                 detail=f"cannot read the spec: {exc}")
    if not isinstance(spec, dict) or not spec.get("task"):
        return OnboardResult(status="error", task="?",
                             detail="the spec has no `task` name")

    task = str(spec["task"])
    repo = repo or DOMAIN_ROOT
    runs_dir = runs_dir or (REPO_ROOT / "runs")

    if reason := check_feasibility(spec):
        return OnboardResult(status="gave_up", task=task, giveup_reason=reason)

    try:
        task_dir = instantiate(spec, repo=repo, force=force)
    except (OSError, FileExistsError, KeyError, ValueError) as exc:
        return OnboardResult(status="error", task=task, detail=str(exc))

    if not gate:
        return OnboardResult(
            status="needs_input", task=task, task_dir=str(task_dir),
            next_steps=list(_NEXT_STEPS),
            detail="instantiated from the template; the environment, the oracle and the "
                   "grader's physics still have to be written.",
        )

    return run_gates(task=task, task_dir=task_dir, repo=repo, runs_dir=runs_dir,
                     validate_timeout=validate_timeout)


def run_gates(*, task: str, task_dir: Path, repo: Path, runs_dir: Path,
              validate_timeout: int = 14400) -> OnboardResult:
    """Lint, validate — and classify the honest outcome.

    Separate from `onboard` because it is also the whole job when a task folder already
    exists: an agent that has just finished writing one asks exactly this question.

    `built` means the double pass held: the untouched pass scored exactly 0 on every reward
    key and the oracle pass exactly 1 on every reward key. Both are the engine's own gates
    (`untouched_nonzero`, `oracle_not_full`). This function reads the records rather than
    the exit status because the null-anchor bootstrap run fails the engine's gate on
    purpose and must come back as `needs_anchor`, not as an error. The tolerance that lets
    an honest oracle land on 1.0 under fresh seeds is the task's `full_at`
    (verify/anchor.json), not a bar declared here.
    """
    linted = lint(repo)
    if linted.returncode != 0:
        return OnboardResult(
            status="needs_input", task=task, task_dir=str(task_dir),
            detail="`ale lint` rejected the task; fix these before anything is scored.",
            log_tail=_tail(linted.stderr or linted.stdout),
            next_steps=list(_NEXT_STEPS),
        )

    proc, passes = validate(task_dir, runs_dir=runs_dir, timeout=validate_timeout)

    if proc is None:
        return OnboardResult(
            status="needs_input", task=task, task_dir=str(task_dir),
            detail=f"`ale validate` exceeded its {validate_timeout}s deadline. If the "
                   f"oracle trains, raise `timeouts.agent` and the deadline; if it hangs, "
                   f"the solver is not answering the wire.",
            next_steps=list(_NEXT_STEPS),
        )
    if passes is None or (passes.oracle is None and passes.oracle_failure is None):
        return OnboardResult(
            status="needs_input", task=task, task_dir=str(task_dir),
            detail="`ale validate` produced no verdict — the image did not build, the "
                   "oracle did not run, or the verify stage crashed. The log tail says "
                   "which.",
            log_tail=_tail((proc.stderr or "") + (proc.stdout or "")),
            next_steps=list(_NEXT_STEPS),
        )

    metrics = passes.metrics
    oracle = passes.oracle or {}
    reward = oracle.get("reward")
    declared = _declared_metric(task_dir)
    if declared is not None and declared in metrics:
        metric, measured = declared, metrics[declared]
    else:
        metric, measured = _primary_metric(metrics)
    anchored = metrics.get("anchor_recorded", 1.0) >= 1.0
    lock_ok = metrics.get("match_lock_ok")
    untouched_zero = (passes.untouched is not None
                      and all(v == 0.0 for v in passes.untouched.values()))
    common = dict(
        task=task, task_dir=str(task_dir), reward=reward, ratio=metrics.get("ratio"),
        measured=measured, metric=metric, anchor_recorded=anchored,
        match_lock_ok=None if lock_ok is None else bool(lock_ok >= 1.0),
        untouched_zero=untouched_zero,
        engine_failures=list(passes.engine_failures),
    )

    if metrics and declared is not None and declared not in metrics:
        reported = repr(metric) if metric else "no raw metric"
        return OnboardResult(
            status="needs_input",
            detail=(f"verify/anchor.json names the metric {declared!r}, but the grader "
                    f"reported {reported}. Set `metric_name=` in verify/verify.py to the "
                    f"anchor's metric, or fix the anchor: the anchor's name is what the "
                    f"registry export publishes for this task."),
            next_steps=list(_NEXT_STEPS),
            **common,
        )
    if not anchored:
        name_it = "" if declared else f" (as `value`, with `metric`: {metric!r})"
        return OnboardResult(
            status="needs_anchor",
            detail=(f"the run measured {metric} = {measured}. Record it in "
                    f"verify/anchor.json{name_it} and re-validate; until then the oracle scores 0 "
                    f"by design and `ale validate` fails with `oracle_not_full`, because "
                    f"a task nobody has measured cannot score anybody."),
            next_steps=[_NEXT_STEPS[-1]],
            **common,
        )
    if not untouched_zero:
        return OnboardResult(
            status="needs_input",
            detail=("the untouched pass did not score an honest all-zero "
                    f"({passes.untouched_failure or passes.untouched}). The verify stage "
                    "must produce a real 0 verdict when nobody has touched the sandbox — "
                    "catch the missing submission and write `zero_verdict(...)` instead "
                    "of failing."),
            log_tail=_tail(proc.stderr or ""),
            next_steps=list(_NEXT_STEPS),
            **common,
        )
    if passes.oracle is not None and all(v == 1.0 for v in passes.oracle.values()):
        return OnboardResult(
            status="built",
            detail=f"verified: untouched all-zero and oracle all-one "
                   f"({metric} = {measured}, ratio = {metrics.get('ratio')}).",
            next_steps=[_DIFFICULTY_STEP],
            **common,
        )
    return OnboardResult(
        status="needs_input",
        detail=(f"the oracle pass did not reach an all-one verdict "
                f"({passes.oracle_failure or passes.oracle}); `ale validate` fails this as "
                f"`oracle_not_full`. A task its own reference implementation cannot pass "
                f"is broken — either the oracle is not really solving it, the anchor came "
                f"from a different setup, or `full_at` (verify/anchor.json) leaves no room "
                f"for seed-to-seed variance."),
        log_tail=_tail(proc.stderr or ""),
        next_steps=list(_NEXT_STEPS),
        **common,
    )


def _declared_metric(task_dir: Path) -> str | None:
    """The metric verify/anchor.json declares, or None when it names none (the template's `TODO`)."""
    try:
        metric = json.loads((task_dir / "verify" / "anchor.json").read_text(encoding="utf-8")).get("metric")
    except (OSError, ValueError, AttributeError):
        return None
    return metric if isinstance(metric, str) and metric not in ("", "TODO") else None


def _primary_metric(metrics: dict[str, float]) -> tuple[str | None, float | None]:
    """The task's raw metric — whichever metrics key is not framework bookkeeping."""
    reserved = {"ratio", "anchor_recorded", "match_lock_ok",
                "probes_total", "probes_passed", "episodes_aborted"}
    for name, value in metrics.items():
        if name not in reserved:
            return name, value
    return None, None


def _tail(text: str, limit: int = 2000) -> str | None:
    text = (text or "").strip()
    return text[-limit:] if text else None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="ale-onboard",
        description="Build an ALE-shaped robotics task and gate it on `ale validate`.",
    )
    parser.add_argument("spec", type=Path, nargs="?",
                        help="spec YAML (task, platform, metric, optional image)")
    parser.add_argument("--gate-only", type=Path, metavar="TASK_DIR",
                        help="skip instantiation; lint + validate an existing task folder")
    parser.add_argument("--repo", type=Path, default=DOMAIN_ROOT,
                        help="the repository root holding tasks/ and templates/ "
                             "(default: this repository)")
    parser.add_argument("--runs-dir", type=Path, default=None)
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing task folder")
    parser.add_argument("--no-gate", action="store_true",
                        help="instantiate only; do not lint or validate")
    parser.add_argument("--validate-timeout", type=int, default=14400)
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    runs_dir = args.runs_dir or (REPO_ROOT / "runs")
    if args.gate_only:
        task_dir = args.gate_only.resolve()
        result = run_gates(task=task_dir.name, task_dir=task_dir, repo=args.repo.resolve(),
                           runs_dir=runs_dir, validate_timeout=args.validate_timeout)
    elif args.spec:
        result = onboard(args.spec, repo=args.repo.resolve(), force=args.force,
                         gate=not args.no_gate, runs_dir=runs_dir,
                         validate_timeout=args.validate_timeout)
    else:
        parser.error("give a spec, or --gate-only <task_dir>")

    if args.json:
        print(result.to_json())
    else:
        print(f"[ale-onboard] {result.status}: {result.task}"
              + (f"  reward={result.reward}" if result.reward is not None else ""))
        if result.giveup_reason:
            print(f"  gave up: {result.giveup_reason}")
        if result.detail:
            print(f"  {result.detail}")
        for step in result.next_steps:
            print(f"  - {step}")
        if result.log_tail:
            print(f"  --- log tail ---\n{result.log_tail}")
    # built / needs_anchor / needs_input / gave_up are all real answers; only a crash is a
    # failure of this tool.
    return 1 if result.status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
