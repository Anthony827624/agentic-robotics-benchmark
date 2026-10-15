"""TemplateReach — the toy simulator this template scores against.

Stands in for whatever your task's real environment is. In a real task the simulator comes
from the image (a pinned upstream repo installed at build time) and this file disappears;
what stays is the shape: the grader constructs the environment itself, steps it itself, and
computes the metric from its own state.

Two byte-identical copies ship, and a test in the tasks repository holds them identical:
the grader's own copy, staged only when scoring runs and never on a path the agent can
reach, and the agent's development copy at /home/user/template_env.py. The agent may edit
its copy freely — that changes what it develops against, not what it is scored on. A
metric computed from code the agent can reach is not a metric.
"""

import math

__all__ = ["TemplateReach"]

STEPS = 200
DT = 0.05
DAMPING = 0.98
ARENA = 1.0

_MASK = (1 << 64) - 1


def _lcg(state):
    """One step of a 64-bit LCG. Stdlib-only and identical on every interpreter, so an
    episode is defined by its seed alone — which is what makes hidden seeds meaningful.

    An LCG is fine HERE only because the seed decides nothing the first observation does not
    already show (start and goal). If your simulator draws hidden parameters, an agent that
    holds its source can recover them from an LCG's outputs: draw them from a keyed hash
    (e.g. `hashlib.blake2b(counter, key=seed)`) and seed with 64 bits."""
    return (state * 6364136223846793005 + 1442695040888963407) & _MASK


class TemplateReach(object):
    """A damped 2-D point mass that has to reach a goal and stay on it."""

    obs_size = 6
    action_size = 2
    max_steps = STEPS

    def __init__(self):
        self.x = self.y = self.vx = self.vy = 0.0
        self.gx = self.gy = 0.0
        self.steps = 0

    def reset(self, seed):
        state = _lcg(int(seed) & _MASK)
        values = []
        for _ in range(4):
            state = _lcg(state)
            values.append(((state >> 33) / float(1 << 30)) - 1.0)  # in [-1, 1)
        self.gx = values[0] * ARENA * 0.8
        self.gy = values[1] * ARENA * 0.8
        self.x = values[2] * ARENA * 0.5
        self.y = values[3] * ARENA * 0.5
        self.vx = self.vy = 0.0
        self.steps = 0
        return self.observation()

    def observation(self):
        return [self.x, self.y, self.vx, self.vy, self.gx, self.gy]

    def step(self, action):
        ax = _clip(float(action[0]), -1.0, 1.0)
        ay = _clip(float(action[1]), -1.0, 1.0)
        self.vx = (self.vx + ax * DT) * DAMPING
        self.vy = (self.vy + ay * DT) * DAMPING
        self.x = _clip(self.x + self.vx * DT, -ARENA, ARENA)
        self.y = _clip(self.y + self.vy * DT, -ARENA, ARENA)
        self.steps += 1
        distance = math.sqrt((self.x - self.gx) ** 2 + (self.y - self.gy) ** 2)
        return self.observation(), -distance, self.steps >= STEPS


def _clip(value, low, high):
    return low if value < low else (high if value > high else value)
