"""Validate NPZ submissions and score independent physical replays."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env import ReplayError, run_copy  # noqa: E402
from robotics_grader import (  # noqa: E402
    ratio_rewards,
    read_anchor,
    require,
    stage_config,
    write_verdict,
    zero_verdict,
)

MAX_FILE_BYTES = 10_000_000
MAX_MEMBER_BYTES = 16_000_000  # uncompressed, per array in the NPZ


class SubmissionError(ValueError):
    pass


def _scalar(data, key):
    value = np.asarray(data[key])
    if value.size != 1:
        raise SubmissionError("{} must be a scalar".format(key))
    return value.reshape(()).item()


def _check_container(raw, config):
    """The NPZ as a ZIP: every member small enough once inflated, and the header of pd_targets declaring the expected
    shape and dtype before any array is read (a few bytes can otherwise declare terabytes)."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = {info.filename: info for info in archive.infolist()}
        for info in members.values():
            if info.file_size > MAX_MEMBER_BYTES:
                raise SubmissionError("NPZ member {} is too large".format(info.filename))
        if "pd_targets.npy" not in members:
            raise SubmissionError("missing NPZ key: pd_targets")
        with archive.open("pd_targets.npy") as handle:
            version = np.lib.format.read_magic(handle)
            if version == (1, 0):
                shape, fortran, dtype = np.lib.format.read_array_header_1_0(handle)
            elif version == (2, 0):
                shape, fortran, dtype = np.lib.format.read_array_header_2_0(handle)
            else:
                raise SubmissionError("unsupported .npy version {}".format(version))
    expected_shape = (int(config["frames"]), int(config["action_dim"]))
    if tuple(shape) != expected_shape:
        raise SubmissionError("pd_targets must have shape {}, got {}".format(expected_shape, tuple(shape)))
    if np.dtype(dtype) != np.float32:
        raise SubmissionError("pd_targets must be float32")


def validate_submission(path, config):
    """Check one clip's file against the protocol; return its bytes and their sha256. Any problem with the file is a
    SubmissionError (that clip scores zero), never a crash of the grader."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise SubmissionError("missing submission: {}".format(path.name)) from None
    except OSError as exc:
        raise SubmissionError("unreadable submission: {}".format(exc)) from exc
    if not stat.S_ISREG(info.st_mode):
        raise SubmissionError("submission is not a regular file: {}".format(path.name))
    if info.st_size > MAX_FILE_BYTES:
        raise SubmissionError("submission is too large")
    bounds = json.loads(
        (Path(__file__).resolve().parent / config["protocol"]).read_text()
    )
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise SubmissionError("submission is too large")
        _check_container(raw, config)
        with np.load(io.BytesIO(raw), allow_pickle=False) as data:
            for key in ("pd_targets", "control_dt", "joint_names", "motion_id"):
                if key not in data.files:
                    raise SubmissionError("missing NPZ key: {}".format(key))
            target = data["pd_targets"]
            expected_shape = (int(config["frames"]), int(config["action_dim"]))
            if target.shape != expected_shape:
                raise SubmissionError(
                    "pd_targets must have shape {}, got {}".format(
                        expected_shape, target.shape
                    )
                )
            if target.dtype != np.float32:
                raise SubmissionError("pd_targets must be float32")
            if not np.isfinite(target).all():
                raise SubmissionError("pd_targets contains non-finite values")
            lower = np.asarray(bounds["lower"], dtype=np.float32)
            upper = np.asarray(bounds["upper"], dtype=np.float32)
            tolerance = float(config["limit_tolerance"])
            if (target < lower - tolerance).any() or (target > upper + tolerance).any():
                raise SubmissionError("pd_targets exceeds the declared joint limits")
            dt = float(_scalar(data, "control_dt"))
            if not math.isfinite(dt) or abs(dt - float(config["control_dt"])) > float(
                config["dt_tolerance"]
            ):
                raise SubmissionError("control_dt does not match the 60 Hz protocol")
            names = np.asarray(data["joint_names"])
            if (
                names.shape != (int(config["action_dim"]),)
                or names.dtype.kind not in "US"
            ):
                raise SubmissionError(
                    "joint_names must be a one-dimensional string array"
                )
            if names.tolist() != bounds["joint_names"]:
                raise SubmissionError(
                    "joint_names does not match the Sharpa control order"
                )
            motion_id = _scalar(data, "motion_id")
            if isinstance(motion_id, float) and not math.isfinite(motion_id):
                raise SubmissionError("motion_id must be a finite integer")
            if int(motion_id) != int(config["source_motion_id"]):
                raise SubmissionError("motion_id does not match the requested clip")
    except SubmissionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- whatever the file does to the reader is the file's problem
        raise SubmissionError("unreadable NPZ submission: {}: {}".format(type(exc).__name__, exc)) from exc
    return raw, hashlib.sha256(raw).hexdigest()


def hold_control(config, bounds):
    """A valid submission that holds the clip's public initial joint positions: the control replay."""
    target = np.tile(np.asarray(config["initial_joint_pos"], dtype=np.float32), (int(config["frames"]), 1))
    buffer = io.BytesIO()
    np.savez(
        buffer,
        pd_targets=target,
        control_dt=np.float64(config["control_dt"]),
        joint_names=np.asarray(bounds["joint_names"]),
        motion_id=np.int64(config["source_motion_id"]),
    )
    return buffer.getvalue()


