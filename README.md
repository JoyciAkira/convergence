# convergence

A skill plus the state machine that enforces it. The model-facing contract is
[`SKILL.md`](SKILL.md); the gates that make it real are
[`lib/convergence.py`](lib/convergence.py).

```bash
./scripts/run_tests.sh
```

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
