"""Archive the eval clips that pass the success criterion: one npz rollout per motion plus
manifest.json. A motion keeps the first clip archived for it."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import torch


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic rewrite, so a killed job cannot leave a truncated manifest."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def export_success(
    out_dir: str | Path,
    rec_cb: Any,
    rec_states: list[dict[str, Any]],
    ok: torch.Tensor,
    motion_ids: torch.Tensor,
    env_step: int,
    criterion: str,
    motion_names: list[str] | None = None,
    motion_file: str | None = None,
    source_info: dict[str, Any] | None = None,
) -> int:
    """Archive the first passing env row of each motion not archived yet; returns the count.
    ``ok`` and ``motion_ids`` are per env over the concatenated eval sweep."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    entries: dict[str, dict[str, Any]] = {}
    if manifest_path.exists():
        try:
            prev = json.loads(manifest_path.read_text())
            previous = prev.get("saved", [])
            if previous and prev.get("criterion") != criterion:
                raise ValueError(
                    f"archive criterion {prev.get('criterion')!r} != {criterion!r}"
                )
            previous_motion = prev.get("motion_file")
            if (
                previous
                and previous_motion
                and motion_file
                and Path(previous_motion).resolve() != Path(motion_file).resolve()
            ):
                raise ValueError(
                    f"archive motion file {previous_motion!r} != {motion_file!r}"
                )
            entries = {str(e["motion_id"]): e for e in previous}
            if len(entries) != len(previous):
                raise ValueError("archive contains duplicate motion_id entries")
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"unreadable success manifest {manifest_path}: {exc}") from exc

    source = source_info or {}
    written = 0
    for row in torch.nonzero(ok, as_tuple=False).flatten().tolist():
        mid = int(motion_ids[row].item())
        if str(mid) in entries:
            continue
        arrays = rec_cb.rollout_arrays(rec_states, row)
        if arrays is None:
            continue
        rel = f"rollouts/m{mid:05d}.npz"
        rollout_path = out_dir / rel
        rollout_path.parent.mkdir(exist_ok=True)
        tmp_path = rollout_path.with_suffix(".npz.tmp")
        with tmp_path.open("wb") as f:
            np.savez_compressed(f, **{k: np.asarray(v) for k, v in arrays.items()})
        os.replace(tmp_path, rollout_path)
        entries[str(mid)] = {
            "motion_id": mid,
            "name": motion_names[mid] if motion_names is not None and mid < len(motion_names) else None,
            "frames": int(arrays["joint_pos"].shape[0]),
            "rollout": rel,
            "first_saved_env_step": int(env_step),
            "source_run_dir": source.get("run_dir"),
            "source_checkpoint": source.get("checkpoint"),
            "parent_checkpoint": source.get("parent_checkpoint"),
            "source_eval_file": source.get("eval_file"),
            "eval_num_envs": source.get("num_envs"),
            "eval_num_batches": source.get("num_batches"),
        }
        written += 1
    if not written:
        return 0

    saved = sorted(entries.values(), key=lambda e: int(e["motion_id"]))
    _write_json(
        manifest_path,
        {
            "criterion": criterion,
            "motion_file": motion_file,
            "num_saved": len(saved),
            "updated_at_env_step": int(env_step),
            "fps": 60,
            "coordinate_frame": "env-local (scene env_origins subtracted); env_origin stored per rollout",
            "frame_alignment": "time series follow env.step; reset state is in initial_* fields",
            "fields": "joint_pos, joint_vel, actions, root_pos, root_quat, obj_pos, obj_quat, initial_*, env_origin",
            "saved": saved,
        },
    )
    print(f"[success] archived {written} new clip(s); {len(saved)} motions in {out_dir}", flush=True)
    return written
