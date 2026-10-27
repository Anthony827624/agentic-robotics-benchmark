#!/usr/bin/env bash
# Verify stage: runs as root, cwd = this staged folder, after the agent phase. The folder was absent while the
# agent worked; the engine uploads it world-readable, so the FIRST thing done here -- before the interpreter starts --
# is to close the folder and its parent to every other account (one chmod each; verify.py records the original modes
# from the two variables and restores them at the end, then kills the agent account's leftover processes, snapshots
# the submission, gates late writes and locks every file: CHAIN_PLAN 4.0 stage lock, hardened 2026-09-27).
set -euo pipefail
STAGE="$(pwd -P)"
export ARB_STAGE_MODE="$(stat -c %a "$STAGE")"
chmod 700 "$STAGE"
PARENT="$(dirname "$STAGE")"
if [ "$PARENT" != "/" ] && [ "$PARENT" != "${ALE_HOME:-/home/user}" ]; then
  export ARB_STAGE_PARENT_MODE="$(stat -c %a "$PARENT")"
  chmod 700 "$PARENT"
fi
export MUJOCO_GL="${MUJOCO_GL:-osmesa}"
exec python3 verify.py
