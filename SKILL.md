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

### Several agents, one ledger

This is supported and intended: a team of agents working the same run holds an advisory
lock across the whole read-validate-write section. Every mutator re-reads the ledger
under that lock before it checks anything, so a gate you pass is a gate that was true at
the instant your write landed.

What that means in practice:

- Two agents racing to record the same hypothesis: **one wins**, the other is refused.
- Two agents racing to close the same fork: **one decision lands**, once. There is no
  silent overwrite and no lost decision.
- Concurrent distinct work is never dropped — every accepted event survives to replay.

So you do not need to partition the ledger between agents. You do need to expect a
`BLOCKED` that is really *"someone else got there first"*; re-read `status` and move on
rather than retrying the identical call.

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

To check the shape before creating anything, run `init --intake`: it validates the
magnet and prints the verdict as JSON **without writing a ledger**. An intent with no
criteria, or with criteria too short to name a check, is refused at the door:

```bash
$CONVERGENCE init "$LEDGER" --intent "..." --criterion "..." --intake
# → {"shape_ok": true, ...}  — nothing written; drop --intake to create
```

Once the magnet is right, seal it:

```bash
$CONVERGENCE freeze-intent "$LEDGER"
```

After the freeze, the only way to move the right edge is a disputed fork (below).
Freezing is optional but cheap; a frozen run's report states when the intent was sealed.

`record-evidence --criterion` must match the init-time text **exactly**. A near-miss used
to open a second, uncancellable criterion and strand the run at `NOT REACHED` for reasons
nobody could see; the machine now refuses it and tells you the closest real one. Copy the
text from `status`, do not retype it from memory.

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
and it is **disclosed**: the run can still reach `REACHED`, but the report names it under
"How the outcome was reached", saying the fix was chosen without searching over rivals.
That disclosure outlives the run; the *verdict* is unaffected. Use `narrow` when the
defect is fully reproduced and you can say in one sentence why the mechanism is not in
doubt. Use `full` the moment that sentence would be a guess.

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

### Not every fork deserves an expensive challenger

Every fork is disputed — that gate does not move. But the *weight* of the challenger should
follow what the decision binds, and the distinction is not subtle once you name it:

| the fork is | challenger |
|---|---|
| a design that **binds a human, a public interface, or money** | a real adversarial subagent. Spend the minutes. |
| a fact you simply **misremembered** ("the count was 3, it is 4 now") | a separate context that re-reads the artifact and says so. Seconds. |
| **your own criterion** turning out to be wrong | still a real dispute — this moves the right edge — but the challenger attacks the *new* wording, not the architecture. |
| **the machine** behaving differently than the run assumed | a re-read of `status`. This is diagnosis, not a decision. |

Paying a heavyweight challenger for the bottom three is how a run spends most of its
ledger budget on decisions nobody was bound by. It is also how you end up treating the
dispute as a formality, which is the thing this entry exists to prevent.

Record `record-risk` too — it is load-bearing and easy to miss:

```bash
$CONVERGENCE record-risk "$LEDGER" --text "the SQS poll interval is now 2s; not signed off yet"
$CONVERGENCE record-risk "$LEDGER" --text "the public schema changed" --blocking
```

It is the only way to disclose a residual that no acceptance criterion captures. Without
`--blocking` it is a disclosure; with it, it stands between the run and `REACHED`.

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
overriding it silently is the thing this ledger exists to prevent. It is **disclosed**:
the run can still reach `REACHED`, but the report names the fork, your reason, and the
fact that the challenge was overruled.

The converse matters too: a dispute the challenger ran and the evidence then settled
discloses nothing. **Method notes never gate the verdict.** If you find yourself padding
the hypothesis ledger to protect a status that was never at risk, stop — you are working
from a stale copy of this document.

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
  It makes the run `NOT REACHED` and appears in the report under both `Unavailable` and
  "What still stands between the run and the outcome". Do not reach for it to get to a
  green result; reach for whatever would let you actually verify the criterion.

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
- Trusting a tool's success message without re-observing the effect: "stopped"
  with the port still bound, "deleted" with the file still there. Verify, then claim.
- Starting over. The ledger holds what you already refuted; resume from it.

# Infrastructure — turning parked paths into executable work

The ledger knows how to park a path when a fact is missing. These three commands are
the mechanical arms that let it *resolve* the missing fact without waiting for a human
to paste values, copy `node_modules`, or provision a database by hand. Each one records
its action in the ledger so the audit trail survives.

## Provision an external dependency on-demand

When a criterion requires a service that does not yet exist (a database, a Vercel
project, an OAuth app), provision it and record the result:

```bash
$CONVERGENCE provision-env "$LEDGER" \
  --spec neon-db \
  --output-var DATABASE_URL \
  --command "neonctl project create --name parity-test --json"
```

This turns an `information_missing` block into a resolved fact. The ledger now knows
the value exists and how it was obtained, so criteria that depend on it can proceed
to evidence collection. Pair with `resolve-block` to release the parked path:

