---
name: convergence
description: "Run work to a defensible finish: an append-only ledger that enforces hypothesis elimination before any fix, a mandatory adversarial dispute before any mid-flight decision, classification instead of iteration caps when something stops converging, and three-tier evidence with derived final status. Use when the task is a project rather than a single change, when something fails repeatedly, when a genuine fork appears mid-implementation, or when the user demands no workarounds, no fake passes and no scope reduction."
---

# Convergence

**This skill has two halves. The prose tells you what to do. The machine decides whether
you are allowed to have done it.** If the two disagree, the machine wins — it exits `3`
and prints the gate that refused you. Do not look for a way around it, because there is
none, and that is the point.

## The magnet

The skill holds the run between two edges.

- **Left — the request.** One intent and its acceptance criteria, fixed at `init`. No
  command edits them. They move only through a disputed, recorded fork, and a moved edge
  starts unverified.
- **Right — the result.** Not negotiable, never traded, never reported as partly reached.
  There are exactly two answers: `REACHED` or `NOT REACHED`.
- **The middle.** Every hypothesis, fork, obstacle and retry. Governed by the machine,
  never terminal. A block parks one path and the run continues.

An obstacle is never an outcome. "No limitazioni" means the limit belongs where the
information is missing, not where persistence ran out.

The machine is `lib/convergence.py`. Every command below is a real subcommand of it.

```bash
CONVERGENCE="python3 <skill-dir>/lib/convergence.py"
LEDGER=".omc/convergence/<run-id>"     # append-only, inside the working repo
```

Exit codes: `0` applied · `2` malformed input · `3` a gate refused you.

---

## Why this exists

The surrounding skill library states the right rules in prose and then lets them be
skipped. `ralph` asks for fresh evidence; its `prd.json` is written by the same agent
that implemented the story. `ultraqa` stops after three identical failures and calls
that a diagnosis. `autopilot` says "never block on unavailable tools" and proceeds
without the reviewer it just required. `atomic-spec` has no protocol at all for a fork
discovered at minute five. Every one of those is a place where an unearned claim can
pass. This skill closes them mechanically.

## The loop

```bash
$CONVERGENCE next-action "$LEDGER"     # what the ledger requires of you, right now
```

Run that whenever you are unsure. It is derived state, not advice.

`status` prints the whole derived state as a **flat JSON object at the top level** — no
`state` wrapper. It is meant for scripts and for `grep`; do not paste it into a prompt on a
large run, it grows with the ledger. Use `next-action` and `close` when you want to read.

The ledger lives inside the working repo because that is where the evidence lives. Add
`.omc/convergence/` to `.gitignore` unless the audit trail is meant to ship with the
change; `report.md` is the human artefact, the four `.jsonl` files are the machine record.

## Start

```bash
$CONVERGENCE init "$LEDGER" \
  --intent "what outcome is actually wanted" \
  --criterion "an observable claim" "another one" "and a third"
```

`--criterion` is repeatable and takes several values per flag. Every fork option is one
JSON object per `--option`, requiring `id`, `cost`, `risk` and `reversibility`; an
optional `note` carries the argument *for* that option, which is usually what a challenger
needs to rebut it.

Criteria are the unit of verification. Each one must be closeable by a command someone
else could re-run. If you cannot name the command, the criterion is not a criterion yet.

### The right edge does not move quietly

`--intent` and the criteria are the two poles of the magnet. They are set once, at `init`,
and **no command edits them**. That is deliberate: without it, "done" is something an
agent can reach by rewriting the goal.

When reality makes a criterion unreachable or wrong, you do not edit it. You open a fork
that carries the move, and it goes through the same dispute as any other decision:

```bash
$CONVERGENCE open-fork "$LEDGER" --id FR \
  --question "this criterion turned out to be unreachable as written" \
  --trigger "the fourth fixture carries a BOM and was never in the original report" \
  --assumed keep \
  --criterion-from "handles CRLF" \
  --criterion-to "handles CRLF and BOM-prefixed fixtures" \
  --option '{"id":"keep","cost":"0d","risk":"low","reversibility":"reversible"}' \
          '{"id":"revise","cost":"1d","risk":"med","reversibility":"reversible"}'
```

