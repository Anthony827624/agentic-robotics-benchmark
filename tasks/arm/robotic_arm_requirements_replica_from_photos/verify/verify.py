"""Grader of robotic_arm_requirements_replica_from_photos (info tier, requirements stage, rung I2). Root, verify stage, agent gone.

  0. FIRST THING, stdlib only (`_early_lockdown`; run.sh has already chmod 0700 the stage and its parent): record when
     the engine began uploading this folder (earliest ctime of its files), freeze and kill every process of the agent
     account (stats agent_processes_killed / _left: a survivor voids the run -> task error), lock the stage (0700 / 0600,
     restored at the end; stat stage_locked), take a root-owned 0700 snapshot of the submission that every later read
     uses, and zero every criterion when an entry of the submission was written at or after the stage upload began
     (gate G1 submission_integrity, stat submission_late_writes: the deliverable must be final when the session ends).
     G1 also checks the submission ROOT (lstat, never followed): a symbolic link, a non-directory, or a directory whose
     own ctime / mtime is at or after the upload (a directory renamed into place or edited by a leftover process: a
     rename updates the moved directory's ctime, not its children's) zeroes every criterion (stat
     submission_root_violation); nothing behind a symlinked root is read.
  1. G0 structure: the three files parse (YAML safe_load, JSON), validate against the v2 schemas, ids unique, one
     servo line with count 1 per joint, one PSU line, quote membership with the quote's role, bus 7.5 / 12 V, links =
     nominal +- tol, sketch servo ids = BOM lines. Failure -> every criterion 0.
  2. The GT arm of the hidden instance from gt/ (dynamics-only MJCF with the pockets' adapters + slots + stops), the
     vendors' own numbers gt/catalogue.json (the generator-side truth of the datasheet pages, the erratum and the quote:
     the only place these numbers exist in the task besides the documents), the acceptance set (IK cache re-solved and
     compared at 1e-6 rad: self-test, a mismatch is a task error), the linear mass regressor.
  3. Criteria (weights, grader_config.json, v2): kinematic_replication 0.30, requirements_sheet 0.10,
     derived_requirements 0.20, bom_acceptance 0.10 (the acceptance verdict: all nine items pass), bom_economy 0.30
     (live GT optimum J*, must equal gt/optimum.json; ECO repair; linear from J* to J* + one ECO).
     requirements_score = the weighted mean of the five, on [0, 1]; the verdict's single gated `reward` comes from the
     benchmark kit (robotics_grader.ratio_rewards against anchor.json: value 1.0 = the oracle, full_at 1.0), so reward =
     requirements_score; the five criteria, the gates and every stat ride along in `metrics`.
No agent code runs here (no network audit needed, E-REQ-14). Knobs: grader_config.json.
"""
import json
import os
import pwd
import stat as statmod
import sys
import time

sys.dont_write_bytecode = True
STAGE_DIR = os.environ.get("ALE_STAGE_DIR", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, STAGE_DIR)
HOME = os.environ.get("ALE_HOME", "/home/user")
AGENT_USER = os.path.basename(HOME.rstrip("/")) or "user"
SUBMISSION = os.path.join(HOME, "submission")
SNAP_ROOT = os.environ.get("ARB_SNAPSHOT_ROOT", "/opt/arb-verify")
SNAPSHOT = os.path.join(SNAP_ROOT, "submission")
LATE_GATE_MAX_GAP_S = 600.0

from arm.assembly_six import lockdown  # noqa: E402  (stdlib only)
from arm.assembly_cell import runner  # noqa: E402  (stdlib only)

CRITERIA = ("kinematic_replication", "requirements_sheet", "derived_requirements", "bom_acceptance", "bom_economy")


def log(msg):
    print("[grader] " + msg, file=sys.stderr, flush=True)


