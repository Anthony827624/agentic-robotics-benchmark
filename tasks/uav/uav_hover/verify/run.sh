#!/usr/bin/env bash
# Verify stage — runs as root, cwd = this staged folder, AFTER the agent phase; verify/
# was absent while the agent worked ("absent, not hidden"). verify.py owns the sim and
# drives the agent-built solver as the unprivileged agent user.
set -euo pipefail

exec python3 verify.py
