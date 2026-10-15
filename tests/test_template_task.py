"""The shipped task template — what contributors copy — keeps its promises.

These are the rehearsal findings of 2026-09-02, pinned: the probe wire is indistinguishable
(step index `t`, never a probe marker), the two environment copies are byte-identical,
`setup/run.sh` stages `payload/` without naming its files, the manifest's platform hint
lists the benchmark's own platform keys, no contributor-facing file points at the maintainers'
harness, and the instruction states what the kit actually enforces (cwd, READY deadline,
repeated-`t` semantics, saturation).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "templates" / "task"
KIT = REPO_ROOT / "shared" / "robotics_grader"
# The benchmark's platform keys: this repository's own vocabulary, not the website's.
PLATFORM_KEYS = [entry["key"] for entry in
                 json.loads((REPO_ROOT / "tasks" / "platforms.json").read_text(encoding="utf-8"))]

CONTRIBUTOR_FACING = ["task.yaml", "instruction.md", "verify/verify.py", "verify/anchor.json",
                      "verify/env.py", "setup/run.sh", "oracle/run.sh", "oracle/solver.py",
                      "setup/payload/template_env.py", "setup/payload/practice_grader/grade.py"]


def _text(rel: str) -> str:
    return (TEMPLATE / rel).read_text()


# --------------------------------------------------------------------------- #
# the probe wire
# --------------------------------------------------------------------------- #
def test_no_probe_marker_anywhere_on_the_agent_facing_surface():
    """Neither the instruction, the practice grader nor the kit ever says `"probe"` on
    the wire; a solver has nothing to key on."""
    for rel in ("instruction.md", "setup/payload/practice_grader/grade.py", "oracle/solver.py"):
        assert '"probe"' not in _text(rel), rel
    for path in KIT.glob("*.py"):
        src = path.read_text()
        assert 'message["probe"]' not in src and '"probe": True' not in src, path.name
        assert "probe=True" not in src, path.name


def test_instruction_states_the_wire_the_kit_speaks():
    text = _text("instruction.md")
    assert '{"type": "act", "t": 0, "obs": [x, y, vx, vy, gx, gy]}' in text
    assert '{"obs": {"obs"' not in text          # the double nesting is gone
    assert "same `t`" in text
    assert "do not advance the episode" in text
    assert "do not count requests as steps" in text
    assert "/home/user/submission" in text and "working directory" in text
    assert "saturates" in text and "not disclosed" in text
    assert "/opt/venv/bin/python3" in text and "numpy" in text


def test_instruction_quotes_the_kits_real_deadlines():
    """The READY deadline is READY_TIMEOUT_FACTOR step deadlines; the instruction must
    quote the product of the real config and the real factor, not a remembered number."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("kit_solver", KIT / "solver.py",
                                                  submodule_search_locations=[])
    factor = int(re.search(r"^READY_TIMEOUT_FACTOR = (\d+)", (KIT / "solver.py").read_text(),
                           re.MULTILINE).group(1))
    step = json.loads(_text("verify/grader_config.json"))["step_timeout_s"]
    text = _text("instruction.md")
    assert f"within {step * factor} s" in text, (step, factor)
    assert f"within {step} s" in text
    grade = _text("setup/payload/practice_grader/grade.py")
    assert f"STEP_TIMEOUT_S = {float(step)}" in grade
    assert f"READY_TIMEOUT_S = {factor} * STEP_TIMEOUT_S" in grade
    assert spec is not None


def test_verify_loop_owns_t_and_probes_through_the_lock():
    src = _text("verify/verify.py")
    assert "for t in range(env.max_steps)" in src
    assert "lock.probe(solver, t, obs)" in src
    assert "solver.act(t, obs)" in src
    assert "probe=True" not in src and '{"obs": obs}' not in src


def test_practice_grader_speaks_the_same_wire_with_repeated_t():
    src = _text("setup/payload/practice_grader/grade.py")
    assert '{"type": "act", "t": t, "obs": obs}' in src
    assert "REPEAT_STEPS" in src and "perturbed" in src
    assert "queue.Queue" in src and "threading.Thread" in src   # real deadlines, not readline()
    for leak in ("anchor", "robotics_grader", "verify/"):
        assert leak not in src, leak


# --------------------------------------------------------------------------- #
# the two environment copies
# --------------------------------------------------------------------------- #
def test_grader_env_and_agent_dev_env_are_byte_identical():
    """The agent develops against exactly the dynamics it is scored on. Edit one, copy."""
    assert (TEMPLATE / "verify" / "env.py").read_bytes() == \
        (TEMPLATE / "setup" / "payload" / "template_env.py").read_bytes()


def test_setup_stages_payload_without_naming_its_files():
    src = _text("setup/run.sh")
    assert "cp -r payload/." in src
    assert 'for entry in payload/*' in src and "chown -R" in src
    for hardcoded in ("template_env.py", "practice_grader"):
        assert hardcoded not in src, hardcoded


# --------------------------------------------------------------------------- #
# the manifest's hints and the maintainers' tooling
# --------------------------------------------------------------------------- #
def test_task_yaml_platform_hint_lists_the_platform_keys():
    text = _text("task.yaml")
    listed = [k for k in PLATFORM_KEYS if re.search(rf"\b{k}\b", text)]
    assert listed == PLATFORM_KEYS, set(PLATFORM_KEYS) - set(listed)
    assert "drone" not in text
    assert "tasks/platforms.json" in text
    assert "needs a maintainer" in text
    manifest = yaml.safe_load(text)
    assert manifest["metadata"] == {"platform": "TODO"}   # direction and metric are retired


def test_no_contributor_facing_file_points_at_the_maintainers_harness():
    for rel in CONTRIBUTOR_FACING:
        assert "ale_onboard" not in _text(rel) and "ale-onboard" not in _text(rel), rel
    for path in KIT.glob("*.py"):
        assert "ale_onboard" not in path.read_text(), path.name


def test_requirements_txt_is_the_single_dependency_list():
    """image/requirements.txt is the ONE list of Python deps; the Dockerfile installs from
    it and nothing is pip-installed inline."""
    requirements = (TEMPLATE / "image" / "requirements.txt").read_text()
    packages = [line.split("#")[0].strip() for line in requirements.splitlines()]
    packages = [line for line in packages if line]
    assert packages == ["numpy>=1.26,<3"]
    dockerfile = _text("image/Dockerfile")
    assert "COPY requirements.txt /tmp/requirements.txt" in dockerfile
    assert "uv pip install --python /opt/venv -r /tmp/requirements.txt" in dockerfile
    # no package literal installed inline — that would be a second list
    assert not re.search(r'uv pip install[^\n]*"[a-zA-Z]', dockerfile)
    assert "uv venv /opt/venv --python 3.12" in dockerfile
    assert "runuser -u user -- python3 -c" in dockerfile      # the agent-UID self-check stays


def test_vendored_kit_is_byte_identical_to_the_canonical():
    vendored = TEMPLATE / "verify" / "robotics_grader"
    canonical = {p.name: p.read_bytes() for p in KIT.glob("*.py")}
    shipped = {p.name: p.read_bytes() for p in vendored.glob("*.py")}
    assert shipped == canonical
