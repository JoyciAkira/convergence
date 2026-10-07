---
name: spec-implement
description: Drive any request to a proven close on the convergence ledger — a /to-spec or /to-tickets breakdown, a tracker export, or a raw prompt of any size or complexity. Imports tickets or decomposes the prompt into acceptance criteria, implements the frontier test-first, records evidence per criterion, and lets `close` decide. Use for ANY implementation request in any phrasing or language: "implement the spec", "work the tickets", "build this properly", "porta a termine questo progetto", "fai il progetto", "senza scorciatoie", or a complex multi-goal brief — whenever the user wants something built and carried to a proven finish.
---

# Spec Implement

Drive a ticket breakdown to a **proven** close. This is the model-invocable
counterpart of `implement-spec` (which is human-triggered only); the same task
graph, with one difference: a merged ticket is not a done ticket. The
convergence ledger decides when the run may call itself finished.

## Inputs

- A spec (optional, for context pointers only — never duplicated here).
- A ticket directory: `.scratch/<feature-slug>/issues/*.md` from `/to-tickets`,
  or an export from a real tracker (native ids like `ENG-123`, issue-template
  sections — `import-tickets` accepts both).
- Or a raw prompt, if there is no breakdown yet. `init --prompt-file <file|->`
  decomposes it — intent, acceptance criteria with provenance, candidate
  hypotheses, exclusions — so even a very complex multi-goal brief enters the
  ledger without a planning phase. Use `--intake` first to audit the shape.
- A ledger path. If none exists, create one: `init` with the spec's outcome as
  `--intent` and its observable claims as `--criterion` (or `--intent-file` /
  `--criteria-file` for large text), then `freeze-intent`.

## Steps

1. **Import or decompose.** Tickets:
   `convergence.py import-tickets <ledger> --tickets <dir>`. Every ticket's
   `- [ ]` acceptance criteria become ledger criteria; the ticket boundary is
   kept as provenance. Raw prompt: `convergence.py init <ledger>
   --prompt-file <file>`. Refusals at either door (criterionless tickets,
   duplicated criteria, dangling `Blocked by` edges, a prompt that states only
   tasks) are intake errors — fix the input, do not work around them.

2. **Route.** `convergence.py route <ledger>` before every grab. It returns the
   lane (`investigate` / `decide` / `unblock` / `verify` / `human` / `close`)
   plus a fallback for when the primary lane is blocked from outside the
   session. A parked gap is work, not a stop: follow the fallback and keep
   verifying what is independent of it.

3. **Work the frontier.** A ticket is on the frontier when its `Blocked by`
   tickets are all earned. Implement frontier tickets in parallel where they
   do not share files, each test-first (red, then green). One ticket, one
   focused change — wide refactors follow expand–contract per the
   `to-tickets` rules.

4. **Record evidence per criterion, not per ticket.** For every acceptance
   criterion the ticket introduced:
   `record-evidence --criterion "<exact text>" --command <the check> --tier
   executed_here --expected-source literal --output-ref <artifact>`. The text
   must match the imported criterion exactly — copy it from `status`. A
   criterion whose check cannot run here is recorded `unavailable` with a
   `--reason`, never silently skipped.

5. **Earn before claiming.** The ticket is *earned* when every criterion it
   introduced carries evidence — visible in the report's `## Ticket provenance`
   section. Merging the branch is upstream's notion of done; earning is this
   ledger's. Do not report a ticket complete on merge alone.

6. **Close.** `close` derives the outcome and writes the report. It exits
   non-zero while any criterion — imported or init-time — is unproven. That is
   the contract, not an obstacle: the `## Ticket provenance` and
   `## Auto-critique` sections name exactly what is missing and what is weak
   about what exists.

## Rules

- The ledger's gates apply unchanged: a red-capable loop before hypotheses,
  rivals refuted before a fix, disputed forks before decisions, irreversible
  decisions escalated to a human (`route` names the `human` lane).
- The right edge moves only via a disputed, decided fork (`--criterion-from` /
  `--criterion-to`); importing tickets does not move it silently.
- Communicate through pointers (spec path, ticket paths, `status` output) —
  never duplicate ticket or criterion text into prose that can go stale.
