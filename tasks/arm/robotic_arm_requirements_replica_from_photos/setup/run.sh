#!/usr/bin/env bash
# Setup: trusted root, cwd = this staged folder, before the agent phase. Stages the givens (brief, build log, quote, photos,
# datasheet pages, the two lot reports, schemas, the structure check and the public, catalogue-free grading modules) into
# the agent home, owned by the agent. Nothing hidden is in this folder: the arm's ground truth and the vendors' own
# numbers live in verify/ (absent during the agent phase). Dynamic-only: packages are in image/Dockerfile.
set -euo pipefail
HOME_DIR="${ALE_HOME:-/home/user}"
AGENT_USER="$(basename "$HOME_DIR")"
cp -r payload/. "$HOME_DIR/"
# the large givens (photos, datasheet pages, lot reports) come from the image (image/Dockerfile, task assets)
if [ -d /opt/task-assets/payload ]; then
  cp -r /opt/task-assets/payload/. "$HOME_DIR/"
fi
for entry in payload/* payload/.[!.]* /opt/task-assets/payload/*; do
  [ -e "$entry" ] || continue
  chown -R "$AGENT_USER:$AGENT_USER" "$HOME_DIR/$(basename "$entry")"
done
mkdir -p "$HOME_DIR/submission" && chown "$AGENT_USER:$AGENT_USER" "$HOME_DIR/submission"
chmod +x "$HOME_DIR/schema_check.py" 2>/dev/null || true
echo "[setup] givens staged in $HOME_DIR (owner $AGENT_USER)" >&2
