"""Validate NPZ submissions and score independent physical replays."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env import ReplayError, run_replay  # noqa: E402
from robotics_grader import (  # noqa: E402
    ratio_rewards,
    read_anchor,
    require,
    stage_config,
    write_verdict,
    zero_verdict,
)


class SubmissionError(ValueError):
    pass


def _scalar(data, key):
    value = np.asarray(data[key])
    if value.size != 1:
        raise SubmissionError("{} must be a scalar".format(key))
    return value.reshape(()).item()


def validate_submission(path, config):
    if not path.is_file():
        raise SubmissionError("missing submission: {}".format(path.name))
    if path.stat().st_size > 10_000_000:
        raise SubmissionError("submission is too large")
    bounds = json.loads(
        (Path(__file__).resolve().parent / config["protocol"]).read_text()
    )
    try:
        with np.load(path, allow_pickle=False) as data:
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
            if not np.isfinite(dt) or abs(dt - float(config["control_dt"])) > float(
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
            if int(motion_id) != int(config["source_motion_id"]):
                raise SubmissionError("motion_id does not match the requested clip")
    except SubmissionError:
        raise
    except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
        raise SubmissionError("unreadable NPZ submission: {}".format(exc)) from exc


def worst_ratio(errors, thresholds):
    return max(float(errors[key]) / float(limit) for key, limit in thresholds.items())


def progress_gate(errors, config):
    """Share of the object's reference motion reproduced, saturating at progress_full.

    An object left where it started has error static_obj_trans and gets 0, however well
    the hands follow the reference.
    """
    progress = 1.0 - float(errors["obj_trans"]) / float(config["static_obj_trans"])
    return min(1.0, max(0.0, progress / float(config["progress_full"])))


def clip_score(report, config):
    """Mean over the physics copies of hand quality times object credit.

    Hand quality is the linear score on the worse hand's worst hand ratio (fingertips,
    finger links). Object credit is hand_only_credit plus the rest scaled by the
    progress gate: tracking the hands without moving the object earns at most
    hand_only_credit, and everything above it requires reproducing the object's motion.
    """
    zero_at = float(config["score_zero_at_ratio"])
    floor = float(config["hand_only_credit"])
    hand = {k: config["thresholds"][k] for k in config["hand_criteria"]}
    scores, passes, ratios = [], [], []
    for copy in report["clips"]:
        if copy["scored"]:
            ratio = worst_ratio(copy["errors"], hand)
            gate = progress_gate(copy["errors"], config)
            passes.append(float(worst_ratio(copy["errors"], config["thresholds"]) <= 1.0))
        else:
            ratio, gate = float("inf"), 0.0
            passes.append(0.0)
        ratios.append(ratio)
        quality = min(1.0, max(0.0, (zero_at - ratio) / (zero_at - 1.0)))
        scores.append(quality * (floor + (1.0 - floor) * gate))
    return sum(scores) / len(scores), sum(passes) / len(passes), sum(ratios) / len(ratios)


def replay_all(configs, submissions, workers):
    """Replay each valid clip in its own Isaac process, `workers` at a time.

    Every clip keeps its own scene: a scene that holds several clips changes the PhysX
    solution, so the processes run side by side instead of sharing one simulator.
    """
    jobs = [(cfg, submissions[cfg["clip"]]) for cfg in configs if cfg["clip"] in submissions]
    reports = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(run_replay, cfg, path): cfg["clip"] for cfg, path in jobs}
        for future in as_completed(futures):
            try:
                reports[futures[future]] = future.result()
            except ReplayError as exc:
                require(False, "{}: {}".format(futures[future], exc))
    return reports


def main():
    config = stage_config()
    anchor, anchor_meta = read_anchor()
    home = Path(os.environ.get("ALE_HOME", "/home/user"))
    shared = {key: value for key, value in config.items() if key != "clips"}
    configs = [{**shared, **override} for override in config["clips"]]
    measured = []
    passed = []
    ratios = {}
    invalid = []
    completed = 0
    scored_envs = 0.0
    valid = {}
    for cfg in configs:
        submission = home / "submission" / (cfg["clip"] + ".npz")
        try:
            validate_submission(submission, cfg)
        except SubmissionError as exc:
            invalid.append("{}: {}".format(cfg["clip"], exc))
            continue
        valid[cfg["clip"]] = submission
    reports = replay_all(configs, valid, int(config.get("replay_workers", 1)))
    for cfg in configs:
        report = reports.get(cfg["clip"])
        if report is None:
            measured.append(0.0)
            passed.append(0.0)
            continue
        completed += 1
        score, pass_rate, mean_ratio = clip_score(report, cfg)
        measured.append(score)
        passed.append(pass_rate)
        ratios[cfg["clip"]] = mean_ratio
        scored_envs += float(report["metrics"].get("eval/num_scored_envs", 0.0))

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
                "full_success_rate": sum(passed) / len(passed),
                **{"clip_score/" + c["clip"]: m for c, m in zip(configs, measured)},
                **{"mean_hand_ratio/" + c: r for c, r in ratios.items()},
            },
        )
    write_verdict(verdict)
    print(
        "[grader] mean_clip_score={} clip_scores={} sr1_rate={} reward={} invalid={}".format(
            sum(measured) / len(measured), measured, sum(passed) / len(passed),
            verdict["rewards"]["reward"], invalid
        ),
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
