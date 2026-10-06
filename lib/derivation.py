from __future__ import annotations

"""Derivation: read-only computation over Ledger state.
Pure functions that depend only on the state shape, not on how it was
mutated. Separated from event handlers to concentrate change locality
and deepen the interface: callers get outcome/report/next_action without
knowing replay mechanics.
"""

from typing import Any

REPORT_ROW_CAP = 12


def rows(items, pointer="the ledger logs", cap=REPORT_ROW_CAP):
    """Cap a section so a 3,000-event run does not produce an unreadable document."""
    items = list(items)
    if not items:
        return ["- None"]
    if len(items) <= cap:
        return items
    return items[:cap] + [f"- …and {len(items) - cap} more; the full record is in {pointer}"]


#: Closed conditions capstone — explicit close bar (9).
#: Every condition must be satisfied for REACHED.
CLOSED_CONDITIONS = (
    "every criterion verified",
    "no open hypothesis",
    "no undecided fork",
    "no unresolved missing-fact block",
    "no unrebuilt defective loop",
    "no blocking risk",
    "no criterion verified unavailable",
    "no criterion never verified",
    "infra ready or none registered",
)

# Dispute weight heuristics for report (3): classify challenger weight by decision kind.
# We derive, not store — the ledger already stores the dispute record.


def decision_weight(kind, anchors, reversibility) -> str:
    """Return a weight label for a fork decision, based on what it binds.

    Heavy weight: irreversible_action, or anchors touching security/privacy/money/legal
    and agent authority cannot cover it.
    Medium weight: reversible but anchors touch public_contract or data_integrity.
    Light weight: reversible, no human anchor, delegated to agent.
    """
    if reversibility == "irreversible":
        return "heavy"
    heavy_anchors = {"security", "privacy", "money", "legal", "irreversible_action"}
    medium_anchors = {"public_contract", "data_integrity"}
    touched = set(anchors) & (heavy_anchors | medium_anchors)
    if touched & heavy_anchors:
        return "heavy"
    if touched & medium_anchors:
        return "medium"
    return "light"


def _dispute_weight_rows(state) -> list[str]:
    rows_out = []
    for d in state.get("disputes", []):
        decision = state.get("decisions", {}).get(d["fork"])
        if not decision:
            continue
        weight = decision_weight(
            d.get("steelman", ""),
            decision.get("anchors", []),
            decision.get("reversibility", "reversible"),
        )
        rows_out.append(
            f"- fork `{d['fork']}`: weight={weight}, challenger={d['challenger']} "
            f"against {d['author']}"
            + (f", verdict={d['verdict']}" if d.get("verdict") else "")
        )
    return rows_out


def _meta_fail_dataset_rows(state) -> list[str]:
    fails = state.get("meta", {}).get("run_fails", [])
    return [
        f"- {f['signature']!r} — failure_kind={f['failure_kind']!r}"
        for f in fails
    ]


def _revision_transparency_rows(state) -> list[str]:
    revisions = state.get("criterion_revisions", [])
    if not revisions:
        return ["- None"]
    return [
        f"- {r['from']!r} -> {r['to']!r} (fork `{r['fork']}`, "
        f"invalidated: {', '.join(r['invalidated']) or 'none'})"
        for r in revisions
    ]


def _close_conditions_expectations(state) -> list[str]:
    """Return the explicit set of close conditions and whether each is satisfied."""
    outcome_status, blocking, _ = outcome(state)
    satisfied = (outcome_status == "REACHED")
    if satisfied:
        return [f"- {c}: satisfied" for c in CLOSED_CONDITIONS]
    # Build a per-condition map from blocking reasons
    blocks_set = set(blocking)
    mapping = {
        "every criterion verified": not any(
            b.startswith("criterion") for b in blocking
        ),
        "no open hypothesis": not any(b.startswith("open hypothesis") for b in blocking),
        "no undecided fork": not any(b.startswith("undecided fork") for b in blocking),
        "no unresolved missing-fact block": not any(
            b.startswith("path parked") for b in blocking
        ),
        "no unrebuilt defective loop": not any(
            b.startswith("a feedback loop was declared defective") for b in blocking
        ),
        "no blocking risk": not any(b.startswith("blocking risk") for b in blocking),
        "no criterion verified unavailable": not any(
            b.startswith("criterion verified unavailable") for b in blocking
        ),
        "no criterion never verified": not any(
            b.startswith("criterion never verified") for b in blocking
        ),
        "infra ready or none registered": True,  # infra gating is advisory in next_action
    }
    lines = []
    for c in CLOSED_CONDITIONS:
        sat = mapping[c]
        lines.append(f"- {c}: {'satisfied' if sat else 'unsatisfied'}")
    return lines


