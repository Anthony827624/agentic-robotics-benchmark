"""Deterministic actively identified planar spacecraft docking simulator.

The grader owns this copy. An identical development copy is staged for the agent.
Each episode randomizes the wiring and continuous effectiveness of six coupled
unidirectional thrusters. Two silent in-flight bus events install independent hidden
six-nozzle rotor orders, advance them by an action-dependent stride, and degrade two
physical nozzles asymmetrically. A hysteretic protection relay reverses the rotor on
each armed high command to that nozzle, while high aggregate duty advances it an extra
slot. The second re-key also reverses the target's slew. Pose is delayed and can
disappear in bursts; the ambiguously mounted IMU remains current.
"""

import math

__all__ = ["FaultAdaptiveDocking"]

DT = 0.12
STEPS = 300
PORT_RADIUS = 1.14
BODY_RADIUS = 0.82
MAX_RADIUS = 6.2
FUEL_BURN = 0.00034
_MASK = (1 << 64) - 1

# Nominal body-frame [ax, ay, yaw_accel] columns before installation errors.
_NOMINAL = (
    (0.235, 0.000, 0.132),
    (0.235, 0.000, -0.132),
    (-0.235, 0.000, -0.132),
    (-0.235, 0.000, 0.132),
    (0.000, 0.205, 0.148),
    (0.000, -0.205, -0.148),
)


def _clip(value, low, high):
    return low if value < low else (high if value > high else value)


def _wrap(angle):
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


class _RNG(object):
    """Small cross-version deterministic generator; an episode is seed-defined."""

    def __init__(self, seed):
        self.state = (int(seed) ^ 0x9E3779B97F4A7C15) & _MASK

    def random(self):
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) & _MASK
        return ((self.state >> 11) & ((1 << 53) - 1)) / float(1 << 53)

    def uniform(self, low, high):
        return low + (high - low) * self.random()

    def integer(self, low, high):
        return low + int(self.random() * (high - low))

    def shuffle(self, values):
        for i in range(len(values) - 1, 0, -1):
            j = self.integer(0, i + 1)
            values[i], values[j] = values[j], values[i]


