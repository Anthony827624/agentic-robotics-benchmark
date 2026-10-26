"""Eval callback that captures what the action term actually did, per step.

raw mode (ResidualAction/LpfResidualAction envs): for each tracked env it
records the action manager's raw policy row, the term's processed (post
scale/offset/clip/LPF) row, and ``ref_joint_pos_used`` — the reference frame
the term baked into its PD targets at apply time. The actual applied target is
recomputed OFFLINE from these three series (calling ``_target_parts()`` in
``on_step`` would read the reference of the NEXT frame, since the command has
already advanced ``motion_steps`` by then).

direct mode (DirectPDAction envs): records ``last_target``, the absolute PD
row the term wrote on the last decimation substep of the step.

``reduce_state`` writes one capture NPZ per motion_id. This callback
deliberately does NOT implement ``rollout_arrays`` (it must not be picked as
the success-archive recorder) and keeps ``rollout_steps = 1`` so it never
lengthens the eval sweep. It also dumps ``action_term_params.json`` once per
eval pass: the authoritative runtime values (joint layout, soft limits,
residual flags/scales, LPF alphas, group sizes, action scale/offset, dt) that
the offline raw->absolute-PD conversion needs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import torch

from envs._common import action_term


class ActionCapture:
    def __init__(
        self,
        command_name: str = "motion",
        mode: str = "raw",
        env_indices: list[int] | tuple[int, ...] | None = None,
        max_steps: int | None = None,
        out_dir: str | Path | None = None,
    ) -> None:
        if mode not in ("raw", "direct"):
            raise ValueError(f"mode must be 'raw' or 'direct', got {mode!r}")
        self.command_name = str(command_name)
        self.mode = mode
        self.env_indices = [int(i) for i in (env_indices or [])]
        self.max_steps = None if max_steps is None else int(max_steps)
        self.out_dir = Path(out_dir) if out_dir is not None else None
        # Never drive the sweep length; never become the archive recorder.
        self.rollout_steps = 1
        self._params_written = False

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def on_eval_setup(self, env: Any) -> None:
        self.cmd = env.command_manager.get_term(self.command_name)
        if self.out_dir is not None and not self._params_written:
            self._write_params(env)
            self._params_written = True

    def on_start(self, env: Any) -> None:
        self.cmd = env.command_manager.get_term(self.command_name)
        self._rows: dict[int, list[dict[str, torch.Tensor]]] = {
            i: [] for i in self.env_indices
        }
        self.t = 0

    def on_step(self, env: Any) -> None:
        t = self.t
        self.t += 1
        if self.max_steps is not None and t >= self.max_steps:
            return
        if not self.env_indices:
            return
        manager = env.action_manager
        term = action_term(env)
        for i in self.env_indices:
            if self.mode == "raw":
                row = {
                    "raw": manager.action[i].detach().to("cpu", torch.float32),
                    "processed": term._processed_actions[i]
                    .detach()
                    .to("cpu", torch.float32),
                    "ref": (
                        term.ref_joint_pos_used[i].detach().to("cpu", torch.float32)
                        if term.ref_joint_pos_used is not None
                        else torch.zeros(term.action_dim)
                    ),
                    "motion_steps": self.cmd.motion_steps[i].detach().to("cpu"),
                }
            else:
                row = {
                    "target": (
                        term.last_target[i].detach().to("cpu", torch.float32)
                        if term.last_target is not None
                        else torch.full((term.action_dim,), float("nan"))
                    ),
                    "motion_steps": self.cmd.motion_steps[i].detach().to("cpu"),
                }
            self._rows[i].append(row)

    def collect_state(self, active_mask: torch.Tensor | None = None) -> dict[str, Any]:
        del active_mask  # capture envs are explicit, not active-mask derived
        motion_ids = (
            self.cmd.motion_ids[self.env_indices].detach().to("cpu", torch.long)
            if self.env_indices
            else torch.zeros(0, dtype=torch.long)
        )
        return {
            "rows": {i: self._rows[i] for i in self.env_indices},
            "motion_ids": motion_ids,
            "env_indices": list(self.env_indices),
            "mode": self.mode,
        }

    def reduce_state(self, states: list[dict[str, Any]]) -> dict[str, float]:
        if self.out_dir is None:
            return {}
        out_dir = self.out_dir / f"capture_{self.mode}"
        out_dir.mkdir(parents=True, exist_ok=True)
        for batch_idx, state in enumerate(states):
            for local_idx, env_idx in enumerate(state["env_indices"]):
                rows = state["rows"][env_idx]
                if not rows:
                    continue
                mid = int(state["motion_ids"][local_idx].item())
                fields: dict[str, np.ndarray] = {}
                for key in rows[0]:
                    stacked = torch.stack([r[key] for r in rows], dim=0)
                    fields[key] = stacked.numpy()
                fields["env_index"] = np.int64(env_idx)
                fields["batch_idx"] = np.int64(batch_idx)
                name = f"m{mid:05d}.npz"
                if (out_dir / name).exists():
                    name = f"m{mid:05d}_b{batch_idx}.npz"
                np.savez_compressed(out_dir / name, **fields)
        return {}

    def get_metrics(self) -> dict[str, float]:
        return {}

    def on_end(self) -> None:
        pass

    # ── authoritative runtime parameters ──────────────────────────────────────
    def _write_params(self, env: Any) -> None:
        term = action_term(env)
        entity = term._entity
        cfg = term.cfg
        clip = getattr(term, "_clip", None)
        params: dict[str, Any] = {
            "term_class": type(term).__name__,
            "term_key": next(iter(env.action_manager._terms)),
            "mode": self.mode,
            "command_name": self.command_name,
            "action_dim": int(term.action_dim),
            "all_joint_ids": [int(i) for i in term._all_joint_ids.tolist()],
            "joint_names": [
                entity.joint_names[j] for j in term._all_joint_ids.tolist()
            ],
            "lower": [float(v) for v in term._lower.tolist()],
            "upper": [float(v) for v in term._upper.tolist()],
            "group_sizes": {k: int(v) for k, v in term._group_sizes.items()},
            "is_residual": (
                [bool(v) for v in term._is_residual.tolist()]
                if hasattr(term, "_is_residual")
                else None
            ),
            "residual_scale": (
                [float(v) for v in term._residual_scale.tolist()]
                if hasattr(term, "_residual_scale")
                else None
            ),
            "lpf_alpha": (
                [float(v) for v in term._lpf_alpha.tolist()]
                if hasattr(term, "_lpf_alpha")
                else None
            ),
            "action_scale": float(getattr(cfg, "action_scale", 1.0)),
            "action_offset": float(getattr(cfg, "action_offset", 0.0)),
            "clip_min": (
                [float(v) for v in clip[0, :, 0].tolist()] if clip is not None else None
            ),
            "clip_max": (
                [float(v) for v in clip[0, :, 1].tolist()] if clip is not None else None
            ),
            "ref_lag": int(getattr(term, "_ref_lag", 0))
            if hasattr(term, "_ref_lag")
            else 0,
            "dt": float(env.step_dt),
            "physics_dt": float(env.physics_dt),
            "decimation": int(env.cfg.decimation),
            "num_envs": int(env.num_envs),
            "device": str(env.device),
            "motion_file": str(getattr(self.cmd.motion_lib, "motion_file", "") or ""),
        }
        out_dir = Path(self.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "action_term_params.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(params, indent=2))
        tmp.replace(path)
        print(f"[capture] action term params -> {path}", flush=True)