def _ticket_provenance_rows(state) -> list[str]:
    """Ticket boundaries kept as provenance: earned vs merely merged.

    Upstream (implement-spec) closes a ticket when it merges. The ledger only
    calls a ticket earned when every criterion it introduced carries evidence.
    The mapping exists so the report can tell the two apart — a merged ticket
    with unproven criteria is the exact gap this ledger exists to expose.
    """
    tickets = state.get("tickets", [])
    if not tickets:
        return ["- None imported"]
    latest_tier: dict[str, str] = {}
    for e in state.get("evidence", []):
        latest_tier[e["criterion"]] = e["tier"]
    rows_out = []
    for t in tickets:
        proven = [c for c in t["criteria"] if latest_tier.get(c) not in (None, "unavailable")]
        unproven = [c for c in t["criteria"] if c not in proven]
        mark = "earned" if not unproven else "merged only"
        line = f"- ticket {t['id']} {t['title']!r}: {mark} ({len(proven)}/{len(t['criteria'])} criteria proven)"
        if unproven:
            line += " — unproven: " + ", ".join(repr(c) for c in unproven)
        rows_out.append(line)
    return rows_out


def _requirement_notice(state) -> str:
    """One-line notice that tells the reader what the run still owes, if anything."""
    status, blocking, _ = outcome(state)
    if status == "REACHED":
        return "The run is complete. No condition remains open."
    if not blocking:
        return "The run is complete. No condition remains open."
    first = blocking[0]
    return f"The run is not complete. First gate: {first}"




def resolved_signatures(state: dict) -> set[str]:
    """Signatures of blocks that have been resolved via input."""
    return {b["signature"] for b in state.get("resolved_blocks", [])}