Then `dispute`, then `decide --chosen revise --invalidated "AC-1 the CRLF test"`. The
revision applies only once the dispute is settled and the invalidation is named — and the
new criterion **starts unverified**, so the run is immediately `NOT REACHED` until you earn
it again. Widening the edge is allowed; widening it silently, or keeping the old green
while the new edge is unproven, is not.

---

# Entry A — a check went red

Trigger: any check fails, any test goes red, any behaviour is not what it should be.

## A1. Build the feedback loop before you think

```bash
$CONVERGENCE declare-loop "$LEDGER" --incident INC-1 \
  --command "pytest -k crlf" --symptom "import blows up on CRLF input"
$CONVERGENCE run-loop "$LEDGER" --incident INC-1 --red true
```

**`run-loop` takes the observed result, not an intention.** A loop that has not gone red
on the user's exact symptom cannot discriminate between explanations, and the gate will
refuse the next step until one has. This is the single highest-value gate in the skill:
it makes theorising without a signal impossible.

Spend disproportionate effort here. Be aggressive. Be creative. Refuse to give up. A
2-second deterministic red-capable command is worth more than an hour of reading code.
If no loop is possible, that is not a dead end — it is a finding, and you go to Entry C
with `information_missing`.

## A2. Write down rival explanations, each with a prediction

```bash
$CONVERGENCE open-hypothesis "$LEDGER" --id H1 --incident INC-1 \
  --claim "the file is opened in text mode" \
  --prediction "opening it in binary mode makes the loop go green"
```

At least three, before any fix is permitted (`--search full`, the default). One plausible
idea anchoring the whole investigation is the failure mode, and padding the ledger is
refused: not just identical claims, but any claim in the same incident sharing at least
70% of the other's content words. The refusal names the shared terms, so if two claims
really are different mechanisms, say what distinguishes them.

A hypothesis you cannot state a prediction for is a vibe. Discard or sharpen it.

## A3. Test them one variable at a time

```bash
$CONVERGENCE refute-hypothesis "$LEDGER" --id H1 --probe "strace -e openat" --observed "still red: the CR survives"
$CONVERGENCE survive-hypothesis "$LEDGER" --id H2 --probe "open(path,'rb')" --observed "green"
$CONVERGENCE merge-hypotheses "$LEDGER" --into H2 --from H3 --note "both are newline stripping"
```

Every closure needs a real probe and a real observation. "I think it's not that" is
refused. If two lanes turn out to be the same mechanism, merge them explicitly — that
convergence is a finding, and it shrinks the space.

## A4. Only now, the fix

```bash
$CONVERGENCE begin-fix "$LEDGER" --incident INC-1 --note "open in binary mode and strip the BOM"
```

Refused while any hypothesis on the incident is `open`, or with fewer rivals settled than
the declared search width. A fix chosen before its rivals are refuted is a guess, and the
gate is not interested in your confidence.

### Small, fully reproduced defects

Three rivals is theatre for a one-line null check whose mechanism is already pinned by a
red loop. That is not an excuse to skip the ledger — it is a different, *declared* search
width, and it is priced:

```bash
$CONVERGENCE begin-fix "$LEDGER" --incident INC-1 --note "add the null guard" --search narrow
```

`narrow` requires one settled explanation. It is recorded on the fix, shown in the report,
and it costs the run its clean `COMPLETE`: the run closes as `COMPLETE WITH RISKS`, naming
that the fix was chosen without searching over rivals. Use it when the defect is fully
reproduced and you can say in one sentence why the mechanism is not in doubt. Use `full`
the moment that sentence would be a guess.

The minimal honest sequence for a small change is therefore four commands: `init`,
`declare-loop`, `run-loop`, then `open-hypothesis` → `refute`/`survive` → `begin-fix
--search narrow` → `record-evidence` → `close`. Never fewer: a run with no ledger has no
machine, and no machine means no gate.

