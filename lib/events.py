"""Event handlers: pure state mutators applied during ledger replay.

Each handler takes (state, event) and mutates state in place. No IO, no
derivation calls. The dispatch table maps event kind strings to handlers
so replay is explicit and enumerable.
"""
from __future__ import annotations

from typing import Any

# Shared exceptions avoid circular imports: convergence.py imports events.py,
# so events.py cannot import convergence.py at module scope. Both import from
# this leaf module instead, giving tests and handlers the same class identity.
from exceptions import Blocked, UsageError


def _ev_init(s: dict, ev: dict) -> None:
    s["intent"] = ev["intent"]
    s["criteria"] = list(ev.get("criteria") or [])
    s["intent_frozen"] = False
    s["intent_freeze_at"] = None
    s["infra"] = {}
    s["meta"] = {
        "exploratory": bool(ev.get("exploratory", False)),
        "critic": list(ev.get("critic") or []),
    }
    s["tickets"] = []
    s["obligations"] = []
    s["candidates"] = []


def _ev_candidates_recorded(s: dict, ev: dict) -> None:
    """Record that an exploration pass produced candidate solutions.

    Provenance, not endorsement: the ledger stores where the candidates came
    from (arena run, parallel agents, manual drafts) so a later selection can
    name what it chose among. Validation lives here, not only in the caller,
    so replay refuses the same violations.
    """
    cands = s.setdefault("candidates", [])
    known = {c["run_id"] for c in cands}
    for c in ev.get("candidates", []):
        if c["run_id"] in known:
            prev = next((p for p in cands if p["run_id"] == c["run_id"]), None)
            if prev == c:
                continue  # idempotent replay of an identical recording
            raise UsageError(
                f"candidate run {c['run_id']!r} is already recorded with different "
                f"content; refusing ambiguous re-recording"
            )
        known.add(c["run_id"])
        cands.append(dict(c))


def _ev_candidate_selected(s: dict, ev: dict) -> None:
    """Record which candidate solution this run pursues.

    ``by='disputed'`` means a challenger contested the choice on a recorded
    fork; ``by='author'`` is a disclosure the report must carry — selecting
    your own candidate without opposition is a method note, not a verdict.
    """
    cands = s.get("candidates", [])
    hit = next((c for c in cands if c["run_id"] == ev["run_id"]), None)
    if hit is None:
        raise UsageError(
            f"no candidate run {ev['run_id']!r} recorded; record the exploration "
            f"pass with record-candidates before selecting from it"
        )
    if hit.get("selected") is not None:
        raise UsageError(
            f"candidate run {ev['run_id']!r} already has a selection "
            f"({hit['selected']!r}); a new selection needs a disputed fork that "
            f"moves it"
        )
    hit["selected"] = ev["id"]
    hit["selected_by"] = ev["by"]
    hit["selection_method"] = ev["method"]
    hit["selection_fork"] = ev.get("fork")


def _ev_intent_frozen(s: dict, ev: dict) -> None:
    s["intent_frozen"] = True
    s["intent_freeze_at"] = ev["at"]


def _ev_tickets_imported(s: dict, ev: dict) -> None:
    """Map tickets from the spec's task graph onto acceptance criteria.

    Ticket boundaries are kept as provenance: the report can then say which
    ticket earned its close and which was merely merged. Validation lives here,
    not only in the caller, so replay refuses the same violations.
    """
    tickets = s.setdefault("tickets", [])
    known_ids = {t["id"] for t in tickets}
    known_criteria = set(s.get("criteria", []))
    for t in ev.get("tickets", []):
        if t["id"] in known_ids:
            prev = next((p for p in tickets if p["id"] == t["id"]), None)
            if prev == t:
                continue  # idempotent replay of an identical import
            raise UsageError(f"ticket {t['id']} is already imported")
        for c in t.get("criteria", []):
            if c in known_criteria:
                raise UsageError(
                    f"criterion {c!r} is already an acceptance criterion; each must map "
                    f"to exactly one ticket, or the report cannot say which earned it"
                )
        known_ids.add(t["id"])
        known_criteria.update(t.get("criteria", []))
        tickets.append(dict(t))
        s["criteria"] = list(s.get("criteria", [])) + list(t.get("criteria", []))


