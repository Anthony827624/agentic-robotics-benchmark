#!/usr/bin/env bash
# Verify (root, separate verifier sandbox): root-only scratch, no user site, no agent environment.
set -euo pipefail
umask 077
STAGE_TMP="$(mktemp -d)"
chmod 700 "$STAGE_TMP"
export HOME=/root PYTHONNOUSERSITE=1 TMPDIR="$STAGE_TMP" PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
unset PYTHONPATH PYTHONSTARTUP
cd "$(dirname "$0")"
exec python3 -I -B verify.py
