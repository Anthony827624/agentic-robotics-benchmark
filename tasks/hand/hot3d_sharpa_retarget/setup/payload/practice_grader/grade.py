"""Check trajectory containers against the public control protocol (the grader's file rules, without any replay)."""

import io
import json
import os
from pathlib import Path
import stat
import sys
import zipfile

import numpy as np

MAX_FILE_BYTES = 10_000_000  # the grader's limits
MAX_MEMBER_BYTES = 16_000_000
REQUIRED = ("pd_targets", "control_dt", "joint_names", "motion_id")


def directory_problems(root):
    """What makes the whole run fail before any grading: the engine collects /home/user/submission as a tree of plain
    directories and regular files with UTF-8 names."""
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        return ["{} does not exist".format(root)]
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return ["{} is not a plain directory".format(root)]
    problems = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = os.path.join(parent, name)
            try:
                name.encode("utf-8")
            except UnicodeEncodeError:
                problems.append("{!r}: the name is not valid UTF-8".format(path))
                continue
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                problems.append("{}: a symbolic link".format(path))
            elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                problems.append("{}: not a regular file or directory".format(path))
    return problems


def _member(names, key):
    for name in (key + ".npy", key):
        if name in names:
            return name
    return None


def check_container(raw, clip, protocol):
    """As the grader: each required array present and at most 16 MB inflated, pd_targets' header right before reading."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = {info.filename: info for info in archive.infolist()}
        for key in REQUIRED:
            name = _member(infos, key)
            if name is None:
                return "missing NPZ key: {}".format(key)
            if infos[name].file_size > MAX_MEMBER_BYTES:
                return "NPZ array {} is larger than 16 MB".format(key)
        with archive.open(_member(infos, "pd_targets")) as handle:
            version = np.lib.format.read_magic(handle)
            if version == (1, 0):
                shape, _, dtype = np.lib.format.read_array_header_1_0(handle)
            elif version in ((2, 0), (3, 0)):
                shape, _, dtype = np.lib.format.read_array_header_2_0(handle)
            else:
                return "unsupported .npy version {}".format(version)
    if tuple(shape) != (clip["frames"], protocol["action_dim"]) or np.dtype(dtype) != np.float32:
        return "pd_targets is {} {}, expected float32 {}".format(
            np.dtype(dtype), tuple(shape), (clip["frames"], protocol["action_dim"]))
    return None


def check_file(path, clip, protocol):
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            return False, {"reason": "not a regular file"}
        if info.st_size > MAX_FILE_BYTES:
            return False, {"reason": "larger than 10 MB"}
        raw = path.read_bytes()
        problem = check_container(raw, clip, protocol)
        if problem:
            return False, {"reason": problem}
        with np.load(io.BytesIO(raw), allow_pickle=False) as data:
            target = data["pd_targets"]
            names = data["joint_names"].tolist()
            dt = float(np.asarray(data["control_dt"]).reshape(()))
            motion_id = int(np.asarray(data["motion_id"]).reshape(()))
        valid = (
            target.shape == (clip["frames"], protocol["action_dim"])
            and target.dtype == np.float32
            and bool(np.isfinite(target).all())
            and bool((target >= protocol["lower"]).all())
            and bool((target <= protocol["upper"]).all())
            and names == protocol["joint_names"]
            and abs(dt - protocol["control_dt"]) <= protocol["dt_tolerance"]
            and motion_id == clip["source_motion_id"]
        )
        detail = {"shape": list(target.shape), "dtype": str(target.dtype)}
    except Exception as exc:  # noqa: BLE001 -- any unreadable file is an invalid file, as in the grader
        valid = False
        detail = {"reason": "{}: {}".format(type(exc).__name__, exc)}
    return valid, detail


def main():
    home = Path(os.environ.get("ALE_HOME", "/home/user"))
    protocol = json.loads((home / "control_protocol.json").read_text())
    fatal = directory_problems(str(home / "submission"))
    results = {}
    if not fatal:
        for clip in protocol["clips"]:
            path = home / "submission" / (clip["clip"] + ".npz")
            valid, detail = check_file(path, clip, protocol)
            results[clip["clip"]] = {"format_valid": valid, "detail": detail}
    all_valid = not fatal and all(item["format_valid"] for item in results.values())
    report = {"format_valid": all_valid, "clips": results}
    if fatal:
        report["run_fails"] = fatal
        report["note"] = "with these, the whole run fails before grading and is not scored"
    print(json.dumps(report))
    return 0 if all_valid else 1


if __name__ == "__main__":
    sys.exit(main())
