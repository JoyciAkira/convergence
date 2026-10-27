#!/usr/bin/env bash
# Clean-room reverse engineering verification workflow.
# Usage: ./run.sh [ledger-dir]
set -euo pipefail
CONVERGENCE="python3 lib/convergence.py"
LEDGER="${1:-.omc/convergence/example-re}"

# 1. Initialize intent and baseline criterion
$CONVERGENCE init "$LEDGER" \
  --intent "clean-room reimplementation of libnative_crypto.so" \
  --criterion "crypto library exports public C ABI"

# 2. Ingest reverse engineering reconstruction obligations from REA / Ghidra
$CONVERGENCE import-obligations "$LEDGER" \
  --obligations examples/clean-room-re/obligations.json

# 3. Seal the magnet
$CONVERGENCE freeze-intent "$LEDGER"

# 4. Entry A: Red loop on round key discrepancy
$CONVERGENCE declare-loop "$LEDGER" --incident INC-ROUNDKEY \
  --command "python3 -c 'import crypto; assert crypto.check_fips()'" \
  --symptom "round key 10 word 0 mismatch"
$CONVERGENCE run-loop "$LEDGER" --incident INC-ROUNDKEY --red true

# 5. Formulate rival structural hypotheses
$CONVERGENCE open-hypothesis "$LEDGER" --id H1 --incident INC-ROUNDKEY \
  --claim "Rcon table index wraps at modulo 10 instead of 11" \
  --prediction "fixing modulo makes loop go green"
$CONVERGENCE open-hypothesis "$LEDGER" --id H2 --incident INC-ROUNDKEY \
  --claim "word rotation shifts left by 16 bits instead of 8 bits" \
  --prediction "aligning RotWord to 8-bit rotation fixes word 0"
$CONVERGENCE open-hypothesis "$LEDGER" --id H3 --incident INC-ROUNDKEY \
  --claim "S-box substitution table contains affine transform bug" \
  --prediction "verifying Sbox table isolates constant offset"

# 6. Test hypotheses with concrete probes (Ghidra / REA instruction analysis)
$CONVERGENCE refute-hypothesis "$LEDGER" --id H1 \
  --probe "rea_inspect_native_instruction --addr 0x4012a0" \
  --observed "modulo 10 bound is in key scheduler, not rcon"
$CONVERGENCE refute-hypothesis "$LEDGER" --id H3 \
  --probe "diff -u sbox.table native_sbox.table" \
  --observed "tables are identical"
$CONVERGENCE survive-hypothesis "$LEDGER" --id H2 \
  --probe "rea_trace_native_values --addr 0x4012c4" \
  --observed "native RotWord applies byte roll (8-bit); green after local roll patch"

# 7. Begin fix after hypotheses elimination
$CONVERGENCE begin-fix "$LEDGER" --incident INC-ROUNDKEY \
  --note "align clean-room RotWord implementation to 8-bit byte roll"

# 8. Record evidence using binary_oracle and rea_evidence
$CONVERGENCE record-evidence "$LEDGER" \
  --criterion "crypto library exports public C ABI" \
  --command "nm -D libcrypto_re.so | grep 'T aes_'" \
  --tier executed_here \
  --expected-source literal \
  --output-ref "artifacts/nm.log"

$CONVERGENCE record-evidence "$LEDGER" \
  --criterion "aes_expand_key produces 11 round keys matching FIPS-197" \
  --command "pytest tests/test_fips.py" \
  --tier executed_here \
  --expected-source rea_evidence \
  --output-ref "rea://bundle-901/evidence/EV-KEYEXP"

$CONVERGENCE record-evidence "$LEDGER" \
  --criterion "aes_decrypt_block matches binary oracle outputs across 1000 test vectors" \
  --command "python3 diff_harness.py --binary libnative_crypto.so --clean-room libcrypto_re.so" \
  --tier executed_here \
  --expected-source binary_oracle \
  --output-ref "differential_traces/parity_run_1000.log"

# 9. Close run (verifies outcome and outputs report)
$CONVERGENCE close "$LEDGER"
