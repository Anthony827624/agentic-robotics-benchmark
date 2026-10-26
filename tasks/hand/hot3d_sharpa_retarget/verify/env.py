"""Run one absolute-PD submission in the hidden Isaac/PhysX scene."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


class ReplayError(RuntimeError):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tail(text, count=60):
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    return "\n".join((text or "").splitlines()[-count:])


def run_replay(config, submission):
    stage = Path(__file__).resolve().parent
    flashdex = Path("/opt/flashdex")
    scene = stage / config["scene"]
    clip = config["clip"]
    source_id = int(config["source_motion_id"])
    local_id = int(config["local_motion_id"])
    copies = int(config["replay_copies"])
    work = Path(tempfile.mkdtemp(prefix="hot3d-sharpa-replay-"))
    spec = {
        "task": "hot3d_sharpa_retarget",
        "mode": "direct",
        "num_envs": copies,
        "steps": int(config["frames"]),
        "motion_file": str(scene),
        "motion_sha256": _sha256(scene),
        "motion_ids": [local_id] * copies,
        "target_envs": list(range(copies)),
        "target_motion_ids": [source_id] * copies,
        "pd_targets": {
            str(source_id): {"path": str(submission), "sha256": _sha256(submission)}
        },
        "targets": [{"motion_id": source_id, "name": clip, "frames": config["frames"]}],
        "perturbation": config["perturbation"],
        "id_map": {str(local_id): source_id},
        "source_eval_file": None,
        "eval": {
            "seed": int(config["seed"]),
            "num_per_motion": 1,
            "success_criterion": "full",
            "grace_steps": int(config["grace_steps"]),
            "threshold_ks": config["threshold_ks"],
            "thresholds": config["thresholds"],
            "command": {
                "sampling_mode": "start",
                "noise_to_initial_level": 0.0,
                "start_frame": 0,
            },
        },
    }
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec, indent=2))
    env = os.environ.copy()
    env.pop("HEADLESS", None)
    env["PYTHONPATH"] = str(flashdex / "src")
    env["MUJOCO_GL"] = "disabled"
    env["OMNI_KIT_ACCEPT_EULA"] = "1"
    env["NVIDIA_DRIVER_CAPABILITIES"] = "compute,utility"
    command = [
        config["isaac_python"],
        str(flashdex / "scripts/replay_hot3d_pd.py"),
        "--spec",
        str(spec_path),
        "--out-dir",
        str(work / "result"),
    ]
    try:
        process = subprocess.run(
            command,
            cwd=flashdex,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=int(config["replay_timeout_s"]),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReplayError(
            "Isaac replay exceeded {} seconds\n{}".format(
                config["replay_timeout_s"], _tail(exc.stdout)
            )
        ) from exc
    (work / "run.log").write_text(process.stdout or "")
    report_path = work / "result/report.json"
    if not report_path.is_file():
        raise ReplayError(
            "Isaac replay exited {} without a report\n{}".format(
                process.returncode, _tail(process.stdout)
            )
        )
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError) as exc:
        raise ReplayError("cannot read replay report: {}".format(exc)) from exc
    clips = report.get("clips")
    if not isinstance(clips, list) or len(clips) != copies:
        raise ReplayError("replay report does not contain {} copies".format(copies))
    if any(clip.get("motion_id") != local_id for clip in clips):
        raise ReplayError("replay report has the wrong local motion id")
    if any(set(clip.get("errors", {})) != set(config["thresholds"]) for clip in clips):
        raise ReplayError("replay report lacks the per-criterion errors")
    if process.returncode not in (0, 2):
        raise ReplayError(
            "Isaac replay exited {} after producing an incomplete report\n{}".format(
                process.returncode, _tail(process.stdout)
            )
        )
    return report
