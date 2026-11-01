#!/usr/bin/env bash
# Privileged reference solution (SPEC 14.1 / 17): a scripted controller that knows every hidden box's
# mechanism (oracle/ref/boxes.json, protected reference data: part types, positions, directions and
# the unlock order). At each episode start it estimates the box placement from the two fixed cameras
# (top-face segmentation + fit of the known outline), then drives the stages in order with scripted
# primitives. Runs as the agent account with the agent's timeouts and network policy; spends no lab
# trials; reads nothing outside oracle/.
set -euo pipefail
SUB="${ALE_HOME:-/home/user}/submission"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SUB"
cp "$HERE"/solver/*.py "$SUB/"
cp "$HERE/ref/boxes.json" "$SUB/boxes.json"
cat > "$SUB/run.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec /opt/venv/bin/python3 -I oracle_main.py
SH
chmod +x "$SUB/run.sh"
echo "[oracle] submission ready at $SUB" >&2