def _early_lockdown():
    out = {"agent_processes_killed": 0.0, "agent_processes_left": 0.0, "stage_locked": 0.0, "stage_parent_locked": 0.0,
           "submission_late_writes": 0.0, "submission_root_violation": 0.0, "late_write_gate_active": 0.0,
           "submission_snapshot": 0.0, "saved": None, "parent": None, "reasons": [], "t_ref": None}
    if os.getuid() != 0:
        return out
    t_ref = lockdown.stage_upload_time(STAGE_DIR)
    out["t_ref"] = t_ref
    try:
        uid = pwd.getpwnam(AGENT_USER).pw_uid
    except KeyError:
        uid = None
    if uid is not None and uid != 0:
        killed, left = lockdown.stop_and_kill_uid(uid, grace_s=8.0)
        out["agent_processes_killed"], out["agent_processes_left"] = float(killed), float(left)
    out["saved"] = runner.lock_stage(STAGE_DIR)
    out["stage_locked"] = 1.0 if out["saved"] is not None else 0.0
    parent = os.path.dirname(os.path.abspath(STAGE_DIR))
    if parent and parent not in ("/", os.path.abspath(HOME)) and os.path.isdir(parent):
        try:
            mode = statmod.S_IMODE(os.lstat(parent).st_mode)
            os.chmod(parent, 0o700)
            saved_mode = os.environ.get("ARB_STAGE_PARENT_MODE")
            out["parent"] = (parent, int(saved_mode, 8) if saved_mode else mode)
            out["stage_parent_locked"] = 1.0
        except (OSError, ValueError):
            pass
    gate_active = t_ref is not None
    cfg_json = os.path.join(STAGE_DIR, "config.json")
    if gate_active and os.path.isfile(cfg_json):
        try:
            gate_active = (os.lstat(cfg_json).st_ctime - t_ref) <= LATE_GATE_MAX_GAP_S
        except OSError:
            pass
    out["late_write_gate_active"] = 1.0 if gate_active else 0.0
    try:
        rst = os.lstat(SUBMISSION)
    except OSError:
        rst = None
    root_problem = None
    if rst is not None:
        if statmod.S_ISLNK(rst.st_mode):
            root_problem = "the submission root is a symbolic link"
        elif not statmod.S_ISDIR(rst.st_mode):
            root_problem = "the submission root is not a directory"
        elif gate_active and max(rst.st_ctime, rst.st_mtime) >= t_ref:
            root_problem = "the submission directory itself was replaced or changed after the agent phase ended"
    if root_problem:
        out["submission_root_violation"] = 1.0
        out["submission_late_writes"] += 1.0
        out["reasons"].append(root_problem)
    if rst is not None and statmod.S_ISDIR(rst.st_mode):
        if gate_active:
            late = lockdown.late_entries(SUBMISSION, t_ref)
            out["submission_late_writes"] += float(len(late))
            if late:
                out["reasons"].append("%d entr%s of the submission written after the agent phase ended (%s)" % (
                    len(late), "y" if len(late) == 1 else "ies", ", ".join(e[0] for e in late[:5])))
        os.makedirs(SNAP_ROOT, exist_ok=True)
        os.chmod(SNAP_ROOT, 0o700)
        lockdown.snapshot_dir(SUBMISSION, SNAPSHOT)
        out["submission_snapshot"] = 1.0
    return out


def _restore(early):
    try:
        if early.get("saved") is not None:
            runner.unlock_stage(early["saved"])
        if early.get("parent"):
            os.chmod(early["parent"][0], early["parent"][1])
        mode = os.environ.get("ARB_STAGE_MODE")
        if mode:
            os.chmod(STAGE_DIR, int(mode, 8))
    except OSError:
        pass


