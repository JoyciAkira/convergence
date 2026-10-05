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