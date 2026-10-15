#!/usr/bin/env python3
"""Build the downloadable task template zip for the website's Contribute page.

The contributor path is: download the template -> build the task locally -> submit the
task folder, either as a pull request opened by hand or as a zip uploaded on the website
(the bot then opens the same pull request). This script produces the download. It packs
``templates/task/`` (the ONE template, gates green as shipped) as::

    agentic-robotics-task-template/        the task folder — rename it to your slug
    GETTING-STARTED.md                     the guide: get the engine, build, validate, submit
    TEMPLATE-SOURCE.json                   {repo, commit, generated_at, template_path, zip_folder}

The two helper files sit NEXT TO the task folder, not inside it: the engine enforces a
strict whitelist at a task's top level, so anything extra inside the folder would fail
``ale lint`` and the server precheck. Contributors submit the task folder only.

The guide is engine-first and single-track: the upstream engine repository is public, so a
contributor clones it, builds the base image locally and runs ``ale lint`` / ``ale validate``
themselves.

    uv run python scripts/build_template_zip.py --out agentic-robotics-task-template.zip \\
        --source-out SOURCE.json

The zip IS the committed tree: members are enumerated from git (``git ls-files -s``), so
the file list and the permission bits (a committed ``100755`` ships as 0755, everything
else as 0644) come from the index, not from whatever the working tree happens to hold —
ignored droppings (``__pycache__/``, ``*.pyc``, ``.ale-cache/``, ``.DS_Store``, ``*.log``,
the platform-synced ``assets/`` roots, model blobs, ...) can never reach it. Entries are
sorted and every member carries a fixed mtime, so two builds of the same commit are
byte-identical when ``--generated-at`` is pinned. A symlink or a submodule inside the
template is an error (the server rejects symlinks). The template tree must be clean in
git (modified, staged or untracked files would ship an identity no commit reproduces)
unless ``--allow-dirty`` is given, in which case the working-tree bytes plus the
untracked (still never the ignored) files ship, and ``TEMPLATE-SOURCE.json`` says
``dirty: true`` and stamps ``<sha>-dirty``.

``--source-out <path>`` also writes the website's ``SOURCE.json``: the TEMPLATE-SOURCE
document plus the ``sha256`` of the written zip (the Contribute page shows the commit; the
hash is the integrity check for a downloaded copy).

stdlib-only; runs under the repo's `uv run` python (3.11).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = REPO_ROOT / "templates" / "task"
DEFAULT_REPO = "Grounded-Intelligence-Initiative/agentic-robotics-benchmark"

ZIP_FOLDER = "agentic-robotics-task-template"
GUIDE_NAME = "GETTING-STARTED.md"
SOURCE_NAME = "TEMPLATE-SOURCE.json"

# A fixed timestamp for every member (zip stores local time, 2-second resolution;
# 1980-01-01 is the format's epoch and the smallest value zipfile accepts).
FIXED_MTIME = (1980, 1, 1, 0, 0, 0)

# Belt and braces on top of the git enumeration: even a tracked copy of these is
# authoring residue, never part of a task.
_EXCLUDED_DIRS = {"__pycache__", ".ale-cache"}
_EXCLUDED_SUFFIXES = (".pyc",)

# git index modes
_MODE_EXECUTABLE = "100755"
_MODE_SYMLINK = "120000"
_MODE_SUBMODULE = "160000"

GETTING_STARTED = """\
# Agentic Robotics Benchmark task template: getting started

`agentic-robotics-task-template/` is a complete task. It passes the engine's checks as shipped, except
that its anchor is null, so the reference solution scores 0 until you record a value (step 5). Tasks
run on the upstream ALE engine; its two specs (step 3) define the task format and the quality bar.
You need `uv`, `just` (`uv tool install rust-just`) and Docker.

## 1. Get the engine

```bash
git clone https://github.com/AgentsLastExam/ale.git && cd ale
just bootstrap
uv run ale --help
images/build.sh container-ubuntu22      # the sandbox base image; build it once, it takes a while
```

## 2. Unzip and rename

Rename `agentic-robotics-task-template/` to your task slug (`^[a-z0-9][a-z0-9_-]*$`) and set `name` in
`task.yaml` to the same string. If unzipping dropped Unix permissions, run `chmod +x setup/run.sh oracle/run.sh verify/run.sh`.

## 3. Fill the folder

The folder contract is the engine's authoring spec:
https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-authoring.md. The bar a task has to
clear is the quality standard:
https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-quality-standard.md. Read both once.
A robotics task here adds five rules:

- The metric is an outcome on a positive scale (success rate, path cost), never wall-clock time or
  throughput. `verify/anchor.json` declares it (`metric`, `direction`); `metric_name=` must match.
- The anchor (`verify/anchor.json` `value`) is your reference solution's own measured value from a
  real run, never a published figure. `full_at` is the ratio where the reward saturates to 1; size
  it from the metric's spread over several oracle runs.
