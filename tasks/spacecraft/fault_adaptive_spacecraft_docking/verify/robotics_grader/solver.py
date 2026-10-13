"""Driving an agent-built solver across the privilege boundary (the closed_loop pattern).

The grader owns the simulator and asks the agent's policy for one action at a time. Three
properties make that safe to score:

1. **The solver runs as the agent, not as root.** `verify/run.sh` is root — the framework
   runs it on the task's behalf — so spawning the solver directly would give the agent's
   code the grader's own privileges. `stage.deprivileged` drops to the agent account, and
   from there `/opt/ale` is untraversable and the grader's memory unreadable.
2. **Every exchange has a deadline.** A solver that hangs must cost the episode, not the
   run. Reads happen on a queue fed by a reader thread, so a stall is a timeout rather
   than a wedged pipe.
3. **The wire carries no scoring input.** The solver returns actions. The metric is
   computed by the grader from its own simulator state, so nothing the solver prints can
   move its score.

The protocol is one JSON object per line, stdout only, `READY` first (within
`READY_TIMEOUT_FACTOR` step deadlines of launch):

    {"type": "reset"}                        -> {"ok": true}
    {"type": "act", "t": 0, "obs": <json>}   -> {"action": <json>}
    {"type": "close"}                        -> {"ok": true}, then exit

`t` is the step index inside the episode — an integer the GRADER owns, restarting at 0
after every `reset`. `obs` is the observation exactly as the task's environment produces
it (a flat list for most tasks; a task with structured observations documents the shape
in its instruction). The solver answers every request from the observation it carries.

**Nothing on the wire says whether a request is a probe.** The match lock (`ProbeLock`)
asks a probed step twice with the SAME `t` — once with the real observation, once with a
perturbed copy, in an order the solver cannot predict — and applies only the real reply
to the simulator. A repeated `t` is therefore an ordinary event the instruction tells the
agent about ("a step may be queried more than once; repeated queries do not advance the
episode; count steps by `t`, not by requests"), and a solver that wanted to answer probes
differently has nothing to key on. The old design flagged probes with `"probe": true`,
which let a canned policy pass the lock by reacting only to flagged requests.
"""

import json
import os
import subprocess

try:  # py3
    import queue
except ImportError:  # pragma: no cover — py2 never runs here
    import Queue as queue  # type: ignore[no-redef]
import threading

from .stage import agent_home, deprivileged

__all__ = ["READY_TIMEOUT_FACTOR", "ProbeLock", "Solver", "SolverAborted", "SolverMissing"]

#: A solver may print library noise before its handshake; tolerate a bounded banner.
MAX_PRE_READY_LINES = 200

#: `READY` must arrive within this many step deadlines of launch (model loading is slower
#: than a step). With the template's 30 s step deadline that is 120 s; the instruction
#: quotes the product, so keep the two in sync.
READY_TIMEOUT_FACTOR = 4


class SolverAborted(Exception):
    """The solver crashed, stalled, or broke the protocol.

    This aborts the EPISODE, not the run: an aborted episode counts against the agent's
    score (it failed to produce a usable policy), which is different from a task error.
    """


class SolverMissing(Exception):
    """No submission where the task said one would be.

    An honest ZERO, not a task error: `ale validate`'s untouched pass runs the verify
    stage against a sandbox nobody touched, and demands a real all-zero verdict from it —
    a crash there reads as a broken task. The grader catches this and writes
    `zero_verdict(...)`; the checked path is in the message so an author who declared the
    wrong path can still see what was looked for.
    """


class Solver(object):
    """The agent's `run.sh`, running as the agent, spoken to over stdio.

    Launched as `bash run.sh` with the working directory set to the submission folder
    (`<agent home>/submission` unless `submission=` says otherwise).
    """

    def __init__(self, step_timeout=30.0, submission=None, entry="run.sh"):
        self.submission = submission or os.path.join(agent_home(), "submission")
        if not os.path.isfile(os.path.join(self.submission, entry)):
            raise SolverMissing("no submission at {}/{}".format(self.submission, entry))
        self.step_timeout = float(step_timeout)
        self.proc = deprivileged(
            ["bash", entry],
            cwd=self.submission,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            bufsize=1,
        )
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._await_ready()

    # -- wire ---------------------------------------------------------------- #
    def _pump(self):
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)  # EOF

    def _readline(self, timeout):
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise SolverAborted("solver exceeded its {}s step deadline".format(timeout))
        if line is None:
            raise SolverAborted("solver closed its stdout")
        return line

    def _await_ready(self):
        for _ in range(MAX_PRE_READY_LINES):
            if self._readline(self.step_timeout * READY_TIMEOUT_FACTOR).strip() == "READY":
                return
        raise SolverAborted("solver never printed READY")

    def request(self, message):
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (IOError, OSError):
            raise SolverAborted("solver stdin closed")
        while True:
            line = self._readline(self.step_timeout).strip()
            if not line:
                continue
            try:
                reply = json.loads(line)
            except ValueError:
                raise SolverAborted(
                    "solver wrote non-JSON on the wire: {!r}".format(line[:120])
                )
            if isinstance(reply, dict):
                return reply
            raise SolverAborted("solver reply is not an object")

    # -- protocol ------------------------------------------------------------ #
    def reset(self):
        self.request({"type": "reset"})

    def act(self, t, obs):
        """One action for step `t` of the current episode, from observation `obs`.

        The request is exactly `{"type": "act", "t": t, "obs": obs}` — no other key, ever.
        The same `t` may be asked more than once (see `ProbeLock.probe`); the solver cannot
        tell which of those requests the simulator will apply, and that is the point.
        """
        reply = self.request({"type": "act", "t": int(t), "obs": obs})
        if "action" not in reply:
            raise SolverAborted("solver error: {}".format(reply.get("error", "no action")))
        return reply["action"]

    def close(self):
        try:
            self.proc.stdin.write(json.dumps({"type": "close"}) + "\n")
            self.proc.stdin.flush()
        except (IOError, OSError):
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 — TimeoutExpired on py3.3+, anything else is moot
            self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


