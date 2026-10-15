"""ale-onboard on the ALE base: honest outcomes, real gates, no fabrication.

Docker is not exercised here — the real gates need images and minutes, and they are covered
by actual `ale validate` runs (see docs/evidence/). What these cover is the decision
logic, which is where a fabricated `built` would come from: feasibility refusals, template
instantiation, verdict classification, and the bootstrap-anchor path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from harness import ale_onboard as ob


# --------------------------------------------------------------------------- #
# feasibility — refuse early rather than build something unscoreable
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "planning_time_ms", "control_frequency_hz", "throughput_fps", "inference_latency",
    "steps_per_second_fps",
])
def test_hardware_metrics_are_refused(name):
    reason = ob.check_feasibility({"metric": {"name": name, "direction": "higher"}})
    assert reason and "hardware-dependent" in reason


@pytest.mark.parametrize("name", [
    "success_rate", "collision_rate", "mean_episode_reward", "ATE", "SPL",
    "mean_goal_distance",
])
def test_outcome_metrics_are_accepted(name):
    assert ob.check_feasibility({"metric": {"name": name, "direction": "higher"}}) is None


def test_bad_metric_direction_is_refused():
    reason = ob.check_feasibility({"metric": {"name": "success_rate", "direction": "up"}})
    assert reason and "direction" in reason


def test_retired_genre_and_paper_keys_are_ignored_not_refused():
    """One kind of task (design 20260902 §8): a spec still carrying the retired genre /
    paper block is stale, not infeasible — and a missing paper block is never a reason
    to give up, because feasibility is about the metric, not about provenance prose."""
    assert ob.check_feasibility({
        "genre": "reproduction",
        "metric": {"name": "success_rate", "direction": "higher"},
        "paper": {"title": "x", "reference_repo": "  ", "year": 2009},
    }) is None
    assert ob.check_feasibility({
        "metric": {"name": "reward", "direction": "higher"},
    }) is None


# --------------------------------------------------------------------------- #
# instantiation — the ALE folder shape, metadata filled, physics left alone
# --------------------------------------------------------------------------- #
SPEC = {
    "task": "probe_task",
    "platform": "arm",
    "metric": {"name": "success_rate", "direction": "lower"},
}


def test_instantiate_writes_the_core_v1_shape(tmp_path):
    task_dir = ob.instantiate(SPEC, repo=tmp_path)
    assert task_dir == tmp_path / "tasks" / "arm" / "probe_task"
    for required in ("task.yaml", "instruction.md", "image/Dockerfile", "setup/run.sh",
                     "verify/run.sh", "verify/verify.py", "oracle/run.sh"):
        assert (task_dir / required).is_file(), required
    # the visible-material channel, staged by setup/run.sh — not a sibling of the grader
    assert (task_dir / "setup" / "payload" / "practice_grader" / "grade.py").is_file()
    # the vendored verify-stage machinery travels with the task
    assert (task_dir / "verify" / "robotics_grader" / "__init__.py").is_file()
    # nothing from a retired shape survives
    for gone in ("Dockerfile", "description.md", "grader", "example", "practice_grader",
                 "files", "kits"):
        assert not (task_dir / gone).exists(), gone


@pytest.mark.parametrize("direction", ["higher", "lower"])
def test_instantiate_sets_the_declared_metric_direction(tmp_path, direction):
    """The template's anchor ships `direction: lower` (its grader scores a cost); the spec's
    value must land in verify/anchor.json either way, never the template's default."""
    spec = dict(SPEC, metric={"name": "success_rate", "direction": direction})
    task_dir = ob.instantiate(spec, repo=tmp_path)
    anchor = json.loads((task_dir / "verify" / "anchor.json").read_text())
    assert (anchor["metric"], anchor["direction"]) == ("success_rate", direction)


def test_instantiate_fills_metadata_and_keeps_todos(tmp_path):
    task_dir = ob.instantiate(SPEC, repo=tmp_path)
    manifest = yaml.safe_load((task_dir / "task.yaml").read_text())
    assert manifest["spec_type"] == "core/v1"
    assert manifest["name"] == "probe_task"          # the stable identity, stamped
    assert manifest["image"] == {"kind": "container"}
    meta = manifest["metadata"]
    assert meta == {"platform": "arm"}          # platform is the only metadata field
    # One kind of task: the retired genre / paper block is not written.
    assert "genre" not in meta and "paper" not in meta
    # The metric and its direction go into the anchor. Its value is NOT filled: it can only
    # come from a real measurement.
    anchor = json.loads((task_dir / "verify" / "anchor.json").read_text())
    assert (anchor["metric"], anchor["direction"]) == ("success_rate", "lower")
    assert anchor["value"] is None


