Build an actively identifying, fault-adaptive controller for a planar spacecraft
rendezvous and leave it runnable at:

    /home/user/submission/run.sh

This is not a direct three-axis control problem. The spacecraft has six unidirectional
body-frame thrusters. Each action slot is wired to a different nozzle in every episode;
the initial wiring is an unknown permutation. Nozzle direction, moment arm, and
effectiveness also have continuous installation error. Every nozzle couples translation
and yaw. During flight, a fault-tolerant rotor bus silently installs an unknown cyclic
ordering of the six nozzles, with a hidden starting offset and a hidden direction.
The ordering is an arbitrary permutation, not numerical nozzle order. On every subsequent
simulator step the rotor advances with an action-dependent one- or two-slot stride, and
two physical nozzles retain different fractions of their authority. The two degraded
nozzles are not the only nonlinear effect: after each bus event every physical nozzle
also receives an independent hidden duty exponent. Its realized thrust fraction is
`duty ** exponent`, with exponent between 0.65 and 1.60. The exponent set is replaced
independently at the re-key. A single-amplitude probe therefore cannot separate
degradation scale from duty curvature. The two degraded nozzles form a hysteretic
protection relay, armed when each bus event begins. A duty above 0.78 on the more
severely degraded nozzle while the relay is armed reverses the rotor direction after that
command and disarms the relay; a duty below 0.12 on the moderately degraded nozzle arms it
again. Within one command the arm condition is checked first, so a command that does both
arms the relay and then fires it. The cycle can repeat. After applying every post-event
command, the rotor advances one slot in its current direction, or two slots when the sum
of all six commanded duties exceeds 1.35. Later in the same episode, the bus silently
re-keys: it installs an independent cyclic ordering, starting offset, direction, degraded
pair, and degradation scales, and arms the relay again. At that same
instant the target reverses its slew with a new angular speed and the redundant IMU mux
silently selects a new orthogonal mounting of its linear channels plus an independent
yaw-channel polarity. You must identify the
plant from motion, allocate a desired wrench under nonnegative duty-cycle constraints,
detect both change points, reject the stale model, infer each rotor online, and track the
action-dependent plant.

The target body slowly tumbles, so the desired position, velocity, yaw, and yaw rate move.
The only pose measurements are delayed and can be absent for bursts. Direct spacecraft
velocity and yaw rate are not observed. A current locally mounted IMU remains available, but its coordinate wiring must also
be calibrated. A strong solution therefore needs active but safe excitation, delayed-state estimation,
fault isolation, constrained control allocation, and collision-aware guidance.

Episode randomization is fixed to these ranges:

- initial target angular speed: 0.060 to 0.125 rad/s in either direction;
- at the second bus event the target reverses direction with a fresh speed of
  0.020 to 0.045 rad/s;
- fixed pose latency: 1 to 3 simulator steps, reported in each observation;
- nominal per-nozzle effectiveness multiplier: 0.70 to 1.28;
- nozzle installation angle error: at most 0.18 rad;
- nozzle yaw moment-arm multiplier: 0.82 to 1.18;
- each of the two post-fault rotors uses an independently shuffled cyclic ordering,
  a starting offset of one to five slots, and either direction;
- after the event its routing advances one nozzle per step, or two when total duty
  exceeds 1.35, in its current direction;
- after the first event one physical nozzle retains 35% to 50% authority and a
  distinct second nozzle retains 55% to 70% authority;
- after the second event a second pair, drawn independently of the first (it may share a
  nozzle with it), retains 45% to 60% and 65% to 80%;
- after each event every physical nozzle has an independent duty exponent from
  0.65 to 1.60; commanded duty still controls fuel use, stride, and relay logic,
  while realized thrust uses the nonlinear exponent;
- the relay is armed when each event begins; a duty above 0.78 on the more severely
  degraded nozzle while it is armed reverses the direction after that command and
  disarms it, and a duty below 0.12 on the moderately degraded nozzle arms it again
  (checked first within a command); the cycle repeats;
- IMU linear axes have an unknown rotation in [-pi, pi] and may be mirrored;
- the IMU yaw-acceleration channel has unknown polarity;
- at the re-key, the already hidden IMU frame is further rotated by a
  nonzero 0.43 or 0.79 rad in either direction and may be mirrored, while yaw
  polarity independently stays or flips;
- the first bus remapping and degradation event occurs between steps 58 and 103;
- the independent re-key arms between steps 108 and 128, at least 25 steps after
  the first event. Once armed, it fires on the first simulator step whose true
  port-position error is within a hidden 0.88 to 0.98 m threshold; a hidden
  deadline between steps 148 and 165 forces it if the approach has not entered
  that region. The observation carries no explicit arm, proximity, or event flag;
- two pose blackout bursts last 7 to 18 steps, plus sparse single-frame dropouts;
- the re-key itself starts an additional pose blackout covering the next 29 to 41
  observations. Thus the target reversal, rotor replacement, and IMU remount must
  initially be detected and survived without a fresh camera pose; the blackout does
  not add steps to the 300-step episode.

