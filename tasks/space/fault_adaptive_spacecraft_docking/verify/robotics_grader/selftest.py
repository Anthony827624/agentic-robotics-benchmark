"""Self-test for the kit: `python3 -m robotics_grader.selftest`.

Runs on the host and inside any image the kit lands in — stdlib only, no framework, no
docker. It exercises the parts that would fail silently rather than loudly: a probe lock
that passes a constant policy, a ratio that ignores its cap, an anchor that reads as zero.

The solver tests spawn real subprocesses over the real wire, but through `_LocalSolver`,
which skips the `runuser` UID drop — the drop needs two accounts and root. The drop itself
is covered by `ale validate` on a real task, where a solver that cannot be spawned aborts
every episode.
"""

import json
import os
import subprocess
import sys
import tempfile

from . import scoring, seeds, solver as solver_mod, stage

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   {}".format(label))
    else:
        print("  FAIL {} {}".format(label, detail))
        FAILURES.append(label)


# --------------------------------------------------------------------------- #
# seeds
# --------------------------------------------------------------------------- #
def test_seeds():
    print("[seeds]")
    a = seeds.seed_rng(entropy=12345)
    b = seeds.seed_rng(entropy=12345)
    check("same entropy reproduces the stream", a.seeds(5) == b.seeds(5))

    c, d = seeds.seed_rng(entropy=1), seeds.seed_rng(entropy=2)
    check("different entropy diverges", c.seeds(5) != d.seeds(5))

    fresh = [seeds.seed_rng().seed() for _ in range(8)]
    check("os entropy differs across instances", len(set(fresh)) == 8)

    rng = seeds.seed_rng(entropy=99)
    check("seeds fit gymnasium's range", all(0 <= s < 2 ** 31 for s in rng.seeds(200)))
    floats = [seeds.seed_rng(entropy=i).random() for i in range(200)]
    check("random() stays in [0, 1)", all(0.0 <= f < 1.0 for f in floats))
    lows = [seeds.seed_rng(entropy=i).uniform(-0.4, 0.4) for i in range(200)]
    check("uniform() respects its bounds", all(-0.4 <= x <= 0.4 for x in lows))


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #
def test_scoring():
    print("[scoring]")
    check("on-anchor scores 1.0", abs(scoring.ratio(100, 100) - 1.0) < 1e-9)
    check("half of anchor scores 0.5", abs(scoring.ratio(50, 100) - 0.5) < 1e-9)
    check("cap bounds a huge overshoot", scoring.ratio(10_000, 100, cap=1.5) == 1.5)
    check(
        "lower-is-better inverts",
        abs(scoring.ratio(50, 100, higher_is_better=False) - 1.5) < 1e-9,
    )
    check(
        "lower-is-better: zero measured is not a free win",
        scoring.ratio(0, 100, higher_is_better=False) == 0.0,
    )
    check(
        "lower-is-better: an infinite cost (no usable episode) scores 0",
        scoring.ratio(float("inf"), 100, higher_is_better=False) == 0.0,
    )
    try:
        scoring.ratio(float("inf"), 100)
        check("higher-is-better: an infinite measurement is a task error", False)
    except SystemExit:
        check("higher-is-better: an infinite measurement is a task error", True)
    no_episode = scoring.ratio_rewards(float("inf"), 0.1, higher_is_better=False,
                                       metric_name="mean_goal_distance", full_at=0.8)
    check("an infinite raw metric stays out of the record",
          "mean_goal_distance" not in no_episode["metrics"]
          and no_episode["rewards"]["reward"] == 0.0)

    # The scale guard. A negative anchor silently reverses the ranking, so it has to be a
    # task error rather than a clamp — clamping would hide a permanently wrong task.
    for label, args in [
        ("a negative anchor is a task error", (100, -50)),
        ("a zero anchor is a task error", (100, 0)),
        ("a negative measurement is a task error", (-5, 100)),
    ]:
        try:
            scoring.ratio(*args)
            check(label, False, "ratio returned instead of failing")
        except SystemExit:
            check(label, True)

    verdict = scoring.ratio_rewards(472.07, 472.07, metric_name="mean_episode_reward",
                                    full_at=0.8)
    check("the envelope splits rewards from metrics",
          set(verdict) == {"rewards", "metrics"})
    check("the gated map carries only the reward", set(verdict["rewards"]) == {"reward"})
    check("an on-anchor run saturates to exactly 1.0", verdict["rewards"]["reward"] == 1.0)
    check("the capped ratio rides along in metrics",
          abs(verdict["metrics"]["ratio"] - 1.0) < 1e-9)
    check("raw metric rides along in metrics",
          abs(verdict["metrics"]["mean_episode_reward"] - 472.07) < 1e-9)

    unlucky = scoring.ratio_rewards(0.86 * 472.07, 472.07, full_at=0.8)
    check("an unlucky-seed oracle still saturates", unlucky["rewards"]["reward"] == 1.0)
    partial = scoring.ratio_rewards(0.4 * 472.07, 472.07, full_at=0.8)
    check("below the band the gradient is continuous",
          abs(partial["rewards"]["reward"] - 0.5) < 1e-9)

    for label, bad in [("full_at above the cap is a task error", 2.0),
                       ("a non-positive full_at is a task error", 0.0)]:
        try:
            scoring.ratio_rewards(1.0, 1.0, full_at=bad)
            check(label, False, "ratio_rewards returned")
        except SystemExit:
            check(label, True)

    failed = scoring.ratio_rewards(472.07, 472.07, locks_ok=False)
    check("a failed lock zeroes the score",
          failed["rewards"]["reward"] == 0.0 and failed["metrics"]["ratio"] == 0.0)

    zero = scoring.zero_verdict(reason="no submission", anchored=True)
    check("the honest zero is a real verdict, not a crash",
          zero["rewards"] == {"reward": 0.0})
    check("the honest zero names the same reward keys",
          set(zero["rewards"]) == set(verdict["rewards"]))
    check("the honest zero fabricates no measurement",
          set(zero["metrics"]) == {"ratio", "anchor_recorded"})
    check("the honest zero still tells the truth about the anchor",
          zero["metrics"]["anchor_recorded"] == 1.0)

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ALE_STAGE_DIR"] = tmp
        with open(os.path.join(tmp, "anchor.json"), "w") as handle:
            json.dump({"metric": "success_rate", "value": 0.82}, handle)
        value, payload = scoring.read_anchor()
        check("anchor reads back", value == 0.82 and payload["metric"] == "success_rate")

        with open(os.path.join(tmp, "grader_config.json"), "w") as handle:
            json.dump({"episodes": 7}, handle)
        check("stage config reads back", scoring.stage_config()["episodes"] == 7)
        check("missing stage config is empty, not fatal",
              scoring.stage_config("absent.json") == {})
        # The engine overwrites <stage>/config.json with its own judge settings at
        # verify time; a kit that read it would get {} and every knob would silently
        # fall back to its default (10 episodes instead of 60 cost a real green run).
        with open(os.path.join(tmp, "config.json"), "w") as handle:
            json.dump({"engine": "judge-settings"}, handle)
        check("the engine-reserved config.json is not the grader's config",
              "engine" not in scoring.stage_config())

        with open(os.path.join(tmp, "anchor.json"), "w") as handle:
            json.dump({"value": None}, handle)
        value, _ = scoring.read_anchor()
        check("a null anchor reads as None, not a crash", value is None)

        os.remove(os.path.join(tmp, "anchor.json"))
        try:
            scoring.read_anchor()
            check("a MISSING anchor is a task error", False, "read_anchor returned")
        except SystemExit:
            check("a MISSING anchor is a task error", True)
        os.environ.pop("ALE_STAGE_DIR", None)

    bootstrap = scoring.ratio_rewards(123.4, None, metric_name="success_rate")
    check("a bootstrap run scores 0", bootstrap["rewards"]["reward"] == 0.0)
    check("a bootstrap run still reports the measurement",
          abs(bootstrap["metrics"]["success_rate"] - 123.4) < 1e-9)
    check("a bootstrap run is flagged as unanchored",
          bootstrap["metrics"]["anchor_recorded"] == 0.0)
    check("an anchored run is flagged as anchored",
          scoring.ratio_rewards(1.0, 1.0)["metrics"]["anchor_recorded"] == 1.0)


