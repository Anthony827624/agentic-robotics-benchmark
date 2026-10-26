"""Check trajectory containers against the public control protocol."""

import json
import os
from pathlib import Path
import sys

import numpy as np


def check_file(path, clip, protocol):
    try:
        with np.load(path, allow_pickle=False) as data:
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
    except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
        valid = False
        detail = {"reason": str(exc)}
    return valid, detail


def main():
    home = Path(os.environ.get("ALE_HOME", "/home/user"))
    protocol = json.loads((home / "control_protocol.json").read_text())
    results = {}
    for clip in protocol["clips"]:
        path = home / "submission" / (clip["clip"] + ".npz")
        valid, detail = check_file(path, clip, protocol)
        results[clip["clip"]] = {"format_valid": valid, "detail": detail}
    all_valid = all(item["format_valid"] for item in results.values())
    print(json.dumps({"format_valid": all_valid, "clips": results}))
    return 0 if all_valid else 1


if __name__ == "__main__":
    sys.exit(main())