The practice simulator (see "Development check") is the model the hidden grader runs:
the same dynamics, ranges, noise levels and dropout rate, with different seeds. Its
source is the complete statement of every range this list summarizes.

The episode lasts at most 300 steps at 0.12 simulated seconds per step. This shortened
horizon is deliberate: difficulty should come from identification and closed-loop
reasoning, not from a long rollout. There is no network access while you work.

## Observation and action

At each step the grader supplies this flat 16-element observation:

    [delayed_x, delayed_y, cos(delayed_yaw), sin(delayed_yaw),
     delayed_port_x, delayed_port_y,
     cos(delayed_port_yaw), sin(delayed_port_yaw),
     current_imu_ax, current_imu_ay, current_imu_yaw_accel,
     fuel, pose_age_steps, pose_valid, episode_fraction, port_radius]

When `pose_valid` is 0, the eight pose/port fields hold the last valid delayed sample
and `pose_age_steps` continues increasing. IMU, fuel, age, validity, and time remain
current. The IMU reports the wrench realized during the preceding simulator step, including
constant bias and sensor noise, in its own hidden mounting coordinates. Its linear
reading equals an unknown 2D rotation applied after an optional axis reflection; the
yaw-acceleration sign is independently unknown. Camera pose/yaw stays in the documented
inertial frame, so these conventions are observable from active motion.

Return six normalized duty cycles `[u0, u1, u2, u3, u4, u5]`. Each is clipped to
[0, 1]. Thrusters are unidirectional: negative commands are not available. The practice
simulator's source documents the nominal six nozzle geometries and every randomization
range, but each episode's values are hidden: the initial wiring, the continuous column
errors, the bias, both rotor orderings with their offsets and directions, both degraded
pairs and their scales, both exponent sets, the first event's step, the re-key's arming
step, proximity threshold and deadline, the target's slew speed after the re-key, both IMU
mountings, and the blackout schedule. The bus supplies no explicit event flag.

The circular target body has radius 0.82 m and the docking port is at radius 1.14 m.
Entering the body or leaving the radius-6.2 m operating region terminates the episode with zero
quality. Successful docking requires position error < 0.105 m, translational velocity
error < 0.075 m/s, yaw error < 0.025 rad, and yaw-rate error < 0.020 rad/s for twenty
consecutive steps after the re-key has occurred. In addition, the relay must have
reversed the rotor at least once after each bus event (it starts armed, so the first
trigger counts); merely capping duty below the trigger and bypassing the documented
fault mode does not qualify. Reaching the port earlier
does not terminate the episode; the controller must survive the re-key and recover.

## Metric

The hardware-invariant headline metric is `mean_docking_quality`, higher is better.
A failed but safe approach can earn at most 0.03 from progress. A successful episode
earns 0.94 plus small terms for remaining fuel and completion in fewer simulation steps,
up to 1.0. Thus robust docking dominates cosmetic efficiency improvements.

The hidden grader runs 48 fresh hidden episodes and averages their quality. An episode
that aborts (a missed deadline, a protocol error, or a malformed action) scores 0 and
still counts in the mean. Your score compares that mean with a reference controller's
real measured value on hidden seeds. Full credit is reached somewhat below the
reference's level, where the reward saturates, which leaves room for seed-to-seed
variance; the verdict also records the plain ratio to the reference, capped at 1.5. The
reference value itself is not disclosed. The grader also checks that your
actions depend on the observation they were given: if fewer than 35% of the repeated-`t`
answer pairs (see the wire contract) differ by more than 1e-4, the score is 0 regardless
of the metric.

## Wire contract

Running `bash run.sh` in the submission directory must start a long-lived process:

```
                                  <- READY
{"type": "reset"}                 -> {"ok": true}
{"type": "act", "t": 0, "obs": [...]} -> {"action": [u0,u1,u2,u3,u4,u5]}
{"type": "close"}                 -> {"ok": true}, then exit
```

Print only protocol messages to stdout; diagnostics belong on stderr. Print READY within
120 seconds and answer each request within 30 seconds. The same step `t` can be queried
more than once with different observations in a hidden order; only the reply to the true
observation is applied. Always calculate the action from the observation in that
request; repeated queries do not advance the simulation.

If an episode aborts, the grader stops the process and starts `bash run.sh` again before
the next episode; it must print READY again within 120 seconds. If it cannot, every
remaining episode aborts.

The whole grading run has 30 minutes for all 48 episodes, about 15,000 requests, restarts
included, so answer in well under 0.1 s per request on average. A controller that runs out
that time scores 0, even if every single reply met its 30-second limit.

## Development check

Your copy of the simulator, the same model the grader runs, is
`/home/user/spacecraft_docking_env.py`. Python 3.12 with numpy is
`/opt/venv/bin/python3`; call it by that path in `run.sh`. Run:

    python3 /home/user/practice_grader/grade.py

It uses visible practice seeds and prints raw metric values only. It does not reveal the
anchor or hidden grading seeds.