# --------------------------------------------------------------------------- #
# verdict
# --------------------------------------------------------------------------- #
def test_verdict():
    print("[verdict]")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "verdict.json")
        stage.write_verdict({"rewards": {"reward": 0.5},
                             "metrics": {"ratio": 0.75, "success_rate": 41.0}}, path=path)
        with open(path) as handle:
            payload = json.load(handle)
        check("verdict nests under 'rewards'", payload["rewards"]["reward"] == 0.5)
        check("metrics ride along beside the rewards",
              payload["metrics"]["ratio"] == 0.75)
        check("every value is a float", all(
            isinstance(v, float)
            for section in payload.values() for v in section.values()))
        try:
            stage.write_verdict({"rewards": {"ratio": 1.0}}, path=path)
            check("a verdict without 'reward' is a task error", False)
        except SystemExit:
            check("a verdict without 'reward' is a task error", True)
        try:
            stage.write_verdict({"rewards": {"reward": 1.2}}, path=path)
            check("a reward outside [0, 1] is a task error", False)
        except SystemExit:
            check("a reward outside [0, 1] is a task error", True)

        # The engine's record path without the engine is a task error, and nothing is
        # written. `sys.modules["ale_verify"] = None` makes the import fail whatever this
        # interpreter happens to have installed.
        record_path = os.path.join(tmp, "verification.json")
        verdict = {"rewards": {"reward": 0.5},
                   "metrics": {"ratio": 0.75, "success_rate": 41.0}}
        saved_module = sys.modules.get("ale_verify", "<absent>")
        sys.modules["ale_verify"] = None
        os.environ["ALE_VERIFICATION_PATH"] = record_path
        try:
            for f in (path, record_path):
                if os.path.exists(f):
                    os.remove(f)
            try:
                stage.write_verdict(verdict, path=path)
                check("ALE_VERIFICATION_PATH without ale_verify is a task error", False)
            except SystemExit:
                check("ALE_VERIFICATION_PATH without ale_verify is a task error", True)
            check("... and nothing is written",
                  not os.path.exists(path) and not os.path.exists(record_path))
        finally:
            os.environ.pop("ALE_VERIFICATION_PATH", None)
            if saved_module == "<absent>":
                sys.modules.pop("ale_verify", None)
            else:
                sys.modules["ale_verify"] = saved_module


