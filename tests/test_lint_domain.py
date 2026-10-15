"""scripts/lint_domain.py — the domain's policy lint, exercised on throwaway task folders.

The interesting property after the 2026-09-02 narrative change is what the lint no longer
asks for: `metadata.genre` and the paper block are gone (one kind of task, design §8), and a
task without them must lint clean. The batteries that stay — hardware-invariant metric,
positive anchor, `full_at`, anchor provenance, visible-surface isolation — are pinned so the
genre removal cannot be mistaken for a general loosening. The placement battery (2026-09-07,
flat layout; the parent folder is `metadata.platform` since 2026-10-12:
`tasks/<platform>/<slug>/`) replaces the retired staging-area promote check. On 2026-10-15
`metadata.direction` and `metadata.metric` were retired: the metric and its direction live in
verify/anchor.json only, and `metadata.platform` must be one of tasks/platforms.json.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "lint_domain", REPO_ROOT / "scripts" / "lint_domain.py")
lint_domain = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_domain"] = lint_domain
_SPEC.loader.exec_module(lint_domain)  # type: ignore[union-attr]

_MANIFEST = """\
spec_type: core/v1
name: {name}
image: {{kind: container}}
resources: {{cpus: 2, memory_mb: 4096}}
network: {{mode: block}}
timeouts: {{setup: 120, agent: 3600, verify: 900}}
artifacts: [/home/user/submission]
metadata:
  platform: {platform}
{extra}"""

_ANCHOR = {"metric": "mean_episode_reward", "direction": "higher", "value": 472.07, "full_at": 0.825,
           "source": "oracle/run.sh real graded run — NOT the paper's printed figure"}


def _task(tmp_path: Path, *, name: str = "probe", metric: str = "mean_episode_reward",
          extra_metadata: str = "", anchor: dict | None = None,
          platform: str = "uav") -> Path:
    """A minimal core/v1 task folder that the lint accepts as shipped (`metric` lands in the
    anchor, the one place a task declares it)."""
    task = tmp_path / "tasks" / platform / name
    (task / "image").mkdir(parents=True)
    (task / "verify").mkdir()
    (task / "setup" / "payload" / "practice_grader").mkdir(parents=True)
    (task / "task.yaml").write_text(_MANIFEST.format(name=name, platform=platform,
                                                     extra=extra_metadata))
    (task / "image" / "Dockerfile").write_text(
        "FROM ghcr.io/agentslastexam/container-ubuntu22-base:latest\n")
    (task / "verify" / "anchor.json").write_text(
        json.dumps(dict(_ANCHOR, metric=metric) if anchor is None else anchor))
    (task / "setup" / "payload" / "practice_grader" / "grade.py").write_text(
        "print({'mean_episode_reward': 1.0})\n")
    return task


def test_a_task_without_genre_or_paper_lints_clean(tmp_path):
    assert lint_domain.lint_task(_task(tmp_path)) == []


def test_a_task_still_carrying_genre_is_not_flagged(tmp_path):
    """The retired keys are stale, not wrong — nothing reads them any more."""
    task = _task(tmp_path, extra_metadata=(
        "  genre: reproduction\n"
        "  paper: {title: x, year: 2009, reference_repo: TODO, commit: TODO}\n"))
    problems = lint_domain.lint_task(task)
    assert problems == [], problems


def test_the_shipped_domain_lints_clean():
    """The real tree — template excluded from task batteries, the worked example included, and the
    repository root as the default target (only tasks/ and templates/ are walked)."""
    assert lint_domain.lint_domain(REPO_ROOT) == []
    assert lint_domain.lint_paths([REPO_ROOT]) == []
    assert lint_domain.DEFAULT_DOMAIN == REPO_ROOT


# --------------------------------------------------------------------------- #
# battery 7: placement at tasks/<platform>/<slug>/
# --------------------------------------------------------------------------- #
def test_placement_accepts_the_landing_path(tmp_path):
    task = _task(tmp_path)                       # tasks/uav/probe, platform uav
    assert lint_domain.tasks_root_of(task) == (tmp_path / "tasks").resolve()
    assert lint_domain.lint_paths([task]) == []
    assert lint_domain.lint_paths([tmp_path]) == []            # a root holding tasks/
    assert lint_domain.lint_paths([tmp_path / "tasks"]) == []  # the collection itself


def test_placement_refuses_a_folder_named_unlike_its_manifest(tmp_path):
    task = _task(tmp_path)
    renamed = task.rename(task.parent / "probe_v2")
    problems = lint_domain.lint_paths([renamed])
    assert len(problems) == 1, problems
    assert "folder name 'probe_v2' != task.yaml `name` 'probe'" in problems[0]


def test_placement_refuses_the_wrong_platform_folder(tmp_path):
    task = _task(tmp_path)                       # manifest says platform: uav
    (tmp_path / "tasks" / "control").mkdir()     # its direction, not its platform
    moved = task.rename(tmp_path / "tasks" / "control" / "probe")
    problems = lint_domain.lint_paths([moved])
    assert len(problems) == 1, problems
    assert "tasks/control/probe/" in problems[0] and "metadata.platform 'uav'" in problems[0]
    assert "tasks/uav/probe/" in problems[0]   # the fix is spelled out


def test_placement_refuses_a_task_nested_too_deep(tmp_path):
    task = _task(tmp_path)
    (tmp_path / "tasks" / "uav" / "extra").mkdir()
    moved = task.rename(tmp_path / "tasks" / "uav" / "extra" / "probe")
    problems = lint_domain.lint_paths([moved])
    assert any("exactly at tasks/<platform>/<slug>/" in p for p in problems), problems


def test_placement_is_not_checked_outside_a_tasks_root(tmp_path):
    """A template or a scratch copy is not a landed task; the other batteries still run."""
    task = _task(tmp_path)
    (tmp_path / "scratch").mkdir()
    moved = task.rename(tmp_path / "scratch" / "renamed")
    assert lint_domain.tasks_root_of(moved) is None
    assert lint_domain.lint_paths([moved]) == []
    assert lint_domain.lint_task(moved) == []


@pytest.mark.parametrize("metric", ["planning_time_ms", "control_frequency_hz",
                                    "throughput_fps", "inference_latency", "fps",
                                    "inferenceLatency", "runtime_s", "time2goal"])
def test_hardware_dependent_metrics_are_still_refused(tmp_path, metric):
    anchor = dict(_ANCHOR, metric=metric)
    problems = lint_domain.lint_task(_task(tmp_path, metric=metric, anchor=anchor))
    assert any("hardware-dependent" in p for p in problems), problems


@pytest.mark.parametrize("metric", ["rms_error", "rmse", "items_total", "timesteps_to_goal",
                                    "mean_goal_distance", "success_rate", "atempo_score"])
def test_hardware_words_inside_other_tokens_are_not_refused(tmp_path, metric):
    """Whole tokens only: `rms_error` is not `ms`, `timesteps` is not `time`."""
    anchor = dict(_ANCHOR, metric=metric)
    problems = lint_domain.lint_task(_task(tmp_path, metric=metric, anchor=anchor))
    assert problems == [], problems


def test_hw_metric_token_names_the_offending_segment():
    assert lint_domain.hw_metric_token("planning_time_ms") == "time"
    assert lint_domain.hw_metric_token("control-frequency-hz") == "frequency"
    assert lint_domain.hw_metric_token("throughputFps") == "throughput"
    assert lint_domain.hw_metric_token("rms_error") is None
    assert lint_domain.hw_metric_token("") is None


def test_a_non_positive_anchor_is_still_refused(tmp_path):
    problems = lint_domain.lint_task(_task(tmp_path, anchor=dict(_ANCHOR, value=-3.0)))
    assert any("not positive" in p for p in problems), problems


def test_a_missing_full_at_names_the_engine_gate(tmp_path):
    anchor = {k: v for k, v in _ANCHOR.items() if k != "full_at"}
    problems = lint_domain.lint_task(_task(tmp_path, anchor=anchor))
    assert len(problems) == 1 and "full_at" in problems[0], problems
    # The exact-1.0 oracle is the engine's own gate (`oracle_not_full`); say so.
    assert "oracle_not_full" in problems[0] and "domain policy" not in problems[0]


@pytest.mark.parametrize("source", [None, 474, ["oracle run"], "", "   "])
def test_an_anchor_source_that_is_not_text_is_refused(tmp_path, source):
    """str(None) is "None": a null, numeric or list source must not pass as attribution."""
    problems = lint_domain.lint_task(_task(tmp_path, anchor=dict(_ANCHOR, source=source)))
    assert any("no `source`" in p for p in problems), problems


def test_an_anchor_quoting_a_published_figure_is_still_refused(tmp_path):
    anchor = dict(_ANCHOR, source="the paper reports 474")
    problems = lint_domain.lint_task(_task(tmp_path, anchor=anchor))
    assert any("paper" in p for p in problems), problems


@pytest.mark.parametrize("source", [
    # a former task's exact wording (uav_hover, removed 2026-10-15)
    "oracle implementation (oracle/run.sh, PPO on HoverAviary at the pinned gym-pybullet-drones "
    "commit): real graded run over hidden per-run seeds, never a published figure",
    "oracle/run.sh real graded run — NOT the paper's printed figure",
    "measured by the oracle, not any reported number",
    "own real run; never the printed value",
])
def test_an_anchor_disclaiming_the_paper_is_accepted(tmp_path, source):
    problems = lint_domain.lint_task(_task(tmp_path, anchor=dict(_ANCHOR, source=source)))
    assert problems == [], problems


def test_anchor_direction_must_be_higher_or_lower(tmp_path):
    """The anchor is the one place a task declares its direction (the grader reads it there)."""
    problems = lint_domain.lint_task(_task(tmp_path, anchor=dict(_ANCHOR, direction="up")))
    assert len(problems) == 1 and "`direction`" in problems[0], problems
    missing = {key: value for key, value in _ANCHOR.items() if key != "direction"}
    assert len(lint_domain.lint_task(_task(tmp_path / "missing", anchor=missing))) == 1
    assert lint_domain.lint_task(_task(tmp_path / "ok", anchor=dict(_ANCHOR, direction="lower"))) == []


@pytest.mark.parametrize("metric", [None, "", "TODO"])
def test_anchor_must_name_its_metric(tmp_path, metric):
    problems = lint_domain.lint_task(_task(tmp_path, anchor=dict(_ANCHOR, metric=metric)))
    assert len(problems) == 1 and "names no `metric`" in problems[0], problems


def test_retired_direction_and_metric_metadata_is_not_flagged(tmp_path):
    """metadata.direction / metadata.metric (retired 2026-10-15) are stale, not wrong."""
    task = _task(tmp_path, extra_metadata=(
        "  direction: control\n"
        "  metric: {name: mean_episode_reward, direction: higher, hardware_invariant: true}\n"))
    assert lint_domain.lint_task(task) == []


# --------------------------------------------------------------------------- #
# the platform vocabulary (tasks/platforms.json, owned by this repository)
# --------------------------------------------------------------------------- #
def test_the_platform_vocabulary_is_the_fixed_list():
    assert lint_domain.platform_keys() == (
        "arm", "hand", "quadruped", "humanoid", "uav", "driving", "underwater", "space",
        "soft", "surgical", "swarm", "subsystems")


def test_every_registered_platform_is_accepted(tmp_path):
    for key in lint_domain.platform_keys():
        assert lint_domain.lint_paths([_task(tmp_path / key, platform=key)]) == [], key


@pytest.mark.parametrize("platform", ["spacecraft", "manipulation", "TODO"])
def test_an_unregistered_platform_is_refused(tmp_path, platform):
    problems = lint_domain.lint_task(_task(tmp_path, platform=platform))
    assert len(problems) == 1, problems
    assert f"metadata.platform {platform!r}" in problems[0] and "tasks/platforms.json" in problems[0]


def test_a_missing_platform_is_refused(tmp_path):
    problems = lint_domain.lint_task(_task(tmp_path, platform=""))
    assert len(problems) == 1 and "metadata.platform None" in problems[0], problems


def test_a_visible_file_reaching_the_anchor_is_still_refused(tmp_path):
    task = _task(tmp_path)
    (task / "setup" / "payload" / "practice_grader" / "grade.py").write_text(
        "import json; a = json.load(open('../verify/anchor.json'))\n")
    problems = lint_domain.lint_task(task)
    assert any("agent-visible" in p for p in problems), problems


def test_a_pdf_in_the_image_is_no_longer_hidden_surface(tmp_path):
    """The paper PDF was hidden for the retired faithfulness judge; with no judge there is
    nothing to hide, and the image battery must not invent a leak."""
    task = _task(tmp_path)
    (task / "image" / "Dockerfile").write_text(
        "FROM ghcr.io/agentslastexam/container-ubuntu22-base:latest\n"
        "COPY docs/manual.pdf /opt/manual.pdf\n")
    assert lint_domain.lint_image(task / "image" / "Dockerfile") == []


def test_full_at_is_bounded_by_the_task_own_scoring_cap(tmp_path):
    """The cap comes from verify/grader_config.json (1.5 by default), read exactly as the
    kit reads it — a task its own grader accepts must never go red in PR CI over a
    hardcoded 1.5."""
    over_default = _task(tmp_path / "a", anchor=dict(_ANCHOR, full_at=1.8))
    problems = lint_domain.lint_task(over_default)
    assert len(problems) == 1 and "full_at" in problems[0] and "(0, 1.5]" in problems[0], problems

    raised = _task(tmp_path / "b", anchor=dict(_ANCHOR, full_at=1.8))
    (raised / "verify" / "grader_config.json").write_text(json.dumps({"scoring_cap": 2.0}))
    assert lint_domain.lint_task(raised) == []

    lowered = _task(tmp_path / "c", anchor=dict(_ANCHOR, full_at=1.2))
    (lowered / "verify" / "grader_config.json").write_text(json.dumps({"scoring_cap": 1.0}))
    problems = lint_domain.lint_task(lowered)
    assert len(problems) == 1 and "(0, 1]" in problems[0], problems

    # a malformed or non-positive cap falls back to the default rather than exploding
    for i, bogus_cfg in enumerate(('{"scoring_cap": "big"}', '{"scoring_cap": true}',
                                   '{"scoring_cap": -1}', "not json")):
        bogus = _task(tmp_path / f"d{i}", anchor=dict(_ANCHOR, full_at=1.5))
        (bogus / "verify" / "grader_config.json").write_text(bogus_cfg)
        assert lint_domain.lint_task(bogus) == [], bogus_cfg
