#!/usr/bin/env bash
set -euo pipefail

HOME_DIR="${ALE_HOME:-/home/user}"
AGENT_USER="$(basename "$HOME_DIR")"

cp -r payload/. "$HOME_DIR/"
# the clips come from the image (image/Dockerfile, task assets)
if [ -d /opt/task-assets/payload ]; then
  cp -r /opt/task-assets/payload/. "$HOME_DIR/"
fi
for entry in payload/* payload/.[!.]* /opt/task-assets/payload/*; do
  [ -e "$entry" ] || continue
  chown -R "$AGENT_USER:$AGENT_USER" "$HOME_DIR/$(basename "$entry")"
done
