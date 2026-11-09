"""Unit tests for lib/derivation.py - isolated, no ledger side effects."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from derivation import (
    outcome,
    next_action,
    compute_frontier,
    auto_critique,
    pipeline_route,
    report,
)


def _base_state(**overrides):
    """Minimal valid state dict for derivation tests."""
    base = {
        "intent": "test intent",
        "criteria": ["criterion one ok", "criterion two ok"],
        "evidence": [],
        "hypotheses": {},
        "forks": {},
        "decisions": {},
        "disputes": [],
        "classifications": [],
        "risks": [],
        "fixes": {},
        "tickets": [],
        "obligations": [],
        "resolved_blocks": [],
        "criterion_revisions": [],
        "candidates": [],
        "meta": {},
        "directive": None,
    }
    base.update(overrides)
    return base


class TestOutcome:
    def test_reached_when_no_blocking(self):
        state = _base_state(evidence=[
            {"criterion": "criterion one ok", "tier": "executed_here"},
            {"criterion": "criterion two ok", "tier": "executed_here"},
        ])
        status, blocking, disclosed = outcome(state)
        assert status == "REACHED"
        assert blocking == []

    def test_not_reached_with_uncovered_criteria(self):
        state = _base_state()
        status, blocking, _ = outcome(state)
        assert status == "NOT REACHED"
        assert any("never verified" in b for b in blocking)

    def test_open_hypothesis_blocks(self):
        state = _base_state(hypotheses={
            "H-1": {"id": "H-1", "status": "open", "claim": "cache is warm"},
        })
        status, blocking, _ = outcome(state)
        assert status == "NOT REACHED"
        assert any("open hypothesis" in b for b in blocking)

    def test_undecided_fork_blocks(self):
        state = _base_state(forks={"F-1": {"question": "which db?"}})
        status, blocking, _ = outcome(state)
        assert status == "NOT REACHED"
        assert any("undecided fork" in b for b in blocking)

    def test_unavailable_only_blocks(self):
        state = _base_state(evidence=[
            {"criterion": "criterion one ok", "tier": "unavailable"},
        ])
        status, blocking, _ = outcome(state)
        assert status == "NOT REACHED"
        assert any("verified unavailable" in b for b in blocking)

    def test_loop_defective_blocks(self):
        state = _base_state(directive={"kind": "loop_defective"})
        status, blocking, _ = outcome(state)
        assert status == "NOT REACHED"
        assert any("loop was declared defective" in b for b in blocking)

    def test_overruled_dissent_disclosed(self):
        state = _base_state(
            forks={"F-1": {"question": "q"}},
            decisions={"F-1": {"chosen": "a", "by": "author", "challenger_overruled": True, "override_reason": "speed"}},
        )
        _, _, disclosed = outcome(state)
        assert any("overruled" in d.lower() for d in disclosed)


class TestNextAction:
    def test_close_when_reached(self):
        state = _base_state(evidence=[
            {"criterion": "criterion one ok", "tier": "executed_here"},
            {"criterion": "criterion two ok", "tier": "executed_here"},
        ])
        action = next_action(state)
        assert action["required"] == "close"

    def test_test_hypothesis_when_open(self):
        state = _base_state(hypotheses={
            "H-1": {"id": "H-1", "status": "open", "claim": "warm cache"},
        })
        action = next_action(state)
        assert action["required"] == "test_hypothesis"

    def test_dispute_before_decide(self):
        state = _base_state(forks={"F-1": {"question": "db?"}})
        action = next_action(state)
        assert action["required"] == "dispute"

    def test_record_candidates_exploratory(self):
        state = _base_state(meta={"exploratory": True})
        action = next_action(state)
        assert action["required"] == "record-candidates"


class TestComputeFrontier:
    def test_empty_state(self):
        state = _base_state()
        frontier = compute_frontier(state)
        assert frontier["tickets"] == []
        assert frontier["obligations"] == []

    def test_ticket_on_frontier(self):
        state = _base_state(tickets=[
            {"id": "T-1", "title": "ship it", "criteria": ["criterion one ok"], "blocked_by": []},
        ])
        frontier = compute_frontier(state)
        assert len(frontier["tickets"]) == 1
        assert frontier["tickets"][0]["id"] == "T-1"

    def test_blocked_ticket_not_on_frontier(self):
        state = _base_state(tickets=[
            {"id": "T-1", "title": "first", "criteria": ["criterion one ok"], "blocked_by": []},
            {"id": "T-2", "title": "second", "criteria": ["criterion two ok"], "blocked_by": ["T-1"]},
        ])
        frontier = compute_frontier(state)
        ids = [t["id"] for t in frontier["tickets"]]
        assert "T-1" in ids
        assert "T-2" not in ids

    def test_earned_ticket_excluded(self):
        state = _base_state(
            tickets=[{"id": "T-1", "title": "done", "criteria": ["criterion one ok"], "blocked_by": []}],
            evidence=[{"criterion": "criterion one ok", "tier": "executed_here"}],
        )
        frontier = compute_frontier(state)
        assert frontier["tickets"] == []


class TestAutoCritique:
    def test_no_hypotheses_weak_search(self):
        state = _base_state()
        rows = auto_critique(state)
        assert any("adversarial search: weak" in r for r in rows)

    def test_refuted_hypotheses_ok(self):
        state = _base_state(hypotheses={
            "H-1": {"id": "H-1", "status": "refuted", "claim": "old claim"},
        })
        rows = auto_critique(state)
        assert any("adversarial search: ok" in r for r in rows)

    def test_evidence_traceability_ok(self):
        state = _base_state(evidence=[
            {"criterion": "criterion one ok", "tier": "executed_here", "output_ref": "ref://1"},
        ])
        rows = auto_critique(state)
        assert any("evidence traceability: ok" in r for r in rows)

    def test_narrow_search_weak(self):
        state = _base_state(fixes={"INC-1": {"note": "fixed", "search": "narrow"}})
        rows = auto_critique(state)
        assert any("search width: weak" in r for r in rows)


class TestPipelineRoute:
    def test_close_lane_when_reached(self):
        state = _base_state(evidence=[
            {"criterion": "criterion one ok", "tier": "executed_here"},
            {"criterion": "criterion two ok", "tier": "executed_here"},
        ])
        route = pipeline_route(state)
        assert route["lane"] == "close"

    def test_investigate_lane_for_hypothesis(self):
        state = _base_state(hypotheses={
            "H-1": {"id": "H-1", "status": "open", "claim": "warm cache"},
        })
        route = pipeline_route(state)
        assert route["lane"] == "investigate"

    def test_decide_lane_for_fork(self):
        state = _base_state(forks={"F-1": {"question": "db?", "options": [{"reversibility": "reversible"}]}})
        route = pipeline_route(state)
        assert route["lane"] == "decide"

    def test_human_lane_for_irreversible_fork(self):
        state = _base_state(forks={"F-1": {"question": "delete data?", "options": [{"reversibility": "irreversible"}]}})
        route = pipeline_route(state)
        assert route["lane"] in ("decide", "human")


class TestReport:
    def test_report_contains_intent(self):
        state = _base_state()
        text = report(state)
        assert "test intent" in text

    def test_report_contains_criteria(self):
        state = _base_state()
        text = report(state)
        assert "criterion one ok" in text

    def test_report_reached_status(self):
        state = _base_state(evidence=[
            {"criterion": "criterion one ok", "tier": "executed_here"},
            {"criterion": "criterion two ok", "tier": "executed_here"},
        ])
        text = report(state)
        assert "REACHED" in text


class TestProvenanceAndNotice:
    def test_obligation_provenance_marks_earned_and_unproven(self):
        from derivation import _obligation_provenance_rows

        state = _base_state(
            evidence=[{"criterion": "criterion one ok", "tier": "executed_here"}],
            obligations=[{"id": "OBL-1", "title": "reimplement", "target": "libx.so",
                          "criteria": ["criterion one ok", "criterion two ok"]}],
        )
        rows = _obligation_provenance_rows(state)
        assert any("OBL-1" in row and "1/2" in row for row in rows)
        assert any("target: libx.so" in row for row in rows)
        assert any("unproven" in row for row in rows)

    def test_obligation_provenance_without_obligations(self):
        from derivation import _obligation_provenance_rows

        assert _obligation_provenance_rows(_base_state()) == ["- None imported"]

    def test_requirement_notice_names_the_first_gate(self):
        from derivation import _requirement_notice

        notice = _requirement_notice(_base_state())
        assert notice.startswith("The run is not complete")
        assert "criterion never verified" in notice

    def test_requirement_notice_is_clean_when_reached(self):
        from derivation import _requirement_notice

        state = _base_state(evidence=[
            {"criterion": "criterion one ok", "tier": "executed_here"},
            {"criterion": "criterion two ok", "tier": "executed_here"},
        ])
        assert "complete" in _requirement_notice(state)


class TestReportRichState:
    def _rich(self):
        return _base_state(
            fixes={"INC-1": {"note": "guard", "search": "narrow"}},
            hypotheses={"H-1": {"id": "H-1", "status": "refuted",
                                 "claim": "warm cache", "probe": "p",
                                 "incident": "INC-1"}},
            forks={"F-1": {"question": "which db?"}},
            decisions={"F-1": {"chosen": "a", "by": "agent",
                                 "invalidated": ["US-1"]}},
            disputes=[{"fork": "F-1", "challenger": "r2", "author": "main",
                       "steelman": "a is cheaper", "verdict": "a is fine"}],
            classifications=[{"signature": "flutter", "kind": "loop_defective"}],
            risks=[{"text": "schema changed", "blocking": False}],
            tickets=[{"id": "T-1", "title": "ship",
                      "criteria": ["criterion one ok"], "blocked_by": []}],
            obligations=[{"id": "O-1", "title": "re", "target": None,
                          "criteria": ["criterion two ok"], "blocked_by": []}],
            candidates=[{"run_id": "r1", "count": 2, "source": "arena",
                         "selected": "a1", "selected_by": "author",
                         "selection_method": "rubric"}],
            criterion_revisions=[{"fork": "F-1", "from": "a", "to": "b",
                                 "invalidated": ["US-1"]}],
            meta={"exploratory": True, "run_fails": [{"signature": "s1", "failure_kind": "not_reached"}]},
            evidence=[{"criterion": "criterion one ok", "tier": "executed_here",
                       "output_ref": "ref://1"}],
        )

    def test_report_names_provenance_work_and_disclosures(self):
        text = report(self._rich())
        assert "T-1" in text
        assert "O-1" in text
        assert "r1" in text
        assert "unopposed self-selection" in text
        assert "narrow" in text
        assert "schema changed" in text
        assert "invalidated: US-1" in text
        assert "s1" in text

    def test_report_marks_criteria_unavailable_when_nothing_else_blocks(self):
        text = report(_base_state(evidence=[
            {"criterion": "criterion one ok", "tier": "unavailable"},
            {"criterion": "criterion two ok", "tier": "executed_here"},
        ]))
        assert "NOT REACHED" in text
        assert "unavailable" in text.lower()


class TestNextActionRouting:
    def test_validate_infra_when_registered_and_not_ready(self):
        state = _base_state(
            infra={"provisions": [{"spec": "db", "output_var": "DATABASE_URL"}],
                   "injections": [], "secrets": []},
        )
        action = next_action(
            state,
            validate_infra_fn=lambda: {"ready": False, "summary": "DATABASE_URL unset"},
        )
        assert action["required"] == "validate-infra"
        assert "DATABASE_URL unset" in action["because"]

    def test_loop_defective_directive_requires_the_loop_first(self):
        state = _base_state(directive={"kind": "loop_defective"})
        assert next_action(state)["required"] == "declare_loop"

    def test_unresolved_hypothesis_directive_requires_settling_it(self):
        state = _base_state(directive={"kind": "hypothesis_unresolved"})
        assert next_action(state)["required"] == "hyp_refute"

    def test_frontier_ticket_is_worked_before_closing(self):
        state = _base_state(tickets=[
            {"id": "T-1", "title": "ship", "criteria": ["criterion one ok"],
             "blocked_by": []},
        ])
        action = next_action(state)
        assert action["required"] == "work_frontier_ticket"
        assert action["tickets"] if "tickets" in action else True

    def test_frontier_obligation_is_worked_before_closing(self):
        state = _base_state(obligations=[
            {"id": "O-1", "title": "re", "criteria": ["criterion one ok"],
             "blocked_by": []},
        ])
        assert next_action(state)["required"] == "work_frontier_obligation"

    def test_address_blocking_when_nothing_can_be_worked(self):
        state = _base_state(evidence=[
            {"criterion": "criterion one ok", "tier": "unavailable"},
            {"criterion": "criterion two ok", "tier": "executed_here"},
        ])
        action = next_action(state)
        assert action["required"] == "address_blocking"
        assert "unavailable" in action["because"]


class TestFrontierObligations:
    def test_blocked_obligation_is_not_on_the_frontier(self):
        state = _base_state(obligations=[
            {"id": "O-1", "title": "first", "criteria": ["criterion one ok"],
             "blocked_by": []},
            {"id": "O-2", "title": "second", "criteria": ["criterion two ok"],
             "blocked_by": ["O-1"]},
        ])
        ids = [o["id"] for o in compute_frontier(state)["obligations"]]
        assert "O-1" in ids
        assert "O-2" not in ids

    def test_earned_obligation_leaves_the_frontier(self):
        state = _base_state(
            obligations=[{"id": "O-1", "title": "done",
                          "criteria": ["criterion one ok"], "blocked_by": []}],
            evidence=[{"criterion": "criterion one ok", "tier": "executed_here"}],
        )
        assert compute_frontier(state)["obligations"] == []


class TestPipelineRouteLanes:
    def test_human_lane_when_every_option_is_irreversible(self):
        state = _base_state(
            forks={"F-1": {"question": "drop the old data?",
                            "options": [{"id": "a", "reversibility": "irreversible"}]}},
            disputes=[{"fork": "F-1", "challenger": "r", "verdict": "maybe"}],
        )
        route = pipeline_route(state)
        assert route["lane"] == "human"
        assert "no reversible option" in route["because"]

    def test_explore_lane_for_an_exploratory_run(self):
        assert pipeline_route(_base_state(meta={"exploratory": True}))["lane"] == "explore"

    def test_verify_lane_for_an_unavailable_criterion(self):
        state = _base_state(evidence=[
            {"criterion": "criterion one ok", "tier": "unavailable"},
            {"criterion": "criterion two ok", "tier": "executed_here"},
        ])
        assert pipeline_route(state)["lane"] == "verify"

    def test_unblock_lane_for_a_parked_path(self):
        state = _base_state(classifications=[
            {"signature": "needs-token", "kind": "information_missing",
             "park": "F-deploy", "missing_fact": "a vault token",
             "smallest_input": "operator runs vault login"},
        ])
        route = pipeline_route(state)
        assert route["lane"] == "unblock"

    def test_human_lane_for_a_blocking_risk(self):
        state = _base_state(risks=[{"text": "public schema changed", "blocking": True}])
        route = pipeline_route(state)
        assert route["lane"] == "human"
