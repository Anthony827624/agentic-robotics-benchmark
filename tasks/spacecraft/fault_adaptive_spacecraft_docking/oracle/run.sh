#!/usr/bin/env bash
# The task's own reference solution, run in place of the agent by `ale validate` — as the
# same unprivileged account, with the same timeout. That identity is not a detail: an
# oracle with more privilege would pass exactly the tasks a real agent then fails on
# access alone.
#
# Two jobs: (1) produce whatever the real method produces — for a learning task, TRAIN
# here, inside the agent's budget, and save a checkpoint; (2) leave a self-contained
# /home/user/submission/ that the verify stage can drive without this folder. Its real
# measured score becomes the anchor.
set -euo pipefail

SUB="${ALE_HOME:-/home/user}/submission"
mkdir -p "$SUB"

# A real task trains here first, e.g.:
#   python3 "$(dirname "$0")/train.py" --out "$SUB/policy.pt"
# This task's reference policy is analytic, so there is nothing to train.

cp "$(dirname "$0")/solver.py" "$SUB/solver.py"

cat > "$SUB/run.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec python3 solver.py
SH
chmod +x "$SUB/run.sh"

echo "[oracle] submission ready at $SUB" >&2