def _finite_errors(copy):
    return all(math.isfinite(float(v)) for v in copy.get("errors", {}).values())


def worst_ratio(errors, thresholds):
    return max(float(errors[key]) / float(limit) for key, limit in thresholds.items())


def progress_gate(errors, config):
    """Share of the object's reference motion reproduced, saturating at progress_full.

    An object left where it started has error static_obj_trans and gets 0, however well
    the hands follow the reference.
    """
    progress = 1.0 - float(errors["obj_trans"]) / float(config["static_obj_trans"])
    return min(1.0, max(0.0, progress / float(config["progress_full"])))


def clip_score(copies, config):
    """Mean over the physics copies of hand quality times object credit.

    Hand quality is the linear score on the worse hand's worst hand ratio (fingertips, finger links, finger motion in
    the wrist frame). Object credit is hand_only_credit plus the rest scaled by the progress gate: tracking the hands
    without moving the object earns at most hand_only_credit, and everything above it requires reproducing the
    object's motion. A copy that failed to replay, was not scored, or reports a non-finite error scores 0.
    """
    zero_at = float(config["score_zero_at_ratio"])
    floor = float(config["hand_only_credit"])
    hand = {k: config["thresholds"][k] for k in config["hand_criteria"]}
    scores, passes, ratios = [], [], []
    for copy in copies:
        if copy is not None and copy["scored"] and _finite_errors(copy):
            ratio = worst_ratio(copy["errors"], hand)
            gate = progress_gate(copy["errors"], config)
            passes.append(float(worst_ratio(copy["errors"], config["thresholds"]) <= 1.0))
        else:
            ratio, gate = zero_at, 0.0     # hand quality 0 (and a finite mean ratio in the metrics)
            passes.append(0.0)
        ratios.append(ratio)
        quality = min(1.0, max(0.0, (zero_at - ratio) / (zero_at - 1.0)))
        scores.append(quality * (floor + (1.0 - floor) * gate))
    return sum(scores) / len(scores), sum(passes) / len(passes), sum(ratios) / len(ratios)


