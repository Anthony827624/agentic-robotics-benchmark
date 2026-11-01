"""Exercise real ALE isolation in a disposable task copy, not the submitted task."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

TASK = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ale", default="ale")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    task = out/"task"
    shutil.copytree(TASK, task, ignore=shutil.ignore_patterns(".ale-cache", "__pycache__", "*.pyc"))
    shutil.copy2(TASK/"tools/security_probe.py", task/"oracle/security_probe.py")
    (task/"oracle/run.sh").write_text('#!/usr/bin/env bash\nset -euo pipefail\ncd "$(dirname "$0")"\nexec python3 security_probe.py\n')
    (task/"oracle/run.sh").chmod(0o755)
    with (out/"ale.log").open("w") as log:
        process = subprocess.run([args.ale, "validate", str(task), "--runs-dir", str(out/"runs")],
                                 stdout=log, stderr=subprocess.STDOUT)
    rows = []
    for path in (out/"runs").rglob("result.json"):
        result = json.loads(path.read_text())
        if "-oracle-" not in result.get("episode_id", ""):
            continue
        trace = (path.parent/"trace.execution.jsonl").read_text()
        checks = trace.count("PASS ")
        rewards = result.get("rewards")
        passed = result["status"] == "completed" and rewards == {"reward": 0.0} and checks >= 16
        rows.append({"episode": result["episode_id"], "checks": checks,
                     "forged_reward": rewards, "status": result["status"], "passed": passed})
    summary = {"ale_returncode_expected_nonzero": process.returncode, "checks": rows,
               "passed": bool(rows) and all(row["passed"] for row in rows)}
    (out/"summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