def test_a_named_metric_without_direction_is_higher_is_better(tmp_path):
    """Never inherit the template anchor's `lower`: it belongs to the template's cost metric."""
    task_dir = ob.instantiate(dict(SPEC, metric={"name": "success_rate"}), repo=tmp_path)
    anchor = json.loads((task_dir / "verify" / "anchor.json").read_text())
    assert (anchor["metric"], anchor["direction"]) == ("success_rate", "higher")


def test_a_direction_without_a_metric_name_leaves_the_anchor_unnamed(tmp_path):
    """The template's anchor names no metric (`TODO`), so the lint keeps asking for one."""
    task_dir = ob.instantiate(dict(SPEC, metric={"direction": "higher"}), repo=tmp_path)
    anchor = json.loads((task_dir / "verify" / "anchor.json").read_text())
    assert (anchor["metric"], anchor["direction"]) == ("TODO", "higher")


def test_instantiate_refuses_an_unregistered_platform(tmp_path):
    with pytest.raises(ValueError, match="tasks/platforms.json"):
        ob.instantiate(dict(SPEC, platform="spacecraft"), repo=tmp_path)
    assert not (tmp_path / "tasks").exists()        # nothing written


def test_instantiate_ignores_a_stale_genre_in_the_spec(tmp_path):
    """An old spec file that still says `genre:` instantiates cleanly and the manifest
    does not grow a genre — the template has no slot for it any more."""
    stale = dict(SPEC, genre="reproduction",
                 paper={"title": "A: study", "reference_repo": "https://x/y"})
    task_dir = ob.instantiate(stale, repo=tmp_path)
    manifest = yaml.safe_load((task_dir / "task.yaml").read_text())
    assert "genre" not in manifest["metadata"]
    assert "paper" not in manifest["metadata"]
    assert "genre" not in (task_dir / "task.yaml").read_text()


def test_instantiate_with_an_image_ref_drops_the_dockerfile(tmp_path):
    task_dir = ob.instantiate(dict(SPEC, image="ghcr.io/example/env:1"), repo=tmp_path)
    manifest = yaml.safe_load((task_dir / "task.yaml").read_text())
    assert manifest["image"] == {"kind": "container", "ref": "ghcr.io/example/env:1"}
    # a local Dockerfile would win over the declared ref, so it must be gone
    assert not (task_dir / "image" / "Dockerfile").exists()


def test_instantiate_rejects_a_bad_slug(tmp_path):
    with pytest.raises(ValueError, match="slug"):
        ob.instantiate(dict(SPEC, task="Probe Task"), repo=tmp_path)


def test_instantiate_rejects_a_claimed_name(tmp_path):
    ob.instantiate(SPEC, repo=tmp_path)
    other = dict(SPEC, platform="uav")               # different folder, same name
    with pytest.raises(ValueError, match="already claimed"):
        ob.instantiate(other, repo=tmp_path)
    # force-overwriting the SAME folder is not a collision
    assert ob.instantiate(SPEC, repo=tmp_path, force=True).is_dir()


def test_instantiate_marks_the_stage_scripts_executable(tmp_path):
    task_dir = ob.instantiate(SPEC, repo=tmp_path)
    for entry in ("setup/run.sh", "verify/run.sh", "oracle/run.sh"):
        assert (task_dir / entry).stat().st_mode & 0o111, entry


def test_instantiate_refuses_to_clobber(tmp_path):
    ob.instantiate(SPEC, repo=tmp_path)
    with pytest.raises(FileExistsError):
        ob.instantiate(SPEC, repo=tmp_path)
    # force replaces it
    assert ob.instantiate(SPEC, repo=tmp_path, force=True).is_dir()