# --------------------------------------------------------------------------- #
# probe lock
# --------------------------------------------------------------------------- #
def test_probe_lock():
    print("[probe lock]")
    lock = solver_mod.ProbeLock(seeds.seed_rng(entropy=7))
    for _ in range(10):
        lock.record([0.5], [0.5])          # a constant policy: probe == real
    check("a constant policy fails the lock", not lock.ok)
    check("lock rewards report the failure", lock.rewards()["match_lock_ok"] == 0.0)

    lock = solver_mod.ProbeLock(seeds.seed_rng(entropy=7))
    for i in range(10):
        lock.record([0.5 + 0.01 * (i + 1)], [0.5])
    check("an obs-sensitive policy passes the lock", lock.ok)
    check("passed count is exact", lock.rewards()["probes_passed"] == 10.0)

    lock = solver_mod.ProbeLock(seeds.seed_rng(entropy=7), pass_fraction=0.5)
    for i in range(10):
        lock.record([0.5 + (0.01 if i % 2 else 0.0)], [0.5])
    check("half-sensitive meets a 0.5 fraction", lock.ok)

    lock = solver_mod.ProbeLock(seeds.seed_rng(entropy=7), pass_fraction=0.9)
    for i in range(10):
        lock.record([0.5 + (0.01 if i % 2 else 0.0)], [0.5])
    check("half-sensitive misses a 0.9 fraction", not lock.ok)

    check("no probes is not a failure",
          solver_mod.ProbeLock(seeds.seed_rng(entropy=7)).ok)

    lock = solver_mod.ProbeLock(seeds.seed_rng(entropy=7))
    original = [1.0, 2.0, 3.0, 4.0]
    perturbed = lock.perturb(original)
    check("perturb leaves the original untouched", original == [1.0, 2.0, 3.0, 4.0])
    check("perturb shifts only the declared dims",
          perturbed[3] == 4.0 and perturbed[:3] != original[:3])

    lock = solver_mod.ProbeLock(seeds.seed_rng(entropy=7), tolerance=1e-4)
    check("a sub-tolerance wobble does not count as sensitivity",
          not lock.record([0.5 + 1e-9], [0.5]))

    lock = solver_mod.ProbeLock(seeds.seed_rng(entropy=7))
    check("nested actions flatten before comparison",
          lock.record([[0.1, 0.2]], [[0.9, 0.2]]))


