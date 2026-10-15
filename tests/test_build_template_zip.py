"""scripts/build_template_zip.py: the Contribute page's downloadable template.

Everything runs against a throwaway git repository holding a fake template, so the tests
do not depend on the real template's contents or the working tree being clean; one
integration test builds the real template from the real checkout (skipped only when the
template tree is dirty, which the script must refuse anyway).

The zip is the COMMITTED tree: members and modes come from the git index, so the fixture
plants every kind of ignored dropping the real .gitignore covers and the tests assert none
of it ships and none of it counts as dirty. Beside the task folder the download carries
only the guide and the source document — never inside the folder, which must stay
byte-identical to the committed template.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "build_template_zip", REPO_ROOT / "scripts" / "build_template_zip.py"
)
assert _SPEC is not None and _SPEC.loader is not None
btz = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = btz
_SPEC.loader.exec_module(btz)

STAMP = "2026-09-02T00:00:00Z"
FOLDER = "agentic-robotics-task-template"
HELPERS = ["GETTING-STARTED.md", "TEMPLATE-SOURCE.json"]
TASK_FILES = [
    f"{FOLDER}/instruction.md",
    f"{FOLDER}/oracle/run.sh",
    f"{FOLDER}/setup/run.sh",
    f"{FOLDER}/task.yaml",
    f"{FOLDER}/verify/anchor.json",
    f"{FOLDER}/verify/robotics_grader/__init__.py",
    f"{FOLDER}/verify/run.sh",
]
SOURCE_KEYS = {"repo", "commit", "generated_at", "template_path", "zip_folder"}

# The real repository's ignore patterns that matter for a template tree.
_GITIGNORE = ("__pycache__/\n*.pyc\n.ale-cache/\n*.log\n.DS_Store\n*.swp\n*.pt\n"
              "templates/**/verify/assets/\n")

# Ignored droppings planted in the working tree after the commit: none may ship, none may
# count as dirty. Two are the old exclusion lists; the rest are what those lists missed.
_DROPPINGS = ("verify/__pycache__/env.cpython-312.pyc", "oracle/stale.pyc",
              ".ale-cache/image.txt", ".DS_Store", "run.log", "verify/.verify.py.swp",
              "verify/policy.pt", "verify/assets/big.bin")


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                          check=True, env=env).stdout.strip()


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A committed git repo with a small template tree plus ignored droppings."""
    repo = tmp_path / "repo"
    tpl = repo / "templates" / "task"
    (tpl / "verify" / "robotics_grader").mkdir(parents=True)
    (tpl / "oracle").mkdir()
    (tpl / "setup").mkdir()
    (tpl / "task.yaml").write_text("spec_type: core/v1\nname: template_reach\n")
    (tpl / "instruction.md").write_text("# do the thing\n")
    for entry in ("setup/run.sh", "oracle/run.sh", "verify/run.sh"):
        (tpl / entry).write_text("#!/bin/sh\nexit 0\n")
        (tpl / entry).chmod(0o755)
    (tpl / "verify" / "anchor.json").write_text('{"value": null, "full_at": 0.8}\n')
    (tpl / "verify" / "robotics_grader" / "__init__.py").write_text("")
    (repo / ".gitignore").write_text(_GITIGNORE)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "template")
    for rel in _DROPPINGS:
        (tpl / rel).parent.mkdir(parents=True, exist_ok=True)
        (tpl / rel).write_bytes(b"\x00")
    return repo


def _tpl(repo: Path) -> Path:
    return repo / "templates" / "task"


def _build(out: Path, fake: Path, **kw: object) -> dict[str, object]:
    """build_zip over the fake template repo (STAMP pinned)."""
    kw.setdefault("generated_at", STAMP)
    return btz.build_zip(out, template=_tpl(fake), **kw)  # type: ignore[arg-type]


def _names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def _modes(path: Path) -> dict[str, int]:
    with zipfile.ZipFile(path) as zf:
        return {i.filename: stat.S_IMODE(i.external_attr >> 16) for i in zf.infolist()}


