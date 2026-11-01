#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec python3 oracle_run.py