def test_onboard_gives_up_before_touching_the_disk(tmp_path):
    spec = dict(SPEC, task="hw", metric={"name": "planning_time_ms", "direction": "lower"})
    result = ob.onboard(spec, repo=tmp_path, gate=False)
    assert result.status == "gave_up"
    assert result.giveup_reason and "hardware-dependent" in result.giveup_reason
    assert not (tmp_path / "tasks").exists()      # nothing written
    assert result.reward is None and result.anchor_recorded is False


def test_onboard_without_gate_reports_the_work_left(tmp_path):
    result = ob.onboard(SPEC, repo=tmp_path, gate=False)
    assert result.status == "needs_input"
    assert result.next_steps
    assert result.reward is None                  # no score without a real run
    assert Path(result.task_dir).is_dir()


def test_onboard_rejects_a_spec_without_a_task_name(tmp_path):
    assert ob.onboard({"platform": "uav"}, repo=tmp_path, gate=False).status == "error"


def test_onboard_reports_an_unreadable_spec_file(tmp_path):
    assert ob.onboard(tmp_path / "nope.yaml", repo=tmp_path, gate=False).status == "error"


# --------------------------------------------------------------------------- #
# verdict classification — the only place a fake `built` could come from
# --------------------------------------------------------------------------- #
def _task(tmp_path, metric=None):
    # the template's grader reports mean_goal_distance, so that is the anchor's metric here
    return ob.instantiate(dict(SPEC, task="cls",
                               metric=metric or {"name": "mean_goal_distance", "direction": "lower"}),
                          repo=tmp_path)


_CLEAN_ZERO = object()  # sentinel: "the untouched pass scored an honest all-zero"


def _passes(oracle=None, untouched=_CLEAN_ZERO, metrics=None,
            oracle_failure=None, untouched_failure=None, engine_failures=()):
    return ob.ValidatePasses(
        untouched={"reward": 0.0} if untouched is _CLEAN_ZERO else untouched,
        oracle=oracle,
        metrics=metrics or {},
        oracle_failure=oracle_failure,
        untouched_failure=untouched_failure,
        engine_failures=list(engine_failures),
    )


def _gate(monkeypatch, tmp_path, passes, *, lint_rc=0, proc=True, proc_rc=0, metric=None):
    task_dir = _task(tmp_path, metric)

    class FakeLint:
        returncode = lint_rc
        stdout = "lint said so"
        stderr = "stderr tail"

    class FakeValidate:
        returncode = proc_rc            # 2 = the engine's "some validation failed"
        stdout = ""
        stderr = "stderr tail"

    monkeypatch.setattr(ob, "lint", lambda *a, **k: FakeLint())
    monkeypatch.setattr(ob, "validate",
                        lambda *a, **k: ((FakeValidate() if proc else None), passes))
    return ob.run_gates(task="cls", task_dir=task_dir, repo=tmp_path,
                        runs_dir=tmp_path / "runs")


def test_a_passing_oracle_is_built(monkeypatch, tmp_path):
    result = _gate(monkeypatch, tmp_path, _passes(
        oracle={"reward": 1.0},
        metrics={"ratio": 1.13, "mean_goal_distance": 0.094,
                 "anchor_recorded": 1.0, "match_lock_ok": 1.0},
    ))
    assert result.status == "built"
    assert result.reward == 1.0 and result.ratio == 1.13
    assert result.metric == "mean_goal_distance" and result.measured == 0.094
    assert result.match_lock_ok is True
    assert result.untouched_zero is True
    # a green gate proves the task runs, not that it is hard: the next step is the
    # frontier-model difficulty test
    assert any("ale run" in step and "0.5" in step for step in result.next_steps)


def test_a_null_anchor_is_needs_anchor_with_the_measurement(monkeypatch, tmp_path):
    """Engine >= 90a7c1c fails a null-anchor run with `oracle_not_full` (exit 2). The
    records still carry the measurement, and that, not the exit status, decides."""
    result = _gate(monkeypatch, tmp_path, _passes(
        oracle={"reward": 0.0},
        metrics={"ratio": 0.0, "mean_goal_distance": 0.1024,
                 "anchor_recorded": 0.0, "match_lock_ok": 1.0},
        engine_failures=["oracle_not_full"],
    ), lint_rc=0, proc_rc=2)
    assert result.status == "needs_anchor"
    assert result.measured == 0.1024                   # the number the author needs
    assert result.anchor_recorded is False
    assert result.engine_failures == ["oracle_not_full"]
    assert "verify/anchor.json" in result.detail and "oracle_not_full" in result.detail
    assert "`metric`" not in result.detail              # the anchor already names it