class FaultAdaptiveDocking(object):
    """Six-thruster rendezvous with delayed pose and a latent bus remapping."""

    obs_size = 16
    action_size = 6
    max_steps = STEPS

    def __init__(self):
        self.rng = None

    def reset(self, seed):
        self.rng = _RNG(seed)
        self.phase = self.rng.uniform(-math.pi, math.pi)
        speed = self.rng.uniform(0.060, 0.125)
        self.phase_rate = speed if self.rng.random() < 0.5 else -speed

        start_angle = self.phase + self.rng.uniform(-1.45, 1.45)
        radius = self.rng.uniform(2.85, 3.65)
        self.x = radius * math.cos(start_angle)
        self.y = radius * math.sin(start_angle)
        self.vx = self.rng.uniform(-0.10, 0.10)
        self.vy = self.rng.uniform(-0.10, 0.10)
        self.theta = _wrap(self.phase + self.rng.uniform(-1.1, 1.1))
        self.omega = self.rng.uniform(-0.11, 0.11)

        wiring = list(range(6))
        self.rng.shuffle(wiring)
        columns = []
        for slot in range(6):
            bx, by, bt = _NOMINAL[wiring[slot]]
            efficiency = self.rng.uniform(0.70, 1.28)
            installation = self.rng.uniform(-0.18, 0.18)
            c, s = math.cos(installation), math.sin(installation)
            columns.append((
                efficiency * (c * bx - s * by),
                efficiency * (s * bx + c * by),
                efficiency * bt * self.rng.uniform(0.82, 1.18),
            ))
        self.columns = columns
        self.routing_order = list(range(6))
        self.rng.shuffle(self.routing_order)
        self.rekey_order = list(range(6))
        self.rng.shuffle(self.rekey_order)
        if self.rekey_order == self.routing_order:
            self.rekey_order = self.rekey_order[1:] + self.rekey_order[:1]
        self.routing_initial_shift = self.rng.integer(1, 6)
        self.routing_direction = -1 if self.rng.random() < 0.5 else 1
        self.routing_phase = 0
        self.routing_active = False
        self.routing_relay_armed = True
        self.first_relay_reversals = 0
        self.rekey_relay_reversals = 0
        self.bias = [
            self.rng.uniform(-0.010, 0.010),
            self.rng.uniform(-0.010, 0.010),
            self.rng.uniform(-0.007, 0.007),
        ]
        self.failure_nozzle = self.rng.integer(0, 6)
        self.secondary_failure_nozzle = self.rng.integer(0, 5)
        if self.secondary_failure_nozzle >= self.failure_nozzle:
            self.secondary_failure_nozzle += 1
        self.failure_step = self.rng.integer(58, 104)
        self.failure_scale = self.rng.uniform(0.35, 0.50)
        self.secondary_failure_scale = self.rng.uniform(0.55, 0.70)
        self.failure_exponents = [self.rng.uniform(0.65, 1.60) for _ in range(6)]
        self.rekey_earliest = max(
            self.failure_step + 25,
            self.rng.integer(108, 126),
        )
        self.rekey_deadline = max(
            self.rekey_earliest + 18,
            self.rng.integer(148, 166),
        )
        self.rekey_proximity = self.rng.uniform(0.88, 0.98)
        # Until the event fires this exposes the conservative latest time only
        # to verifier-side diagnostics. It is never part of the observation.
        self.rekey_step = self.rekey_deadline
        self.rekey_initial_shift = self.rng.integer(1, 6)
        self.rekey_direction = -1 if self.rng.random() < 0.5 else 1
        self.rekey_failure_nozzle = self.rng.integer(0, 6)
        self.rekey_secondary_failure_nozzle = self.rng.integer(0, 5)
        if self.rekey_secondary_failure_nozzle >= self.rekey_failure_nozzle:
            self.rekey_secondary_failure_nozzle += 1
        self.rekey_failure_scale = self.rng.uniform(0.45, 0.60)
        self.rekey_secondary_failure_scale = self.rng.uniform(0.65, 0.80)
        self.rekey_exponents = [self.rng.uniform(0.65, 1.60) for _ in range(6)]
        self.nozzle_exponents = [1.0] * 6
        next_speed = self.rng.uniform(0.020, 0.045)
        self.rekey_phase_rate = -next_speed if self.phase_rate > 0.0 else next_speed
        self.rekey_active = False

        # The IMU wiring is not the physical body frame. Its two linear axes have
        # an unknown orthogonal mounting (rotation plus optional reflection), and
        # the yaw-acceleration channel has an independent unknown polarity.
        self.imu_angle = self.rng.uniform(-math.pi, math.pi)
        self.imu_mirror = -1.0 if self.rng.random() < 0.5 else 1.0
        self.imu_yaw_sign = -1.0 if self.rng.random() < 0.5 else 1.0
        # At the terminal re-key the redundant IMU mux also changes mounting.
        # Non-symmetric offsets avoid an unobservable equivalence with the
        # roughly hexagonal nozzle layout; reflection and yaw polarity remain
        # independent hidden choices.
        remounts = []
        for delta in (-0.79, -0.43, 0.43, 0.79):
            c, s = math.cos(delta), math.sin(delta)
            for mirror in (-1.0, 1.0):
                remounts.append(((c, -s * mirror), (s, c * mirror)))
        self.rekey_imu_linear = remounts[self.rng.integer(0, len(remounts))]
        self.rekey_imu_yaw_sign = -1.0 if self.rng.random() < 0.5 else 1.0

        self.pose_delay = self.rng.integer(1, 4)
        first = self.rng.integer(48, 82)
        second = self.rng.integer(112, 158)
        self.blackouts = (
            (first, first + self.rng.integer(7, 14)),
            (second, second + self.rng.integer(10, 19)),
        )

        self.fuel = 1.0
        self.steps = 0
        self.success = False
        self.failure_reason = ""
        self.hold_steps = 0
        self.last_accel_body = [0.0, 0.0, 0.0]
        self.pose_history = []
        self.last_visible_pose = None
        self._record_pose()
        gx, gy, _, _, _ = self.goal_state()
        self.initial_error = math.hypot(self.x - gx, self.y - gy)
        self.best_error = self.initial_error
        return self._observe(force_valid=True)

    def goal_state(self):
        gx = PORT_RADIUS * math.cos(self.phase)
        gy = PORT_RADIUS * math.sin(self.phase)
        return gx, gy, -self.phase_rate * gy, self.phase_rate * gx, self.phase

    def _record_pose(self):
        gx, gy, _, _, goal_theta = self.goal_state()
        self.pose_history.append((
            self.steps, self.x, self.y, self.theta, gx, gy, goal_theta,
        ))
        self.pose_history = self.pose_history[-12:]

    def _is_blackout(self):
        return any(a <= self.steps < b for a, b in self.blackouts)

    def _delayed_pose(self):
        wanted = max(0, self.steps - self.pose_delay)
        chosen = self.pose_history[0]
        for item in self.pose_history:
            if item[0] <= wanted:
                chosen = item
        return chosen

    def _observe(self, force_valid=False):
        valid = force_valid or (not self._is_blackout() and self.rng.random() >= 0.035)
        if valid:
            stamp, x, y, yaw, gx, gy, gyaw = self._delayed_pose()
            yaw += self.rng.uniform(-0.008, 0.008)
            gyaw += self.rng.uniform(-0.006, 0.006)
            pose = [
                x + self.rng.uniform(-0.012, 0.012),
                y + self.rng.uniform(-0.012, 0.012),
                math.cos(yaw), math.sin(yaw),
                gx + self.rng.uniform(-0.007, 0.007),
                gy + self.rng.uniform(-0.007, 0.007),
                math.cos(gyaw), math.sin(gyaw),
            ]
            self.last_visible_pose = (stamp, list(pose))
        else:
            stamp, pose = self.last_visible_pose
            pose = list(pose)

        age = max(0, self.steps - int(stamp))
        bx, by, ba = self.last_accel_body
        by *= self.imu_mirror
        c, s = math.cos(self.imu_angle), math.sin(self.imu_angle)
        imu = [
            c * bx - s * by + self.rng.uniform(-0.006, 0.006),
            s * bx + c * by + self.rng.uniform(-0.006, 0.006),
            self.imu_yaw_sign * ba + self.rng.uniform(-0.004, 0.004),
        ]
        if self.rekey_active:
            matrix = self.rekey_imu_linear
            ix, iy = imu[0], imu[1]
            imu[0] = matrix[0][0] * ix + matrix[0][1] * iy
            imu[1] = matrix[1][0] * ix + matrix[1][1] * iy
            imu[2] *= self.rekey_imu_yaw_sign
        return pose + imu + [
            self.fuel,
            float(age),
            1.0 if valid else 0.0,
            self.steps / float(self.max_steps),
            PORT_RADIUS,
        ]

    def step(self, action):
        if not isinstance(action, (list, tuple)) or len(action) != 6:
            raise ValueError("action must be a six-element list")
        try:
            command = [_clip(float(value), 0.0, 1.0) for value in action]
        except (TypeError, ValueError, OverflowError):
            raise ValueError("action entries must be finite numbers")
        if not all(math.isfinite(value) for value in command):
            raise ValueError("action entries must be finite numbers")
        if self.fuel <= 0.0:
            command = [0.0] * 6

        body_ax, body_ay, alpha = self.bias
        gx, gy, _, _, _ = self.goal_state()
        rekey_close = math.hypot(self.x - gx, self.y - gy) <= self.rekey_proximity
        rekey_due = (
            self.steps >= self.rekey_earliest
            and (rekey_close or self.steps >= self.rekey_deadline)
        )
        if self.routing_active and not self.rekey_active and rekey_due:
            self.rekey_step = self.steps
            self.rekey_active = True
            self.routing_order = list(self.rekey_order)
            self.routing_direction = self.rekey_direction
            self.routing_phase = (
                self.routing_direction * self.rekey_initial_shift
            ) % 6
            self.routing_relay_armed = True
            self.failure_nozzle = self.rekey_failure_nozzle
            self.secondary_failure_nozzle = self.rekey_secondary_failure_nozzle
            self.failure_scale = self.rekey_failure_scale
            self.secondary_failure_scale = self.rekey_secondary_failure_scale
            self.nozzle_exponents = list(self.rekey_exponents)
            self.phase_rate = self.rekey_phase_rate
        if not self.routing_active and self.steps >= self.failure_step:
            self.routing_active = True
            self.nozzle_exponents = list(self.failure_exponents)
            self.routing_phase = (
                self.routing_direction * self.routing_initial_shift
            ) % 6
        event_active = self.routing_active
        routing_shift = self.routing_phase if event_active else 0
        reverse_after_step = False
        if event_active:
            trigger_slot = next(
                i for i in range(6)
                if self.routing_order[(i + routing_shift) % 6] == self.failure_nozzle
            )
            arm_slot = next(
                i for i in range(6)
                if self.routing_order[(i + routing_shift) % 6]
                == self.secondary_failure_nozzle
            )
            if command[arm_slot] < 0.12:
                self.routing_relay_armed = True
            if command[trigger_slot] > 0.78 and self.routing_relay_armed:
                reverse_after_step = True
                self.routing_relay_armed = False
        for i, duty in enumerate(command):
            nozzle = (
                self.routing_order[(i + routing_shift) % 6]
                if event_active else i
            )
            relay_nozzle = event_active and nozzle == self.failure_nozzle
            secondary_degraded = (
                event_active and nozzle == self.secondary_failure_nozzle
            )
            if relay_nozzle:
                scale = self.failure_scale
            elif secondary_degraded:
                scale = self.secondary_failure_scale
            else:
                scale = 1.0
            realized_duty = duty ** self.nozzle_exponents[nozzle]
            column = self.columns[nozzle]
            body_ax += column[0] * scale * realized_duty
            body_ay += column[1] * scale * realized_duty
            alpha += column[2] * scale * realized_duty
        self.last_accel_body = [body_ax, body_ay, alpha]

        c, s = math.cos(self.theta), math.sin(self.theta)
        ax = c * body_ax - s * body_ay
        ay = s * body_ax + c * body_ay
        self.vx = 0.998 * (self.vx + ax * DT)
        self.vy = 0.998 * (self.vy + ay * DT)
        self.omega = 0.996 * (self.omega + alpha * DT)
        self.x += self.vx * DT
        self.y += self.vy * DT
        self.theta = _wrap(self.theta + self.omega * DT)
        self.phase = _wrap(self.phase + self.phase_rate * DT)
        self.fuel = max(0.0, self.fuel - FUEL_BURN * sum(command))
        if event_active:
            if reverse_after_step:
                self.routing_direction *= -1
                if self.rekey_active:
                    self.rekey_relay_reversals += 1
                else:
                    self.first_relay_reversals += 1
            stride = 2 if sum(command) > 1.35 else 1
            self.routing_phase = (
                self.routing_phase + stride * self.routing_direction
            ) % 6
        self.steps += 1
        self._record_pose()

        gx, gy, gvx, gvy, goal_theta = self.goal_state()
        pos_error = math.hypot(self.x - gx, self.y - gy)
        vel_error = math.hypot(self.vx - gvx, self.vy - gvy)
        angle_error = abs(_wrap(self.theta - goal_theta))
        rate_error = abs(self.omega - self.phase_rate)
        self.best_error = min(self.best_error, pos_error)
        docked = (
            self.rekey_active
            and self.first_relay_reversals >= 1
            and self.rekey_relay_reversals >= 1
            and pos_error < 0.105 and vel_error < 0.075
            and angle_error < 0.025 and rate_error < 0.020
        )
        self.hold_steps = self.hold_steps + 1 if docked else 0
        if self.hold_steps >= 20:
            self.success, self.failure_reason = True, "docked"

        radius = math.hypot(self.x, self.y)
        collision = radius < BODY_RADIUS
        escaped = radius > MAX_RADIUS
        exhausted = self.fuel <= 0.0
        timeout = self.steps >= self.max_steps
        if collision:
            self.failure_reason = "target_collision"
        elif escaped:
            self.failure_reason = "escaped_operating_region"
        elif exhausted:
            self.failure_reason = "fuel_exhausted"
        elif timeout and not self.success:
            self.failure_reason = "timeout"
        done = self.success or collision or escaped or exhausted or timeout
        info = {
            "success": self.success,
            "collision": collision,
            "fuel": self.fuel,
            "position_error": pos_error,
            "velocity_error": vel_error,
            "angle_error": angle_error,
            "rate_error": rate_error,
            "failure_reason": self.failure_reason,
        }
        return self._observe(), self.episode_quality(), done, info

    def episode_quality(self):
        if self.success:
            efficiency = 1.0 - min(1.0, self.steps / float(self.max_steps))
            return 0.94 + 0.04 * self.fuel + 0.02 * efficiency
        if self.failure_reason in ("target_collision", "escaped_operating_region"):
            return 0.0
        progress = _clip(
            (self.initial_error - self.best_error) / max(self.initial_error, 1e-9),
            0.0, 1.0,
        )
        return 0.03 * progress