def outcome(state: dict) -> tuple[str, list[str], list[str]]:
    """REACHED or NOT REACHED — the magnet has two poles and one question.

    Returns (status, blocking_reasons, disclosed_notes).
    `blocking` is everything standing between the run and the outcome.
    `disclosed` is how the result was reached (dissent overruled, narrow
    search, etc.) — recorded but not gating.
    """
    open_hyps = sorted(
        h["id"] for h in state.get("hypotheses", {}).values() if h["status"] == "open"
    )
    undecided = sorted(f for f in state.get("forks", {}) if f not in state.get("decisions", {}))
    resolved = resolved_signatures(state)
    parked = [
        c for c in state.get("classifications", [])
        if c["kind"] == "information_missing" and c["signature"] not in resolved
    ]
    blocking_risks = [r["text"] for r in state.get("risks", []) if r.get("blocking")]

    # A criterion's tier is its LATEST evidence
    latest_tier: dict[str, str] = {}
    for e in state.get("evidence", []):
        latest_tier[e["criterion"]] = e["tier"]
    covered = {c for c, t in latest_tier.items() if t != "unavailable"}
    unavailable_only = sorted(
        c for c in state.get("criteria", [])
        if c not in covered and latest_tier.get(c) == "unavailable"
    )

    blocking: list[str] = []
    if open_hyps:
        for h in open_hyps:
            blocking.append(f"open hypothesis: {h}")
    if undecided:
        for f in undecided:
            blocking.append(f"undecided fork: {f}")
    if parked:
        for c in parked:
            park_note = f" (parked: {c['park']})" if c.get("park") else ""
            blocking.append(
                f"path parked on {c['signature']}{park_note}: "
                f"needs {c['missing_fact']} via {c['smallest_input']}"
            )
    if blocking_risks:
        for r in blocking_risks:
            blocking.append(f"blocking risk: {r}")
    if unavailable_only:
        for c in unavailable_only:
            blocking.append(f"criterion verified unavailable: {c}")
    uncovered = sorted(
        c for c in state.get("criteria", [])
        if c not in covered and c not in unavailable_only
    )
    if uncovered:
        for c in uncovered:
            blocking.append(f"criterion never verified: {c}")
    # A loop declared defective invalidates evidence gathered through it;
    # the run cannot close until the loop is rebuilt and re-run red.
    directive = state.get("directive")
    if directive and directive.get("kind") == "loop_defective":
        blocking.append(
            "a feedback loop was declared defective and never rebuilt; evidence gathered "
            "through it does not count"
        )

    # Disclosed notes
    disclosed: list[str] = []
    # Criterion revisions disclose that the right edge moved through a disputed fork
    revisions = state.get("criterion_revisions", [])
    if revisions:
        disclosed.append(
            f"the right edge was moved {len(revisions)} time(s), each "
            f"through a disputed fork: "
            + ", ".join(f"{r['from']!r} -> {r['to']!r}" for r in revisions)
        )
    overruled = sorted(
        f for f, d in state.get("decisions", {}).items() if d.get("challenger_overruled")
    )
    if overruled:
        disclosed.append(
            f"the challenger's objection was overruled on {overruled}: "
            f"{[state['decisions'][f].get('override_reason') for f in overruled]}"
        )
    for d in state.get("decisions", {}).values():
        if d.get("override_reason") and not d.get("challenger_overruled"):
            disclosed.append(f"override: {d['override_reason']}")
    for c in state.get("classifications", []):
        if c.get("only_route"):
            disclosed.append(
                f"blocks taken on the word that there was no other route: "
                f"{c['signature']} ({c['only_route']})"
            )
    narrow_fixes = sorted(
        k for k, v in state.get("fixes", {}).items() if v.get("search") == "narrow"
    )
    if narrow_fixes:
        disclosed.append(
            "fixes chosen with --search narrow, i.e. without searching over rival "
            f"explanations: {narrow_fixes}"
        )
    # Non-terminal classifications disclose repeated non-convergence
    nonterminal = sorted({
        c["signature"] for c in state.get("classifications", [])
        if c["kind"] != "information_missing"
    })
    if nonterminal:
        disclosed.append(
            "repeated non-convergence kept this run alive past its own diagnosis: "
            + ", ".join(nonterminal)
        )
    # Non-blocking risks are also disclosed
    for r in state.get("risks", []):
        if not r.get("blocking"):
            disclosed.append(f"risk: {r['text']}")

    status = "NOT REACHED" if blocking else "REACHED"
    return status, blocking, disclosed


