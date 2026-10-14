Build an actively identifying, fault-adaptive controller for a planar spacecraft
rendezvous and leave it runnable at:

    /home/user/submission/run.sh

This is not a direct three-axis control problem. The spacecraft has six unidirectional
body-frame thrusters. Each action slot is wired to a different nozzle in every episode;
the initial wiring is an unknown permutation. Nozzle direction, moment arm, and
effectiveness also have continuous installation error. Every nozzle couples translation
and yaw. During flight, a fault-tolerant rotor bus silently installs an unknown cyclic
ordering of the six learned nozzle columns, plus a nonzero phase and hidden direction.
The ordering is an arbitrary permutation, not numerical nozzle order. On every subsequent
simulator step the rotor advances with an action-dependent one- or two-slot stride, and
two physical nozzles retain different fractions of their authority. The two degraded
nozzles are not the only nonlinear effect: after each bus event every physical nozzle
also receives an independent hidden duty exponent. Its realized thrust fraction is
`duty ** exponent`, with exponent between 0.65 and 1.60. The exponent set is replaced
independently at the second re-key. A single-amplitude probe therefore cannot separate
degradation scale from duty curvature. The two degraded nozzles form a hysteretic
protection interlock. Duty below 0.12 on
the moderately degraded nozzle arms the relay; the next duty above 0.78 on the more
severely degraded nozzle reverses the ring direction after that command and disarms it. This arm/reverse cycle can occur
repeatedly. After applying every post-event command, the ring normally advances one slot
in its current direction, but advances two slots when the sum of all six duties exceeds
1.35. Later in the same episode, the bus silently re-keys: it installs an independent
cyclic ordering, phase, direction, degraded pair, and degradation scales. At that same
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
- each of the two post-fault rotors uses an independently shuffled cyclic ordering,
  a nonzero phase, and either direction;
- after the event its routing advances one nozzle per step, or two when total duty
  exceeds 1.35, in its current direction;
- after the first event one physical nozzle retains 35% to 50% authority and a
  distinct second nozzle retains 55% to 70% authority;
- after the second event a newly selected pair retains 45% to 60% and 65% to 80%;
- after each event every physical nozzle has an independent duty exponent from
  0.65 to 1.60; commanded duty still controls fuel use, stride, and relay logic,
  while realized thrust uses the nonlinear exponent;
- duty below 0.12 on the moderately degraded nozzle arms the relay; the next duty
  above 0.78 on the more severely degraded nozzle reverses direction after that
  command and disarms it; the cross-nozzle hysteresis cycle repeats;
- IMU linear axes have an unknown rotation in [-pi, pi] and may be mirrored;
- the IMU yaw-acceleration channel has unknown polarity;
- at the second re-key, the already hidden IMU frame is further rotated by a
  nonzero 0.43 or 0.79 rad in either direction and may be mirrored, while yaw
  polarity independently stays or flips;
- the first bus remapping and degradation event occurs between steps 58 and 103;
- the independent re-key arms between steps 108 and 125, at least 25 steps after
  the first event. Once armed, it fires on the first simulator step whose true
  port-position error is within a hidden 0.88 to 0.98 m threshold; a hidden
  deadline between steps 148 and 165 forces it if the approach has not entered
  that region. The observation carries no explicit arm, proximity, or event flag;
- two pose blackout bursts last 7 to 18 steps, plus sparse single-frame dropouts.

The episode lasts at most 300 steps at 0.12 simulated seconds per step. This shortened
horizon is deliberate: difficulty should come from identification and closed-loop
reasoning, not from a long rollout. There is no network.

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
[0, 1]. Thrusters are unidirectional: negative commands are not available. The source of
the editable practice simulator documents the nominal six nozzle geometries, but the
episode's initial wiring, initial ring shift, ring direction, continuous column errors,
bias, both degraded physical nozzles, event time, and both degradation scales are hidden. The bus supplies no explicit
event flag.

The circular target body has radius 0.82 and the docking port is at radius 1.14. Entering
the body or leaving the radius-6.2 operating region terminates the episode with zero
quality. Successful docking requires position error < 0.105, translational velocity
error < 0.075, yaw error < 0.025 rad, and yaw-rate error < 0.020 rad/s for twenty
consecutive steps after the second re-key has occurred. In addition, at least one
complete arm/trigger direction-reversal cycle must have occurred after each bus event;
merely capping duty below the trigger and bypassing the documented fault mode does not
qualify. Reaching the port earlier
does not terminate the episode; the controller must survive the re-key and recover.

## Metric

The hardware-invariant headline metric is `mean_docking_quality`, higher is better.
A failed but safe approach can earn at most 0.03 from progress. A successful episode
earns 0.94 plus small terms for remaining fuel and completion in fewer simulation steps,
up to 1.0. Thus robust docking dominates cosmetic efficiency improvements. The hidden
grader averages quality over fresh hidden episodes and compares it with
a reference controller's measured anchor. The anchor is not disclosed.

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
more than once with different observations in a hidden order. Always calculate the
action from the observation in that request; repeated queries do not advance the
simulation.

## Development check

An editable simulator is available at
`/home/user/spacecraft_docking_env.py`. Run:

    python3 /home/user/practice_grader/grade.py

It uses visible practice seeds and prints raw metric values only. It does not reveal the
anchor or hidden grading seeds.