- `verify/verify.py` scores whatever the agent left in `/home/user/submission/` (a controller, a plan
  file, a URDF, generated code). Leave `verify/robotics_grader/` alone; CI compares it byte for byte.
- `setup/payload/` is what the agent sees. The practice grader there prints the raw metric only,
  never a score, and never imports from `verify/`.
- `instruction.md` states the deliverable and the grading protocol. For a controller on the wire,
  keep the paragraph about the step index `t`: a step may be asked twice with the same `t`, and
  repeated queries do not advance the episode.

## 4. Lint and validate

From the engine checkout, with `<task>` the path to your folder and `<runs>` a scratch directory:

```bash
uv run ale lint <task>
uv run ale validate <task> --runs-dir <runs>
```

`validate` runs the task twice. An untouched sandbox must score 0 on every reward key. Then
`oracle/run.sh` runs in place of the agent and must score 1 on every key.

## 5. Record the anchor

The first `validate` prints `FAIL` while the anchor is null; the run's `validation.json` names the
reason, `oracle_not_full`. That is expected. Read the measured metric off the oracle record:

```bash
python3 -c 'import glob,json; [print(p, json.load(open(p))["metrics"]) for p in
  sorted(glob.glob("<runs>/validate-*/<slug>-oracle-*/result.json"))]'
```

The metric is the key that is not bookkeeping (`ratio`, `anchor_recorded`, `match_lock_ok`,
`probes_*`, `episodes_aborted`). Write it into `verify/anchor.json` as `value`, say where it came from
in `source`, and set `full_at`. Run `validate` a few more times, lower `full_at` to the worst honest
ratio you see, and repeat until the result line starts with `ok`.

## 6. Submit the task

Two ways, one result: a pull request into https://github.com/Grounded-Intelligence-Initiative/agentic-robotics-benchmark
that adds your folder at `tasks/<platform>/<slug>/` (`<platform>` is `metadata.platform` in `task.yaml`).

Open it yourself: fork the repository, copy the folder to that path, push a branch and open the pull
request. CI runs `ale lint`, the robotics lint and the kit comparison on it.

Or upload the folder at https://agentic-robotics-benchmark.org/contribute with a title. Zip only the
task folder (the folder itself, or its contents at the zip root). Never zip this download root: an
archive holding `GETTING-STARTED.md` or more than one folder is rejected. Limits: 50 MB, 256 files,
2 MB per file, no symlinks. The server prechecks the archive (required files, `name` equal to the
folder slug, metadata filled in, a hardware-invariant metric, an anchor with `value` > 0 and a real
`source`, `full_at` in (0, scoring_cap], a clean Dockerfile), lists every problem and stores nothing
on a rejection, or opens the same pull request for you and shows you the link.

## What happens next