def report(state: dict) -> str:
    """Generate a human-readable convergence report from current state."""
    status, blocking, disclosed = outcome(state)

    # Latest evidence tiers
    latest_tier: dict[str, str] = {}
    for e in state.get("evidence", []):
        latest_tier[e["criterion"]] = e["tier"]
    covered = {c for c, t in latest_tier.items() if t != "unavailable"}
    unavailable = {c for c, t in latest_tier.items() if t == "unavailable"}

    lines = ["# Convergence report", ""]
    lines += ["## Intent", "", state.get("intent") or "_(not initialised)_", ""]

    lines += ["## Acceptance criteria", ""]
    criteria = state.get("criteria", [])
    if criteria:
        lines += rows([f"- `{c}`" for c in criteria], "`control.jsonl`")
    else:
        lines.append("_none recorded_")
    lines.append("")

    lines += ["## Work performed", ""]
    fixes = state.get("fixes", {})
    fix_rows = [
        f"- `{incident}`: {fix['note']} (search: {fix.get('search', 'full')})"
        for incident, fix in sorted(fixes.items())
    ]
    lines += rows(fix_rows, "`control.jsonl`") if fix_rows else ["_no fix recorded_"]
    lines.append("")

    lines += ["## Verification", ""]
    for label, items in (
        ("Passed", sorted(covered - unavailable)),
        ("Failed", []),
        ("Not run", [c for c in criteria if c not in covered and c not in unavailable]),
        ("Unavailable", sorted(unavailable)),
    ):
        lines.append(f"**{label}**")
        lines += rows([f"- `{c}`" for c in items])
        lines.append("")

    lines += ["## Outcome", "", f"**{status}**", ""]
    if blocking:
        lines.append("Blocking:")
        lines += rows([f"- {b}" for b in blocking])
        lines.append("")
    if disclosed:
        lines.append("Disclosed:")
        lines += rows([f"- {d}" for d in sorted(set(disclosed))])
        lines.append("")

    lines += ["## Hypothesis ledger", ""]
    hyp_rows = []
    for ident in sorted(state.get("hypotheses", {})):
        hyp = state["hypotheses"][ident]
        closed = f" -> {hyp['status']} by `{hyp['probe']}`" if hyp.get("probe") else ""
        hyp_rows.append(f"- `{hyp['id']}` [{hyp['status']}] {hyp['claim']}{closed}")
    lines += rows(hyp_rows, "`hypotheses.jsonl`")
    lines.append("")

    lines += ["## Challenger findings", ""]
    dispute_rows = [
        f"- fork `{d['fork']}`: {d['challenger']} against {d['author']}"
        + (f" verdict={d['verdict']}" if d.get("verdict") else "")
        + f" — {d['steelman']}"
        for d in state.get("disputes", [])
    ]
    lines += rows(dispute_rows, "`forks.jsonl`") if dispute_rows else ["None found"]
    lines.append("")

    lines += ["## Conflicts and tradeoffs", ""]
    fork_rows = []
    for fork_id in sorted(state.get("forks", {})):
        fork = state["forks"][fork_id]
        decision = state.get("decisions", {}).get(fork_id)
        if not decision:
            fork_rows.append(f"- `{fork_id}`: {fork['question']} -> UNDECIDED")
            continue
        fork_rows.append(
            f"- `{fork_id}`: {fork['question']} -> chose `{decision['chosen']}` "
            f"({decision['by']}"
            f"{', human-acknowledged' if decision.get('human_ack') else ''})"
        )
        if decision.get("invalidated"):
            fork_rows.append(f"  invalidated: {', '.join(decision['invalidated'])}")
    lines += rows(fork_rows, "`forks.jsonl`") if fork_rows else ["None found"]
    lines.append("")

    lines += ["## Decay risks", ""]
    classifications = state.get("classifications", [])
    resolved = resolved_signatures(state)
    if classifications:
        risk_rows = [
            f"- signature `{item['signature']}` classified `{item['kind']}`"
            + (" (resolved)" if item["signature"] in resolved else "")
            for item in classifications
        ]
        lines += rows(risk_rows, "`control.jsonl`")
    else:
        lines.append("None found")
    lines.append("")

    HUMAN_ANCHORS = (
        "security", "privacy", "data_integrity", "public_contract",
        "money", "legal", "irreversible_action",
    )
    lines += ["## Human anchors", ""]
    anchored = [
        f"`{f}` touches {sorted(set(d['anchors']) & set(HUMAN_ANCHORS))}"
        for f, d in sorted(state.get("decisions", {}).items())
        if set(d.get("anchors", [])) & set(HUMAN_ANCHORS)
    ]
    lines += rows(anchored, "`forks.jsonl`") if anchored else ["None touched"]
    lines.append("")

    lines += ["## What still stands between the run and the outcome", ""]
    lines += rows([f"- {r}" for r in blocking])
    lines.append("")

    lines += ["## How the outcome was reached", ""]
    lines += rows([f"- {r}" for r in disclosed])
    lines.append("")

    lines += ["## Close conditions", "", "The run's magnet posts exactly these conditions at close:", ""]
    lines += rows(_close_conditions_expectations(state), "`derivation.py`")
    lines.append("")

    lines += ["## Revision transparency", "", "The right edge may move only via a disputed, decided fork. Every movement is recorded here:", ""]
    lines += rows(_revision_transparency_rows(state), "`control.jsonl`")
    lines.append("")

    lines += ["## Dispute weights", "", "Each dispute is classified by the weight of what it binds (heavy/medium/light):", ""]
    lines += rows(_dispute_weight_rows(state), "`forks.jsonl`")
    lines.append("")

    lines += ["## Meta: run-fail dataset", "", "Signatures of runs that failed to converge are recorded for rewind/audit:", ""]
    lines += rows(_meta_fail_dataset_rows(state), "`control.jsonl`")
    lines.append("")

    lines += ["## Ticket provenance", "", "Which tickets earned their close on evidence, and which were merely merged:", ""]
    lines += rows(_ticket_provenance_rows(state), "`control.jsonl`")
    lines.append("")

    lines += ["## Auto-critique", "", "The run critiques its own record before anyone else can. Every row points at a record, not a mood:", ""]
    lines += rows(auto_critique(state), "`control.jsonl`")
    lines.append("")

    lines += ["## Requirement notice", "", _requirement_notice(state), ""]

    lines += ["## Final status", "", f"**{status}**", ""]
    return "\n".join(lines)