# --------------------------------------------------------------------------- #
# solver wire (real subprocesses; UID drop excluded — see the module docstring)
# --------------------------------------------------------------------------- #
class _LocalSolver(solver_mod.Solver):
    """Same wire, spawned in-process instead of through `runuser`."""

    def __init__(self, script, step_timeout=5.0):
        self.submission = os.path.dirname(script)
        self.step_timeout = float(step_timeout)
        self.proc = subprocess.Popen(
            [sys.executable, script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            universal_newlines=True, bufsize=1,
        )
        try:
            import queue as _q
        except ImportError:  # pragma: no cover
            import Queue as _q  # type: ignore[no-redef]
        import threading
        self._lines = _q.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._await_ready()


_ECHO_SOLVER = '''\
import json, sys
print("READY", flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if req.get("type") == "close":
        print(json.dumps({"ok": True}), flush=True); break
    if req.get("type") == "reset":
        print(json.dumps({"ok": True}), flush=True); continue
    obs = req["obs"]
    print(json.dumps({"action": [sum(obs) * 0.1]}), flush=True)
'''

_NOISY_SOLVER = '''\
import json, sys
for i in range(5):
    print("loading library %d" % i, flush=True)
print("READY", flush=True)
for line in sys.stdin:
    req = json.loads(line.strip() or "{}")
    if req.get("type") == "close":
        print(json.dumps({"ok": True}), flush=True); break
    print(json.dumps({"action": [0.0]}), flush=True)
'''

# Logs every raw request it receives (one per line, to WIRE_LOG) so the test can read the
# wire itself; answers from the observation like _ECHO_SOLVER.
_WIRE_LOG_SOLVER = '''\
import json, os, sys
log = open(os.environ["WIRE_LOG"], "a")
print("READY", flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    log.write(line + "\\n"); log.flush()
    req = json.loads(line)
    if req.get("type") == "close":
        print(json.dumps({"ok": True}), flush=True); break
    if req.get("type") == "reset":
        print(json.dumps({"ok": True}), flush=True); continue
    print(json.dumps({"action": [sum(req["obs"]) * 0.1]}), flush=True)
'''

# A canned policy: ignores the observation entirely. Must fail the lock.
_CONSTANT_SOLVER = '''\
import json, sys
print("READY", flush=True)
for line in sys.stdin:
    req = json.loads(line.strip() or "{}")
    if req.get("type") == "close":
        print(json.dumps({"ok": True}), flush=True); break
    if req.get("type") == "reset":
        print(json.dumps({"ok": True}), flush=True); continue
    print(json.dumps({"action": [0.5, -0.5]}), flush=True)
'''

_SILENT_SOLVER = 'import time\ntime.sleep(60)\n'
_CRASH_SOLVER = 'raise SystemExit(3)\n'
_GARBAGE_SOLVER = '''\
import sys
print("READY", flush=True)
for line in sys.stdin:
    print("not json at all", flush=True)
'''
_STALL_SOLVER = '''\
import json, sys, time
print("READY", flush=True)
for line in sys.stdin:
    req = json.loads(line.strip() or "{}")
    if req.get("type") == "reset":
        print(json.dumps({"ok": True}), flush=True); continue
    time.sleep(30)
'''


def _write(tmp, body, name="solver.py"):
    path = os.path.join(tmp, name)
    with open(path, "w") as handle:
        handle.write(body)
    return path


def test_solver_wire():
    print("[solver wire]")
    with tempfile.TemporaryDirectory() as tmp:
        with _LocalSolver(_write(tmp, _ECHO_SOLVER)) as solver:
            solver.reset()
            action = solver.act(0, [1.0, 2.0, 3.0])
            check("a well-behaved solver answers", abs(action[0] - 0.6) < 1e-9)
            second = solver.act(1, [10.0])
            check("the wire survives many exchanges", abs(second[0] - 1.0) < 1e-9)

    with tempfile.TemporaryDirectory() as tmp:
        with _LocalSolver(_write(tmp, _NOISY_SOLVER)) as solver:
            check("a banner before READY is tolerated", solver.act(0, [0.0]) == [0.0])

    with tempfile.TemporaryDirectory() as tmp:
        try:
            _LocalSolver(_write(tmp, _SILENT_SOLVER), step_timeout=0.25)
            check("a solver that never says READY aborts", False, "no exception")
        except solver_mod.SolverAborted:
            check("a solver that never says READY aborts", True)

    with tempfile.TemporaryDirectory() as tmp:
        try:
            _LocalSolver(_write(tmp, _CRASH_SOLVER), step_timeout=2.0)
            check("a solver that exits at once aborts", False, "no exception")
        except solver_mod.SolverAborted:
            check("a solver that exits at once aborts", True)

    with tempfile.TemporaryDirectory() as tmp:
        with _LocalSolver(_write(tmp, _GARBAGE_SOLVER), step_timeout=2.0) as solver:
            try:
                solver.act(0, [0.0])
                check("non-JSON on the wire aborts", False, "no exception")
            except solver_mod.SolverAborted:
                check("non-JSON on the wire aborts", True)

    with tempfile.TemporaryDirectory() as tmp:
        with _LocalSolver(_write(tmp, _STALL_SOLVER), step_timeout=0.5) as solver:
            solver.reset()
            try:
                solver.act(0, [0.0])
                check("a stalled step aborts on its deadline", False, "no exception")
            except solver_mod.SolverAborted:
                check("a stalled step aborts on its deadline", True)

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ALE_HOME"] = tmp
        try:
            solver_mod.Solver()
            check("a missing submission raises SolverMissing", False, "no exception")
        except solver_mod.SolverMissing as exc:
            check("a missing submission raises SolverMissing", True)
            check("the missing-submission error names the checked path",
                  tmp in str(exc))
        os.environ.pop("ALE_HOME", None)


def _read_wire(path):
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_probe_wire():
    """The lock's requests are indistinguishable from ordinary ones ON THE WIRE."""
    print("[probe wire]")
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "wire.jsonl")
        os.environ["WIRE_LOG"] = log
        try:
            with _LocalSolver(_write(tmp, _WIRE_LOG_SOLVER)) as solver:
                solver.reset()
                solver.act(0, [1.0, 2.0, 3.0])
                lock = solver_mod.ProbeLock(seeds.seed_rng(entropy=11), dims=2)
                obs = [0.25, -0.5, 0.0, 0.0, 0.7, 0.7]
                real = lock.probe(solver, 7, obs)
                solver.act(8, obs)
        finally:
            os.environ.pop("WIRE_LOG", None)
        wire = _read_wire(log)
        acts = [m for m in wire if m.get("type") == "act"]
        check("every act request carries exactly type, t and obs",
              all(set(m) == {"type", "t", "obs"} for m in acts), str(acts))
        check("no request on the wire carries a probe key",
              not any("probe" in m for m in wire))
        check("t is an integer the grader owns",
              [m["t"] for m in acts] == [0, 7, 7, 8], str([m["t"] for m in acts]))
        check("obs is the observation itself, not nested under a second key",
              acts[0]["obs"] == [1.0, 2.0, 3.0])
        pair = [m["obs"] for m in acts if m["t"] == 7]
        check("a probed step is asked twice with the same t", len(pair) == 2)
        check("exactly one of the pair is the real observation",
              pair.count(obs) == 1 and pair[0] != pair[1])
        perturbed = pair[0] if pair[1] == obs else pair[1]
        check("the perturbed copy differs only in the declared dims",
              perturbed[2:] == obs[2:] and perturbed[:2] != obs[:2])
        check("probe() returns the REAL reply", abs(real[0] - sum(obs) * 0.1) < 1e-9)
        check("one probe recorded, and it showed sensitivity",
              lock.total == 1 and lock.passed == 1)

    # order is hidden: over many probes both orders occur
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "wire.jsonl")
        os.environ["WIRE_LOG"] = log
        try:
            with _LocalSolver(_write(tmp, _WIRE_LOG_SOLVER)) as solver:
                lock = solver_mod.ProbeLock(seeds.seed_rng(entropy=5), dims=2)
                obs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
                for t in range(40):
                    lock.probe(solver, t, obs)
        finally:
            os.environ.pop("WIRE_LOG", None)
        acts = [m for m in _read_wire(log) if m.get("type") == "act"]
        real_first = sum(1 for i in range(0, len(acts), 2) if acts[i]["obs"] == obs)
        check("the real observation comes first sometimes and second sometimes",
              0 < real_first < 40, "real-first {} of 40".format(real_first))
        check("an obs-sensitive policy passes the lock end to end", lock.ok)

    # a constant policy speaking the wire perfectly fails the lock end to end
    with tempfile.TemporaryDirectory() as tmp, \
            _LocalSolver(_write(tmp, _CONSTANT_SOLVER)) as solver:
        solver.reset()
        lock = solver_mod.ProbeLock(seeds.seed_rng(entropy=3), dims=2)
        for t in range(20):
            action = lock.probe(solver, t, [0.1 * t, 0.0, 0.0, 0.0, 1.0, 1.0])
        check("the constant policy's real action still comes back", action == [0.5, -0.5])
        check("a constant policy fails the lock end to end",
              not lock.ok and lock.total == 20 and lock.passed == 0)


def test_stage_identity():
    print("[stage identity]")
    os.environ["ALE_HOME"] = "/home/agent"
    check("agent user derives from the home", stage.agent_user() == "agent")
    os.environ["ALE_HOME"] = "/home/user/"
    check("a trailing slash does not break it", stage.agent_user() == "user")
    os.environ.pop("ALE_HOME", None)
    check("the default home is /home/user", stage.agent_user() == "user")


def main():
    print("robotics_grader selftest (python {}.{})".format(*sys.version_info[:2]))
    test_seeds()
    test_scoring()
    test_verdict()
    test_probe_lock()
    test_solver_wire()
    test_probe_wire()
    test_stage_identity()
    print()
    if FAILURES:
        print("FAILED: {}".format(", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