def _ev_obligations_imported(s: dict, ev: dict) -> None:
    """Map reconstruction obligations onto acceptance criteria.

    Obligation boundaries are kept as provenance: the report discloses which
    obligations were earned through verification evidence. Validation lives here,
    not only in the caller, so replay refuses the same violations.
    """
    obligations = s.setdefault("obligations", [])
    known_ids = {o["id"] for o in obligations}
    known_criteria = set(s.get("criteria", []))
    for o in ev.get("obligations", []):
        if o["id"] in known_ids:
            prev = next((p for p in obligations if p["id"] == o["id"]), None)
            if prev == o:
                continue  # idempotent replay of an identical import
            raise UsageError(f"obligation {o['id']} is already imported")
        for c in o.get("criteria", []):
            if c in known_criteria:
                raise UsageError(
                    f"criterion {c!r} is already an acceptance criterion; each must map "
                    f"to exactly one obligation, or the report cannot say which earned it"
                )
        known_ids.add(o["id"])
        known_criteria.update(o.get("criteria", []))
        obligations.append(dict(o))
        s["criteria"] = list(s.get("criteria", [])) + list(o.get("criteria", []))


def _ev_loop_declare(s: dict, ev: dict) -> None:
    s["loops"][ev["incident"]] = {
        "command": ev["command"],
        "symptom": ev["symptom"],
        "ran": False,
        "red": False,
        "declared_at": ev["at"],
    }


def _ev_loop_run(s: dict, ev: dict) -> None:
    loop = s["loops"].get(ev["incident"])
    if loop is None:
        raise UsageError(f"no loop declared for incident {ev['incident']!r}")
    loop["ran"] = True
    loop["red"] = ev["red"]
    loop["ran_at"] = ev["at"]


def _ev_hyp_open(s: dict, ev: dict) -> None:
    s["hypotheses"][ev["id"]] = {
        "id": ev["id"],
        "incident": ev["incident"],
        "claim": ev["claim"],
        "prediction": ev["prediction"],
        "rank": ev.get("rank"),
        "status": "open",
        "probe": None,
        "observed": None,
        "opened_at": ev["at"],
        "closed_at": None,
    }


def _close_hypothesis(s: dict, ev: dict, status: str) -> None:
    hyp = s["hypotheses"].get(ev["id"])
    if hyp is None:
        raise UsageError(f"unknown hypothesis {ev['id']!r}")
    if hyp["status"] != "open":
        raise Blocked(f"hypothesis {ev['id']!r} is already {hyp['status']}")
    hyp["status"] = status
    hyp["probe"] = ev["probe"]
    hyp["observed"] = ev["observed"]
    hyp["closed_at"] = ev["at"]


def _ev_hyp_refute(s: dict, ev: dict) -> None:
    _close_hypothesis(s, ev, "refuted")


def _ev_hyp_survive(s: dict, ev: dict) -> None:
    _close_hypothesis(s, ev, "surviving")


def _ev_hyp_merge(s: dict, ev: dict) -> None:
    target = s["hypotheses"].get(ev["into"])
    source = s["hypotheses"].get(ev["from"])
    if target is None or source is None:
        raise UsageError("merge needs an existing --into and --from hypothesis")
    if target["status"] == "merged":
        raise Blocked(f"cannot merge into {ev['into']!r}: it was itself merged elsewhere")
    if source["status"] == "merged":
        raise Blocked(f"cannot merge {ev['from']!r}: it was already merged into {source['merged_into']!r}")
    if source["id"] == target["id"]:
        raise Blocked(f"cannot merge {ev['from']!r} into itself")
    if source["claim"].strip().lower() == target["claim"].strip().lower():
        raise Blocked(
            f"merge is a no-op: {ev['from']!r} and {ev['into']!r} state the same claim"
        )
    target["merged_from"] = target.get("merged_from", []) + [ev["from"]]
    target["merge_note"] = ev["note"]
    source["status"] = "merged"
    source["merged_into"] = ev["into"]
    source["closed_at"] = ev["at"]


