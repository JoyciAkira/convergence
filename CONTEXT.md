# Convergence Domain Glossary

## Ledger
Append-only state machine that records hypotheses, forks, evidence, directives, and outcomes. Owns its own concurrency invariant (file locking via `transaction`). Does NOT perform OS side effects directly after infrastructure extraction.

## Runtime
The external process environment that the convergence system manages: listener processes bound to ports, their lifecycle (boot/stop), and the signals used to retire them. A Runtime is something the Ledger *records events about* but does not *execute*.

## Infrastructure Seam
The boundary between the Ledger's pure state-machine logic and the OS-level side effects required to manage a Runtime. Crossed only through the RuntimeManager interface. Two adapters justify this seam: real OS runtime in production, stateful fake in tests.

## RuntimeManager
Adapter at the Infrastructure seam. Exposes higher-level operations (`start_listener`, `stop_listener`, `check_runtime`) that encapsulate port allocation, process spawning, signal delivery, and liveness verification. Ledger calls these; it never touches sockets or subprocesses directly.

## Event
A recorded state transition in the Ledger (e.g., hypothesis opened, fork closed, listener booted). Events are applied by handlers in the events module; they mutate Ledger state but do not perform IO.

## Derivation
Read-only computation over Ledger state (e.g., `outcome`, `report`, `next_action`). Pure functions that depend only on the state shape, not on how it was mutated. Separated from event handlers to concentrate change locality.

## ClaimComparator
Interface at the similarity seam for paraphrase detection during hypothesis opening. Default adapter uses Jaccard overlap; test adapter uses exact match. Injected into Ledger at construction.
## ReconstructionObligation
A structural, control-flow, or behavioral invariant derived by reverse engineering tools (REA, Ghidra, Hopper) that a clean-room reimplementation or binary fix must satisfy. Ingested via `import-obligations` and tracked to proven/unproven status.

## BinaryOracle
An execution environment (native runner, QEMU, Wine) running the original binary to generate ground-truth output against which clean-room code is differentially tested, satisfying the anti-tautology evidence requirement without circular code verification.

## Candidate
A recorded solution proposal from an exploration pass (arena run, parallel sub-agents, manual drafts). The Ledger never generates candidates; `record-candidates` stores provenance (source, count, files) and `select-candidate` records which one the run pursues, with `disputed` (contested on a fork) or `author` (unopposed self-selection, disclosed in the report) as the two selection weights.

## Intake Critic
Deterministic red-flag pass over the magnet's criteria at init: `restates_intent` (long criterion adding nothing to the intent) refuses the init; `unbounded_quality` and `no_oracle` flag the criterion in the ledger, and evidence against a flagged criterion requires an explicit `--expected-source`.

## One-shot
The minimal honest narrow sequence (init, declare/run loop, one settled hypothesis, begin-fix narrow, evidence) batched into a single CLI command. Every step delegates to the same gate-checked Ledger methods; `--red-observed` accepts only `true`, and each step is idempotent so an interrupted batch resumes.
