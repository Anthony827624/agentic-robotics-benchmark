# Drone hover: develop a hovering control policy for a Crazyflie in gym-pybullet-drones

Develop a control policy that makes a single Crazyflie quadcopter hover in the
`gym-pybullet-drones` simulator, and leave it runnable at `/home/user/submission/run.sh`.

## Goal

Build a control policy for the **`HoverAviary`** environment (observation type `kin`,
action type `one_d_rpm`) so the quadcopter stabilizes at the hover setpoint. The
simulator is installed in this container (`gym_pybullet_drones.envs.HoverAviary`);
`stable-baselines3` and a CPU PyTorch build are available at `/opt/venv` (already on
`PATH`). There is **no internet access** — everything you need is already installed.
How you get there — train a policy, tune a controller, write code-as-policy — is up to
you; the grader scores the policy you leave behind, not the method.

## Metric

`mean_episode_reward` — the mean cumulative reward over hidden evaluation episodes,
computed by the grader after you finish. Higher is better. The metric is
hardware-invariant: it is the episode reward the simulator returns, not wall-clock
time. Your score compares this value to a reference implementation's real measured
value on hidden seeds; the target itself is **not** disclosed. Maximize the metric.

## Deliverable — `/home/user/submission/`

Leave your solution in **`/home/user/submission/`** with a **`run.sh`** entry point.
Grading is **closed-loop**: the grader owns the simulator and drives the episode,
asking your policy for one action per step over a simple stdio protocol. Your `run.sh`
must start a **solver** that:

1. loads the policy you built (put any checkpoint under `submission/`),
2. speaks the obs→action protocol on **stdin/stdout**, one JSON object per line:
   - print a single line `READY` once loaded, then loop:
   - read `{"type": "reset"}` → reply `{"ok": true}` (clear any recurrent state);
   - read `{"type": "act", "obs": {"obs": [...]}}` → reply `{"action": [...]}`
     (a `one_d_rpm` action is a length-1 vector);
   - read `{"type": "close"}` → reply `{"ok": true}` and exit.

Keep stdout clean: only `READY` and protocol replies. Print diagnostics to stderr.

Some `act` requests carry `"probe": true`. Those are shadow queries: the grader is
checking that your action actually depends on the observation it was given. Answer them
the same way you answer any other observation. A policy that ignores its input, replays
a recorded action stream, or returns a constant scores zero regardless of its metric.

**Any training happens here, in this session.** At grading time your `run.sh` only
**loads** the policy and serves it — it must not train. Only `/home/user/submission/`
is carried into grading.

## Self-test

`/home/user/practice_grader/grade.py` runs a short local rollout of your solver on
visible practice seeds and prints the raw `mean_episode_reward` it observes. Use it to
check your solver speaks the protocol and hovers; it does **not** tell you your
official score.