def _ev_fix_begin(s: dict, ev: dict) -> None:
    s["fixes"][ev["incident"]] = {
        "note": ev["note"], "search": ev.get("search", "full"), "at": ev["at"],
    }


def _ev_classify(s: dict, ev: dict) -> None:
    s["classifications"].append(
        {
            "signature": ev["signature"],
            "kind": ev["kind"],
            "missing_fact": ev.get("missing_fact"),
            "smallest_input": ev.get("smallest_input"),
            "tried": ev.get("tried") or [],
            "park": ev.get("park"),
            "only_route": ev.get("only_route"),
            "note": ev.get("note"),
            "at": ev["at"],
        }
    )
    if ev["kind"] == "information_missing":
        s["directive"] = None
    else:
        s["directive"] = {"kind": ev["kind"], "set_at": ev["at"]}


def _ev_snapshot(s: dict, ev: dict) -> None:
    """Compaction checkpoint: replace the state wholesale with the snapshotted one.

    Written only by Ledger.compact(), after proving the snapshot replays to the
    same derived state as the logs it replaces.
    """
    s.clear()
    s.update(ev["state"])
    s.setdefault("events", 0)


def _ev_meta_record(s: dict, ev: dict) -> None:
    s.setdefault("meta", {}).setdefault("run_fails", []).append(
        {
            "signature": ev.get("signature"),
            "failure_kind": ev.get("failure_kind"),
            "at": ev["at"],
        }
    )


def _ev_directive_clear(s: dict, ev: dict) -> None:
    s["directive"] = None


def _ev_resolve_block(s: dict, ev: dict) -> None:
    s["resolved_blocks"].append(
        {"signature": ev["signature"], "input": ev["input"], "at": ev["at"]}
    )


def _ev_fork_open(s: dict, ev: dict) -> None:
    s["forks"][ev["id"]] = {
        "id": ev["id"],
        "question": ev["question"],
        "trigger": ev["trigger"],
        "assumed": ev["assumed"],
        "options": ev["options"],
        "invalidation_rationale": ev.get("invalidation_rationale"),
        "criterion_from": ev.get("criterion_from"),
        "criterion_to": ev.get("criterion_to"),
        "opened_at": ev["at"],
    }


def _ev_criterion_revised(s: dict, ev: dict) -> None:
    """Move the right edge. Only ever reached through a disputed, decided fork."""
    s["criteria"] = [c for c in s["criteria"] if c != ev["from"]]
    s["criteria"].append(ev["to"])
    s["criterion_revisions"].append(
        {
            "from": ev["from"],
            "to": ev["to"],
            "fork": ev["fork"],
            "invalidated": ev.get("invalidated") or [],
            "at": ev["at"],
        }
    )


def _ev_dispute(s: dict, ev: dict) -> None:
    s["disputes"].append(
        {
            "fork": ev["fork"],
            "challenger": ev["challenger"],
            "author": ev["author"],
            "steelman": ev["steelman"],
            "verdict": ev.get("verdict"),
            "at": ev["at"],
        }
    )


def _ev_decide(s: dict, ev: dict) -> None:
    s["decisions"][ev["fork"]] = {
        "chosen": ev["chosen"],
        "by": ev["by"],
        "human_ack": ev.get("human_ack", False),
        "reversibility": ev["reversibility"],
        "anchors": ev.get("anchors") or [],
        "invalidated": ev.get("invalidated") or [],
        "rationale": ev.get("rationale"),
        "challenger_overruled": ev.get("challenger_overruled", False),
        "override_reason": ev.get("override_reason"),
        "at": ev["at"],
    }