def write_verdict(rewards, weights, stats, diagnostics):
    """One gated `reward` (the kit's envelope); the criteria and stats go to `metrics`, the per-criterion diagnostics
    into the reward check's text (the record keeps them for the run's reader)."""
    from robotics_grader import ratio_rewards, read_anchor
    overall = sum(float(rewards[c]) * weights[c] for c in CRITERIA) / sum(weights[c] for c in CRITERIA)
    anchor, meta = read_anchor()
    extra = {k: float(v) for k, v in stats.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    extra.update({c: float(rewards[c]) for c in CRITERIA})
    verdict = ratio_rewards(overall, anchor, higher_is_better=meta.get("direction", "higher") == "higher",
                            metric_name=meta.get("metric", "requirements_score"), extra=extra,
                            full_at=meta.get("full_at", 1.0))
    text = "; ".join("%s %.4f: %s" % (c, float(rewards[c]), str(diagnostics.get(c, ""))[:380]) for c in CRITERIA)
    if os.environ.get("ALE_VERIFICATION_PATH"):
        from ale_verify import CheckResult, Verification
        rec = Verification()
        rec.check("reward", CheckResult(float(verdict["rewards"]["reward"]), text[:2000]))  # ale_verify's diagnostic cap
        for k, v in sorted(verdict["metrics"].items()):
            try:
                rec.stat(k, float(v))
            except (TypeError, ValueError):
                pass
        rec.write()
        return overall
    from robotics_grader import write_verdict as kit_write
    kit_write(verdict, os.environ.get("ALE_VERDICT_PATH", os.path.join(STAGE_DIR, "verdict.local.json")))
    return overall


def main():
    t0 = time.time()
    early = _early_lockdown()
    stats = {k: v for k, v in early.items() if isinstance(v, float)}
    stats["lockdown_s"] = time.time() - t0
    try:
        if early["agent_processes_left"] > 0:
            raise RuntimeError("%d agent process(es) survived the verify-start kill: run void" % early["agent_processes_left"])
        with open(os.path.join(STAGE_DIR, "grader_config.json")) as fh:
            cfg = json.load(fh)
        weights = cfg["weights"]
        sub = SNAPSHOT if early["submission_snapshot"] else SUBMISSION
        import hashlib
        from arm.req_info.i2 import grade as GR
        from arm.req_info.i2.catalog import Catalogue
        diagnostics = {}
        rewards = {c: 0.0 for c in CRITERIA}
        # given files unchanged (stat only): the agent's home copies vs given_hashes.json. lstat, regular files only
        # (a symlink counts as changed), a size different from the pinned one counts as changed without reading, the
        # hash is streamed; any error sets the stat to -1 (an agent-owned file never breaks the verdict)
        try:
            with open(os.path.join(STAGE_DIR, "given_hashes.json")) as fh:
                ghj = json.load(fh)
            gh, sizes = ghj["files"], ghj.get("sizes", {})
            ok = 0
            for rel, dig in gh.items():
                p = os.path.join(HOME, rel)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if not statmod.S_ISREG(st.st_mode) or (rel in sizes and st.st_size != int(sizes[rel])):
                    continue
                h = hashlib.sha256()
                with open(p, "rb") as fh:
                    for blk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(blk)
                if h.hexdigest() == dig:
                    ok += 1
            stats["given_files_unchanged"] = ok / max(1, len(gh))
        except Exception:  # noqa: BLE001  (a stat, never a verdict)
            stats["given_files_unchanged"] = -1.0
        if early["submission_late_writes"] > 0:
            stats["gate_g1_submission_integrity"] = 0.0
            diagnostics = {c: "G1: " + "; ".join(early["reasons"]) for c in CRITERIA}
            stats["gate_g0_structure"] = -1.0
            return write_verdict(rewards, weights, stats, diagnostics)
        stats["gate_g1_submission_integrity"] = 1.0
        cat = Catalogue(os.path.join(STAGE_DIR, "gt", "catalogue.json"))
        roles = {pid: cat.role(pid) for pid in cat.order}
        docs, problems = GR.load_submission(sub, roles)
        stats["gate_g0_structure"] = 0.0 if problems else 1.0
        if problems:
            diagnostics = {c: "G0 structure: " + "; ".join(problems[:6]) for c in CRITERIA}
            stats["g0_problems"] = float(len(problems))
            return write_verdict(rewards, weights, stats, diagnostics)
        t1 = time.time()
        gt = GR.load_gt(os.path.join(STAGE_DIR, "gt"))
        acc = GR.acceptance_for(gt)
        stats["acceptance_build_s"] = time.time() - t1
        # IK self-test: re-solve the cached grid, compare at 1e-6 rad (a mismatch is a task error)
        t2 = time.time()
        fresh = acc.solve_grid()
        import numpy as np
        dq = max(float(np.max(np.abs(np.array(a["q"]) - np.array(b["q"])))) for a, b in zip(fresh, acc.ik))
        stats["ik_selftest_max_dq_rad"] = dq
        stats["ik_selftest_s"] = time.time() - t2
        if dq > float(cfg.get("ik_selftest_tol_rad", 1e-6)):
            raise RuntimeError("acceptance-set IK self-test failed (max |dq| %.3g rad)" % dq)
        t3 = time.time()
        res = GR.grade(docs, gt, cat, cfg=cfg["grader"], acc=acc, optimum_mode="check")
        stats["grade_s"] = time.time() - t3
        rewards = res["criteria"]
        stats.update({k: v for k, v in res["stats"].items() if isinstance(v, (int, float))})
        diagnostics.update(res["diagnostics"])
        stats["verify_wall_s"] = time.time() - t0
        return write_verdict(rewards, weights, stats, diagnostics)
    finally:
        _restore(early)


if __name__ == "__main__":
    main()
