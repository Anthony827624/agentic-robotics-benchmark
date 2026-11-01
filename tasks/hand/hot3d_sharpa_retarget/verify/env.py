"""Run one copy of an absolute-PD submission in the hidden Isaac/PhysX scene, in its own process.

Each copy is its own Isaac process with a one-environment scene: copies that share a PhysX scene change each other's
solution (the unperturbed copy's result moved with the perturbations of the others)."""

import json
import os
from pathlib import Path
import subprocess
import tempfile


class ReplayError(RuntimeError):
    pass


def _tail(text, count=60):
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    return "\n".join((text or "").splitlines()[-count:])


def run_copy(config, submission, sha256, copy_index):
    """Replay `submission` (a root-only copy of the validated file, `sha256` its digest) as copy `copy_index` of the
    clip: copy 0 starts exactly from the clip's initial state, copy k > 0 from the fixed perturbation seed + k."""
    stage = Path(__file__).resolve().parent
    flashdex = Path("/opt/flashdex")
    scene = stage / config["scene"]
    clip = config["clip"]
    source_id = int(config["source_motion_id"])
    local_id = int(config["local_motion_id"])
    work = Path(tempfile.mkdtemp(prefix="hot3d-sharpa-replay-"))
    spec = {
        "task": "hot3d_sharpa_retarget",
        "mode": "direct",
        "num_envs": 1,
        "steps": int(config["frames"]),
        "motion_file": str(scene),
        "motion_ids": [local_id],
        "target_envs": [0],
        "target_motion_ids": [source_id],
        "pd_targets": {str(source_id): {"path": str(submission), "sha256": sha256}},
        "targets": [{"motion_id": source_id, "name": clip, "frames": config["frames"], "copy": copy_index}],
        "perturbation": {**config["perturbation"], "copy_index": int(copy_index)},
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
    if not isinstance(clips, list) or len(clips) != 1:
        raise ReplayError("replay report does not contain exactly one copy")
    if clips[0].get("motion_id") != local_id:
        raise ReplayError("replay report has the wrong local motion id")
    if set(clips[0].get("errors", {})) != set(config["thresholds"]):
        raise ReplayError("replay report lacks the per-criterion errors")
    if process.returncode not in (0, 2):
        raise ReplayError(
            "Isaac replay exited {} after producing an incomplete report\n{}".format(
                process.returncode, _tail(process.stdout)
            )
        )
    return report
