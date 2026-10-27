#!/usr/bin/env bash
# Oracle: the photographed unit as the FreeCAD document the task asks for (one Body per part, the six servos, the
# Assembly with its grounded, fixed and revolute joints, configuration Z), installed in place of the agent's work by
# `ale validate` (same unprivileged account). Scores 1.0 on every gate and criterion by construction.
set -euo pipefail
HOME_DIR="${ALE_HOME:-/home/user}"
SUB="$HOME_DIR/submission"
mkdir -p "$SUB"
cp "$(dirname "$0")/reference/arm.FCStd" "$SUB/arm.FCStd"
echo "[oracle] reference document installed at $SUB/arm.FCStd" >&2