---

# Entry B — a genuine fork appears mid-execution

Trigger: an observed fact invalidates an assumption the plan depended on. Not "this is
hard" — a real choice between real alternatives, discovered after the plan was written.

**Freeze the path, never the run.** Other independent work continues.

## B1. Make it a closed question

```bash
$CONVERGENCE open-fork "$LEDGER" --id F1 \
  --question "which queue backend survives the managed-Redis retirement?" \
  --trigger "the broker returned 503 on the first publish at 14:02" \
  --assumed redis \
  --option '{"id":"sqs","cost":"1d","risk":"low","reversibility":"reversible"}' \
  --option '{"id":"nats","cost":"3d","risk":"med","reversibility":"reversible"}' \
  --option '{"id":"self-hosted","cost":"5d","risk":"high","reversibility":"irreversible"}'
```

`--assumed` is what the plan currently believes. If only one option survives, you owe
`--invalidation-rationale` naming what you ruled out and why — silence there is
indistinguishable from not having looked.

## B2. Dispute it, in a context that is not yours

This is the step the rest of the library has no equivalent for. Spawn a separate agent or
subagent. Give it the intent, the criteria, the options and the evidence. **Do not give it
your recommendation.** Its job is to argue *against* the favoured option, in its strongest
form.

```bash
$CONVERGENCE dispute "$LEDGER" --fork F1 \
  --challenger reviewer-subagent-2 --author main \
  --steelman "NATS handles the 40k msg/s fan-out natively; SQS would push our poll interval to 2s and blow the latency budget in AC-2." \
  --verdict "SQS is viable if the poll interval budget is renegotiated"
```

The ledger refuses a dispute whose challenger is the author. Re-reading your own summary
is not a dispute.

**Validity test:** if the challenger cannot produce a serious argument against the option
you favour, that option is not demonstrated. Switch, or go find a better challenger. A
dispute that rubber-stamps your conclusion is worse than none, because it launders a
preference into a verdict.

## B3. Decide inside your authority, or hand it to a human

```bash
$CONVERGENCE decide "$LEDGER" --fork F1 --chosen sqs --by agent \
  --invalidated "US-004 redis adapter" "AC-2 latency budget" "docs/runbook.md §3"
```

The machine refuses `--by agent` when the decision is **irreversible** or touches a human
anchor: `security`, `privacy`, `data_integrity`, `public_contract`, `money`, `legal`,
`irreversible_action`. Those require:

```bash
$CONVERGENCE decide "$LEDGER" --fork F1 --chosen rotate-tokens --by human --human-ack \
  --anchor security --invalidated "log scrubber" "AC-4"
```

Present the human one page: three options, the tradeoff, your recommendation, and the cost
of not deciding. Then stop that path and keep the run.

If your decision departs from `--assumed`, `--invalidated` is mandatory. Name the code,
tests, acceptance criteria and PRD stories the dead assumption made stale. Silence here is
how work ends up shipped on top of an assumption nobody noticed died.

### When the dispute wins against you

If the challenger's objection did not hold, say so out loud and price it:

```bash
$CONVERGENCE decide "$LEDGER" --fork F1 --chosen sqs --by agent \
  --invalidated "US-004" \
  --challenger-overruled --override-reason "the counter-example did not reproduce at 16/4"
```

`--challenger-overruled` without a reason is refused. Overriding a dispute is allowed;
overriding it silently is the thing this ledger exists to prevent. It also costs the run
its clean `COMPLETE`.

The converse matters too: a dispute the challenger ran and the evidence then settled does
**not** downgrade the verdict. The status tracks the quality of the decision, not how many
forks the run happened to hit. An agent may legitimately reach `COMPLETE`.

## B4. Resume

Everything above is append-only. After a crash, a compaction, or a session change, the
ledger replays and `next-action` picks up exactly where it stopped. Nothing is lost,
including the hypotheses you already refuted.

---

# Entry C — it stops converging

Trigger: the same failure signature repeats, or two attempts die on the same underlying
approach. **This replaces every iteration cap in every other skill.** Classify it; do not
count it.

