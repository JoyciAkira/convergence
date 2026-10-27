# convergence

A skill plus the state machine that enforces it. The model-facing contract is
[`SKILL.md`](SKILL.md); the gates that make it real are
[`lib/convergence.py`](lib/convergence.py).

```bash
./scripts/run_tests.sh
python3 scripts/skill_drift.py   # prose->lib->tests mapping must hold
```

Domain glossary: [`CONTEXT.md`](CONTEXT.md). Minimal honest run:
[`examples/minimal-narrow/run.sh`](examples/minimal-narrow/run.sh).

## What it is

An append-only ledger (`control.jsonl`, `hypotheses.jsonl`, `forks.jsonl`,
`evidence.jsonl`) under `.omc/convergence/<run-id>/`. State is derived by replay, so a
run survives a crash, a compaction or a session change, and `next-action` resumes exactly
where it stopped.

Three entry points, all funnelling into the same ledger:

- **RED** — a feedback loop must be declared *and run red* before a hypothesis may exist;
  three rival explanations must be tested with real probes before a fix is permitted.
- **FORK** — a mid-flight decision needs at least two real options, an adversarial
  dispute from a context that is not the author's, a delegated-authority check against
  the human anchors, and an explicit list of what the old assumption invalidated.
- **NON-CONVERGENCE** — repetition is *classified*, never counted. A defective loop and an
  unresolved hypothesis are directives with no cap. Only a named missing fact stops a run.

## Working from a prompt or a spec

`init --prompt-file` takes a raw request — one line or a very complex multi-goal brief
— and decomposes it into intent, acceptance criteria with provenance, candidate
hypotheses and exclusions, deterministically and without truncation. Requirement
sentences (`must`, `deve`, `ensure ...`) become checks wherever they appear; task
instructions are refused with the question the asker must answer: what would prove it
done? The shape is auditable at `--intake` before anything is written. Huge or
shell-hostile text enters via `--intent-file` / `--criteria-file` (or `-` for stdin):
no argv limits, multiline, unicode and BOM-prefixed files survive intact.

`import-tickets` maps a `/to-tickets` issue directory onto acceptance criteria, keeping
the ticket boundaries as provenance. A ticket *merged* upstream is not *earned* here:
the report's `## Ticket provenance` section separates the two, and `close` refuses while
any imported criterion is unverified. Tickets without verifiable criteria, duplicated
criteria and dangling `Blocked by` edges are refused at intake and again on replay.
Both to-tickets shapes are read — the local template and the issue template used on
real trackers — with native ticket ids (`01`, `42`, `ENG-123`) and `Blocked by` edges as
numbers, native keys, titles or tracker URLs, so a GitHub/Linear export imports
unchanged. The `spec-implement` skill drives the whole loop (import → frontier →
evidence → close) and is model-invocable, unlike the human-triggered `/implement-spec`.

`import-obligations` maps reverse engineering reconstruction manifests (REA
bundles, Ghidra export ledgers, clean-room obligation specs) into acceptance
criteria. Provenance is maintained under `## Reconstruction obligation provenance`,
distinguishing earned from unproven obligations. Supported `--expected-source`
expands to `binary_oracle` (differential execution against original binaries)
and `rea_evidence` (cryptographic Evidence ID reference).

`route` gives a driving pipeline one routable word (`investigate` / `decide` / `unblock`
/ `verify` / `human` / `close`) plus a fallback lane for when the primary one is blocked
from outside the session. `init --intake` validates the magnet without writing anything;
`freeze-intent` seals it so the only way to move the right edge is a disputed fork.

Every report closes with `## Auto-critique`: the run names its own weaknesses — unfalsified
searches, untraceable evidence, verdict-less disputes, overruled dissent, narrow searches,
intent drift — each row pointing at a record.

## Why a machine and not prose

The skills this composes with already state these rules, and already let them be skipped.
Here a refusal is an exit code:

| code | meaning |
|---|---|
| 0 | applied |
| 2 | malformed input |
| 3 | a gate refused; the reason is on stderr |

`close` derives the final status by precedence — `FAILED VERIFICATION` > `BLOCKED` >
`COMPLETE WITH RISKS` > `COMPLETE` — and exits non-zero unless the run earned the right to
call itself finished. It cannot be asserted into existence.

## Tests

`tests/test_convergence.py` is behavioural: every case drives a gate and asserts the
observable effect. No test asserts on the contents of a source file, so deleting
behaviour while leaving prose behind turns the suite red. Removing any one of these
guards fails the suite:

```
self-dispute gate · min-3-hypotheses · open-hypothesis-blocks-fix · dispute-before-decide
irreversibility authority · tautology · criteria-coverage · red-capable loop
```

Concurrency is covered at both levels: threads racing through the in-process lock, and
real OS processes (each CLI invocation is one) racing through `flock` — exactly one
winner for contested writes, no lost events, and `compact()` proven to replay to the
same state after multi-process churn.