def test_an_anchor_still_naming_todo_asks_for_the_metric_name(monkeypatch, tmp_path):
    """`TODO` is the template's placeholder, not a metric: no mismatch, but ask for the name."""
    result = _gate(monkeypatch, tmp_path, _passes(
        oracle={"reward": 0.0},
        metrics={"ratio": 0.0, "mean_goal_distance": 0.1024,
                 "anchor_recorded": 0.0, "match_lock_ok": 1.0},
        engine_failures=["oracle_not_full"],
    ), proc_rc=2, metric={"direction": "lower"})
    assert result.status == "needs_anchor"
    assert "`metric`: 'mean_goal_distance'" in result.detail


def test_an_oracle_below_one_is_not_built(monkeypatch, tmp_path):
    result = _gate(monkeypatch, tmp_path, _passes(
        oracle={"reward": 0.62},
        metrics={"ratio": 0.5, "mean_goal_distance": 0.4,
                 "anchor_recorded": 1.0, "match_lock_ok": 1.0},
    ))
    assert result.status == "needs_input"
    assert result.reward == 0.62
    assert "all-one" in result.detail and "full_at" in result.detail
    # The exact-1.0 oracle is the engine's own gate (`oracle_not_full`); say so, and never
    # call it a warning or a domain rule.
    assert "oracle_not_full" in result.detail
    assert "partial_oracle" not in result.detail and "domain policy" not in result.detail


def test_a_grader_reporting_another_metric_is_needs_input(monkeypatch, tmp_path):
    """The anchor's metric is what the registry publishes for the task; the grader must report it."""
    result = _gate(monkeypatch, tmp_path, _passes(
        oracle={"reward": 1.0},
        metrics={"ratio": 1.1, "success_rate": 0.9, "anchor_recorded": 1.0, "match_lock_ok": 1.0},
    ))
    assert result.status == "needs_input"
    assert "mean_goal_distance" in result.detail and "success_rate" in result.detail
    assert "metric_name" in result.detail


def test_an_untouched_pass_that_crashed_is_not_built(monkeypatch, tmp_path):
    result = _gate(monkeypatch, tmp_path, _passes(
        oracle={"reward": 1.0},
        untouched=None, untouched_failure="verify failed with exit code 1",
        metrics={"ratio": 1.1, "mean_goal_distance": 0.09,
                 "anchor_recorded": 1.0, "match_lock_ok": 1.0},
    ))
    assert result.status == "needs_input"
    assert result.untouched_zero is False
    assert "zero_verdict" in result.detail


def test_a_failed_match_lock_is_not_built(monkeypatch, tmp_path):
    result = _gate(monkeypatch, tmp_path, _passes(
        oracle={"reward": 0.0},
        metrics={"ratio": 0.0, "mean_goal_distance": 0.73,
                 "anchor_recorded": 1.0, "match_lock_ok": 0.0},
    ))
    assert result.status == "needs_input"
    assert result.match_lock_ok is False


def test_a_failing_lint_never_reaches_validate(monkeypatch, tmp_path):
    called = []
    task_dir = _task(tmp_path)

    class Bad:
        returncode = 1
        stdout = ""
        stderr = "task.yaml: something is wrong"

    monkeypatch.setattr(ob, "lint", lambda *a, **k: Bad())
    monkeypatch.setattr(ob, "validate", lambda *a, **k: called.append(1) or (None, None))
    result = ob.run_gates(task="cls", task_dir=task_dir, repo=tmp_path,
                          runs_dir=tmp_path / "runs")
    assert result.status == "needs_input"
    assert not called                                   # no container was started
    assert "something is wrong" in (result.log_tail or "")


def test_no_verdict_is_reported_not_scored_as_zero(monkeypatch, tmp_path):
    result = _gate(monkeypatch, tmp_path, None)
    assert result.status == "needs_input"
    assert result.reward is None                        # NOT 0.0 — there was no measurement
    assert "no verdict" in result.detail


def test_a_timeout_is_its_own_message(monkeypatch, tmp_path):
    result = _gate(monkeypatch, tmp_path, None, proc=False)
    assert result.status == "needs_input"
    assert result.reward is None
    assert "deadline" in result.detail