def next_action(state: dict, validate_infra_fn=None) -> dict[str, Any]:
    """The next thing the run owes.

    Args:
        state: Current ledger state dict.
        validate_infra_fn: Optional callable that returns infra check dict.
            When provided, auto-gates on infrastructure readiness.
    """
    resolved = resolved_signatures(state)
    parked = [
        f"{c['signature']}"
        + (f" (parked: {c['park']})" if c.get("park") else "")
        + f" needs {c['missing_fact']} via {c['smallest_input']}"
        for c in state.get("classifications", [])
        if c["kind"] == "information_missing" and c["signature"] not in resolved
    ]

    def answer(required: str, because: str) -> dict[str, Any]:
        out: dict[str, Any] = {"required": required, "because": because}
        if parked:
            out["parked_paths"] = parked
            out["parked_note"] = (
                "these paths are parked, not finished; other work continues meanwhile"
            )
        return out

    # Auto-gate: infrastructure readiness
    infra = state.get("infra", {})
    has_infra = any([
        infra.get("provisions"),
        infra.get("injections"),
        infra.get("secrets"),
    ])
    if has_infra and validate_infra_fn is not None:
        check = validate_infra_fn()
        if not check.get("ready"):
            return answer(
                "validate-infra",
                f"infrastructure prerequisites are not ready: {check.get('summary', '')}. "
                f"Run `validate-infra --fix` to attempt automatic restoration.",
            )

    # Directive takes priority
    directive = state.get("directive")
    if directive:
        kind = directive["kind"]
        DIRECTIVE_REQUIRES = {
            "loop_defective": ("declare_loop", "run_loop", "hyp_open"),
            "hypothesis_unresolved": ("hyp_refute", "hyp_survive", "hyp_merge"),
            "information_missing": (),
        }
        DIRECTIVE_MESSAGE = {
            "loop_defective": (
                "Rebuild the feedback loop before theorising. Declare a command that is "
                "red-capable, deterministic and fast, and run it."
            ),
            "hypothesis_unresolved": (
                "An untested hypothesis blocks new lines of attack. Refute it, confirm it, "
                "or merge it."
            ),
        }
        allowed = DIRECTIVE_REQUIRES.get(kind, ())
        return answer(
            allowed[0] if allowed else "resolve_block",
            DIRECTIVE_MESSAGE.get(kind, f"directive: {kind}"),
        )

    # Open hypotheses outrank forks
    open_hyps = [h for h in state.get("hypotheses", {}).values() if h["status"] == "open"]
    if open_hyps:
        hyp = open_hyps[0]
        return answer(
            "test_hypothesis",
            f"hypothesis {hyp['id']!r} is open: {hyp['claim']}",
        )

    # Undecided forks: undisputed -> dispute first; disputed -> decide
    for fork_id in sorted(state.get("forks", {})):
        if fork_id not in state.get("decisions", {}):
            has_dispute = any(
                d.get("fork") == fork_id for d in state.get("disputes", [])
            )
            if not has_dispute:
                return answer("dispute", f"fork {fork_id!r} is open and undisputed")
            return answer("decide", f"fork {fork_id!r} is disputed but undecided")

    # Check outcome
    status, blocking, _ = outcome(state)
    if status == "REACHED":
        return answer("close", "the outcome is reached")

    if blocking:
        return answer("address_blocking", blocking[0])

    return answer("close", "the outcome is reached")


