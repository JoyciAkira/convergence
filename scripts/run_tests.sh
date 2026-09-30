#!/usr/bin/env bash
# Runs the behavioural suite for the convergence ledger.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m pytest tests/ -q "$@"