```bash
$CONVERGENCE classify "$LEDGER" --signature "flake-on-ci" --kind loop_defective
```

| kind | means | what the ledger then demands | cap? |
|---|---|---|---|
| `loop_defective` | the signal is not tight, not deterministic, or not the symptom | rebuild the feedback loop first — A1 | **none** |
| `hypothesis_unresolved` | a hypothesis is still `open` | settle it — A3. You may not start a fresh line of attack | **none** |
| `information_missing` | a fact exists only outside your reach | see below | the only real stop |

`loop_defective` and `hypothesis_unresolved` are not failures and never end a run. They
are directives: the ledger refuses everything else until you satisfy them. This is the
whole meaning of "no limitations" — the limit belongs where information is missing, not
where persistence ran out.

**Be precise about the signature.** A signature is a *cause-shaped* fingerprint
(`OOM at 3.2 GB on the transform step`), not an error string (`exit code 1`). Classifying
`exit code 1` is how a run dies for the wrong reason.

## Parking one path — never stopping the run

A block is not an ending. It parks **one path**, names the fact that would release it, and
the run keeps going everywhere else. The block has to be earned: the machine refuses one
that does not name the routes you already took.

```bash
$CONVERGENCE classify "$LEDGER" --signature "needs-vault-token" --kind information_missing \
  --missing-fact "a Vault token with read access to the secrets path" \
  --smallest-input "the operator running `vault login`, or a token pasted into the session" \
  --tried "checked the environment, no VAULT_TOKEN" "asked the secrets endpoint, 403" \
  --park "F-deploy"
```

`--tried` needs at least two **distinct** routes. Repeating one route does not count, and
that is the whole guard: "never stop" must not quietly become "never try", or the block
becomes a give-up button with extra steps.

`--park` names the path being parked — a fork id, an incident id, any tag. It shows up in
`next-action` under `parked_paths` alongside the real next move, never instead of it.

When the input arrives, record what released you; the disclosure outlives the block:

```bash
$CONVERGENCE resolve-block "$LEDGER" --signature "needs-vault-token" \
  --provided "operator ran vault login; token now in the environment"
```

An environment or dependency that will not come up is **not** a stop. A package registry
that is unreachable is a parked path with a named fact. A toolchain that will not build
after three genuine attempts is `loop_defective` — the loop is bad, so rebuild the loop.
Nothing but an unobtainable fact parks anything, and a park is not a wall.

---

# Evidence

Three tiers, and no claim is a claim until a **named, re-runnable command** produced
output you actually looked at.

```bash
$CONVERGENCE record-evidence "$LEDGER" --criterion "handles CRLF" \
  --command "pytest -k crlf" --tier executed_here --expected-source literal
```

- `executed_here` — the command ran in this session and you read its output.
- `reproduced_independently` — a **different actor** re-ran the same command. Requires
  `--recorder` and a *different* `--reproduced-by`. Equality is refused: re-reading your
  own summary is not reproduction.
- `unavailable` — you could not get it. Requires `--reason`, and it is **never** a pass.
  It closes the run as `COMPLETE WITH RISKS` and appears in the report under
  `Unavailable`. Do not reach for it to get to a green result.

`--expected-source` is the anti-tautology control. The expectation must come from
somewhere other than the implementation:

| value | use it when the expected value is |
|---|---|
| `literal` | a fixed value you can write down: exit code `1` on denial, exactly 5 rows, an exact string |
| `spec` | an acceptance criterion in the spec or a PRD story |
| `worked_example` | a hand-worked case, e.g. a window of 3 with limit 2 admits 2 then denies |
| `independent_oracle` | computed by something that is not the code under test: a second implementation, a database constraint, a third-party client |
| `code` | **refused** — recomputing the expected value the way the code does makes the check pass by construction and lets it never disagree with the bug |

When two labels both fit, ask what would still hold if the code were wrong. If the answer
is nothing, it is `code`.

**Evidence is not documentation.** A diff, a screenshot, a summary table, or a reviewer's
opinion is not a tier. Name the command.

