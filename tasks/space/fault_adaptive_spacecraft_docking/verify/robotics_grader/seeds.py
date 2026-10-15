"""Per-run hidden seeds.

The rule the whole benchmark rests on: the seeds an episode is scored under are sampled
fresh, inside the grader process, after the agent is gone — and the values never reach a
path the agent's UID can stat. Anything weaker turns "solve the task" into "memorize the
evaluation".

A stage script gets an integer stream from `seed_rng` and nothing else. There is no
seed file, no environment variable carrying the seed, and no derivation from anything the
agent could have observed.
"""

import os
import struct

__all__ = ["seed_rng"]

_MASK = (1 << 64) - 1


class _SeedStream(object):
    """A small deterministic-per-instance, unpredictable-across-runs integer source.

    Seeded from OS entropy, so two runs of the same task score different worlds; drawn by
    a fixed algorithm, so one grader process can reproduce its own stream if it needs to
    replay an episode within the run. stdlib only — a kit cannot assume numpy.
    """

    def __init__(self, entropy=None):
        raw = entropy if entropy is not None else struct.unpack("<Q", os.urandom(8))[0]
        self._state = raw & _MASK or 0x9E3779B97F4A7C15

    def _next(self):
        # splitmix64: one multiply-xor round, good enough to hand seeds to a real RNG.
        self._state = (self._state + 0x9E3779B97F4A7C15) & _MASK
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        return z ^ (z >> 31)

    def seed(self, bits=31):
        """One seed, sized for the consumer (gymnasium wants < 2**31)."""
        return self._next() & ((1 << bits) - 1)

    def seeds(self, count, bits=31):
        return [self.seed(bits) for _ in range(count)]

    def random(self):
        """A float in [0, 1) — for probe scheduling, not for physics."""
        return (self._next() >> 11) * (1.0 / (1 << 53))

    def uniform(self, low, high):
        return low + (high - low) * self.random()

    def choice(self, items):
        return items[self._next() % len(items)]


def seed_rng(entropy=None):
    """The grader's own hidden seed source. Call once per verify run.

    `entropy` exists for the kit's self-test; a real verify stage passes nothing, so the
    stream comes from `os.urandom` and lives only in this process's memory.
    """
    return _SeedStream(entropy)
