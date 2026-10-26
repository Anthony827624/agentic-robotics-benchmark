"""Absolute joint-position PD action term for open-loop replay.

Unlike ``residual.ResidualAction`` this term carries NO action scale/offset,
NO [-1, 1] clip and NO low-pass filter: the policy-step target IS the absolute
joint position written to the actuators, clamped to the entity soft limits
exactly like the residual term's final clamp. Replay drivers inject the target
sequence with :meth:`DirectPDAction.load_targets`; envs without injected
targets track the motion command's current reference (clamped to soft limits),
which keeps filler envs harmless in partial-coverage replays.

The joint layout mirrors ResidualAction: wrist_trans | wrist_rot | finger in
``actuator_names`` order, mapped to entity joint ids once at build time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import torch
from mjlab.managers.action_manager import ActionTerm, ActionTermCfg

from ..commands.motion_tracking import MotionTrackingCommand

if TYPE_CHECKING:
    from mjlab.entity import Entity
    from mjlab.envs import ManagerBasedRlEnv


@dataclass(kw_only=True)
class DirectPDActionCfg(ActionTermCfg):
    entity_name: str
    command_name: str

    # Groups {wrist_trans, wrist_rot, finger}; the target vector is laid out in
    # this order (same convention as ResidualActionCfg).
    actuator_names: dict[str, tuple[str, ...]]

    def build(self, env: ManagerBasedRlEnv) -> DirectPDAction:
        return DirectPDAction(self, env)


class DirectPDAction(ActionTerm):
    """Absolute PD targets: injected rows for selected envs, ref filler elsewhere."""

    cfg: DirectPDActionCfg
    _entity: Entity

    def __init__(self, cfg: DirectPDActionCfg, env: ManagerBasedRlEnv):
        super().__init__(cfg, env)
        self._command_name = cfg.command_name

        # ── Joint groups: target layout = [wrist_trans | wrist_rot | finger] ────
        group_ids: dict[str, torch.Tensor] = {}
        joint_names: list[str] = []
        for group in ("wrist_trans", "wrist_rot", "finger"):
            ids, names = self._entity.find_joints_by_actuator_names(
                cfg.actuator_names[group]
            )
            group_ids[group] = torch.tensor(ids, device=self.device, dtype=torch.long)
            joint_names += names
        self._group_sizes = {group: len(ids) for group, ids in group_ids.items()}
        self._all_joint_ids = torch.cat(
            [group_ids["wrist_trans"], group_ids["wrist_rot"], group_ids["finger"]]
        )
        self._joint_names = joint_names

        # ── Soft limits (the only clamp, same as the residual term's final one) ──
        limits = self._entity.data.soft_joint_pos_limits[0]
        self._lower = limits[self._all_joint_ids, 0]
        self._upper = limits[self._all_joint_ids, 1]

        action_dim = len(self._all_joint_ids)
        self._raw_actions = torch.zeros(self.num_envs, action_dim, device=self.device)

        # ── Injected absolute targets (None until load_targets is called) ───────
        self._targets: torch.Tensor | None = None  # (T, n_selected, action_dim)
        self._target_env_ids: torch.Tensor | None = None  # (n_selected,)
        self._t = 0
        self._current: torch.Tensor | None = None  # (n_selected, action_dim)
        self.last_target: torch.Tensor | None = None  # (num_envs, action_dim)

    # ── Replay injection ────────────────────────────────────────────────────────

    def load_targets(self, env_ids: torch.Tensor, targets: torch.Tensor) -> None:
        """Queue an absolute-target sequence for a subset of envs.

        Args:
            env_ids: (n_selected,) entity env indices (long tensor).
            targets: (T, n_selected, action_dim) absolute joint-position targets.
                Rows past the sequence end keep repeating the last row. Callers
                pad short clips with their final frame before calling.
        """
        env_ids = torch.as_tensor(env_ids, device=self.device, dtype=torch.long)
        targets = torch.as_tensor(targets, device=self.device, dtype=torch.float32)
        if targets.ndim != 3 or targets.shape[1] != env_ids.numel():
            raise ValueError(
                f"targets must be (T, n_selected, {self.action_dim}), got "
                f"{tuple(targets.shape)} for {env_ids.numel()} envs"
            )
        if targets.shape[2] != self.action_dim:
            raise ValueError(
                f"targets last dim {targets.shape[2]} != action_dim {self.action_dim}"
            )
        if int(env_ids.max().item()) >= self.num_envs:
            raise ValueError(f"env_ids out of range: {env_ids.max().item()}")
        self._targets = torch.clamp(targets, self._lower, self._upper).clone()
        self._target_env_ids = env_ids
        self._t = 0
        self._current = None

    # ── ActionTerm API ──────────────────────────────────────────────────────────

    @property
    def entity_joint_limits(self) -> torch.Tensor:
        """Soft limits (n_entity_joints, 2); the action joints are clamped to them."""
        return self._entity.data.soft_joint_pos_limits[0]

    @property
    def action_dim(self) -> int:
        return len(self._all_joint_ids)

    @property
    def n_dofs(self) -> int:
        return len(self._all_joint_ids)

    @property
    def raw_action(self) -> torch.Tensor:
        return self._raw_actions

    def process_actions(self, actions: torch.Tensor) -> None:
        """Record the driver-fed buffer (diagnostics only; never clipped/scaled).

        Advances the injected absolute-target row once per policy step.
        """
        self._raw_actions[:] = actions
        if self._targets is not None:
            t = min(self._t, self._targets.shape[0] - 1)
            self._current = torch.clamp(self._targets[t], self._lower, self._upper)
            self._t += 1

    def apply_actions(self) -> None:
        # Filler default: current reference clamped to soft limits, so envs
        # without injected targets keep tracking their reference motion.
        command = cast(
            MotionTrackingCommand,
            self._env.command_manager.get_term(self._command_name),
        )
        target = torch.clamp(
            command.ref_joint_pos[:, self._all_joint_ids], self._lower, self._upper
        )
        if self._current is not None:
            target[self._target_env_ids] = self._current
        self.last_target = target
        self._entity.set_joint_position_target(target, joint_ids=self._all_joint_ids)

    def reset(self, env_ids: torch.Tensor | slice | None = None) -> None:
        self._raw_actions[env_ids] = 0.0
        # Loaded target sequences are batch-scoped: a reset restarts the row
        # counter (eval resets are all-env; the replay driver reloads per batch).
        self._t = 0
        self._current = None
