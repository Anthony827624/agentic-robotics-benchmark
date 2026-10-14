#!/usr/bin/env python3
"""Reference controller: active identification, delayed-state filtering and allocation."""

import json
import math
import sys

import numpy as np

DT = 0.12
PORT_RADIUS = 1.14
NOMINAL = np.array([
    [0.235, 0.000, 0.132],
    [0.235, 0.000, -0.132],
    [-0.235, 0.000, -0.132],
    [-0.235, 0.000, 0.132],
    [0.000, 0.205, 0.148],
    [0.000, -0.205, -0.148],
], dtype=float).T


def clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def wrap(x):
    return (x + math.pi) % (2.0 * math.pi) - math.pi


class MultiRLS:
    def __init__(self):
        self.theta = np.zeros((3, 7), dtype=float)
        self.p = np.diag([35.0] * 6 + [160.0])
        self.forgetting = 0.970

    def update(self, action, measured):
        phi = np.asarray(list(action) + [1.0], dtype=float)
        pp = self.p.dot(phi)
        den = self.forgetting + float(phi.dot(pp))
        gain = pp / max(den, 1e-9)
        predicted = self.theta.dot(phi)
        raw_error = np.asarray(measured) - predicted
        error = np.clip(raw_error, -0.18, 0.18)
        self.theta += np.outer(error, gain)
        self.p = (self.p - np.outer(gain, pp)) / self.forgetting
        self.p += np.diag([0.055] * 6 + [0.002])
        return float(np.linalg.norm(raw_error))

    def plant(self):
        return self.theta[:, :6].copy(), self.theta[:, 6].copy()


