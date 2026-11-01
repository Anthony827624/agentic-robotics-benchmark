#!/usr/bin/env bash
# Setup (root): stage the agent's kit and the recordings of the fixed instances (agent-visible data only).
set -euo pipefail
umask 077
export HOME=/root PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONSTARTUP
cd "$(dirname "$0")"
HOME_DIR="${ALE_HOME:-/home/user}"
AGENT_USER="$(basename "$HOME_DIR")"
# fixed order (sorted), fixed mtime, agent-owned, world-readable
(cd payload && find . -type d | LC_ALL=C sort | while read -r d; do mkdir -p "$HOME_DIR/$d"; done)
(cd payload && find . -type f | LC_ALL=C sort | while read -r f; do cp "$f" "$HOME_DIR/$f"; done)
# the recordings fetched into the image (image/assets.sha256, docs/task-assets.md) join data/ before it is normalised
if [ -d /opt/task-assets/payload ]; then
  cp -r /opt/task-assets/payload/. "$HOME_DIR/"
  for entry in /opt/task-assets/payload/*; do chown -R "$AGENT_USER:$AGENT_USER" "$HOME_DIR/$(basename "$entry")"; done
fi
for entry in payload/*; do
  [ -e "$entry" ] || continue
  tgt="$HOME_DIR/$(basename "$entry")"
  chown -R "$AGENT_USER:$AGENT_USER" "$tgt"
  find "$tgt" -type d -exec chmod 0755 {} + ; find "$tgt" -type f -exec chmod 0644 {} +
  find "$tgt" -exec touch -h -d '2026-01-01 00:00:00 UTC' {} +
done
mkdir -p "$HOME_DIR/submission" && chown "$AGENT_USER:$AGENT_USER" "$HOME_DIR/submission"