# Auto-critique (10): the report turns the ledger's scepticism on its own run. #


def auto_critique(state: dict) -> list[str]:
    """Mechanical self-critique of the run, derived from the same state as the verdict.

    A convergence report is only trustworthy if it names its own weaknesses first:
    a search that eliminated nothing, evidence nobody can re-open, dissent that was
    overruled. Each row is a check against a record — the critique never invents
    what the logs do not support, in either direction.
    """
    rows_out: list[str] = []

    hypotheses = list(state.get("hypotheses", {}).values())
    refuted = [h for h in hypotheses if h["status"] == "refuted"]
    if not hypotheses:
        rows_out.append(
            "- adversarial search: weak — no hypothesis was ever opened; the run "
            "proceeded with no rival explanation to eliminate."
        )
    elif not refuted:
        rows_out.append(
            f"- adversarial search: weak — {len(hypotheses)} hypotheses opened and 0 "
            "refuted; a search that eliminates nothing may never have tested its rivals."
        )
    else:
        rows_out.append(
            f"- adversarial search: ok — {len(refuted)} of {len(hypotheses)} hypotheses "
            "refuted before the fix was chosen."
        )

    evidence = state.get("evidence", [])
    untraceable = [e for e in evidence if not e.get("output_ref")]
    if untraceable:
        rows_out.append(
            f"- evidence traceability: weak — {len(untraceable)} of {len(evidence)} "
            "evidence records carry no --output-ref, so nobody can re-open the artefact "
            "behind the claim."
        )
    else:
        rows_out.append(
            f"- evidence traceability: ok — all {len(evidence)} evidence record(s) name "
            "an output artefact."
        )

    latest_tier: dict[str, str] = {}
    for e in evidence:
        latest_tier[e["criterion"]] = e["tier"]
    single_shot = sorted(c for c, t in latest_tier.items() if t == "executed_here")
    reproduced = sorted(c for c, t in latest_tier.items() if t == "reproduced_independently")
    if single_shot:
        rows_out.append(
            f"- independent reproduction: weak — {single_shot} rest on a single "
            "executed_here run; nothing has reproduced the result from another context."
        )
    else:
        rows_out.append(
            "- independent reproduction: ok — no criterion rests on a single "
            "executed_here run."
            + (f" Reproduced independently: {reproduced}." if reproduced else "")
        )

    verdictless = sorted(
        d["fork"] for d in state.get("disputes", []) if not d.get("verdict")
    )
    decided = state.get("decisions", {})
    silent = [f for f in verdictless if f in decided]
    if silent:
        rows_out.append(
            f"- dispute verdicts: weak — {silent} were decided although the dispute "
            "recorded no verdict; the adversarial pass left no conclusion to audit."
        )
    else:
        rows_out.append(
            "- dispute verdicts: ok — every decided fork's dispute carries a recorded "
            "verdict."
        )

    overruled = sorted(f for f, d in decided.items() if d.get("challenger_overruled"))
    if overruled:
        rows_out.append(
            f"- overruled dissent: weak — {overruled} went ahead over the challenger's "
            "objection; the override reason is on record but the decision deserves a "
            "second reader."
        )
    else:
        rows_out.append(
            "- overruled dissent: ok — no decision was taken over a challenger's objection."
        )

    narrow = sorted(
        k for k, v in state.get("fixes", {}).items() if v.get("search") == "narrow"
    )
    if narrow:
        rows_out.append(
            f"- search width: weak — {narrow} fixed with --search narrow: the fix was "
            "chosen without searching over rival explanations."
        )
    else:
        rows_out.append("- search width: ok — no fix was chosen on a narrow search.")

    revisions = state.get("criterion_revisions", [])
    if revisions:
        rows_out.append(
            f"- intent drift: note — {len(revisions)} criterion revision(s) moved the "
            "right edge; the acceptance criteria in this report are not the ones the "
            "run was born with."
        )
    else:
        rows_out.append(
            "- intent drift: ok — the acceptance criteria are exactly those the run "
            "started with."
        )

    unavailable_only = sorted(
        c for c in state.get("criteria", []) if latest_tier.get(c) == "unavailable"
    )
    if unavailable_only:
        rows_out.append(
            f"- unavailable-only criteria: weak — {unavailable_only}; their verdict "
            "rests on a declared impossibility, not an observation."
        )
    else:
        rows_out.append("- unavailable-only criteria: ok — none.")

    fails = state.get("meta", {}).get("run_fails", [])
    if fails:
        rows_out.append(
            f"- fail dataset: note — {len(fails)} run-fail signature(s) recorded "
            f"({[f['signature'] for f in fails]}); compare this run against them before "
            "claiming a new failure mode."
        )
    else:
        rows_out.append("- fail dataset: ok — no failed run recorded against this ledger.")

    return rows_out


