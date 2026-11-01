"""Hidden grader for robocasa_object_dynamics_from_video.

Reads /home/user/submission/params.json (numbers only) and scores objects A, B, C on the fixed hidden tests in the
grader's own copy of the true world (verify/ref): every action is planned on the true collision shape with the
submitted inertial parameters and friction, then executed once in the true world. Metric `object_dynamics_score`
(mean over objects of the mean family score, higher is better). Separate verifier sandbox; agent processes are
killed first (L3) and nothing else the agent left is read.
"""
import json
import os
import stat
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rgrading as RG  # noqa: E402
from robotics_grader import ratio_rewards, read_anchor, stage_config, write_verdict, zero_verdict  # noqa: E402

HOME = os.environ.get("ALE_HOME", "/home/user")
AGENT = os.path.basename(HOME.rstrip("/")) or "user"
REF = os.path.join(HERE, "ref", "objects.json")


def stop_agent(max_rounds=20):
    for _ in range(max_rounds):
        r = subprocess.run(["pgrep", "-u", AGENT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            return True
        subprocess.run(["runuser", "-u", AGENT, "--", "kill", "-KILL", "-1"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        time.sleep(0.2)
    return False


def read_submission():
    """Bytes of params.json, never following a symlink; None if absent or not a regular file."""
    sub = os.path.join(HOME, "submission")
    try:
        dfd = os.open(sub, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None, "no submission directory"
    try:
        try:
            fd = os.open("params.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except OSError:
            return None, "no regular params.json"
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None, "params.json is not a regular file"
            chunks, total = [], 0
            while total <= RG.MAX_BYTES:
                b = os.read(fd, 65536)
                if not b:
                    break
                chunks.append(b)
                total += len(b)
            return b"".join(chunks), None
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def _score_one(args):
    key, entry, deadline = args
    ref = RG.load_ref(REF)
    return key, RG.score_object(ref[key], entry, deadline)


def main():
    t0 = time.time()
    config = stage_config()
    anchor, meta = read_anchor()
    anchored = anchor is not None
    budget = float(config.get("budget_s", 1800))
    # Separate verifier sandbox: no agent code ever ran here, and the base image's supervisord respawns the agent
    # account's desktop within ~1-2 s of a kill (SPEC 14.10), so the L3 kill is best effort and recorded, never a
    # reason to score 0 (a probe-time oracle episode was zeroed by exactly this race, R/tR evidence).
    cleanup_ok = stop_agent()
    data, why = read_submission()
    if data is None:
        write_verdict(zero_verdict(reason=why, anchored=anchored))
        return
    try:
        entries, invalid = RG.parse_submission_bytes(data)
    except RG.InvalidSubmission as exc:
        write_verdict(zero_verdict(reason="invalid params.json: %s" % exc, anchored=anchored))
        return
    deadline = t0 + budget
    per = {k: (0.0, {"invalid": invalid.get(k, "missing")}) for k in RG.OBJECTS}
    jobs = [(k, entries[k], deadline) for k in RG.OBJECTS if k in entries]
    if jobs:
        with ProcessPoolExecutor(max_workers=int(config.get("procs", 3))) as ex:
            for k, res in ex.map(_score_one, jobs):
                per[k] = res
    measured = float(sum(per[k][0] for k in RG.OBJECTS) / len(RG.OBJECTS))
    extra = {"grading_seconds": round(time.time() - t0, 1), "agent_cleanup_incomplete": 0.0 if cleanup_ok else 1.0}
    for k, (s, info) in per.items():
        extra["score_" + k] = round(s, 4)
        for f, v in info.get("families", {}).items():
            extra["q_%s_%s" % (k, f)] = round(v, 4)
        if info.get("unscored"):
            extra["unscored_" + k] = info["unscored"]
        if info.get("invalid"):             # metrics are numbers only: the flag here, the reason on stderr
            extra["invalid_" + k] = 1.0
            print("[grader] object %s scores 0: %s" % (k, str(info["invalid"])[:200]), file=sys.stderr)
    verdict = ratio_rewards(measured, anchor, cap=config.get("scoring_cap", 1.5), higher_is_better=True,
                            metric_name="object_dynamics_score", extra=extra, full_at=meta.get("full_at", 1.0))
    write_verdict(verdict)
    print("[grader] object_dynamics_score=%.4f anchor=%s reward=%.3f %.0fs" % (
        measured, anchor, verdict["rewards"]["reward"], time.time() - t0), file=sys.stderr)


if __name__ == "__main__":
    main()
