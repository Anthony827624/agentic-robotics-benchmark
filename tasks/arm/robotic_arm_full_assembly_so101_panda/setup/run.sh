#!/usr/bin/env bash
# Setup: trusted root, cwd = this staged folder, before the agent phase. Stages the givens (the kit, the manual, the
# cell documents, the Panda and the cell library) into the agent
# home, owned by the agent, and installs a second, ROOT-OWNED read-only copy of the same payload at /opt/arb:
# the grader compiles its physics (the Panda) from that copy and the programs get it as ARB_GIVEN_DIR during
# verify, so nothing the agent can write is ever an input of the grader (verify.py checks its sha256 against
# given_hashes.json). Dynamic-only: packages live in image/Dockerfile.
set -euo pipefail
HOME_DIR="${ALE_HOME:-/home/user}"
AGENT_USER="$(basename "$HOME_DIR")"
ROOT_PAYLOAD="${ARB_ROOT_PAYLOAD:-/opt/arb}"
cp -r payload/. "$HOME_DIR/"
# the large givens (the manual, the parts' STL files, the Panda model) and the cameras' scenery (render/) come from the
# image (image/Dockerfile, task assets)
if [ -d /opt/task-assets/payload ]; then
  cp -r /opt/task-assets/payload/. "$HOME_DIR/"
fi
for entry in payload/* payload/.[!.]* /opt/task-assets/payload/*; do
  [ -e "$entry" ] || continue
  chown -R "$AGENT_USER:$AGENT_USER" "$HOME_DIR/$(basename "$entry")"
done
mkdir -p "$HOME_DIR/submission" && chown "$AGENT_USER:$AGENT_USER" "$HOME_DIR/submission"
rm -rf "$ROOT_PAYLOAD"
mkdir -p "$ROOT_PAYLOAD"
cp -r payload/. "$ROOT_PAYLOAD/"
if [ -d /opt/task-assets/payload ]; then
  cp -r /opt/task-assets/payload/. "$ROOT_PAYLOAD/"
fi
chown -R root:root "$ROOT_PAYLOAD"
chmod -R u+rwX,go+rX,go-w "$ROOT_PAYLOAD"
echo "[setup] givens staged in $HOME_DIR (owner $AGENT_USER) and $ROOT_PAYLOAD (root, read-only)" >&2
