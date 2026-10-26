"""Independent HOT3D archival-motion replay driver (no runner/policy/checkpoint).

Replays the two exported success clips (m676 / m671) through the shared Isaac
eval env in two modes:

  raw    num_envs=1024, batch-0 motion plan of the source eval; the archived
         raw policy actions are re-fed to envs 109/532 (all other envs get
         zeros) for a short run. Purpose: capture authoritative action-term
         runtime parameters + per-step raw/processed/reference series for the
         offline raw->absolute-PD conversion. NOT a success claim.

  direct num_envs=33, one filler motion per object slot and the two target
         clips at env 1/15; the converted 60 Hz absolute PD target sequences
         (padded to 160 rows) are injected via DirectPDAction.load_targets and
         replayed open-loop for 160 policy steps, with the standard eval
         callbacks (TrackingPerformance / ContactPerformance / RolloutRecord /
         ActionCapture) driven exactly like evaluation.evaluate._rollout and
         scored with the UNMODIFIED full SR@1.0 standard.

GPU discipline: run only with CUDA_VISIBLE_DEVICES pinned to the UUID of a
verified-free GPU. Writes nothing under outputs/hot3d_success/.
"""

from __future__ import annotations

import argparse
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

    Copy k (the k-th entry of ``env_ids``) draws from ``seed + k``; copy 0 stays
    unperturbed when ``unperturbed_first``. Wrist translation joints (prismatic, m)
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
    for k, env in enumerate(env_ids.tolist()):
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
        ]
        if mode == "direct":
            overrides.append("envs/actions@actions=replay_pd")
        cfg = hydra.compose(config_name="flashdexretarget", overrides=overrides)
    OmegaConf.resolve(cfg)

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
    if mode == "raw":
        capture = ActionCapture(
            command_name=cfg.eval.command_name,
            mode="raw",
            env_indices=[int(t["env_1024"]) for t in spec["targets"]],
            max_steps=int(spec["capture_max_steps"]),
            out_dir=out_dir,
        )
    else:
        capture = ActionCapture(
            command_name=cfg.eval.command_name,
            mode="direct",
            env_indices=[int(e) for e in spec["target_envs"]],
            max_steps=None,
            out_dir=out_dir,
        )
    callbacks.append(capture)
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

    max_steps = int(spec["capture_max_steps"]) if mode == "raw" else int(spec["steps"])
    try:
        with torch.no_grad():
            # Same protocol as evaluation.evaluate._rollout: every callback gets
            # on_eval_setup (ActionCapture dumps the runtime action-term params
            # here) before the per-batch motion assignment and reset.
            for cb in callbacks:
                if hasattr(cb, "on_eval_setup"):
                    cb.on_eval_setup(raw_eval)
            if mode == "raw":
                plan_ids = torch.tensor(
                    spec["batch_motion_ids"][0], dtype=torch.long, device=device
                )
                env_plan = getattr(raw_eval, "_isaac_eval_plan", None)
                if env_plan is not None:
                    live = env_plan[0]["motion_ids"]
                    assert torch.equal(plan_ids, live), (
                        "spec batch0 != env eval plan batch0"
                    )
                cmd.motion_ids[:] = plan_ids
                npz = {
                    t["motion_id"]: np.load(t["source_npz"]) for t in spec["targets"]
                }
                eval_env.reset()
                for cb in callbacks:
                    cb.on_start(raw_eval)
                for k in range(max_steps):
                    actions = torch.zeros(num_envs, 56, device=device)
                    for t in spec["targets"]:
                        actions[int(t["env_1024"])] = torch.from_numpy(
                            np.ascontiguousarray(npz[t["motion_id"]]["actions"][k])
                        ).to(device)
                    eval_env.step(actions)
                    for cb in callbacks:
                        cb.on_step(raw_eval)
                active_mask = torch.ones(num_envs, dtype=torch.bool, device=device)
            else:
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
                    arr = np.load(pd_path)["pd_targets"]
                    pds.append(torch.from_numpy(np.ascontiguousarray(arr)))
                    print(
                        f"[replay] env{env_idx} <= m{mid} pd targets {arr.shape} "
                        f"sha_ok={pd_path.stat().st_size > 0}",
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
    success_dir = None
    if mode == "direct":
        success_dir = out_dir / "success"
        metrics.update(
            tracking_success_metrics(
                raw_eval,
                cfg.eval,
                callbacks,
                states,
                None,
                success_dir,
                0,
                source_info,
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
    if mode == "direct":
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
        help="spec_1024.json (raw) or spec_33.json (direct)",
    )
    ap.add_argument(
        "--out-dir",
        required=True,
        help="task-local output dir (must NOT be outputs/hot3d_success/*)",
    )
    args = ap.parse_args()
    report = run(args)
    ok = True
    if report["mode"] == "direct":
        for c in report["clips"]:
            ok = ok and c["full_pass_sr1.0"]
        print(f"[replay] direct full SR@1.0 per-clip pass: {ok}", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok else 2)


if __name__ == "__main__":
    main()
