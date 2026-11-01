"""Open-loop replay of absolute joint-position PD targets in Isaac Sim / PhysX.

The spec names a motion file (the scene: robot start state, object, references), the
envs that replay a submission and the NPZ each of them reads. Every env starts from
its motion's frame 0, optionally perturbed (``apply_initial_perturbation``); the
targets are injected through ``DirectPDAction.load_targets`` and stepped open-loop at
60 Hz without object-assist forces, while the standard evaluation callbacks record the
tracking errors. ``report.json`` lists, per replayed env, the time-averaged errors after
the grace period.

Usage: replay_hot3d_pd.py --spec spec.json --out-dir <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# Hydra resolves the composed config's relative paths (e.g. the robot MJCF
# assets/robot/sharpa/bimanual.xml) against the process cwd; pin it to the
# repo root so the driver behaves the same from any launch directory.
os.chdir(REPO_ROOT)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_initial_perturbation(raw_env, cmd, env_ids, pert: dict) -> list[dict]:
    """Seeded, uniform perturbation of the reset state, one draw per replay copy.

    Copy k (the k-th entry of ``env_ids``, plus ``copy_index`` when each copy replays in its
    own process) draws from ``seed + k``; copy 0 stays unperturbed when
    ``unperturbed_first``. Wrist translation joints (prismatic, m)
    move by up to ``wrist_trans_m``, every other joint by up to ``joint_rad``, and the
    object start position by up to ``obj_xy_m`` in x and y. References are untouched.
    """
    import numpy as np
    import torch

    robot = cmd.robot
    names = list(robot.joint_names)
    prismatic = torch.tensor(["_pos_" in n for n in names], device=raw_env.device)
    joint_pos = robot.data.joint_pos.clone()
    joint_vel = robot.data.joint_vel.clone()
    limits = robot.data.soft_joint_pos_limits
    objects = sorted({n for side in cmd._side_list for n in cmd.obj_entity_names(side)})
    obj_state = {}
    for name in objects:
        data = raw_env.scene[name].data
        obj_state[name] = torch.cat(
            [
                data.root_link_pos_w,
                data.root_link_quat_w,
                data.body_link_lin_vel_w[:, 0],
                data.body_link_ang_vel_w[:, 0],
            ],
            dim=-1,
        ).clone()
    draws = []
    first = int(pert.get("copy_index", 0))
    for k, env in enumerate(env_ids.tolist(), start=first):
        if k == 0 and pert.get("unperturbed_first", True):
            draws.append({"copy": k, "env": env, "perturbed": False})
            continue
        rng = np.random.default_rng(int(pert["seed"]) + k)
        scale = torch.where(
            prismatic,
            torch.tensor(float(pert["wrist_trans_m"]), device=raw_env.device),
            torch.tensor(float(pert["joint_rad"]), device=raw_env.device),
        )
        dq = torch.from_numpy(rng.uniform(-1.0, 1.0, len(names))).to(raw_env.device, torch.float32) * scale
        joint_pos[env] = torch.clamp(joint_pos[env] + dq, limits[env, :, 0], limits[env, :, 1])
        dxy = rng.uniform(-1.0, 1.0, 2) * float(pert["obj_xy_m"])
        for name in objects:
            obj_state[name][env, 0:2] += torch.tensor(dxy, device=raw_env.device, dtype=torch.float32)
        draws.append({"copy": k, "env": env, "perturbed": True,
                      "max_abs_dq": float(dq.abs().max()), "obj_dxy_m": dxy.tolist()})
    robot.write_joint_state_to_sim(joint_pos, joint_vel)
    for name in objects:
        raw_env.scene[name].write_root_state_to_sim(obj_state[name])
    return draws


def run(args: argparse.Namespace) -> dict:
    import hydra
    import numpy as np
    from omegaconf import OmegaConf

    spec_path = Path(args.spec).resolve()
    spec = json.loads(spec_path.read_text())
    mode = spec["mode"]
    if mode != "direct":
        raise ValueError(f"unsupported replay mode {mode!r}")
    num_envs = int(spec["num_envs"])
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    OmegaConf.register_new_resolver("eval", lambda s: eval(s), replace=True)
    config_dir = (REPO_ROOT / "config").resolve()
    with hydra.initialize_config_dir(version_base=None, config_dir=str(config_dir)):
        overrides = [
            "envs/robot@robot=sharpa_bimanual",
            f"commands.motion_file={spec['motion_file']}",
            f"num_envs={num_envs}",
            f"seed={spec['eval']['seed']}",
            "headless=true",
            "commands.object.support_disks.enable=true",
            "eval.command.sampling_mode=start",
            "eval.command.noise_to_initial_level=0.0",
            "eval.command.start_frame=0",
            "eval.success_criterion=full",
            "eval.stop_when_all_solved=false",
            "envs/actions@actions=replay_pd",
        ]
        cfg = hydra.compose(config_name="flashdexretarget", overrides=overrides)
    OmegaConf.resolve(cfg)
    # The scoring window and thresholds come from the spec (the grader's config), not from the
    # eval config's defaults: one source for both.
    from omegaconf import open_dict

    with open_dict(cfg):
        for cb in cfg.eval.callbacks:
            target = str(cb.get("_target_", ""))
            if target.endswith(".TrackingPerformance"):
                cb.grace_steps = int(spec["eval"]["grace_steps"])
                cb.threshold_ks = [float(k) for k in spec["eval"]["threshold_ks"]]
                cb.thresholds = {k: float(v) for k, v in spec["eval"]["thresholds"].items()}
            elif target.endswith(".ContactPerformance"):
                cb.grace_steps = int(spec["eval"]["grace_steps"])

    os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "1")
    from isaaclab.app import AppLauncher

    AppLauncher({"headless": bool(cfg.get("headless", True))})

    import torch
    from hydra.utils import instantiate

    from common.utils import configure_torch_backends, set_seed
    from envs import create_envs
    from envs._common import action_term
    from evaluation.callbacks.action_capture import ActionCapture
    from evaluation.evaluate import reduce_metrics, tracking_success_metrics

    set_seed(cfg.seed)
    configure_torch_backends()
    device = str(cfg.device)
    print(
        f"[replay] mode={mode} num_envs={num_envs} device={device} "
        f"({torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'}) "
        f"spec={spec_path}",
        flush=True,
    )

    train_env, eval_env = create_envs(cfg, device=device)
    raw_eval = eval_env.unwrapped
    cmd = raw_eval.command_manager.get_term(cfg.eval.command_name)

    # ── callbacks: the driver replaces index 0 (the eval-setup callback) ──────
    callbacks = [instantiate(cb) for cb in cfg.eval.callbacks[1:]]
    callbacks.append(
        ActionCapture(
            command_name=cfg.eval.command_name,
            mode="direct",
            env_indices=[int(e) for e in spec["target_envs"]],
            max_steps=None,
            out_dir=out_dir,
        )
    )
    metric_cbs = [cb for cb in callbacks if hasattr(cb, "reduce_state")]
    states: dict[int, list] = {id(cb): [] for cb in metric_cbs}

    source_info = {
        "run_dir": str(out_dir),
        "checkpoint": None,
        "parent_checkpoint": spec.get("source_eval_file"),
        "eval_file": str(out_dir / "report.json"),
        "num_envs": num_envs,
        "num_batches": 1,
        "mode": mode,
        "spec": str(spec_path),
    }

    max_steps = int(spec["steps"])
    try:
        with torch.no_grad():
            # Same protocol as evaluation.evaluate._rollout: every callback gets
            # on_eval_setup (ActionCapture dumps the runtime action-term params
            # here) before the per-batch motion assignment and reset.
            for cb in callbacks:
                if hasattr(cb, "on_eval_setup"):
                    cb.on_eval_setup(raw_eval)
            motion_ids = torch.tensor(
                spec["motion_ids"], dtype=torch.long, device=device
            )
            cmd.motion_ids[:] = motion_ids
            term = action_term(raw_eval)
            env_ids = torch.tensor(
                spec["target_envs"], dtype=torch.long, device=device
            )
            pds = []
            for env_idx, mid in zip(
                spec["target_envs"], spec["target_motion_ids"], strict=True
            ):
                entry = spec["pd_targets"][str(mid)]
                pd_path = Path(entry["path"])
                assert pd_path.exists(), pd_path
                data = pd_path.read_bytes()
                # the bytes the grader validated (it copies them to a root-only folder first)
                if entry.get("sha256") and hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise ValueError(f"{pd_path}: sha256 differs from the validated submission")
                with np.load(io.BytesIO(data), allow_pickle=False) as npz:
                    arr = npz["pd_targets"]
                pds.append(torch.from_numpy(np.ascontiguousarray(arr)))
                print(
                    f"[replay] env{env_idx} <= m{mid} pd targets {arr.shape} sha_ok=True",
                    flush=True,
                )
            targets = torch.stack(pds, dim=1).to(device)  # (T, n_sel, 56)
            term.load_targets(env_ids, targets)
            eval_env.reset()
            if spec.get("perturbation"):
                source_info["perturbation"] = apply_initial_perturbation(
                    raw_eval, cmd, env_ids, spec["perturbation"]
                )
            for cb in callbacks:
                cb.on_start(raw_eval)
            for k in range(max_steps):
                eval_env.step(torch.zeros(num_envs, 56, device=device))
                for cb in callbacks:
                    cb.on_step(raw_eval)
            active_mask = torch.zeros(num_envs, dtype=torch.bool, device=device)
            active_mask[env_ids] = True
            for cb in metric_cbs:
                states[id(cb)].append(cb.collect_state(active_mask))
    finally:
        eval_env.exit_eval()

    metrics = reduce_metrics(callbacks, states)
    metrics.update(
        tracking_success_metrics(
            raw_eval, cfg.eval, callbacks, states, None, out_dir / "success", 0, source_info
        )
    )
    metrics["num_batches"] = 1.0
    metrics["num_envs"] = float(num_envs)
    metrics = {
        f"eval/{k}": float(v) for k, v in metrics.items() if isinstance(v, (int, float))
    }

    report: dict = {
        "mode": mode,
        "num_envs": num_envs,
        "steps": max_steps,
        "spec": str(spec_path),
        "source_eval_file": spec.get("source_eval_file"),
        "success_criterion": "full SR@1.0",
        "metrics": metrics,
        "source_info": source_info,
    }
    tp_cb = next(cb for cb in metric_cbs if hasattr(cb, "per_env_fail"))
    st = states[id(tp_cb)]
    fail = tp_cb.per_env_fail(st)
    fail_obj = tp_cb.per_env_fail_obj(st)
    scored = tp_cb.per_env_scored(st)
    mids = tp_cb.per_env_motion_ids(st)
    cat = tp_cb._concat(st)
    errors = {
        k: v.max(dim=-1).values
        for k, v in tp_cb._side_errors(
            cat, cat["count_per_env"].clamp(min=1.0)
        ).items()
    }
    clips = []
    for row in range(int(mids.numel())):
        clips.append(
            {
                "motion_id": int(mids[row].item()),
                "scored": bool(scored[row].item()),
                "full_pass_sr1.0": bool(
                    scored[row].item() and not fail[row, tp_cb.k1_idx].item()
                ),
                "obj_pass_sr1.0": bool(
                    scored[row].item() and not fail_obj[row, tp_cb.k1_idx].item()
                ),
                "errors": {k: float(v[row].item()) for k, v in errors.items()},
            }
        )
    report["clips"] = clips
    (out_dir / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1), flush=True)

    train_env.close()
    eval_env.close()
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--spec",
        required=True,
        help="replay spec (motion file, target envs, PD target files)",
    )
    ap.add_argument(
        "--out-dir",
        required=True,
        help="output directory for report.json",
    )
    args = ap.parse_args()
    report = run(args)
    ok = all(c["full_pass_sr1.0"] for c in report["clips"])
    print(f"[replay] full SR@1.0 pass on every replayed env: {ok}", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok else 2)


if __name__ == "__main__":
    main()