def test_result_is_json_serializable(monkeypatch, tmp_path):
    result = _gate(monkeypatch, tmp_path, _passes(
        oracle={"reward": 1.0},
        metrics={"ratio": 1.08, "mean_goal_distance": 1.0, "anchor_recorded": 1.0},
    ))
    assert json.loads(result.to_json())["status"] == "built"


# --------------------------------------------------------------------------- #
# reading both validation passes out of a real run-directory shape
# --------------------------------------------------------------------------- #
def _write_episode(run, name, rewards, metrics=None, failure=None):
    episode = run / name
    episode.mkdir(parents=True)
    record = {"episode_id": name, "rewards": rewards, "metrics": metrics or {},
              "status": "completed" if rewards is not None else "task_error"}
    if failure:
        record["failure"] = {"error_type": "TaskError", "message": failure,
                             "phase": "verify"}
    (episode / "result.json").write_text(json.dumps(record))


def test_passes_are_read_from_the_framework_records(tmp_path):
    run = tmp_path / "validate-deadbeef"
    _write_episode(run, "cls-untouched-11111111", {"reward": 0.0},
                   {"ratio": 0.0, "anchor_recorded": 1.0})
    _write_episode(run, "cls-oracle-22222222", {"reward": 1.0},
                   {"ratio": 1.13, "mean_goal_distance": 0.094, "anchor_recorded": 1.0})
    passes = ob._read_passes(tmp_path)
    assert passes.untouched == {"reward": 0.0}
    assert passes.oracle == {"reward": 1.0}
    assert passes.metrics["ratio"] == 1.13


def test_a_task_error_pass_reads_as_failure_not_rewards(tmp_path):
    run = tmp_path / "validate-deadbeef"
    _write_episode(run, "cls-untouched-11111111", None,
                   failure="verify failed with exit code 1")
    _write_episode(run, "cls-oracle-22222222", {"reward": 1.0}, {"ratio": 1.1})
    passes = ob._read_passes(tmp_path)
    assert passes.untouched is None
    assert "exit code 1" in passes.untouched_failure


def test_the_newest_validate_run_wins(tmp_path):
    import os as _os
    old = tmp_path / "validate-00000000"
    _write_episode(old, "cls-oracle-11111111", {"reward": 0.0}, {"ratio": 0.0})
    new = tmp_path / "validate-11111111"
    _write_episode(new, "cls-oracle-22222222", {"reward": 1.0}, {"ratio": 1.0})
    _os.utime(old, (1, 1))
    passes = ob._read_passes(tmp_path)
    assert passes.oracle == {"reward": 1.0}


def test_a_missing_runs_dir_reads_as_none(tmp_path):
    assert ob._read_passes(tmp_path / "absent") is None


def test_engine_failure_codes_are_read_from_validation_json(tmp_path):
    """The engine's own verdict codes (`validation.json`) ride along as diagnostics."""
    run = tmp_path / "validate-deadbeef"
    _write_episode(run, "cls-untouched-11111111", {"reward": 0.0},
                   {"ratio": 0.0, "anchor_recorded": 0.0})
    _write_episode(run, "cls-oracle-22222222", {"reward": 0.0},
                   {"ratio": 0.0, "mean_goal_distance": 0.1, "anchor_recorded": 0.0})
    (run / "validation.json").write_text(json.dumps({
        "schema_version": 1, "run_id": "validate-deadbeef",
        "tasks": [{"name": "cls", "passed": False, "warnings": [],
                   "failures": [{"code": "oracle_not_full",
                                 "message": "oracle rewards must all be one, got {'reward': 0.0}"}]}],
    }))
    passes = ob._read_passes(tmp_path)
    assert passes.engine_failures == ["oracle_not_full"]
    assert passes.oracle == {"reward": 0.0}            # the records are still the verdict


def test_a_missing_or_broken_validation_json_is_no_failure_code(tmp_path):
    run = tmp_path / "validate-deadbeef"
    _write_episode(run, "cls-oracle-22222222", {"reward": 1.0}, {"ratio": 1.0})
    assert ob._read_passes(tmp_path).engine_failures == []
    (run / "validation.json").write_text("{not json")
    assert ob._read_passes(tmp_path).engine_failures == []