class ProbeLock(object):
    """The closed_loop match lock: does the action actually depend on the observation?

    A replay or a constant is the cheapest way to fake a policy, and both are invisible in
    the metric alone when a task's reward tolerates mediocrity. So the grader occasionally
    asks a step twice — once with the true observation, once with a perturbed copy — and
    compares. A policy that reads its input answers differently; a canned stream answers
    identically.

    The two requests carry the SAME step index `t` and nothing else that differs, and
    their order is drawn from the grader's hidden RNG, so on the wire a probe is
    indistinguishable from any other repeated query (`Solver.act`). The perturbed reply is
    never applied to the simulator, so a probe cannot change the episode it audits. Verdict
    is a fraction, not a single sample, because a genuine policy may legitimately saturate
    its action range on some states.
    """

    def __init__(self, rng, rate=0.1, pass_fraction=0.5, magnitude=0.4, dims=3,
                 tolerance=1e-4):
        self.rng = rng
        self.rate = float(rate)
        self.pass_fraction = float(pass_fraction)
        self.magnitude = float(magnitude)
        self.dims = int(dims)
        self.tolerance = float(tolerance)
        self.total = 0
        self.passed = 0

    def due(self):
        """Whether this step should carry a probe."""
        return self.rng.random() < self.rate

    def perturb(self, values):
        """Shift the leading components of a flat observation by a hidden amount."""
        shifted = list(values)
        for i in range(min(self.dims, len(shifted))):
            shifted[i] = shifted[i] + self.rng.uniform(-self.magnitude, self.magnitude)
        return shifted

    def probe(self, solver, t, obs, perturbed=None):
        """Query step `t` twice — real and perturbed, in a hidden order — and return the
        REAL action, the only one the caller may apply to the simulator.

        `perturbed` defaults to `self.perturb(obs)`; a task whose observation is not a
        flat list passes its own perturbed copy. Both requests are plain `Solver.act`
        calls with the same `t`, so the wire never says which one is the probe.
        """
        if perturbed is None:
            perturbed = self.perturb(obs)
        if self.rng.random() < 0.5:
            action_probe = solver.act(t, perturbed)
            action_real = solver.act(t, obs)
        else:
            action_real = solver.act(t, obs)
            action_probe = solver.act(t, perturbed)
        self.record(action_probe, action_real)
        return action_real

    def record(self, action_probe, action_real):
        """Score one probe pair. Returns True if this pair showed sensitivity."""
        self.total += 1
        a = [float(x) for x in _flatten(action_probe)]
        b = [float(x) for x in _flatten(action_real)]
        moved = len(a) != len(b) or max(
            [abs(x - y) for x, y in zip(a, b)] or [0.0]
        ) > self.tolerance
        if moved:
            self.passed += 1
        return moved

    @property
    def ok(self):
        """True when enough probes showed the policy reacting to its input.

        No probes fired (a very short episode, or rate 0) is not a failure — absence of
        evidence is not evidence of cheating, and the metric still has to be earned.
        """
        return self.total == 0 or (float(self.passed) / self.total) >= self.pass_fraction

    def rewards(self):
        """Lock outcomes, for the record. Never scored, but always visible."""
        return {
            "probes_total": float(self.total),
            "probes_passed": float(self.passed),
            "match_lock_ok": 1.0 if self.ok else 0.0,
        }


def _flatten(value):
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_flatten(item))
        return out
    if hasattr(value, "ravel"):  # numpy array, without importing numpy
        return [float(x) for x in value.ravel().tolist()]
    return [value]