---

# Close

```bash
$CONVERGENCE close "$LEDGER"     # writes report.md, prints it, exits 0 only on REACHED
```

The magnet has two poles and one question: **was the outcome reached?** There is no third
answer.

- **`REACHED`** — every criterion verified by executed, independently-checkable evidence;
  no untested hypothesis; no undecided fork; no parked path; no unrebuilt defective loop;
  no blocking risk.
- **`NOT REACHED`** — anything of the above stands. The run is *still going*, not finished.
  `next-action` tells you precisely what it is waiting on.

`close` exits `0` **only** on `REACHED`. There is no lesser kind of success: a criterion
you could not verify is unmet intent, not decoration, and it blocks.

Separately, the report carries **disclosures** that do not gate — how the result was
reached rather than whether it was: dissent overruled, a `--search narrow` fix, an
autonomously authored decision, a run that had to be restarted from a non-convergence
diagnosis. Those are method notes about a result you did reach. Gating on them made a clean
verdict unreachable for any run that met a fork, which is backwards; an agent can and
should be able to reach `REACHED`.

You may not announce a status the machine did not print, and you may not describe a
`NOT REACHED` run as finished because the parts you did finish are green.

---

## What the machine refuses

Read this as a list of arguments you cannot win. Each is a test.

- a hypothesis with no declared-and-run red-capable loop for its incident
- a hypothesis whose claim restates or paraphrases one already recorded on that incident
- a refutation or a survival with no probe and no observation
- a fix while any hypothesis on the incident is `open`, or with fewer rivals settled than
  the declared `--search` width
- any action other than rebuilding the loop, after `loop_defective`
- any action other than settling a hypothesis, after `hypothesis_unresolved`
- a new line of attack while a hypothesis is `open`
- `information_missing` without a named fact and a named smallest input
- a fork with a single option and no invalidation rationale
- a decision with no dispute
- a dispute whose challenger is the author
- an agent decision that is irreversible, or touches a human anchor
- a human decision without acknowledgement
- a decision that departs from the plan but invalidates nothing
- `--challenger-overruled` without a reason
- an unknown `--search` width
- a malformed fork option that is not a dict
- an expectation computed from the code
- a reproduction credited to the same actor who recorded it
- `unavailable` without a reason
- a ledger that was already initialised
- a close while a `loop_defective` directive is still outstanding
- an `information_missing` that does not name the fact, the smallest input, and at least
  two distinct routes already tried
- a criterion revision naming an edge that does not exist
- a criterion revision decided without a dispute, or without naming what it invalidates
- an edge moved to something already on the list

## Anti-patterns

- Editing a test, a threshold or a criterion to turn the status green. The change may be
  legitimate, but it must be visible, independently verified, and justified out loud.
- Treating `unavailable` as a pass. It exists to be honest about a gap, not to close it.
- Padding the hypothesis ledger with restatements to reach three.
- `close`-ing to find out what was missing, then fixing the ledger instead of the work.
- Treating `BLOCKED` as a verdict on the work. It is a verdict on the information.
- Starting over. The ledger holds what you already refuted; resume from it.

## Composing with the rest of the library

This skill does not replace them, and it is not a substitute for the planning front end.
Use it as the engine underneath whatever is already running:

| stage | pair it with |
|---|---|
| intent | `deep-interview` for the ambiguity gate, `grilling` for a design tree |
| plan | `ralplan` for Planner/Architect/Critic consensus; `codebase-design` DESIGN-IT-TWICE for interfaces |
| execution | `ralph` for story tracking; **Entry B here fires first** |
| diagnosis | `diagnosing-bugs` for feedback-loop construction; `deep-dive`/`trace` for parallel lanes |
| verification | `graph-of-loops` review-only for the challenger pass; `live-debug-logs` for the evidence format |
| final | `close` here emits the report `graph-of-loops` asks for |

`graph-of-loops` remains the authority on the final status vocabulary and on materiality.
This skill makes that vocabulary *computable* instead of a thing you promise yourself.
