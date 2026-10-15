#!/usr/bin/env bash
# Setup — runs as trusted root, cwd = this staged folder, before the agent phase. Stages
# the agent-visible practice grader into the agent home, owned by the agent. Keep this
# stage dynamic-only: static installation belongs in image/Dockerfile, and lint rejects
# it here.
set -euo pipefail

HOME_DIR="${ALE_HOME:-/home/user}"
AGENT_USER="$(basename "$HOME_DIR")"

# cp -r, NOT -a: with -a the `payload/.` form also copies the payload directory's own
# root ownership onto the agent home, locking the agent out of its own workspace.
cp -r payload/. "$HOME_DIR/"
chown -R "$AGENT_USER:$AGENT_USER" "$HOME_DIR/practice_grader"