def replay_all(configs, submissions, workers, bounds, scratch):
    """Replay every copy of every valid clip, each in its own Isaac process, `workers` at a time.

    A copy whose replay fails is replayed once more. If it fails again, the clip's control (the same scene with a
    submission that holds the initial pose) is replayed: if the control replays, the failure is the submission's and
    that copy scores 0; if the control fails too, the simulator itself does not work here, which is an infrastructure
    failure and ends the verification as a task error (the run is repeated, not scored).
    Returns {clip: [copy report or None]} and the counts of retried and failed copies.
    """
    jobs = [(cfg, k) for cfg in configs if cfg["clip"] in submissions for k in range(int(cfg["replay_copies"]))]

    def one(job):
        cfg, k = job
        path, sha = submissions[cfg["clip"]]
        errors = []
        for _ in range(2):
            try:
                return job, run_copy(cfg, path, sha, k)["clips"][0], len(errors)
            except ReplayError as exc:
                errors.append(str(exc))
        return job, errors, len(errors)

    results, retried, last_error = {}, 0, {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for (cfg, k), outcome, attempts in pool.map(one, jobs):
            results[(cfg["clip"], k)] = outcome
            retried += int(attempts > 0)
            if isinstance(outcome, list):
                last_error.setdefault(cfg["clip"], outcome[-1])
    failed_clips = sorted(last_error)
    by_clip = {cfg["clip"]: cfg for cfg in configs}

    def control(clip):
        cfg = by_clip[clip]
        raw = hold_control(cfg, bounds)
        path = scratch / ("control_" + clip + ".npz")
        path.write_bytes(raw)
        try:
            run_copy(cfg, path, hashlib.sha256(raw).hexdigest(), 0)
            return clip, None
        except ReplayError as exc:
            return clip, str(exc)

    if failed_clips:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for clip, problem in pool.map(control, failed_clips):
                require(problem is None, "{}: the replay failed twice and so did its control replay (an "
                        "infrastructure failure): {}\ncontrol: {}".format(clip, last_error[clip], problem))
                print("[grader] {}: replays failed twice while the control replays: those copies score 0\n{}".format(
                    clip, last_error[clip]), file=sys.stderr, flush=True)
    reports = {}
    failed = 0
    for cfg in configs:
        if cfg["clip"] not in submissions:
            continue
        copies = []
        for k in range(int(cfg["replay_copies"])):
            outcome = results[(cfg["clip"], k)]
            if isinstance(outcome, list):
                failed += 1
                copies.append(None)
            else:
                copies.append(outcome)
        reports[cfg["clip"]] = copies
    return reports, retried, failed


def main():
    config = stage_config()
    anchor, anchor_meta = read_anchor()
    home = Path(os.environ.get("ALE_HOME", "/home/user"))
    bounds = json.loads((Path(__file__).resolve().parent / config["protocol"]).read_text())
    shared = {key: value for key, value in config.items() if key != "clips"}
    configs = [{**shared, **override} for override in config["clips"]]
    measured = []
    passed = []
    ratios = {}
    invalid = []
    completed = 0
    scored_envs = 0.0
    valid = {}
    # the validated bytes go to a root-only folder; the replays read that copy, never the agent's file again
    scratch = Path(tempfile.mkdtemp(prefix="hot3d-sharpa-submission-"))
    for cfg in configs:
        submission = home / "submission" / (cfg["clip"] + ".npz")
        try:
            raw, sha = validate_submission(submission, cfg)
        except SubmissionError as exc:
            invalid.append("{}: {}".format(cfg["clip"], exc))
            continue
        copy = scratch / (cfg["clip"] + ".npz")
        copy.write_bytes(raw)
        valid[cfg["clip"]] = (copy, sha)
    reports, retried, failed = replay_all(configs, valid, int(config.get("replay_workers", 1)), bounds, scratch)
    for cfg in configs:
        copies = reports.get(cfg["clip"])
        if copies is None:
            measured.append(0.0)
            passed.append(0.0)
            continue
        completed += 1
        score, pass_rate, mean_ratio = clip_score(copies, cfg)
        measured.append(score)
        passed.append(pass_rate)
        ratios[cfg["clip"]] = mean_ratio
        scored_envs += float(sum(1 for c in copies if c is not None and c["scored"]))

    if not completed:
        verdict = zero_verdict(
            reason="; ".join(invalid),
            anchored=anchor is not None,
            extra={"match_lock_ok": 0.0, "physics_replay_completed": 0.0},
        )
    else:
        verdict = ratio_rewards(
            sum(measured) / len(measured),
            anchor,
            cap=config.get("scoring_cap", 1.5),
            higher_is_better=anchor_meta.get("direction", "higher") == "higher",
            metric_name="mean_clip_score",
            full_at=anchor_meta.get("full_at", 1.0),
            extra={
                "match_lock_ok": 1.0,
                "physics_replay_completed": completed / len(measured),
                "num_scored_envs": scored_envs,
                "replays_retried": float(retried),
                "replays_failed": float(failed),
                "full_success_rate": sum(passed) / len(passed),
                **{"clip_score/" + c["clip"]: m for c, m in zip(configs, measured)},
                **{"mean_hand_ratio/" + c: r for c, r in ratios.items()},
            },
        )
    write_verdict(verdict)
    print(
        "[grader] mean_clip_score={} clip_scores={} sr1_rate={} reward={} retried={} failed={} invalid={}".format(
            sum(measured) / len(measured), measured, sum(passed) / len(passed),
            verdict["rewards"]["reward"], retried, failed, invalid
        ),
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
