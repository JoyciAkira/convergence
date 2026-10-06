---
name: spec-implement
description: Drive a spec's tickets to a proven close on the convergence ledger: import tickets as acceptance criteria, implement the frontier test-first, record evidence per criterion, and let `close` decide. Use when the user wants to execute a /to-spec or /to-tickets breakdown, says "implement the spec", "work the tickets", or wants the manual-only implement-spec flow driven automatically.
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
- A ledger path. If none exists, create one: `init` with the spec's outcome as
  `--intent` and its observable claims as `--criterion`, then `freeze-intent`.

## Steps

1. **Import.** `convergence.py import-tickets <ledger> --tickets <dir>`. Every
   ticket's `- [ ]` acceptance criteria become ledger criteria; the ticket
   boundary is kept as provenance. Refusals here (criterionless tickets,
   duplicated criteria, dangling `Blocked by` edges) are intake errors — fix
   the tickets, do not work around them.

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
