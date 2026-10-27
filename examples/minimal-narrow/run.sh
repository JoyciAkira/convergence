#!/usr/bin/env bash
# Minimal honest sequence for a small, fully reproduced defect (--search narrow).
# Usage: ./run.sh [ledger-dir]
set -euo pipefail
CONVERGENCE="python3 lib/convergence.py"
LEDGER="${1:-.omc/convergence/example-narrow}"

$CONVERGENCE init "$LEDGER" --intent "null guard ships" --criterion "rejects null input"
$CONVERGENCE freeze-intent "$LEDGER"
$CONVERGENCE declare-loop "$LEDGER" --incident INC-1 \
  --command "pytest -k null-guard" --symptom "crash on null input"
$CONVERGENCE run-loop "$LEDGER" --incident INC-1 --red true
$CONVERGENCE open-hypothesis "$LEDGER" --id H1 --incident INC-1 \
  --claim "the handler dereferences input without a null guard" \
  --prediction "adding the guard makes the loop go green"
$CONVERGENCE survive-hypothesis "$LEDGER" --id H1 \
  --probe "add guard locally and rerun" --observed "green"
$CONVERGENCE begin-fix "$LEDGER" --incident INC-1 --note "add the null guard" --search narrow
$CONVERGENCE record-evidence "$LEDGER" --criterion "rejects null input" \
  --command "pytest -k null-guard" --tier executed_here --expected-source literal
$CONVERGENCE close "$LEDGER"
