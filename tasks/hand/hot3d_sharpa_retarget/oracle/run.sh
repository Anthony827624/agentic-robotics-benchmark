#!/usr/bin/env bash
set -euo pipefail

# The reference artifact: one verified PD-target file per clip, copied as the submission.
DEST="${ALE_HOME:-/home/user}/submission"
mkdir -p "$DEST"
for CLIP in P0002_016222d1_seg016 P0002_016222d1_seg019 P0001_9b6feab7_seg003 \
    P0002_2ea9af5b_seg013 P0001_624f2ba9_seg009 P0001_8d136980_seg005 \
    P0001_624f2ba9_seg013 P0002_016222d1_seg018 P0009_37e30628_seg023; do
  SOURCE="$(dirname "$0")/reference/${CLIP}.npz"
  test -f "$SOURCE"
  cp "$SOURCE" "$DEST/${CLIP}.npz"
  echo "[oracle] wrote $DEST/${CLIP}.npz" >&2
done