A maintainer reads `verify/` and `oracle/`, re-runs `ale validate`, and merges. The task is then live at
`tasks/<platform>/<slug>/` and enters the registry at the next export. To revise a task under review,
push to your branch, or upload a new zip with the same slug.
"""


class BuildError(Exception):
    """A condition that must stop the build (dirty tree, symlink, missing template)."""


class Member(NamedTuple):
    """One file bound for the zip."""
    rel: str          # posix path relative to the template root (the zip path tail)
    path: Path        # where the bytes are read from
    executable: bool  # ships as 0755 when True, 0644 otherwise


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                            text=True, check=False)
    if result.returncode != 0:
        raise BuildError(f"git {' '.join(args)} failed in {cwd}: {result.stderr.strip()}")
    return result.stdout


def template_status(template: Path) -> str:
    """Porcelain status of the template tree — tracked changes (staged or not) and
    untracked files. Ignored files are not reported and never reach the zip, because the
    members come from the git index, not from the filesystem."""
    return _git(["status", "--porcelain", "--", str(template)], cwd=template).strip()


def source_commit(template: Path) -> str:
    return _git(["rev-parse", "HEAD"], cwd=template).strip()


def _tracked_members(template: Path) -> list[Member]:
    """Tracked files under ``template`` with their INDEX modes (``git ls-files -s``)."""
    raw = _git(["ls-files", "-z", "-s", "--", "."], cwd=template)
    members: list[Member] = []
    for entry in raw.split("\0"):
        if not entry:
            continue
        meta, _, rel = entry.partition("\t")
        mode = meta.split()[0]
        if mode == _MODE_SYMLINK:
            raise BuildError(f"symlink inside the template is not allowed: {rel}")
        if mode == _MODE_SUBMODULE:
            raise BuildError(f"submodule inside the template is not allowed: {rel}")
        members.append(Member(rel, template / rel, mode == _MODE_EXECUTABLE))
    return members


def _untracked_members(template: Path) -> list[Member]:
    """Untracked and NOT ignored files (``--allow-dirty`` only); mode from the filesystem,
    the only place an uncommitted file has one."""
    raw = _git(["ls-files", "-z", "-o", "--exclude-standard", "--", "."], cwd=template)
    members: list[Member] = []
    for rel in raw.split("\0"):
        if not rel:
            continue
        path = template / rel
        if path.is_symlink():
            raise BuildError(f"symlink inside the template is not allowed: {rel}")
        members.append(Member(rel, path, bool(path.stat().st_mode & stat.S_IXUSR)))
    return members


def collect_members(template: Path, *, include_untracked: bool = False) -> list[Member]:
    """The zip's members, sorted by relpath: the git index of ``template`` (plus, when
    ``include_untracked``, the untracked-but-not-ignored files). ``__pycache__/``,
    ``.ale-cache/`` and ``*.pyc`` are dropped even if tracked; a symlink or submodule is an
    error (the server rejects symlinks, so shipping one would only teach a shape that
    bounces)."""
    if not template.is_dir():
        raise BuildError(f"template folder not found: {template}")
    members = _tracked_members(template)
    if include_untracked:
        members += _untracked_members(template)
    kept = [m for m in members
            if not any(part in _EXCLUDED_DIRS for part in m.rel.split("/"))
            and not m.rel.endswith(_EXCLUDED_SUFFIXES)]
    kept.sort(key=lambda m: m.rel)
    return kept


def _zip_info(arcname: str, *, executable: bool) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(arcname, date_time=FIXED_MTIME)
    mode = 0o755 if executable else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # unix, so the mode bits are honoured on extraction
    return info


def source_document(*, repo: str, commit: str, generated_at: str, template_path: str,
                    dirty: bool) -> dict[str, object]:
    doc: dict[str, object] = {
        "repo": repo,
        "commit": f"{commit}-dirty" if dirty else commit,
        "generated_at": generated_at,
        "template_path": template_path,
        "zip_folder": ZIP_FOLDER,
    }
    if dirty:
        doc["dirty"] = True
    return doc


def _json_bytes(doc: object) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_zip(out: Path, *, template: Path = DEFAULT_TEMPLATE, repo: str = DEFAULT_REPO,
              generated_at: str | None = None, allow_dirty: bool = False,
              guide: str = GETTING_STARTED) -> dict[str, object]:
    """Write the template zip to ``out`` and return the TEMPLATE-SOURCE document.

    Member order (fixed, so the zip is deterministic): the two root helper files, then the
    task folder, sorted."""
    template = template.resolve()
    if not template.is_dir():
        raise BuildError(f"template folder not found: {template}")
    status = template_status(template)
    if status and not allow_dirty:
        raise BuildError(
            f"{template} is not clean; commit or remove these before building "
            f"(or pass --allow-dirty for a throwaway zip):\n{status}"
        )
    members = collect_members(template, include_untracked=allow_dirty)
    try:
        template_path = template.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        template_path = template.as_posix()
    if generated_at is None:
        generated_at = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    source = source_document(repo=repo, commit=source_commit(template),
                             generated_at=generated_at, template_path=template_path,
                             dirty=bool(status))

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(_zip_info(GUIDE_NAME, executable=False), guide.encode("utf-8"))
        zf.writestr(_zip_info(SOURCE_NAME, executable=False), _json_bytes(source))
        for member in members:
            zf.writestr(_zip_info(f"{ZIP_FOLDER}/{member.rel}", executable=member.executable),
                        member.path.read_bytes())
    return source


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_source_out(source: dict[str, object], zip_path: Path, dest: Path) -> dict[str, object]:
    """Write the website's ``SOURCE.json``: the TEMPLATE-SOURCE document plus the
    ``sha256`` of the zip as written (what the Contribute page reads)."""
    doc: dict[str, object] = dict(source, sha256=sha256_of(zip_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="zip path to write")
    parser.add_argument("--source-out", type=Path, default=None,
                        help="also write SOURCE.json here: TEMPLATE-SOURCE.json plus the "
                             "sha256 of the written zip (the website's copy)")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE,
                        help=f"template folder (default {DEFAULT_TEMPLATE.relative_to(REPO_ROOT)})")
    parser.add_argument("--repo", default=DEFAULT_REPO,
                        help="owner/name recorded in TEMPLATE-SOURCE.json")
    parser.add_argument("--generated-at", default=None,
                        help="ISO-8601 UTC stamp for TEMPLATE-SOURCE.json (default: now; "
                             "pin it for a byte-identical rebuild)")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="build from a template tree with uncommitted changes; the "
                             "source document is stamped <sha>-dirty / dirty=true")
    args = parser.parse_args(argv)
    try:
        source = build_zip(args.out, template=args.template, repo=args.repo,
                           generated_at=args.generated_at, allow_dirty=args.allow_dirty)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    with zipfile.ZipFile(args.out) as zf:
        count = len(zf.namelist())
    print(f"wrote {args.out} ({count} entries) from {source['repo']}@{source['commit']}")
    if args.source_out is not None:
        doc = write_source_out(source, args.out, args.source_out)
        print(f"wrote {args.source_out} (sha256 {str(doc['sha256'])[:12]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