class Controller:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_t = None
        self.last_action = np.zeros(6)
        self.model = MultiRLS()
        self.state = None
        self.measurements = []
        self.all_measurements = []
        self.raw_imus = {}
        self.goal_phase = None
        self.goal_rate = 0.0
        self.close_mode = False
        self.last_alloc = np.zeros(6)
        self.base_B = None
        self.base_bias = None
        self.bad_residuals = 0
        self.ring_probe_start = None
        self.ring_probe_phases = []
        self.ring_cycle = None
        self.ring_orientation = None
        self.ring_move = 1
        self.ring_phase = None
        self.ring_phase_time = None
        self.ring_orient_probe = False
        self.ring_relay_armed = True
        self.ring_bad_nozzle = None
        self.ring_secondary_nozzle = None
        self.ring_scan_start = None
        self.ring_scan_samples = {}
        self.ring_scales = np.ones(6)
        self.ring_exponents = np.ones(6)
        self.ring_identifications = 0
        self.ring_commission_pending = False
        self.pending_score = float("inf")
        self.pending_action = np.zeros(6)
        self.pending_close = False
        self.pending_alloc = np.zeros(6)
        self.imu_angle = 0.0
        self.imu_mirror = 1.0
        self.imu_yaw_sign = 1.0
        self.frame_matrix = np.eye(3)
        self.frame_candidates = [np.eye(3)]
    def _trajectory_frame_cost(self, angle, mirror, yaw_sign):
        samples = sorted(self.all_measurements, key=lambda v: v[0])[1:]
        if len(samples) < 8:
            return 0.0
        stamps = np.array([v[0] for v in samples], dtype=int)
        max_stamp = int(stamps[-1])
        grid = np.arange(max_stamp + 1)
        measured_yaw = np.array([v[3] for v in samples], dtype=float)
        yaw_grid = np.interp(grid, stamps, measured_yaw)
        c, s = math.cos(angle), math.sin(angle)
        px = py = vx = vy = 0.0
        pa = wa = 0.0
        pos_coeff = vel_coeff = 0.0
        yaw_coeff = rate_coeff = 0.0
        pos_x = [0.0]
        pos_y = [0.0]
        yaw_part = [0.0]
        coeff_p = [0.0]
        coeff_a = [0.0]
        for k in range(1, max_stamp + 1):
            sensor = self.raw_imus.get(k)
            if sensor is None:
                sensor = self.raw_imus.get(k - 1, np.zeros(3))
            sx, sy, sa = [float(v) for v in sensor]
            tx, ty = c * sx + s * sy, -s * sx + c * sy
            bx, by = tx, mirror * ty
            cy, syaw = math.cos(yaw_grid[k - 1]), math.sin(yaw_grid[k - 1])
            ax, ay = cy * bx - syaw * by, syaw * bx + cy * by
            vx = 0.998 * (vx + ax * DT)
            vy = 0.998 * (vy + ay * DT)
            px += vx * DT
            py += vy * DT
            vel_coeff *= 0.998
            pos_coeff += vel_coeff * DT
            if k == 1:
                # Initial velocity is multiplied by drag on the first transition.
                vel_coeff = 0.998
                pos_coeff = vel_coeff * DT
            wa = 0.996 * (wa + yaw_sign * sa * DT)
            pa += wa * DT
            rate_coeff *= 0.996
            yaw_coeff += rate_coeff * DT
            if k == 1:
                rate_coeff = 0.996
                yaw_coeff = rate_coeff * DT
            pos_x.append(px)
            pos_y.append(py)
            yaw_part.append(pa)
            coeff_p.append(pos_coeff)
            coeff_a.append(yaw_coeff)

        idx = stamps
        design_p = np.column_stack([np.ones(len(idx)), np.asarray(coeff_p)[idx]])
        rx = np.array([v[1] for v in samples]) - np.asarray(pos_x)[idx]
        ry = np.array([v[2] for v in samples]) - np.asarray(pos_y)[idx]
        fitx = design_p.dot(np.linalg.lstsq(design_p, rx, rcond=None)[0])
        fity = design_p.dot(np.linalg.lstsq(design_p, ry, rcond=None)[0])
        design_a = np.column_stack([np.ones(len(idx)), np.asarray(coeff_a)[idx]])
        ra = measured_yaw - np.asarray(yaw_part)[idx]
        fita = design_a.dot(np.linalg.lstsq(design_a, ra, rcond=None)[0])
        pos_mse = float(np.mean((rx - fitx) ** 2 + (ry - fity) ** 2))
        yaw_mse = float(np.mean((ra - fita) ** 2))
        return pos_mse / (0.030 ** 2) + yaw_mse / (0.020 ** 2)

    def _estimate_sensor_frame(self):
        observed = self.base_B
        obs_force = observed[:2, :]
        obs_norm = np.maximum(1e-6, np.linalg.norm(obs_force, axis=0))
        obs_dirs = obs_force / obs_norm
        obs_ratio = np.abs(observed[2, :]) / obs_norm
        nom_norm = np.linalg.norm(NOMINAL[:2, :], axis=0)
        nom_ratio = np.abs(NOMINAL[2, :]) / nom_norm
        candidates = []
        import itertools
        for perm in itertools.permutations(range(6)):
            for mirror in (-1.0, 1.0):
                dot_sum = cross_sum = 0.0
                transformed = []
                for j, k in enumerate(perm):
                    w = np.array([NOMINAL[0, k], mirror * NOMINAL[1, k]]) / nom_norm[k]
                    transformed.append(w)
                    dot_sum += float(w.dot(obs_dirs[:, j]))
                    cross_sum += float(w[0] * obs_dirs[1, j] - w[1] * obs_dirs[0, j])
                angle = math.atan2(cross_sum, dot_sum)
                c, s = math.cos(angle), math.sin(angle)
                direction_cost = 0.0
                for j, w in enumerate(transformed):
                    pred = np.array([c * w[0] - s * w[1], s * w[0] + c * w[1]])
                    direction_cost += (wrap(math.atan2(
                        pred[0] * obs_dirs[1, j] - pred[1] * obs_dirs[0, j],
                        pred.dot(obs_dirs[:, j]),
                    )) / 0.20) ** 2
                for yaw_sign in (-1.0, 1.0):
                    cost = direction_cost
                    for j, k in enumerate(perm):
                        if observed[2, j] * (yaw_sign * NOMINAL[2, k]) < 0.0:
                            cost += 12.0
                        cost += 0.25 * ((obs_ratio[j] - nom_ratio[k]) / 0.22) ** 2
                    candidates.append((cost, angle, mirror, yaw_sign))
        candidates.sort(key=lambda v: v[0])
        best = None
        for geometry_cost, angle, mirror, yaw_sign in candidates[:48]:
            trajectory_cost = self._trajectory_frame_cost(angle, mirror, yaw_sign)
            total = geometry_cost + 3.0 * trajectory_cost
            if best is None or total < best[0]:
                best = (total, angle, mirror, yaw_sign)
        _, base_angle, self.imu_mirror, self.imu_yaw_sign = best
        refined = None
        for offset in np.linspace(-0.16, 0.16, 25):
            angle = wrap(base_angle + float(offset))
            cost = self._trajectory_frame_cost(
                angle, self.imu_mirror, self.imu_yaw_sign
            ) + 0.18 * (float(offset) / 0.16) ** 2
            if refined is None or cost < refined[0]:
                refined = (cost, angle)
        self.imu_angle = refined[1]

    def _imu_to_body(self, measured):
        sx, sy, sa = np.linalg.solve(
            self.frame_matrix, np.asarray(measured, dtype=float)
        )
        c, s = math.cos(self.imu_angle), math.sin(self.imu_angle)
        tx, ty = c * sx + s * sy, -s * sx + c * sy
        return tx, self.imu_mirror * ty, self.imu_yaw_sign * sa

    def _body_to_imu(self, bx, by, alpha):
        by = self.imu_mirror * by
        c, s = math.cos(self.imu_angle), math.sin(self.imu_angle)
        baseline = np.array([
            c * bx - s * by,
            s * bx + c * by,
            self.imu_yaw_sign * alpha,
        ])
        return self.frame_matrix.dot(baseline)

    @staticmethod
    def _signed_frame_candidates():
        result = []
        for delta in (-0.79, -0.43, 0.43, 0.79):
            c, s = math.cos(delta), math.sin(delta)
            for mirror in (-1.0, 1.0):
                linear = np.array([[c, -s * mirror], [s, c * mirror]])
                for yaw_sign in (-1.0, 1.0):
                    matrix = np.zeros((3, 3))
                    matrix[:2, :2] = linear
                    matrix[2, 2] = yaw_sign
                    result.append(matrix)
        return result

    def _to_baseline(self, measured):
        return np.linalg.solve(self.frame_matrix, np.asarray(measured, dtype=float))

    def _match_ring_column(self, estimate):
        best = None
        estimate = np.asarray(estimate, dtype=float)
        for nozzle in range(6):
            column = self.base_B[:, nozzle]
            scale = clip(
                float(column.dot(estimate)) / max(float(column.dot(column)), 1e-9),
                0.08, 1.45,
            )
            error = float(np.linalg.norm(estimate - scale * column))
            if best is None or error < best[0]:
                best = (error, nozzle, scale)
        return best[1], best[2], best[0]

    def _reset_ring_tracker(self, probe_start):
        if self.ring_identifications >= 1:
            # A later re-key can arrive during final approach. Clear the stale
            # allocator state, but preserve approach mode: the identification
            # pulses already consume a recovery window and retreating to the
            # outer waypoint can strand an otherwise safe vehicle.
            self.last_alloc = np.zeros(6)
            self.frame_candidates = self._signed_frame_candidates()
        else:
            self.frame_candidates = [self.frame_matrix.copy()]
        self.ring_probe_start = int(probe_start)
        self.ring_probe_phases = []
        self.ring_cycle = None
        self.ring_orientation = None
        self.ring_move = 1
        self.ring_phase = None
        self.ring_phase_time = None
        self.ring_orient_probe = False
        self.ring_relay_armed = True
        self.ring_bad_nozzle = None
        self.ring_secondary_nozzle = None
        self.ring_scan_start = None
        self.ring_scan_samples = {}
        self.ring_scales = np.ones(6)
        self.ring_exponents = np.ones(6)
        self.bad_residuals = 0

    def _ring_nozzle(self, slot, phase=None):
        phase = self.ring_phase if phase is None else phase
        index = (int(phase) + self.ring_orientation * int(slot)) % 6
        return int(self.ring_cycle[index])

    def _ring_slot(self, nozzle):
        for slot in range(6):
            if self._ring_nozzle(slot) == nozzle:
                return slot
        raise ValueError("no logical slot maps to physical nozzle")

    def _advance_ring(self, action, t):
        u = np.asarray(action, dtype=float)
        if (self.ring_bad_nozzle is not None
                and self.ring_secondary_nozzle is not None):
            trigger_slot = self._ring_slot(self.ring_bad_nozzle)
            arm_slot = self._ring_slot(self.ring_secondary_nozzle)
            if float(u[arm_slot]) < 0.12:
                self.ring_relay_armed = True
            if float(u[trigger_slot]) > 0.78 and self.ring_relay_armed:
                self.ring_move *= -1
                self.ring_relay_armed = False
                self.ring_commission_pending = False
        stride = 2 if float(np.sum(u)) > 1.35 else 1
        self.ring_phase = int(
            (self.ring_phase + stride * self.ring_move) % 6
        )
        self.ring_phase_time = int(t)

    def _update_fault(self, t, action, measured):
        if self.base_B is None:
            return
        u = np.asarray(action, dtype=float)
        y = np.asarray(measured, dtype=float)

        # Seven low slot-zero pulses reveal one complete hidden rotor cycle
        # without relying on numerical adjacency of physical nozzle indices.
        if self.ring_probe_start is not None and self.ring_cycle is None:
            command_t = t - 1
            if (self.ring_probe_start <= command_t < self.ring_probe_start + 7
                    and u[0] > 0.20 and float(np.max(u[1:])) < 0.05):
                self.ring_probe_phases.append((command_t, y.copy(), float(u[0])))
            if len(self.ring_probe_phases) >= 7:
                best = None
                samples = self.ring_probe_phases[-7:]
                for matrix in self.frame_candidates:
                    sequence, total_error = [], 0.0
                    for _, raw, duty in samples:
                        baseline = np.linalg.solve(matrix, raw)
                        estimate = (baseline - self.base_bias) / duty
                        nozzle, _, error = self._match_ring_column(estimate)
                        sequence.append(nozzle)
                        total_error += error
                    valid = len(set(sequence[:6])) == 6 and sequence[6] == sequence[0]
                    score = total_error + (0.0 if valid else 100.0)
                    if best is None or score < best[0]:
                        best = (score, valid, matrix, sequence)
                if best[1]:
                    self.frame_matrix = best[2].copy()
                    self.frame_candidates = [self.frame_matrix.copy()]
                    self.ring_cycle = list(best[3][:6])
                    self.ring_phase = 0
                    self.ring_phase_time = t - 1
                    self.ring_move = 1
                    self.ring_orient_probe = True
                    self._advance_ring(u, t)
                else:
                    self.ring_probe_start = t
                    self.ring_probe_phases = []
            return

        # A single slot-one pulse disambiguates whether increasing logical slot
        # number runs forward or backward through the observed rotor cycle.
        if self.ring_cycle is not None and self.ring_orientation is None:
            active = int(np.argmax(u))
            ordered = np.sort(u)
            if active == 1 and u[active] > 0.20 and ordered[-2] < 0.05:
                estimate = (self._to_baseline(y) - self.base_bias) / u[active]
                nozzle, scale, _ = self._match_ring_column(estimate)
                plus = self.ring_cycle[(self.ring_phase + 1) % 6]
                minus = self.ring_cycle[(self.ring_phase - 1) % 6]
                self.ring_orientation = 1 if nozzle == plus else -1
                if nozzle not in (plus, minus):
                    self._reset_ring_tracker(t)
                    return
                self.ring_scan_start = t
                self.ring_orient_probe = False
                self._advance_ring(u, t)
            return

        # With the cycle and orientation known, address each physical nozzle
        # once and estimate both degraded scales.
        if self.ring_scan_start is not None:
            command_t = t - 1
            if command_t >= self.ring_scan_start:
                active = int(np.argmax(u))
                ordered = np.sort(u)
                if u[active] > 0.20 and ordered[-2] < 0.05:
                    estimate = self._to_baseline(y) - self.base_bias
                    nozzle = self._ring_nozzle(active)
                    column = self.base_B[:, nozzle]
                    response = clip(
                        float(column.dot(estimate))
                        / max(float(column.dot(column)), 1e-9),
                        0.002,
                        1.45,
                    )
                    self.ring_scan_samples.setdefault(nozzle, []).append(
                        (float(u[active]), response)
                    )
                    self._advance_ring(u, t)
            if (len(self.ring_scan_samples) == 6
                    and all(len(self.ring_scan_samples[j]) >= 2 for j in range(6))):
                scales, exponents = [], []
                for nozzle in range(6):
                    (d1, r1), (d2, r2) = self.ring_scan_samples[nozzle][:2]
                    exponent = clip(
                        math.log(max(r2, 1e-6) / max(r1, 1e-6))
                        / math.log(d2 / d1),
                        0.40,
                        2.10,
                    )
                    scale = clip(r1 / (d1 ** exponent), 0.05, 1.45)
                    scales.append(scale)
                    exponents.append(exponent)
                self.ring_scales = np.asarray(scales)
                self.ring_exponents = np.asarray(exponents)
                order = np.argsort(self.ring_scales)
                self.ring_bad_nozzle = int(order[0])
                self.ring_secondary_nozzle = int(order[1])
                self.ring_scan_start = None
                self.ring_identifications += 1
                self.ring_commission_pending = True
                self.bad_residuals = 0
            return

        # Once identified, a large informative residual means the second
        # independent re-key has happened. Discard the stale rotor immediately
        # and actively identify the new one.
        if (self.ring_cycle is not None and self.ring_orientation is not None
                and self.ring_phase is not None):
            B, bias = self._plant()
            residual = y - (B.dot(u) + bias)
            informative = float(np.max(u)) > 0.12
            if informative and float(np.linalg.norm(residual)) > 0.110:
                self.bad_residuals += 1
            else:
                self.bad_residuals = max(0, self.bad_residuals - 1)
            if self.bad_residuals >= 2:
                self._reset_ring_tracker(t)
                return
            self._advance_ring(u, t)
            return

        residual = y - (self.base_B.dot(u) + self.base_bias)
        informative = float(np.max(u)) > 0.12
        if t > 45 and informative and float(np.linalg.norm(residual)) > 0.050:
            self.bad_residuals += 1
        else:
            self.bad_residuals = max(0, self.bad_residuals - 1)
        if self.bad_residuals >= 1:
            self._reset_ring_tracker(t)

    def _plant(self):
        if self.base_B is None:
            return self.model.plant()
        if (self.ring_cycle is not None and self.ring_orientation is not None
                and self.ring_phase is not None):
            columns = []
            for slot in range(6):
                nozzle = self._ring_nozzle(slot)
                columns.append(self.base_B[:, nozzle] * self.ring_scales[nozzle])
            baseline_B = np.column_stack(columns)
            return (self.frame_matrix.dot(baseline_B),
                    self.frame_matrix.dot(self.base_bias))
        return (self.frame_matrix.dot(self.base_B),
                self.frame_matrix.dot(self.base_bias))

    def _query_offset(self, t, obs):
        if obs[13] <= 0.5 or not self.measurements:
            return 0.0, 0.0, 0.0, 0.0
        stamp = int(round(t - obs[12]))
        last = self.measurements[-1]
        gap = stamp - last[0]
        px = last[1] + self._slope(self.measurements, 1) * gap * DT
        py = last[2] + self._slope(self.measurements, 2) * gap * DT
        pyaw = last[3] + self._slope(self.measurements, 3) * gap * DT
        dx, dy = obs[0] - px, obs[1] - py
        da = wrap(math.atan2(obs[3], obs[2]) - pyaw)
        score = (dx / 0.035) ** 2 + (dy / 0.035) ** 2 + (da / 0.025) ** 2
        return dx, dy, da, score

    @staticmethod
    def _slope(samples, index):
        if len(samples) < 2:
            return 0.0
        recent = samples[-6:]
        tm = sum(v[0] for v in recent) / len(recent)
        ym = sum(v[index] for v in recent) / len(recent)
        den = sum((v[0] - tm) ** 2 for v in recent)
        if den <= 0.0:
            return 0.0
        return sum((v[0] - tm) * (v[index] - ym) for v in recent) / den / DT

    def _measurement_ok(self, stamp, x, y, yaw):
        if not self.measurements:
            return True
        last = self.measurements[-1]
        gap = stamp - last[0]
        if gap <= 0:
            return False
        if gap >= 4:
            return True
        vx = self._slope(self.measurements, 1)
        vy = self._slope(self.measurements, 2)
        omega = self._slope(self.measurements, 3)
        px, py = last[1] + vx * gap * DT, last[2] + vy * gap * DT
        pyaw = last[3] + omega * gap * DT
        return math.hypot(x - px, y - py) < 0.060 and abs(wrap(yaw - pyaw)) < 0.075

    def _advance(self, t, obs):
        t = int(t)
        self.raw_imus[t] = np.asarray(obs[8:11], dtype=float)
        if self.last_t is None:
            yaw = math.atan2(obs[3], obs[2])
            self.state = np.array([obs[0], obs[1], 0.0, 0.0, yaw, 0.0])
            self.last_t = t
        else:
            gap = max(1, t - self.last_t)
            abx, aby, alpha = self._imu_to_body(obs[8:11])
            for _ in range(gap):
                x, y, vx, vy, yaw, omega = self.state
                c, s = math.cos(yaw), math.sin(yaw)
                ax, ay = c * abx - s * aby, s * abx + c * aby
                vx = 0.998 * (vx + ax * DT)
                vy = 0.998 * (vy + ay * DT)
                omega = 0.996 * (omega + alpha * DT)
                x += vx * DT
                y += vy * DT
                yaw = wrap(yaw + omega * DT)
                self.state = np.array([x, y, vx, vy, yaw, omega])
            if t <= 26:
                self.model.update(self.last_action, obs[8:11])
                if t == 26:
                    self.base_B, self.base_bias = self.model.plant()
                    self._estimate_sensor_frame()
            else:
                self._update_fault(t, self.last_action, obs[8:11])
            self.last_t = t
            if self.goal_phase is not None:
                self.goal_phase = wrap(self.goal_phase + self.goal_rate * gap * DT)

        if obs[13] > 0.5:
            stamp = int(round(t - obs[12]))
            mx, my = float(obs[0]), float(obs[1])
            myaw = math.atan2(obs[3], obs[2])
            mg = math.atan2(obs[7], obs[6])
            if self.measurements:
                myaw = self.measurements[-1][3] + wrap(myaw - self.measurements[-1][3])
                mg = self.measurements[-1][4] + wrap(mg - self.measurements[-1][4])
            if self._measurement_ok(stamp, mx, my, myaw):
                item = (stamp, mx, my, myaw, mg)
                self.measurements.append(item)
                self.all_measurements.append(item)
                self.measurements = self.measurements[-10:]
                vx = clip(self._slope(self.measurements, 1), -1.0, 1.0)
                vy = clip(self._slope(self.measurements, 2), -1.0, 1.0)
                omega = clip(self._slope(self.measurements, 3), -0.8, 0.8)
                self.goal_rate = clip(self._slope(self.measurements, 4), -0.16, 0.16)
                age = max(0, t - stamp)
                px, py = mx + vx * age * DT, my + vy * age * DT
                pyaw = myaw + omega * age * DT
                self.state[0] = 0.12 * self.state[0] + 0.88 * px
                self.state[1] = 0.12 * self.state[1] + 0.88 * py
                self.state[2] = 0.35 * self.state[2] + 0.65 * vx
                self.state[3] = 0.35 * self.state[3] + 0.65 * vy
                self.state[4] = wrap(self.state[4] + 0.88 * wrap(pyaw - self.state[4]))
                self.state[5] = 0.35 * self.state[5] + 0.65 * omega
                self.goal_phase = wrap(mg + self.goal_rate * age * DT)

    def _calibration(self, t):
        if t == 0:
            return np.zeros(6)
        if 1 <= t <= 18:
            result = np.zeros(6)
            result[(t - 1) // 3] = 0.68
            return result
        return None

    @staticmethod
    def _allocate(B, bias, desired, start):
        # Projected accelerated gradient on a six-variable convex quadratic.
        weights = np.diag([1.0, 1.0, 1.45])
        bw = weights.dot(B)
        target = weights.dot(desired - bias)
        h = bw.T.dot(bw) + 0.0025 * np.eye(6)
        q = bw.T.dot(target)
        lipschitz = max(0.015, float(np.linalg.norm(h, 2)))
        u = np.clip(start.copy(), 0.0, 1.0)
        z = u.copy()
        momentum = 0.0
        for k in range(55):
            previous = u
            u = np.clip(z - (h.dot(z) - q) / lipschitz, 0.0, 1.0)
            next_momentum = (1.0 + math.sqrt(1.0 + 4.0 * momentum * momentum)) / 2.0
            z = u + ((momentum - 1.0) / next_momentum) * (u - previous)
            momentum = next_momentum
        return u

    def _guidance(self, t, obs):
        x, y, vx, vy, yaw, omega = [float(v) for v in self.state]
        phase = self.goal_phase
        if phase is None:
            phase = math.atan2(obs[7], obs[6])
        rate = self.goal_rate
        radius = math.hypot(x, y)
        bearing = math.atan2(y, x)
        phase_error = wrap(phase - bearing)

        port_c, port_s = math.cos(phase), math.sin(phase)
        if not self.close_mode:
            wx, wy = 1.58 * port_c, 1.58 * port_s
            port_distance = math.hypot(
                x - PORT_RADIUS * port_c, y - PORT_RADIUS * port_s
            )
            if ((math.hypot(x - wx, y - wy) < 0.18
                 and abs(phase_error) < 0.18)
                    or (radius < 1.52 and port_distance < 0.85)):
                self.close_mode = True
        target_radius = PORT_RADIUS if self.close_mode else 1.58

        # When the craft and port are on different sides, rotate on a safe outer arc.
        if radius > 1.48 and abs(phase_error) > 0.28:
            waypoint_angle = bearing + clip(phase_error, -0.34, 0.34)
            # Rotate and recover radius at once. Following the current radius
            # can create an outward spiral after the target reverses.
            target_radius = 1.62
            tx = target_radius * math.cos(waypoint_angle)
            ty = target_radius * math.sin(waypoint_angle)
        else:
            tx = target_radius * port_c
            ty = target_radius * port_s

        tvx, tvy = -rate * ty, rate * tx
        ffx, ffy = -(rate * rate) * tx, -(rate * rate) * ty
        ex, ey = tx - x, ty - y
        evx, evy = tvx - vx, tvy - vy
        kp = 0.52 if self.close_mode else 0.42
        kd = 1.28 if self.close_mode else 1.12
        ax = ffx + kp * ex + kd * evx
        ay = ffy + kp * ey + kd * evy

        if radius < 1.00:
            rx, ry = x / max(radius, 1e-6), y / max(radius, 1e-6)
            inward = min(0.0, vx * rx + vy * ry)
            rescue = 2.8 * (1.00 - radius) - 1.8 * inward
            ax += rescue * rx
            ay += rescue * ry

        yaw_error = wrap(phase - yaw)
        alpha = 0.78 * yaw_error + 1.32 * (rate - omega)
        c, s = math.cos(yaw), math.sin(yaw)
        body_x, body_y = c * ax + s * ay, -s * ax + c * ay
        body_x, body_y = clip(body_x, -0.28, 0.28), clip(body_y, -0.28, 0.28)
        desired = self._body_to_imu(body_x, body_y, clip(alpha, -0.22, 0.22))
        B, bias = self._plant()
        virtual_action = self._allocate(B, bias, desired, self.last_alloc)

        # Persistent, tiny probing keeps every column observable after the hidden loss.
        goal_distance = math.hypot(x - PORT_RADIUS * port_c, y - PORT_RADIUS * port_s)
        if t > 24 and goal_distance > 0.28:
            slot = (t // 2) % 6
            virtual_action[slot] = min(1.0, virtual_action[slot] + 0.060)
        self.last_alloc = virtual_action.copy()
        action = virtual_action.copy()
        if (self.ring_cycle is not None and self.ring_orientation is not None
                and self.ring_phase is not None and self.ring_identifications > 0):
            for slot in range(6):
                nozzle = self._ring_nozzle(slot)
                action[slot] = virtual_action[slot] ** (
                    1.0 / max(float(self.ring_exponents[nozzle]), 0.20)
                )
        return action

    def act(self, t, obs):
        t = int(t)
        obs = [float(v) for v in obs]
        if len(obs) != 16:
            raise ValueError("expected a 16-element observation")
        if self.last_t is None or t != self.last_t:
            if self.last_t is not None and self.pending_score < float("inf"):
                self.last_action = self.pending_action.copy()
                self.close_mode = self.pending_close
                self.last_alloc = self.pending_alloc.copy()
            self._advance(t, obs)
            self.pending_score = float("inf")
            self.pending_action = np.zeros(6)
            self.pending_close = self.close_mode
            self.pending_alloc = self.last_alloc.copy()

        saved_state = self.state.copy()
        saved_close = self.close_mode
        saved_alloc = self.last_alloc.copy()
        dx, dy, da, score = self._query_offset(t, obs)
        self.state[0] += 0.90 * dx
        self.state[1] += 0.90 * dy
        self.state[4] = wrap(self.state[4] + 0.90 * da)
        calibration = self._calibration(t)
        if calibration is not None:
            action = calibration
        elif (self.ring_probe_start is not None
              and self.ring_cycle is None
              and t < self.ring_probe_start + 7):
            action = np.zeros(6)
            action[0] = 0.45
        elif self.ring_orient_probe and self.ring_orientation is None:
            action = np.zeros(6)
            action[1] = 0.45
        elif (self.ring_scan_start is not None
              and (len(self.ring_scan_samples) < 6
                   or any(len(self.ring_scan_samples.get(j, [])) < 2
                          for j in range(6)))):
            nozzle = next(
                j for j in range(6)
                if len(self.ring_scan_samples.get(j, [])) < 2
            )
            slot = self._ring_slot(nozzle)
            action = np.zeros(6)
            action[slot] = (0.35 if nozzle not in self.ring_scan_samples else 0.70)
        elif (self.ring_commission_pending
              and self.ring_bad_nozzle is not None):
            action = np.zeros(6)
            action[self._ring_slot(self.ring_bad_nozzle)] = 0.82
        else:
            action = self._guidance(t, obs)
        candidate_close = self.close_mode
        candidate_alloc = self.last_alloc.copy()
        self.state = saved_state
        self.close_mode = saved_close
        self.last_alloc = saved_alloc

        action = np.asarray(action, dtype=float)
        if score < self.pending_score:
            self.pending_score = score
            self.pending_action = action.copy()
            self.pending_close = candidate_close
            self.pending_alloc = candidate_alloc
            self.last_action = action.copy()
        return [float(clip(v, 0.0, 1.0)) for v in action]


def main():
    controller = Controller()
    print("READY", flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            kind = request.get("type")
            if kind == "reset":
                controller.reset()
                reply = {"ok": True}
            elif kind == "act":
                reply = {"action": controller.act(request.get("t", 0), request.get("obs", []))}
            elif kind == "close":
                print(json.dumps({"ok": True}, separators=(",", ":")), flush=True)
                return
            else:
                reply = {"error": "unknown request type"}
            print(json.dumps(reply, separators=(",", ":")), flush=True)
        except Exception as exc:
            print(json.dumps({"error": str(exc)[:200]}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