```bash
$CONVERGENCE resolve-block "$LEDGER" \
  --signature needs-database \
  --provided "provision-env created neon-db; DATABASE_URL now in environment"
```

## Inject build context into isolated test directories

Generated code tested in `/tmp` produces false negatives because dependencies live
only in the host project. Inject the necessary artifacts before running typechecks
or builds:

```bash
$CONVERGENCE inject-build-context "$LEDGER" \
  --target /tmp/f1-check \
  --source apps/web \
  --artifacts node_modules tsconfig.json src/types \
  --command "cp -r apps/web/node_modules /tmp/f1-check/ && cp apps/web/tsconfig.json /tmp/f1-check/"
```

The injection is recorded so the evidence tier can reference it and the report can
disclose it. Without this gate, F1/F2 criteria fail with TS7026/TS2307 errors that
reflect missing context, not bad output.

## Store secrets for secure criterion verification

API keys and tokens must never touch the ledger or the transcript. Store them once
and reference by name in evidence commands:

```bash
$CONVERGENCE store-secret "$LEDGER" \
  --name LLM_API_KEY \
  --command "echo \$LLM_API_KEY"
```

Evidence commands then retrieve the secret at runtime without exposing it:

```bash
$CONVERGENCE record-evidence "$LEDGER" \
  --criterion "SSE streaming works" \
  --command "curl -H 'Authorization: Bearer $(echo $LLM_API_KEY)' http://localhost:3000/api/chat" \
  --tier executed_here \
  --expected-source literal
```

This eliminates the redaction round-trip that previously blocked autonomous
verification. The secret name is recorded; the value is not.

## Boot a local runtime instead of parking it

The most common fake block in generated-project runs: "the sandbox is not startable".
A server the criteria need is not missing information — it is work. `boot-runtime`
does the work and records it:

```bash
$CONVERGENCE boot-runtime "$LEDGER" \
  --name e2e-sandbox \
  --command "npx next dev -p {port}" \
  --cwd .flow-sandbox/run-convergence-e2e \
  --timeout 60 \
  --resolve-signature sandbox-e2e-unavailable \
  --criterion "C2: Signup→login→logout e2e; sessione cookie sopravvive al reload"
```

It picks a free port (substituting `{port}` into the command; pass `--port` to pin
one), polls until something accepts connections, then records the runtime, releases
the parked block named by `--resolve-signature`, and records `executed_here` listener
evidence for `--criterion`. The server keeps running so the e2e criteria can be
verified against it. Diagnostics land in `runtimes/<name>.log` inside the ledger;
if the process dies or times out the gate refuses with the log tail.

**"Port already in use" is never a decision point.** Unpinned, `boot-runtime` asks
the kernel for a free port, so the case cannot arise. If you pin a port and it is
occupied — an orphaned dev server from a previous run, a neighbour's sandbox — that
is local work, not missing information: find the listener
(`lsof -nP -iTCP:<port> -sTCP:LISTEN`), stop it, boot again. The fact you would be
parking on does not exist outside your reach; it is sitting in your own process
table. Classifying that as `information_missing` is the anti-park refusal doing its
job.

Retire it when the run no longer needs it:

```bash
$CONVERGENCE stop-runtime "$LEDGER" --name e2e-sandbox
```

**Stop is honest or it is nothing.** A dev server may respawn its actual listener
in a fresh process group, so killing the recorded pid can leave the port bound
while the command reports success — and a lying stop poisons every later boot on
that port. `stop-runtime` therefore verifies the *effect*, not the intention: it
re-checks the port with `lsof`, reaps any respawned listener (SIGTERM then
SIGKILL, by group or by pid), and **Blocks** naming the surviving pids if it
cannot free the port. The general rule generalises past this command: a success
report is a claim until its effect is re-observed. When a tool says "stopped",
re-check the port before you believe it.

`validate-infra` checks booted runtimes: a runtime that should be listening but is
not is a missing prerequisite; a stopped one is not.

## Work is not a fact: the anti-park guard

Before classifying `information_missing`, read your own smallest input. If it names
imperative verbs plus local tools or project files — "fix sandbox-server.ts port
binding", "sync generated files and start next dev on a free port" — that is work
this session can do, and the ledger **refuses the park**. Do the work
(`boot-runtime`, `provision-env`, `inject-build-context`), then `resolve-block`.

A legitimate park names an actor outside the session: "the operator running
`vault login`", "a billing account with remaining quota", "a permission we do not
hold". When such an actor is genuinely the only route, say so with `--only-route`
and the block is disclosed in the report instead of refused.

## Preflight: validate infrastructure before starting a run

Before resuming or starting a run, verify that all infrastructure prerequisites are
available. This prevents wasted cycles on runs that will immediately block:

```bash
$CONVERGENCE validate-infra "$LEDGER"
```

The command checks four categories:

| check | what it verifies |
|---|---|
| `provision` | environment variables from `provision-env` are set |
| `injection` | artifacts from `inject-build-context` exist at target path |
| `secret` | retrieval commands from `store-secret` execute successfully |
| `runtime` | runtimes from `boot-runtime` still accept connections |
| `tool` | external binaries referenced in loops/evidence are in PATH |