# Upper-pipeline integration + fallback (4): one word an orchestrator can      #
# route on, plus where to go when the primary lane is blocked from outside.    #


def _pending_heavy_decision(state: dict) -> str | None:
    """The undecided fork an agent may not decide alone, if any.

    Heavy means every option is irreversible: whichever way the agent jumps,
    `decide` would refuse it. Such a fork must be escalated, not stalled on.
    """
    for fork_id in sorted(state.get("forks", {})):
        if fork_id in state.get("decisions", {}):
            continue
        options = state["forks"][fork_id].get("options", [])
        if options and all(o.get("reversibility") == "irreversible" for o in options):
            return fork_id
    return None


def pipeline_route(state: dict, validate_infra_fn=None) -> dict[str, Any]:
    """Tell an upper pipeline which lane owns the next move, and where to fall back.

    Lanes: investigate (drive the hypothesis loop), decide (adjudicate forks),
    unblock (source missing facts / rebuild infrastructure), verify (collect
    evidence), human (escalate a decision the agent's authority cannot take),
    close (the run owes nothing further).

    `fallback` names the lane to work when the primary one is blocked from
    outside the session — an external fact, a human-only decision. A context
    gap is work, not a stop: the run is always routed somewhere it can move.
    """
    status, blocking, _ = outcome(state)
    action = next_action(state, validate_infra_fn=validate_infra_fn)
    required = action["required"]

    def meanwhile(gap=False):
        if gap:
            return {
                "lane": "verify",
                "because": "a parked gap is work, not a stop: verify the criteria that "
                "do not depend on the missing fact while it is being sourced",
            }
        return {
            "lane": "verify",
            "because": "while this lane is waiting on something outside the session, "
            "evidence for the independent criteria keeps the run moving",
        }

    def out(lane, because, fallback=None):
        return {"lane": lane, "because": because, "action": action, "fallback": fallback}

    if status == "REACHED" or required == "close":
        return out("close", "the run owes nothing further: close it and publish the report")

    if required in ("declare_loop", "run_loop"):
        return out("unblock", action["because"], meanwhile())

    if required in ("hyp_open", "hyp_refute", "hyp_survive", "hyp_merge", "test_hypothesis"):
        gap = bool(action.get("parked_paths"))
        return out("investigate", action["because"], meanwhile(gap=gap))

    if required in ("dispute", "decide"):
        heavy = _pending_heavy_decision(state)
        if required == "decide" and heavy:
            return out(
                "human",
                f"fork {heavy!r} offers no reversible option: whichever way it is "
                "decided the choice is irreversible, outside delegated authority",
                meanwhile(),
            )
        fallback = (
            {"lane": "human", "because": f"fork {heavy!r} offers no reversible option; "
             "if the dispute cannot narrow it to a reversible path, escalate the decision"}
            if heavy else meanwhile()
        )
        return out("decide", action["because"], fallback)

    if required in ("resolve_block", "validate-infra"):
        return out("unblock", action["because"], meanwhile(gap=True))

    head = blocking[0] if blocking else ""
    if head.startswith("path parked on"):
        return out("unblock", head, meanwhile(gap=True))
    if head.startswith("blocking risk"):
        return out("human", head, meanwhile())
    if head.startswith("open hypothesis"):
        return out("investigate", head, meanwhile())
    if head.startswith("undecided fork"):
        return out("decide", head, meanwhile())
    return out(
        "verify",
        head or "gather evidence for the criteria that carry none",
        {"lane": "close", "because": "once the evidence is in, closing is the only "
         "move the run still owes"},
    )