def _read_json(path: Path, member: str) -> dict:
    with zipfile.ZipFile(path) as zf:
        return json.loads(zf.read(member))


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #
def test_zip_layout_helpers_then_the_folder(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "tpl.zip"
    _build(out, fake_repo)
    names = _names(out)
    assert names[:2] == HELPERS
    assert names[2:] == TASK_FILES
    # the helpers live NEXT TO the task folder: the folder holds the task, nothing else
    inside = [n for n in names if n.startswith(f"{FOLDER}/")]
    assert inside == TASK_FILES
    assert not any(n.startswith(f"{FOLDER}/") and n.endswith(("GETTING-STARTED.md", "TEMPLATE-SOURCE.json"))
                   for n in names)
    assert not any(n.startswith("_tools") for n in names)   # the dry-run runtime is gone
    # only ONE first-level folder carries a task.yaml (what the server's layout detector keys on)
    assert [n for n in names if n.count("/") == 1 and n.endswith("/task.yaml")] == [f"{FOLDER}/task.yaml"]


def test_task_folder_is_byte_identical_to_the_committed_template(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "tpl.zip"
    _build(out, fake_repo)
    tpl = _tpl(fake_repo)
    with zipfile.ZipFile(out) as zf:
        for name in TASK_FILES:
            rel = name[len(FOLDER) + 1:]
            assert zf.read(name) == (tpl / rel).read_bytes(), rel


def test_members_come_from_git_so_ignored_files_never_ship(fake_repo: Path, tmp_path: Path) -> None:
    """Not just __pycache__/.pyc/.ale-cache: .DS_Store, *.log, *.swp, model blobs and the
    platform-synced assets/ root are all ignored, all present on disk, and none may reach
    the zip while the tree still reads as clean."""
    out = tmp_path / "tpl.zip"
    tpl = _tpl(fake_repo)
    for rel in _DROPPINGS:
        assert (tpl / rel).exists(), rel  # the fixture really planted them
    assert btz.template_status(tpl) == ""
    doc = _build(out, fake_repo)
    assert "dirty" not in doc
    joined = "\n".join(_names(out))
    for needle in (".DS_Store", "run.log", ".swp", "policy.pt", "assets/", "__pycache__",
                   ".pyc", ".ale-cache"):
        assert needle not in joined, needle


def test_source_document_records_repo_commit_stamp_path(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "tpl.zip"
    tpl = _tpl(fake_repo)
    returned = _build(out, fake_repo, repo="owner/name")
    doc = _read_json(out, "TEMPLATE-SOURCE.json")
    assert doc == returned
    assert doc["repo"] == "owner/name"
    assert doc["commit"] == _git(fake_repo, "rev-parse", "HEAD")
    assert doc["generated_at"] == STAMP
    # the template lives outside REPO_ROOT here, so the path is absolute; the real
    # template records the repo-relative path (integration test below)
    assert doc["template_path"] == tpl.resolve().as_posix()
    assert doc["zip_folder"] == FOLDER
    assert set(doc) == SOURCE_KEYS
    assert "sha256" not in doc  # the hash of the zip cannot live inside the zip


def _guide(out: Path) -> str:
    with zipfile.ZipFile(out) as zf:
        return zf.read("GETTING-STARTED.md").decode("utf-8")


def test_getting_started_is_the_engine_first_guide(fake_repo: Path, tmp_path: Path) -> None:
    """One track: get the public engine, build the base image, fill the folder against the
    upstream specs, `ale lint` + `ale validate`, record the anchor, then submit through
    either channel (a pull request by hand, or the website upload that opens the same
    pull request)."""
    out = tmp_path / "tpl.zip"
    _build(out, fake_repo)
    guide = _guide(out)
    assert guide == btz.GETTING_STARTED
    assert guide.count("\n") <= 90                         # the owner's cap (2026-09-05)
    headings = ["## 1. Get the engine", "## 2. Unzip and rename", "## 3. Fill the folder",
                "## 4. Lint and validate", "## 5. Record the anchor", "## 6. Submit the task",
                "## What happens next"]
    positions = [guide.index(h) for h in headings]
    assert positions == sorted(positions)
    flat = " ".join(guide.split())          # prose wraps at ~100 columns
    for needle in (
        # the engine, from its public repository, and the base image built once
        "git clone https://github.com/AgentsLastexam/ale.git".replace("Lastexam", "LastExam"),
        "just bootstrap",
        "uv run ale --help",
        "images/build.sh container-ubuntu22",
        "`uv`, `just`",
        "Docker",
        # the folder contract is upstream's; our deltas are listed, not restated
        "https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-authoring.md",
        "https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-quality-standard.md",
        "positive scale",
        "never a published figure",
        "`full_at`",
        "verify/robotics_grader/",
        "setup/payload/",
        "same `t`",
        # the exact engine commands, contributor-relative
        "uv run ale lint <task>",
        "uv run ale validate <task> --runs-dir <runs>",
        # the anchor loop under the new engine semantics
        "prints `FAIL` while the anchor is null",
        "`validation.json` names the reason, `oracle_not_full`",
        "<runs>/validate-*/<slug>-oracle-*/result.json",
        'json.load(open(p))["metrics"]',
        "`ratio`, `anchor_recorded`, `match_lock_ok`",
        "verify/anchor.json",
        "starts with `ok`",
        # +x for extractors that drop modes; zip only the folder; upload; what happens after
        "chmod +x setup/run.sh oracle/run.sh verify/run.sh",
        "Zip only the task folder",
        "Never zip this download root",
        "50 MB",
        "https://agentic-robotics-benchmark.org/contribute",
        "`value` > 0",
        "(0, scoring_cap]",
        # the two channels land the same pull request at the same path
        "https://github.com/Grounded-Intelligence-Initiative/agentic-robotics-benchmark",
        "fork the repository",
        "opens the same pull request",
        "tasks/<platform>/<slug>/",
        "upstream ALE engine",
    ):
        assert needle in guide or needle in flat, needle
    # retired framings and maintainer-only tooling never reach a contributor
    for gone in ("partial_oracle", "domain policy", "domain rule", "ale-onboard", "vendor/ale",
                 "partner teams", "dryrun", "dry-run", "_tools", "VIRTUAL_ENV=", "DOCKER_HOST"):
        assert gone not in guide, gone
    # the engine's contract is artifact-general; "policy" framed the benchmark as running a
    # learned controller (owner critique 2026-09-05) and stays out of the guide
    assert "policy" not in guide.lower()
    # writing style (owner directive 2026-09-05): no em dashes, sentence-case headings
    assert "—" not in guide
    for line in guide.splitlines():
        if line.startswith("## "):
            words = [w for w in line[3:].split() if not w.rstrip(".").isdigit()]
            assert not any(w[:1].isupper() for w in words[1:] if w.isalpha()), line


def test_two_builds_are_byte_identical(fake_repo: Path, tmp_path: Path) -> None:
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    _build(a, fake_repo)
    _build(b, fake_repo)
    assert a.read_bytes() == b.read_bytes()


def test_fixed_mtimes_and_mode_bits(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "tpl.zip"
    _build(out, fake_repo)
    with zipfile.ZipFile(out) as zf:
        infos = list(zf.infolist())
    assert {i.date_time for i in infos} == {btz.FIXED_MTIME}
    assert all(stat.S_ISREG(i.external_attr >> 16) for i in infos)
    modes = _modes(out)
    for entry in ("setup/run.sh", "oracle/run.sh", "verify/run.sh"):
        assert modes[f"{FOLDER}/{entry}"] == 0o755, entry
    assert modes[f"{FOLDER}/task.yaml"] == 0o644
    assert modes["GETTING-STARTED.md"] == 0o644
    assert modes["TEMPLATE-SOURCE.json"] == 0o644


def test_committed_mode_wins_over_the_working_tree_mode(fake_repo: Path, tmp_path: Path) -> None:
    """A checkout that dropped +x (or picked up a stray one) with core.fileMode=false is
    clean to git; the zip still carries the COMMITTED modes."""
    tpl = _tpl(fake_repo)
    _git(fake_repo, "config", "core.fileMode", "false")
    (tpl / "setup" / "run.sh").chmod(0o644)
    (tpl / "task.yaml").chmod(0o755)
    assert btz.template_status(tpl) == ""
    out = tmp_path / "tpl.zip"
    _build(out, fake_repo)
    modes = _modes(out)
    assert modes[f"{FOLDER}/setup/run.sh"] == 0o755
    assert modes[f"{FOLDER}/task.yaml"] == 0o644


def test_extracts_with_executable_bits(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "tpl.zip"
    _build(out, fake_repo)
    dest = tmp_path / "unzipped"
    with zipfile.ZipFile(out) as zf:
        for info in zf.infolist():
            target = zf.extract(info, dest)
            os.chmod(target, stat.S_IMODE(info.external_attr >> 16))
    assert os.access(dest / FOLDER / "verify" / "run.sh", os.X_OK)
    assert (dest / FOLDER / "task.yaml").read_text().startswith("spec_type")
    assert (dest / "GETTING-STARTED.md").is_file()
    assert json.loads((dest / "TEMPLATE-SOURCE.json").read_text())["zip_folder"] == FOLDER
    assert sorted(p.name for p in dest.iterdir()) == sorted([*HELPERS, FOLDER])


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #
def test_refuses_dirty_template_tree(fake_repo: Path, tmp_path: Path) -> None:
    tpl = _tpl(fake_repo)
    (tpl / "instruction.md").write_text("# edited, not committed\n")
    with pytest.raises(btz.BuildError, match="not clean"):
        _build(tmp_path / "tpl.zip", fake_repo)
    assert not (tmp_path / "tpl.zip").exists()


def test_refuses_staged_but_uncommitted_change(fake_repo: Path, tmp_path: Path) -> None:
    """Staged != committed: HEAD is what TEMPLATE-SOURCE.json names."""
    tpl = _tpl(fake_repo)
    (tpl / "instruction.md").write_text("# staged, not committed\n")
    _git(fake_repo, "add", "-A")
    with pytest.raises(btz.BuildError, match="not clean"):
        _build(tmp_path / "tpl.zip", fake_repo)


def test_refuses_untracked_file_in_template(fake_repo: Path, tmp_path: Path) -> None:
    tpl = _tpl(fake_repo)
    (tpl / "notes.txt").write_text("scratch\n")
    with pytest.raises(btz.BuildError, match="notes.txt"):
        _build(tmp_path / "tpl.zip", fake_repo)


def test_allow_dirty_stamps_the_source_document(fake_repo: Path, tmp_path: Path) -> None:
    tpl = _tpl(fake_repo)
    (tpl / "instruction.md").write_text("# edited, not committed\n")
    out = tmp_path / "tpl.zip"
    doc = _build(out, fake_repo, allow_dirty=True)
    assert doc["dirty"] is True
    assert doc["commit"].endswith("-dirty")
    with zipfile.ZipFile(out) as zf:
        assert zf.read(f"{FOLDER}/instruction.md") == b"# edited, not committed\n"


def test_allow_dirty_ships_untracked_but_never_ignored(fake_repo: Path, tmp_path: Path) -> None:
    tpl = _tpl(fake_repo)
    (tpl / "notes.txt").write_text("scratch\n")
    out = tmp_path / "tpl.zip"
    doc = _build(out, fake_repo, allow_dirty=True)
    assert doc["dirty"] is True
    names = _names(out)
    assert f"{FOLDER}/notes.txt" in names
    joined = "\n".join(names)
    for needle in (".DS_Store", "run.log", "policy.pt", "assets/", "__pycache__", ".pyc"):
        assert needle not in joined, needle


def test_refuses_symlink_inside_template(fake_repo: Path, tmp_path: Path) -> None:
    tpl = _tpl(fake_repo)
    (tpl / "oracle" / "link.sh").symlink_to(tpl / "oracle" / "run.sh")
    _git(fake_repo, "add", "-A")
    _git(fake_repo, "commit", "-q", "-m", "symlink")
    with pytest.raises(btz.BuildError, match="symlink"):
        _build(tmp_path / "tpl.zip", fake_repo)


def test_refuses_untracked_symlink_under_allow_dirty(fake_repo: Path, tmp_path: Path) -> None:
    tpl = _tpl(fake_repo)
    (tpl / "oracle" / "link.sh").symlink_to(tpl / "oracle" / "run.sh")
    with pytest.raises(btz.BuildError, match="symlink"):
        _build(tmp_path / "tpl.zip", fake_repo, allow_dirty=True)


def test_missing_template_folder(tmp_path: Path) -> None:
    with pytest.raises(btz.BuildError):
        btz.collect_members(tmp_path / "nope")
    with pytest.raises(btz.BuildError):
        btz.build_zip(tmp_path / "x.zip", template=tmp_path / "nope", generated_at=STAMP)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_writes_and_reports(fake_repo: Path, tmp_path: Path,
                                capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cli.zip"
    rc = btz.main(["--out", str(out), "--template", str(_tpl(fake_repo)), "--generated-at", STAMP,
                   "--repo", "owner/name"])
    assert rc == 0
    assert out.exists()
    captured = capsys.readouterr().out
    assert f"{len(HELPERS) + len(TASK_FILES)} entries" in captured
    assert "from owner/name@" in captured
    assert not (tmp_path / "SOURCE.json").exists()  # only written on request


def test_cli_source_out_is_template_source_plus_zip_sha256(
        fake_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The website's SOURCE.json = TEMPLATE-SOURCE.json + sha256 of the zip as written."""
    out = tmp_path / "cli.zip"
    src = tmp_path / "public" / "task-template" / "SOURCE.json"
    rc = btz.main(["--out", str(out), "--template", str(_tpl(fake_repo)), "--generated-at", STAMP,
                   "--source-out", str(src)])
    assert rc == 0
    doc = json.loads(src.read_text())
    assert doc["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    inner = _read_json(out, "TEMPLATE-SOURCE.json")
    assert {k: v for k, v in doc.items() if k != "sha256"} == inner
    assert set(doc) == SOURCE_KEYS | {"sha256"}
    assert "SOURCE.json" in capsys.readouterr().out


def test_cli_dirty_tree_is_exit_1(fake_repo: Path, tmp_path: Path,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    tpl = _tpl(fake_repo)
    (tpl / "instruction.md").write_text("# edited\n")
    rc = btz.main(["--out", str(tmp_path / "x.zip"), "--template", str(tpl),
                   "--source-out", str(tmp_path / "SOURCE.json")])
    assert rc == 1
    assert "not clean" in capsys.readouterr().err
    assert not (tmp_path / "SOURCE.json").exists()


# --------------------------------------------------------------------------- #
# integration: the real template from the real checkout
# --------------------------------------------------------------------------- #
def test_real_template_builds(tmp_path: Path) -> None:
    tpl = btz.DEFAULT_TEMPLATE
    if btz.template_status(tpl):
        pytest.skip("real template tree is dirty; the script refuses it by design")
    out = tmp_path / "real.zip"
    doc = btz.build_zip(out, generated_at=STAMP)
    assert doc["template_path"] == "templates/task"
    assert doc["repo"] == btz.DEFAULT_REPO
    assert set(doc) == SOURCE_KEYS
    names = _names(out)
    assert names[:2] == HELPERS
    for required in ("task.yaml", "instruction.md", "image/Dockerfile", "image/requirements.txt",
                     "setup/run.sh", "oracle/run.sh", "verify/run.sh", "verify/anchor.json",
                     "verify/robotics_grader/__init__.py"):
        assert f"{FOLDER}/{required}" in names, required
    assert all(n.startswith(f"{FOLDER}/") for n in names[2:])
    assert "__pycache__" not in "\n".join(names)
    # the three stage entry points are committed 100755 and must ship that way
    modes = _modes(out)
    for entry in ("setup/run.sh", "oracle/run.sh", "verify/run.sh"):
        assert modes[f"{FOLDER}/{entry}"] == 0o755, entry
    assert modes[f"{FOLDER}/task.yaml"] == 0o644
    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None
    # the task folder inside the zip IS the committed template, byte for byte
    with zipfile.ZipFile(out) as zf:
        for name in names:
            if name.startswith(f"{FOLDER}/"):
                assert zf.read(name) == (tpl / name[len(FOLDER) + 1:]).read_bytes(), name