Exit code `0` means all checks passed; exit code `3` means at least one prerequisite
is missing and the run would block. The output is JSON with per-item details so agents
can programmatically decide whether to proceed or park.

### Auto-fix mode

Pass `--fix` to attempt automatic restoration of missing prerequisites by re-running
the recorded provision and injection commands:

```bash
$CONVERGENCE validate-infra "$LEDGER" --fix
```

Fixes are best-effort: provisions are re-executed and their output vars exported into
the current environment; injections are re-run and artifacts re-checked. Secrets and
missing tools cannot be auto-fixed and will still fail. The check detail reports
`restored via --fix` for each successfully recovered item.

### Automatic gate on `next-action`

When infrastructure has been registered (via `provision-env`, `inject-build-context`,
or `store-secret`), `next-action` automatically runs a preflight check before
returning the next step. If any prerequisite is missing, it returns
`validate-infra` as the required action instead of the normal next step. This
prevents wasted cycles on runs that would immediately block:

```bash
$CONVERGENCE next-action "$LEDGER"
# → {"required": "validate-infra", "because": "infrastructure prerequisites are not ready: ..."}
```

Run `validate-infra --fix` to restore what can be restored, then call `next-action`
again to resume normal operation.

---

# Spec integration — tickets in, evidence out

The upstream planning skills (`/to-spec` → `/to-tickets`) end in a directory of ticket
files; `/implement-spec` ends in merged branches. Neither end is this ledger's notion of
done. The bridge is `import-tickets`:

```bash
$CONVERGENCE import-tickets "$LEDGER" --tickets .scratch/<feature>/issues/
```

Both to-tickets shapes are read: the local template (`# <id>: <title>` with inline
`**Field:**` lines) and the issue template used on real trackers (`## What to build` /
`## Acceptance criteria` / `## Blocked by` sections). Ticket ids stay native — `01`,
`42`, `ENG-123` — taken from the heading or the filename, and `Blocked by` accepts
numbers (padding-insensitive: `1` finds `01`), native keys, full titles and tracker
URLs, so a GitHub/Linear export imports unchanged.

Every ticket's `- [ ]` acceptance criteria become ledger criteria, and the ticket
boundary is kept as provenance. From there the two notions of completion diverge
loudly:

- **merged** is what the tracker says when the branch lands;
- **earned** is what the report says when every criterion the ticket introduced carries
  evidence.

The report's `## Ticket provenance` section names which tickets earned their close and
which were merely merged — an unproven criterion listed under its ticket. Since imported
criteria sit in the flat list, `close` refuses while any of them is unverified: a ticket
merged upstream without evidence **cannot** silently count as done here.

Intake rules, enforced on import and again on replay:

- a ticket with no acceptance criterion is refused — nobody can verify it;
- a criterion shorter than 4 characters is refused — a check nobody can name is not a check;
- the same criterion in two tickets is refused — provenance would be a guess;
- a `Blocked by` edge to an unknown ticket is refused — a dangling edge means the export
  was incomplete.

Cite ticket criteria verbatim in `record-evidence --criterion`; they are ordinary
criteria from import onward.

To drive the whole loop automatically — import, work the frontier test-first, record
evidence per criterion, close — use the `spec-implement` skill. It exists because
`/implement-spec` (like its siblings) is human-triggered only (`disable-model-invocation:
true`); `spec-implement` is the model-invocable counterpart, same task graph, with the
ledger deciding what "done" means.

---

## Routing for upper pipelines

A driving loop (autopilot, orchestrator, team runtime) should not parse prose to decide
what to do next. `route` returns one routable word plus a fallback:

```bash
$CONVERGENCE route "$LEDGER"
# → {"lane": "investigate", "because": "...", "action": {...},
#    "fallback": {"lane": "verify", "because": "a parked gap is work, not a stop: ..."}}
```

Lanes: `investigate` (drive the hypothesis loop), `decide` (adjudicate forks), `unblock`
(source missing facts / rebuild infrastructure), `verify` (collect evidence), `human`
(escalate a decision outside delegated authority — e.g. a fork with no reversible
option), `close`. The `fallback` names where to work when the primary lane is blocked
from outside the session: an external fact, a human-only decision. A context gap is
work, not a stop — the run is always routed somewhere it can move.

---

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
| spec | `import-tickets` maps `/to-tickets` output onto criteria; `## Ticket provenance` separates merged from earned |

The report also carries `## Auto-critique`: the run names its own weaknesses first —
searches that eliminated nothing, evidence without an `--output-ref`, verdict-less
disputes, overruled dissent, narrow searches, intent drift — each row pointing at a
record. A report that cannot say what is weak about itself is advocacy, not evidence.

`graph-of-loops` remains the authority on the final status vocabulary and on materiality.
This skill makes that vocabulary *computable* instead of a thing you promise yourself.