def _ev_evidence(s: dict, ev: dict) -> None:
    s["evidence"].append(
        {
            "criterion": ev["criterion"],
            "command": ev["command"],
            "tier": ev["tier"],
            "reproduced_by": ev.get("reproduced_by"),
            "expected_source": ev.get("expected_source"),
            "reason": ev.get("reason"),
            "output_ref": ev.get("output_ref"),
            "at": ev["at"],
        }
    )


def _ev_risk(s: dict, ev: dict) -> None:
    s["risks"].append({"text": ev["text"], "blocking": ev.get("blocking", False), "at": ev["at"]})


def _ev_provision_env(s: dict, ev: dict) -> None:
    s.setdefault("infra", {}).setdefault("provisions", []).append(
        {
            "spec": ev["spec"],
            "output_var": ev["output_var"],
            "command": ev["command"],
            "resolved": ev.get("resolved", False),
            "at": ev["at"],
        }
    )


def _ev_inject_build_context(s: dict, ev: dict) -> None:
    s.setdefault("infra", {}).setdefault("injections", []).append(
        {
            "target": ev["target"],
            "source": ev["source"],
            "artifacts": ev.get("artifacts") or [],
            "command": ev["command"],
            "at": ev["at"],
        }
    )


def _ev_store_secret(s: dict, ev: dict) -> None:
    s.setdefault("infra", {}).setdefault("secrets", []).append(
        {
            "name": ev["name"],
            "command": ev["command"],
            "stored_at": ev["at"],
        }
    )


def _ev_boot_runtime(s: dict, ev: dict) -> None:
    s.setdefault("infra", {}).setdefault("runtimes", []).append(
        {
            "name": ev["name"],
            "command": ev["command"],
            "port": ev["port"],
            "url": ev["url"],
            "pid": ev["pid"],
            "log": ev["log"],
            "stopped": False,
            "booted_at": ev["at"],
        }
    )


def _ev_stop_runtime(s: dict, ev: dict) -> None:
    runtimes = s.setdefault("infra", {}).setdefault("runtimes", [])
    for rt in runtimes:
        if rt["name"] == ev["name"] and not rt.get("stopped"):
            rt["stopped"] = True
            rt["stopped_at"] = ev["at"]
            return
    raise Blocked(f"no running runtime named {ev['name']!r}")


# Explicit dispatch table: event kind -> handler function.
# Makes the handler set enumerable, testable, and self-documenting.
HANDLERS: dict[str, Any] = {
    "init": _ev_init,
    "loop_declare": _ev_loop_declare,
    "loop_run": _ev_loop_run,
    "hyp_open": _ev_hyp_open,
    "hyp_refute": _ev_hyp_refute,
    "hyp_survive": _ev_hyp_survive,
    "hyp_merge": _ev_hyp_merge,
    "fix_begin": _ev_fix_begin,
    "classify": _ev_classify,
    "directive_clear": _ev_directive_clear,
    "resolve_block": _ev_resolve_block,
    "fork_open": _ev_fork_open,
    "criterion_revised": _ev_criterion_revised,
    "dispute": _ev_dispute,
    "decide": _ev_decide,
    "evidence": _ev_evidence,
    "risk": _ev_risk,
    "provision_env": _ev_provision_env,
    "inject_build_context": _ev_inject_build_context,
    "store_secret": _ev_store_secret,
    "boot_runtime": _ev_boot_runtime,
    "stop_runtime": _ev_stop_runtime,
    "intent_frozen": _ev_intent_frozen,
    "tickets_imported": _ev_tickets_imported,
    "obligations_imported": _ev_obligations_imported,
    "candidates_recorded": _ev_candidates_recorded,
    "candidate_selected": _ev_candidate_selected,
    "meta_record": _ev_meta_record,
    "snapshot": _ev_snapshot,
}


def apply_event(state: dict, ev: dict) -> None:
    """Fold one event into `state` in place. Unknown events are a corrupt log."""
    kind = ev.get("ev")
    handler = HANDLERS.get(kind)
    if handler is None:
        raise UsageError(f"corrupt ledger: unknown event {kind!r}")
    handler(state, ev)
    state["events"] += 1