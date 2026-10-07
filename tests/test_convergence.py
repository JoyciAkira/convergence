"""Behavioural tests for the convergence ledger.

Every test drives a gate through the public API and asserts the observable effect:
whether the action was applied, refused, and what the derived status became. No test
asserts on the contents of a source file — deleting behaviour while leaving prose
behind must turn this suite red.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from convergence import (  # noqa: E402
    MIN_HYPOTHESES,
    Blocked,
    IntakeError,
    Ledger,
    UsageError,
    main,
)

INCIDENT = "INC-1"


@pytest.fixture
def led(tmp_path):
    return Ledger.create(tmp_path / "L", "ship the parser", ["handles CRLF", "rejects malformed"])


def armed(led, incident=INCIDENT, red=True):
    """An incident whose feedback loop is declared and has actually gone red."""
    led.declare_loop(incident, "pytest -k crlf", "import blows up on CRLF input")
    led.run_loop(incident, red=red)
    return led


def two_refuted(led, incident=INCIDENT):
    armed(led, incident)
    led.open_hypothesis("H1", incident, "universal newlines strip CR", "isort will strip the CR")
    led.open_hypothesis("H2", incident, "the file is opened in text mode", "opening binary fixes it")
    led.close_hypothesis("H1", "pytest -k crlf", "still red: CR survives", survives=False)
    led.close_hypothesis("H2", "open(path, 'rb') probe", "went green", survives=True)
    return led


# --------------------------------------------------------------------------- #
# the loop must exist and must have been run
# --------------------------------------------------------------------------- #


def test_hypothesis_is_refused_before_a_loop_exists(led):
    with pytest.raises(Blocked, match="no declared feedback loop"):
        led.open_hypothesis("H1", INCIDENT, "bad mode", "mode would be 'r'")
    assert led.state["hypotheses"] == {}


def test_declared_but_unrun_loop_cannot_support_hypotheses(led):
    led.declare_loop(INCIDENT, "pytest -k crlf", "import blows up on CRLF input")
    with pytest.raises(Blocked, match="declared but never run"):
        led.open_hypothesis("H1", INCIDENT, "bad mode", "mode would be 'r'")


def test_a_green_loop_cannot_support_hypotheses(led):
    armed(led, red=False)
    with pytest.raises(Blocked, match="did not go red"):
        led.open_hypothesis("H1", INCIDENT, "bad mode", "mode would be 'r'")


def test_run_loop_requires_an_explicit_boolean(led):
    led.declare_loop(INCIDENT, "pytest -k crlf", "import blows up")
    with pytest.raises(UsageError, match="must be true or false"):
        led.run_loop(INCIDENT, "probably")


# --------------------------------------------------------------------------- #
# entry A: hypotheses are falsifiable, distinct, and settled before a fix
# --------------------------------------------------------------------------- #


def verified(led):
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here", expected_source="literal")
    led.record_evidence("rejects malformed", "pytest -k malformed", "executed_here",
                        expected_source="spec")
    return led

def test_hypothesis_needs_a_prediction(led):
    armed(led)
    with pytest.raises(UsageError, match="--prediction"):
        led.open_hypothesis("H1", INCIDENT, "bad mode", "   ")


def test_restating_the_same_claim_is_refused(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "the mode is wrong", "mode prints as 'r'")
    with pytest.raises(Blocked, match="already recorded"):
        led.open_hypothesis("H2", INCIDENT, "The Mode Is Wrong", "mode prints as r")


def test_a_fix_needs_at_least_three_tested_explanations(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "newline mode", "mode would be 'r'")
    led.close_hypothesis("H1", "mode probe", "mode is 'r'", survives=True)
    with pytest.raises(Blocked, match=f"--search full requires {MIN_HYPOTHESES}"):
        led.begin_fix(INCIDENT, "open the file in binary mode")
    with pytest.raises(Blocked, match="re-run with --search narrow"):
        led.begin_fix(INCIDENT, "open the file in binary mode")


def test_a_fix_is_refused_while_a_rival_is_untested(led):
    armed(led)
    for ident, claim in (("H1", "newline mode"), ("H2", "encoding declaration"), ("H3", "parser bug")):
        led.open_hypothesis(ident, INCIDENT, claim, f"{claim} would show up under strace")
    led.close_hypothesis("H1", "mode probe", "mode is 'r'", survives=True)
    led.close_hypothesis("H2", "iconv probe", "file has no encoding cookie", survives=False)
    with pytest.raises(Blocked, match="open hypotheses: \\['H3'\\]"):
        led.begin_fix(INCIDENT, "binary mode")
    assert led.state["fixes"] == {}


def test_a_narrow_search_is_permitted_but_priced(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "the guard is missing", "adding it goes green")
    led.close_hypothesis("H1", "add the guard", "green", survives=True)
    led.begin_fix(INCIDENT, "add the null guard", search="narrow")
    assert led.state["fixes"][INCIDENT]["search"] == "narrow"
    verified(led)
    status, blocking, disclosed = led.outcome()
    assert status == "REACHED", blocking
    assert any("without searching over rival" in d for d in disclosed)


def test_the_search_width_is_recorded_and_shown_in_the_report(led):
    two_refuted(led)
    led.open_hypothesis("H3", INCIDENT, "BOM handling", "stripping it fixes it")
    led.close_hypothesis("H3", "BOM probe", "green", survives=True)
    led.begin_fix(INCIDENT, "binary mode")
    assert "(search: full)" in led.report()


def test_an_unknown_search_width_is_refused(led):
    two_refuted(led)
    with pytest.raises(UsageError, match="--search"):
        led.begin_fix(INCIDENT, "n", search="whatever")


def test_a_huge_ledger_still_produces_a_readable_report(led):
    for i in range(40):
        inc = f"BULK-{i:03d}"
        led.declare_loop(inc, f"pytest -k b{i}", "bulk failure")
        led.run_loop(inc, red=True)
        unique = inc.replace("-", "")
        led.open_hypothesis(f"B{i}", inc, f"the retry budget for {unique} is unset",
                            f"setting {unique} goes green")
        led.close_hypothesis(f"B{i}", "probe", "green", survives=True)
        led.begin_fix(inc, f"fix {i}", search="narrow")
    report = led.report()
    assert len(report) < 8000, f"report grew to {len(report)} bytes"
    assert "more; the full record is in" in report
    assert "`hypotheses.jsonl`" in report
    assert "## Close conditions" in report
    assert "## Revision transparency" in report
    assert "## Dispute weights" in report
    assert "## Meta: run-fail dataset" in report
    assert "## Requirement notice" in report


def test_report_has_explicit_close_conditions(led):
    settled = two_refuted(led)
    settled.open_hypothesis("H3", INCIDENT, "BOM handling", "stripping the BOM fixes it")
    settled.close_hypothesis("H3", "BOM probe", "green", survives=True)
    settled.begin_fix(INCIDENT, "binary mode")
    settled.record_evidence("handles CRLF", "pytest -k crlf", "executed_here", expected_source="literal")
    settled.record_evidence("rejects malformed", "pytest -k malformed", "executed_here", expected_source="spec")
    report = settled.report()
    assert "## Close conditions" in report
    assert "every criterion verified: satisfied" in report
    assert "no open hypothesis: satisfied" in report
    assert "no undecided fork: satisfied" in report


def test_close_conditions_reflect_blocking(led):
    led.record_evidence("handles CRLF", "pytest", "executed_here", expected_source="literal")
    report = led.report()
    assert "## Close conditions" in report
    assert "every criterion verified: unsatisfied" in report


def test_revision_transparency_appears_when_criterion_moves(led):
    led.open_fork(
        "F1", "criterion drift", "the BOM fixture matters", "keep",
        [
            {"id": "keep", "cost": "0d", "risk": "low", "reversibility": "reversible"},
            {"id": "revise", "cost": "1d", "risk": "med", "reversibility": "reversible"},
        ],
        criterion_from="handles CRLF",
        criterion_to="handles CRLF and BOM-prefixed fixtures",
        invalidation_rationale="BOM fixtures were never in the original report.",
    )
    led.dispute("F1", challenger="reviewer", author="main", steelman="keep the old line")
    led.decide("F1", "revise", by="agent", invalidated=["AC-1"])
    report = led.report()
    assert "## Revision transparency" in report
    assert "handles CRLF" in report
    assert "handles CRLF and BOM-prefixed fixtures" in report


def test_dispute_weights_appear_in_report(led):
    led.open_fork(
        "F1", "which queue?", "broker gone", "redis",
        [
            {"id": "pg", "cost": "3d", "risk": "med", "reversibility": "irreversible"},
            {"id": "sqlite", "cost": "0d", "risk": "low", "reversibility": "reversible"},
        ],
    )
    led.dispute("F1", challenger="reviewer", author="main", steelman="sqlite cannot hold the write volume")
    led.decide("F1", "pg", by="human", human_ack=True, anchors=["security"],
               invalidated=["the redis spike"])
    report = led.report()
    assert "## Dispute weights" in report
    assert "weight=heavy" in report


def test_meta_fail_dataset_appears_on_close(led):
    led.record_meta_fail("run-1", "not_reached")
    report = led.report()
    assert "## Meta: run-fail dataset" in report
    assert "run-1" in report


def test_intake_gate_enforces_shape_before_freeze(led):
    with pytest.raises(IntakeError, match="no criteria"):
        Ledger.validate_intent("ship the parser", [])
    with pytest.raises(IntakeError, match="shorter than 4"):
        Ledger.validate_intent("ship the parser", ["x", "handles CRLF"])
    validated = Ledger.validate_intent("ship the parser", ["handles CRLF", "rejects malformed"])
    assert validated["intent"] == "ship the parser"
    assert validated["criteria"] == ["handles CRLF", "rejects malformed"]


def test_freeze_without_criteria_is_refused(tmp_path):
    # Build a ledger with no criteria by editing the control log
    (tmp_path / "L").mkdir()
    (tmp_path / "L" / "control.jsonl").write_text(
        json.dumps({"ev": "init", "intent": "ship", "criteria": [], "at": 0}) + "\n"
    )
    led3 = Ledger(tmp_path / "L")
    with pytest.raises(Blocked, match="no acceptance criteria"):
        led3.freeze_intent()


def test_intent_frozen_event_is_persisted(led):
    led.freeze_intent()
    assert led.state["intent_frozen"] is True
    assert led.state["intent_freeze_at"] is not None


def test_compact_replays_to_the_same_state(tmp_path):
    led = two_refuted(Ledger.create(tmp_path / "L", "ship", ["handles CRLF"]))
    led.record_evidence("handles CRLF", "pytest", "executed_here", expected_source="literal")
    led.freeze_intent()
    reopened = led.compact()
    assert reopened.state["intent"] == led.state["intent"]
    assert reopened.state["criteria"] == led.state["criteria"]
    assert reopened.state["intent_frozen"] is True
    assert reopened.state["loops"] == led.state["loops"]


def test_compact_refuses_if_state_diverges_under_replay(tmp_path):
    """A ledger whose in-memory state is stale must refuse to snapshot: replay
    sees writes this process never absorbed, and compacting the stale view would
    silently drop them."""
    led = Ledger.create(tmp_path / "L", "ship", ["handles CRLF"])
    led.freeze_intent()
    stale = Ledger(tmp_path / "L")
    # Another process lands a write after `stale` loaded its state.
    led.record_risk("an external write landed here")
    with pytest.raises(Blocked, match="diverges"):
        stale.compact()
    # The external write survives the refused compact.
    fresh = Ledger(tmp_path / "L")
    assert any(r["text"] == "an external write landed here" for r in fresh.state["risks"])


def test_catalog_spec_resolve_known_and_unknown(tmp_path):
    assert Ledger.resolve_catalog_spec("postgres")["driver"] == "postgresql"
    assert Ledger.resolve_catalog_spec("sqlite")["driver"] == "sqlite"
    assert Ledger.resolve_catalog_spec("unknown")["driver"] == "unknown"


def test_provision_env_catalog_uses_placeholder_command(tmp_path):
    led = Ledger.create(tmp_path / "L", "ship", ["handles CRLF"])
    led.provision_env_catalog("postgres", "DATABASE_URL")
    prov = led.state["infra"]["provisions"][-1]
    assert prov["spec"] == "postgresql"
    assert prov["command"] == "echo postgresql://user:pass@host:5432/db"
    assert prov["output_var"] == "DATABASE_URL"


def test_dispute_weight_heuristics(led):
    heavy = Ledger.decision_weight("", [], "irreversible")
    assert heavy == "heavy"
    medium = Ledger.decision_weight("", ["data_integrity"], "reversible")
    assert medium == "medium"
    light = Ledger.decision_weight("", [], "reversible")
    assert light == "light"
    heavy2 = Ledger.decision_weight("", ["money"], "reversible")
    assert heavy2 == "heavy"

def test_freeze_twice_is_refused(led):
    led.freeze_intent()
    with pytest.raises(Blocked, match="already frozen"):
        led.freeze_intent()


def test_three_settled_hypotheses_unlock_the_fix(led):
    two_refuted(led)
    led.open_hypothesis("H3", INCIDENT, "the parser drops the BOM", "stripping the BOM fixes it")
    led.close_hypothesis("H3", "BOM-strip probe", "went green", survives=True)
    led.begin_fix(INCIDENT, "open in binary mode and strip the BOM")
    assert INCIDENT in led.state["fixes"]


def test_a_refutation_without_an_observed_result_is_refused(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "newline mode", "mode would be 'r'")
    with pytest.raises(UsageError, match="--observed"):
        led.close_hypothesis("H1", "mode probe", "", survives=False)
    assert led.state["hypotheses"]["H1"]["status"] == "open"


def test_a_hypothesis_cannot_be_settled_twice(led):
    two_refuted(led)
    with pytest.raises(Blocked, match="already surviving"):
        led.close_hypothesis("H2", "again", "green again", survives=True)


def test_merge_reduces_two_lanes_to_one_mechanism(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "newline mode", "mode would be 'r'")
    led.open_hypothesis("H2", INCIDENT, "encoding cookie missing", "adding a cookie fixes it")
    led.merge_hypotheses("H1", "H2", "both are the same newline-stripping mechanism")
    assert led.state["hypotheses"]["H2"]["status"] == "merged"
    assert led.state["hypotheses"]["H1"]["merged_from"] == ["H2"]


def test_merging_a_claim_into_itself_is_refused(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "newline mode", "mode would be 'r'")
    led.open_hypothesis("H2", INCIDENT, "encoding cookie missing", "adding a cookie fixes it")
    with pytest.raises(Blocked, match="into itself"):
        led.merge_hypotheses("H1", "H1", "same thing")


def test_a_refuted_hypothesis_can_be_folded_into_the_survivor(led):
    two_refuted(led)
    led.open_hypothesis("H3", INCIDENT, "the parser drops the BOM", "stripping the BOM fixes it")
    led.merge_hypotheses("H3", "H1", "same newline-stripping mechanism as H1")
    assert led.state["hypotheses"]["H1"]["status"] == "merged"
    assert led.state["hypotheses"]["H3"]["merged_from"] == ["H1"]


def test_merging_never_lowers_the_number_of_explanations_tested(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "newline mode", "mode would be 'r'")
    led.open_hypothesis("H2", INCIDENT, "encoding cookie missing", "adding a cookie fixes it")
    led.close_hypothesis("H1", "probe", "still red", survives=False)
    led.close_hypothesis("H2", "probe", "green", survives=True)
    led.merge_hypotheses("H2", "H1", "same newline mechanism")
    with pytest.raises(Blocked, match="only 2 hypothesis"):
        led.begin_fix(INCIDENT, "strip the BOM")


def test_an_already_merged_hypothesis_cannot_be_merged_again(led):
    two_refuted(led)
    led.open_hypothesis("H3", INCIDENT, "BOM handling", "stripping it fixes it")
    led.merge_hypotheses("H3", "H1", "same mechanism")
    with pytest.raises(Blocked, match="already merged into"):
        led.merge_hypotheses("H2", "H1", "again")


def test_begin_fix_names_the_missing_loop_rather_than_the_count(led):
    with pytest.raises(Blocked, match="no declared feedback loop"):
        led.begin_fix(INCIDENT, "guess a fix")


def earned_block(led, signature="needs-vault-token", park=None):
    led.classify(
        signature,
        "information_missing",
        missing_fact="a Vault token with read access",
        smallest_input="the operator running `vault login`",
        tried=["checked the env, no VAULT_TOKEN", "asked the secrets endpoint, 403"],
        park=park,
    )
    return led


def test_a_block_must_be_earned_by_recording_routes_already_taken(led):
    with pytest.raises(Blocked, match="must be\n?\\s*earned|a block parks one path"):
        led.classify("needs-vault-token", "information_missing",
                     missing_fact="a Vault token", smallest_input="vault login")
    with pytest.raises(Blocked, match="distinct routes"):
        led.classify("needs-vault-token", "information_missing",
                     missing_fact="a Vault token", smallest_input="vault login",
                     tried=["looked once"])
    with pytest.raises(Blocked, match="distinct routes"):
        led.classify("needs-vault-token", "information_missing",
                     missing_fact="a Vault token", smallest_input="vault login",
                     tried=["the same route", "The Same Route"])
    earned_block(led)
    assert led.state["classifications"][0]["tried"]


def test_a_block_parks_one_path_and_never_stops_the_run(led):
    earned_block(led, park="F-deploy")
    nxt = led.next_action()
    assert nxt["required"] != "resolve_block", "a parked path must not be the next action"
    assert any("F-deploy" in p for p in nxt["parked_paths"])
    assert "continues meanwhile" in nxt["parked_note"]


def test_a_parked_path_is_named_in_what_still_blocks_the_outcome(led):
    earned_block(led, park="INC-deploy")
    status, blocking, _ = led.outcome()
    assert status == "NOT REACHED"
    assert any("parked: INC-deploy" in r for r in blocking)


def test_supplying_the_missing_fact_releases_the_parked_path(led):
    earned_block(led)
    assert led.outcome()[0] == "NOT REACHED"
    led.resolve_block("needs-vault-token", "operator ran `vault login`; token in env")
    verified(led)
    assert led.outcome() == ("REACHED", [], [])


def test_a_criterion_never_verified_means_the_outcome_was_not_reached(led):
    status, blocking, _ = led.outcome()
    assert status == "NOT REACHED"
    assert any("never verified" in r for r in blocking)


def test_a_resolved_block_stays_visible_in_the_report(led):
    earned_block(led)
    led.resolve_block("needs-vault-token", "token supplied by the operator")
    verified(led)
    report = led.report()
    assert "needs-vault-token" in report
    assert "(resolved)" in report


def test_information_missing_must_name_the_fact_and_the_input(led):
    tried = ["checked the env", "asked the endpoint"]
    with pytest.raises(UsageError, match="--missing-fact"):
        led.classify("v", "information_missing", smallest_input="vault login", tried=tried)
    with pytest.raises(UsageError, match="--smallest-input"):
        led.classify("v", "information_missing", missing_fact="a Vault token", tried=tried)
    earned_block(led)
    status, blocking, _ = led.outcome()
    assert status == "NOT REACHED"
    assert any("needs-vault-token" in r for r in blocking)




# --------------------------------------------------------------------------- #
# entry B: forks, disputes and delegated authority
# --------------------------------------------------------------------------- #


def fork_options(*ids):
    return [
        {"id": i, "cost": "1d", "risk": "low", "reversibility": "reversible"} for i in ids
    ]


def test_a_fork_needs_options(led):
    with pytest.raises(UsageError, match="at least one option"):
        led.open_fork("F1", "which queue?", "the broker is gone", "redis", [])


def test_an_option_without_reversibility_is_refused(led):
    with pytest.raises(UsageError, match="reversibility"):
        led.open_fork("F1", "which queue?", "broker gone", "redis", [{"id": "a", "cost": "1d", "risk": "low"}])


def test_a_lone_option_must_say_what_it_ruled_out(led):
    with pytest.raises(Blocked, match="invalidation-rationale"):
        led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs"))
    led.open_fork(
        "F1", "which queue?", "broker gone", "redis", fork_options("sqs"),
        invalidation_rationale="SQS-only and self-hosted are ruled out: both need a region we do not have.",
    )
    assert led.state["forks"]["F1"]["invalidation_rationale"]


def test_a_decision_without_a_dispute_is_refused(led):
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    with pytest.raises(Blocked, match="no dispute"):
        led.decide("F1", "sqs", by="agent")


def test_you_cannot_dispute_yourself(led):
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    with pytest.raises(Blocked, match="same actor"):
        led.dispute("F1", challenger="main", author="main", steelman="nats handles the fan-out better")


def test_an_empty_steelman_is_refused(led):
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    with pytest.raises(UsageError, match="--steelman"):
        led.dispute("F1", challenger="reviewer", author="main", steelman="")


def test_an_agent_may_not_decide_irreversibly(led):
    led.open_fork(
        "F1", "data store?", "postgres driver segfaults", "sqlite",
        [
            {"id": "pg", "cost": "3d", "risk": "med", "reversibility": "irreversible"},
            {"id": "sqlite", "cost": "0d", "risk": "low", "reversibility": "reversible"},
        ],
    )
    led.dispute("F1", challenger="reviewer", author="main", steelman="sqlite cannot hold the write volume")
    with pytest.raises(Blocked, match="irreversible decision is outside delegated authority"):
        led.decide("F1", "pg", by="agent", invalidated=["migration plan"])


def test_an_agent_may_not_decide_a_human_anchor(led):
    led.open_fork("F1", "auth?", "the token is in the log", "rotate", fork_options("rotate", "keep"))
    led.dispute("F1", challenger="reviewer", author="main", steelman="keeping it is cheaper this week")
    with pytest.raises(Blocked, match=r"human anchor\(s\) \['security'\]"):
        led.decide("F1", "keep", by="agent", anchors=["security"], invalidated=["log scrubber"])


def test_human_decision_requires_acknowledgement(led):
    led.open_fork("F1", "auth?", "token in the log", "rotate", fork_options("rotate", "keep"))
    led.dispute("F1", challenger="reviewer", author="main", steelman="keeping it is cheaper this week")
    with pytest.raises(Blocked, match="--human-ack"):
        led.decide("F1", "rotate", by="human", anchors=["security"])
    led.decide("F1", "rotate", by="human", human_ack=True, anchors=["security"])
    assert led.state["decisions"]["F1"]["by"] == "human"


def test_departing_from_the_plan_requires_naming_what_it_invalidated(led):
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="reviewer", author="main", steelman="nats needs an operator we lack")
    with pytest.raises(Blocked, match="records nothing invalidated"):
        led.decide("F1", "sqs", by="agent")
    led.decide("F1", "sqs", by="agent", invalidated=["US-004 redis adapter", "AC-2 latency budget"])


def test_staying_on_the_assumed_path_needs_no_invalidation_list(led):
    led.open_fork("F1", "which queue?", "broker gone", "sqs", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="reviewer", author="main", steelman="nats is better but we lack an operator")
    led.decide("F1", "sqs", by="agent")
    assert led.state["decisions"]["F1"]["invalidated"] == []


def test_choosing_an_option_that_does_not_exist_is_an_error(led):
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="reviewer", author="main", steelman="nats is better")
    with pytest.raises(UsageError, match="not an option"):
        led.decide("F1", "kafka", by="agent")


def test_a_fork_cannot_be_decided_twice(led):
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="reviewer", author="main", steelman="nats is better")
    led.decide("F1", "sqs", by="agent", invalidated=["US-004"])
    with pytest.raises(Blocked, match="already decided"):
        led.decide("F1", "nats", by="agent", invalidated=["US-004"])


# --------------------------------------------------------------------------- #
# evidence: three tiers, no tautologies, no self-reproduction
# --------------------------------------------------------------------------- #


def test_an_expectation_computed_from_the_code_is_refused(led):
    with pytest.raises(Blocked, match="passes by construction"):
        led.record_evidence("handles CRLF", "pytest", "executed_here", expected_source="code")


def test_independent_reproduction_needs_a_second_actor(led):
    with pytest.raises(UsageError, match="--reproduced-by"):
        led.record_evidence("handles CRLF", "pytest", "reproduced_independently", recorder="main")
    with pytest.raises(Blocked, match="equals --recorder"):
        led.record_evidence(
            "handles CRLF", "pytest", "reproduced_independently", recorder="main", reproduced_by="main"
        )
    led.record_evidence(
        "handles CRLF", "pytest", "reproduced_independently",
        recorder="main", reproduced_by="reviewer", expected_source="literal",
    )


def test_unavailable_evidence_must_say_why(led):
    with pytest.raises(UsageError, match="--reason"):
        led.record_evidence("handles CRLF", "pytest -k crlf", "unavailable")


def test_an_unknown_tier_is_an_error(led):
    with pytest.raises(UsageError, match="--tier"):
        led.record_evidence("handles CRLF", "pytest", "probably_fine")


# --------------------------------------------------------------------------- #
# final status is derived by precedence, never asserted
# --------------------------------------------------------------------------- #


def settled(led):
    two_refuted(led)
    led.open_hypothesis("H3", INCIDENT, "BOM handling", "stripping the BOM fixes it")
    led.close_hypothesis("H3", "BOM probe", "green", survives=True)
    led.begin_fix(INCIDENT, "binary mode")
    return led


def test_a_clean_run_closes_complete(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here", expected_source="literal")
    led.record_evidence("rejects malformed", "pytest -k malformed", "executed_here", expected_source="spec")
    assert led.outcome() == ("REACHED", [], [])


def test_a_criterion_with_no_evidence_at_all_fails_verification(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here")
    assert led.outcome()[0] == "NOT REACHED"


def test_evidence_that_could_not_be_gathered_never_counts_as_a_pass(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here")
    led.record_evidence("rejects malformed", "pytest -k malformed", "unavailable", reason="no network")
    assert led.outcome()[0] == "NOT REACHED"


def test_an_untested_hypothesis_outranks_an_undecided_fork(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "newline mode", "mode would be 'r'")
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    status, blocking, _ = led.outcome()
    assert status == "NOT REACHED"
    assert any("H1" in r for r in blocking)


def test_an_undecided_fork_blocks_an_otherwise_complete_run(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here")
    led.record_evidence("rejects malformed", "pytest -k malformed", "executed_here")
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    assert led.outcome()[0] == "NOT REACHED"


def test_the_verdict_tracks_the_dispute_not_the_fork_count(led):
    """A converged dispute must not make a clean verdict unreachable."""
    settled(led)
    verified(led)
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="reviewer", author="main", steelman="nats is better but unstaffed")
    led.decide("F1", "sqs", by="agent", invalidated=["US-004"])
    assert led.outcome() == ("REACHED", [], [])


def test_a_blocking_risk_prevents_completion(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here")
    led.record_evidence("rejects malformed", "pytest -k malformed", "executed_here")
    led.record_risk("the public schema changed", blocking=True)
    assert led.outcome()[0] == "NOT REACHED"


def test_the_report_carries_every_section_a_reviewer_needs(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here")
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="reviewer", author="main", steelman="nats handles fan-out natively")
    report = led.report()
    for section in (
        "## Intent", "## Acceptance criteria", "## Work performed", "## Verification",
        "## Hypothesis ledger", "## Challenger findings", "## Conflicts and tradeoffs",
        "## Decay risks", "## Human anchors",
        "## What still stands between the run and the outcome",
        "## How the outcome was reached", "## Final status",
    ):
        assert section in report
    assert "nats handles fan-out natively" in report
    assert "UNDECIDED" in report


# --------------------------------------------------------------------------- #
# the run survives being closed, reopened and resumed
# --------------------------------------------------------------------------- #


def test_state_survives_a_reopen(tmp_path):
    path = tmp_path / "L"
    first = Ledger.create(path, "ship the parser", ["handles CRLF"])
    armed(first)
    first.open_hypothesis("H1", INCIDENT, "newline mode", "mode would be 'r'")
    first.close_hypothesis("H1", "mode probe", "mode is 'r'", survives=True)
    first.classify("still-red", "hypothesis_unresolved")

    resumed = Ledger(path)
    assert resumed.state["hypotheses"]["H1"]["status"] == "surviving"
    assert resumed.state["directive"]["kind"] == "hypothesis_unresolved"
    assert resumed.next_action()["required"] == "hyp_refute"


def test_a_refused_action_leaves_no_trace_on_disk(led):
    armed(led)
    before = (led.root / "hypotheses.jsonl").read_text()
    with pytest.raises(UsageError):
        led.open_hypothesis("H1", INCIDENT, "x", "")
    assert (led.root / "hypotheses.jsonl").read_text() == before
    assert Ledger(led.root).state["hypotheses"] == {}


def test_a_corrupt_log_is_reported_not_silently_ignored(led):
    with (led.root / "control.jsonl").open("a") as handle:
        handle.write('{"ev":"teleport","where":"elsewhere"}\n')
    with pytest.raises(UsageError, match="corrupt ledger"):
        Ledger(led.root)


def test_reinitialising_an_existing_ledger_is_refused(led):
    with pytest.raises(Blocked, match="already initialised"):
        Ledger.create(led.root, "again", [])


def test_next_action_names_the_outstanding_fork_dispute(led):
    settled(led)
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    assert led.next_action()["required"] == "dispute"
    led.dispute("F1", challenger="reviewer", author="main", steelman="nats is better")
    assert led.next_action()["required"] == "decide"


# --------------------------------------------------------------------------- #
# the CLI is the contract callers gate on
# --------------------------------------------------------------------------- #


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "lib" / "convergence.py"), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_cli_exposes_refusals_as_a_nonzero_exit(tmp_path):
    path = tmp_path / "L"
    assert run_cli("init", str(path), "--intent", "x").returncode == 0

    refused = run_cli("open-hypothesis", str(path), "--id", "H1", "--incident", "I",
                      "--claim", "c", "--prediction", "p")
    assert refused.returncode == 3
    assert "BLOCKED" in refused.stderr

    malformed = run_cli("open-hypothesis", str(path), "--id", "H1", "--incident", "I",
                        "--claim", "c", "--prediction", "p", "--rank", "not-a-number")
    assert malformed.returncode == 2


def test_close_writes_the_report_and_fails_loudly_when_unearned(tmp_path):
    path = tmp_path / "L"
    run_cli("init", str(path), "--intent", "ship it", "--criterion", "works")
    result = run_cli("close", str(path))
    assert result.returncode == 3
    assert "NOT REACHED" in result.stdout
    assert (path / "report.md").exists()


def test_work_after_a_park_passes_and_later_unavailable_fails(tmp_path):
    """A criterion's verdict follows its LATEST evidence: an early park
    discharged with executed_here work passes, and work retracted back to
    unavailable must block the close again."""
    path = tmp_path / "L"
    run_cli("init", str(path), "--intent", "ship it", "--criterion", "e2e passes")
    run_cli("record-evidence", str(path), "--criterion", "e2e passes",
            "--command", "find .", "--tier", "unavailable",
            "--reason", "sandbox not startable")
    run_cli("record-evidence", str(path), "--criterion", "e2e passes",
            "--command", "playwright test", "--tier", "executed_here")
    led = Ledger(path)
    assert led.outcome()[0] == "REACHED"
    report = led.report()
    passed = report.split("**Passed**")[1].split("**Failed**")[0]
    # the Unavailable bucket ends at the next section header, not at the report end
    unavailable = report.split("**Unavailable**")[1].split("\n## ")[0]
    assert "e2e passes" in passed, "work after a park must appear under Passed"
    assert "e2e passes" not in unavailable

    run_cli("record-evidence", str(path), "--criterion", "e2e passes",
            "--command", "sandbox gone", "--tier", "unavailable",
            "--reason", "sandbox gone")
    led = Ledger(path)
    assert led.outcome()[0] == "NOT REACHED", (
        "a retraction after work must block the close again"
    )


def test_a_full_run_closes_zero_through_the_cli(tmp_path):
    path = tmp_path / "L"
    run_cli("init", str(path), "--intent", "ship it", "--criterion", "works")
    run_cli("declare-loop", str(path), "--incident", "I", "--command", "pytest",
            "--symptom", "import blows up")
    run_cli("run-loop", str(path), "--incident", "I", "--red", "true")
    run_cli("open-hypothesis", str(path), "--id", "H1", "--incident", "I",
            "--claim", "newline mode", "--prediction", "mode would be 'r'")
    run_cli("refute-hypothesis", str(path), "--id", "H1", "--probe", "p", "--observed", "still red")
    run_cli("open-hypothesis", str(path), "--id", "H2", "--incident", "I",
            "--claim", "missing encoding cookie", "--prediction", "adding it fixes it")
    run_cli("survive-hypothesis", str(path), "--id", "H2", "--probe", "p2", "--observed", "green")
    run_cli("open-hypothesis", str(path), "--id", "H3", "--incident", "I",
            "--claim", "BOM handling", "--prediction", "stripping it fixes it")
    run_cli("merge-hypotheses", str(path), "--into", "H2", "--from", "H3",
            "--note", "same newline mechanism")
    run_cli("begin-fix", str(path), "--incident", "I", "--note", "binary mode")
    run_cli("record-evidence", str(path), "--criterion", "works", "--command", "pytest",
            "--tier", "executed_here", "--expected-source", "literal")
    result = run_cli("close", str(path))
    assert result.returncode == 0
    assert "**REACHED**" in result.stdout
    r = Ledger(path)
    report = r.report()
    assert "## Close conditions" in report
    assert "## Revision transparency" in report
    assert "## Dispute weights" in report
    assert "## Meta: run-fail dataset" in report
    assert "## Requirement notice" in report
    assert "The run is complete. No condition remains open." in report


def test_intake_mode_through_cli(tmp_path):
    out = run_cli("init", str(tmp_path / "L"), "--intent", "ship it",
                  "--criterion", "works", "--intake")
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["shape_ok"] is True
    assert not (tmp_path / "L").exists()


def test_intake_mode_rejects_no_criteria_through_cli(tmp_path):
    out = run_cli("init", str(tmp_path / "L"), "--intent", "ship it", "--intake")
    # intake refusals are IntakeError -> exit 2, and the shape is reported, not guessed
    assert out.returncode == 2, (out.stdout, out.stderr)
    data = json.loads(out.stdout)
    assert data["shape_ok"] is False
    assert not (tmp_path / "L").exists()


def test_close_records_meta_fail_when_not_reached(tmp_path):
    path = tmp_path / "L"
    run_cli("init", str(path), "--intent", "ship it", "--criterion", "works")
    result = run_cli("close", str(path))
    assert result.returncode == 3
    led = Ledger(path)
    assert led.state["meta"]["run_fails"][-1]["failure_kind"] == "not_reached"


def test_freeze_twice_is_refused_through_cli(tmp_path):
    path = tmp_path / "L"
    run_cli("init", str(path), "--intent", "ship it", "--criterion", "works")
    first = run_cli("freeze-intent", str(path))
    assert first.returncode == 0
    second = run_cli("freeze-intent", str(path))
    assert second.returncode == 3
    assert "already frozen" in second.stderr


def test_main_is_importable_for_in_process_gating(tmp_path):
    path = tmp_path / "L"
    assert main(["init", str(path), "--intent", "x"]) == 0
    assert main(["open-hypothesis", str(path), "--id", "H1", "--incident", "I",
                 "--claim", "c", "--prediction", "p"]) == 3


# --------------------------------------------------------------------------- #
# regressions found by driving the CLI, where each command is its own process
# --------------------------------------------------------------------------- #


def test_a_discharged_directive_stays_cleared_across_processes(tmp_path):
    path = tmp_path / "L"
    assert main(["init", str(path), "--intent", "x"]) == 0
    assert main(["classify", str(path), "--signature", "flake", "--kind", "loop_defective"]) == 0
    assert main(["open-hypothesis", str(path), "--id", "H1", "--incident", "I",
                 "--claim", "shared tmpdir", "--prediction", "a per-run dir removes it"]) == 3
    assert main(["declare-loop", str(path), "--incident", "I",
                 "--command", "pytest -k flake", "--symptom", "fails only on CI"]) == 0
    assert main(["run-loop", str(path), "--incident", "I", "--red", "true"]) == 0
    assert main(["open-hypothesis", str(path), "--id", "H1", "--incident", "I",
                 "--claim", "shared tmpdir", "--prediction", "a per-run dir removes it"]) == 0
    # The directive was discharged by the previous process; a new one must not see it.
    assert Ledger(path).state["directive"] is None
    assert main(["refute-hypothesis", str(path), "--id", "H1",
                 "--probe", "per-run TMPDIR", "--observed", "20/20 green"]) == 0
    assert Ledger(path).state["directive"] is None


def test_multi_valued_flags_accept_several_entries_after_one_flag(tmp_path):
    path = tmp_path / "L"
    assert main(["init", str(path), "--intent", "x", "--criterion", "one", "two"]) == 0
    assert Ledger(path).state["criteria"] == ["one", "two"]
    assert main(["open-fork", str(path), "--id", "F1", "--question", "q", "--trigger", "t",
                 "--assumed", "redis",
                 "--option", '{"id":"sqs","cost":"1d","risk":"low","reversibility":"reversible"}',
                 '{"id":"nats","cost":"3d","risk":"med","reversibility":"reversible"}']) == 0
    assert main(["dispute", str(path), "--fork", "F1", "--challenger", "rev",
                 "--author", "main", "--steelman", "nats is better"]) == 0
    assert main(["decide", str(path), "--fork", "F1", "--chosen", "sqs", "--by", "agent",
                 "--invalidated", "US-004", "AC-1", "runbook 3",
                 "--anchor", "security", "privacy"]) == 3
    assert main(["decide", str(path), "--fork", "F1", "--chosen", "sqs", "--by", "human",
                 "--human-ack", "--anchor", "security", "privacy",
                 "--invalidated", "US-004", "AC-1", "runbook 3"]) == 0
    decision = Ledger(path).state["decisions"]["F1"]
    assert decision["invalidated"] == ["US-004", "AC-1", "runbook 3"]
    assert decision["anchors"] == ["security", "privacy"]


def test_evidence_from_a_loop_you_declared_defective_does_not_close(led):
    settled(led)
    verified(led)
    led.classify("test-suite-red", "loop_defective")
    status, blocking, _ = led.outcome()
    assert status == "NOT REACHED"
    assert any("defective" in r for r in blocking)


def test_rebuilding_the_loop_lets_the_run_close(led):
    settled(led)
    verified(led)
    led.classify("test-suite-red", "loop_defective")
    assert led.outcome()[0] == "NOT REACHED"
    led.declare_loop(INCIDENT, "pytest -k crlf --maxfail=1 -x", "the same failure, isolated")
    led.run_loop(INCIDENT, red=True)
    led.open_hypothesis("H4", INCIDENT, "the fixture order hides the second failure",
                        "isolating one test at a time reproduces it")
    led.close_hypothesis("H4", "per-test isolation probe", "reproduced: the 4th fixture alone", survives=True)
    status, blocking, _ = led.outcome()
    assert status == "REACHED", blocking
    assert not any("defective" in r for r in blocking)


def test_a_malformed_option_is_a_refusal_not_a_crash(led):
    for bad in ("sqs", 42, None, ["sqs"]):
        with pytest.raises(UsageError, match="must be a dict"):
            led.open_fork("F1", "q", "t", "redis", [bad])
    assert led.state["forks"] == {}


def test_a_paraphrased_claim_is_refused_not_just_an_exact_copy(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "the null guard is missing", "adding it goes green")
    with pytest.raises(Blocked, match="String equality does not stop"):
        led.open_hypothesis("H2", INCIDENT, "the missing null guard", "removing it brings it back")
    assert "H2" not in led.state["hypotheses"]


def test_genuinely_distinct_mechanisms_still_get_in(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "the file is opened in text mode", "binary mode goes green")
    led.open_hypothesis("H2", INCIDENT, "a dependency upgrade changed the parser", "pinning it restores")
    led.open_hypothesis("H3", INCIDENT, "the clock skews the window boundary", "a frozen clock fixes it")
    assert len(led.state["hypotheses"]) == 3


def test_overruling_the_challenger_needs_a_reason(led):
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="rev", author="main", steelman="nats is better")
    with pytest.raises(UsageError, match="--override-reason"):
        led.decide("F1", "sqs", by="agent", invalidated=["US-1"], challenger_overruled=True)


def test_a_converged_dispute_still_allows_a_clean_complete(led):
    """The verdict must track the work's quality, not how many forks the run hit."""
    settled(led)
    verified(led)
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="rev", author="main", steelman="nats is better, but unstaffed")
    led.decide("F1", "sqs", by="agent", invalidated=["US-1"])
    assert led.outcome() == ("REACHED", [], [])


def test_overruling_the_challenger_costs_the_clean_verdict(led):
    settled(led)
    verified(led)
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="rev", author="main", steelman="nats is measurably better")
    led.decide("F1", "sqs", by="agent", invalidated=["US-1"],
                challenger_overruled=True, override_reason="the counter-example did not reproduce")
    status, blocking, disclosed = led.outcome()
    assert status == "REACHED", blocking
    assert any("objection was overruled" in d for d in disclosed)
    assert "did not reproduce" in " ".join(disclosed)


# --------------------------------------------------------------------------- #
# the right edge: the criteria list moves only through a disputed fork
# --------------------------------------------------------------------------- #


REV_OPTS = [
    {"id": "keep", "cost": "0d", "risk": "low", "reversibility": "reversible"},
    {"id": "revise", "cost": "1d", "risk": "med", "reversibility": "reversible"},
]

OLD_EDGE = "handles CRLF"
NEW_EDGE = "handles CRLF and BOM-prefixed fixtures"


def revision_fork(led):
    led.open_fork(
        "FR", "this criterion turned out to be unreachable as written",
        "the fourth fixture carries a BOM and was never in the original report",
        "keep", criterion_from=OLD_EDGE, criterion_to=NEW_EDGE, options=REV_OPTS,
    )


def test_the_right_edge_cannot_move_without_a_dispute(led):
    revision_fork(led)
    with pytest.raises(Blocked, match="no dispute"):
        led.decide("FR", "revise", by="agent", invalidated=["AC-1"])
    assert OLD_EDGE in led.state["criteria"]


def test_revising_a_criterion_requires_naming_what_it_invalidates(led):
    revision_fork(led)
    led.dispute("FR", challenger="rev", author="main",
                steelman="widening the criterion is how scope quietly shrinks")
    with pytest.raises(Blocked, match="records nothing invalidated"):
        led.decide("FR", "revise", by="agent")
    assert led.state["criteria"] == ["handles CRLF", "rejects malformed"]


def test_a_disputed_revision_moves_the_edge_and_still_has_to_be_verified(led):
    settled(led)
    verified(led)
    revision_fork(led)
    led.dispute("FR", challenger="rev", author="main",
                steelman="widening the criterion is how scope quietly shrinks")
    led.decide("FR", "revise", by="agent", invalidated=["AC-1 the CRLF test"])
    assert OLD_EDGE not in led.state["criteria"]
    assert NEW_EDGE in led.state["criteria"]
    assert led.state["criterion_revisions"][0]["invalidated"] == ["AC-1 the CRLF test"]
    status, blocking, disclosed = led.outcome()
    assert status == "NOT REACHED"
    assert any("never verified" in r for r in blocking), "the new edge starts unverified"
    assert any("right edge was moved" in d for d in disclosed)
    led.record_evidence(NEW_EDGE, "pytest -k crlf", "executed_here", expected_source="literal")
    assert led.outcome() == ("REACHED", [], led.outcome()[2])


def test_keeping_the_edge_closes_the_fork_without_moving_it(led):
    settled(led)
    verified(led)
    revision_fork(led)
    led.dispute("FR", challenger="rev", author="main", steelman="but the BOM is real")
    led.decide("FR", "keep", by="agent")
    assert led.state["criteria"] == ["handles CRLF", "rejects malformed"]
    assert led.outcome() == ("REACHED", [], [])


def test_a_revision_cannot_name_an_edge_that_does_not_exist(led):
    with pytest.raises(UsageError, match="not a criterion of this run"):
        led.open_fork("FR", "q", "t", "keep",
                      criterion_from="a criterion nobody ever wrote",
                      criterion_to="something else", options=REV_OPTS)


def test_an_edge_cannot_be_moved_onto_an_edge_the_run_already_holds(led):
    with pytest.raises(Blocked, match="already a criterion"):
        led.open_fork("FR", "q", "t", "keep",
                      criterion_from="handles CRLF",
                      criterion_to="rejects malformed", options=REV_OPTS)


# --------------------------------------------------------------------------- #
# several agents, one ledger
# --------------------------------------------------------------------------- #


def test_two_agents_racing_to_record_the_same_hypothesis_cannot_both_win(tmp_path):
    """The padding guard is the whole point of the ledger, and agents run in parallel.

    Each thread is a separate agent that loaded its state before the race started, so
    without a lock every one of them validates against the same pre-write view and every
    one of them lands. Exactly one may win.
    """
    path = tmp_path / "L"
    led = Ledger.create(path, "x", [])
    led.declare_loop(INCIDENT, "pytest -k crlf", "the guard is missing")
    led.run_loop(INCIDENT, red=True)

    workers = 8
    gate = threading.Barrier(workers)

    def race(i):
        other = Ledger(path)
        gate.wait()
        try:
            other.open_hypothesis(f"H{i}", INCIDENT, "the null guard is absent",
                                  "adding it turns the loop green")
            return "won"
        except Blocked:
            return "refused"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(race, range(workers)))
    assert outcomes.count("won") == 1, outcomes
    assert len(Ledger(path).state["hypotheses"]) == 1


def test_concurrent_distinct_work_is_never_lost(tmp_path):
    path = tmp_path / "L"
    words = ["clock", "socket", "kernel", "cursor", "manifest", "padding",
             "locale", "checksum", "timeout", "segment", "vacuum", "trigger"]
    criteria = [f"criterion-{i:02d} is satisfied" for i in range(len(words))]
    led = Ledger.create(path, "x", criteria)
    led.declare_loop(INCIDENT, "pytest -k crlf", "many failures")
    led.run_loop(INCIDENT, red=True)

    gate = threading.Barrier(len(words))

    def worker(i):
        other = Ledger(path)
        gate.wait()
        other.open_hypothesis(f"H{i:02d}", INCIDENT,
                              f"the {words[i]} is left unwritten",
                              f"isolating the {words[i]} path turns the check green")
        other.record_evidence(criteria[i], f"pytest -k e{i}",
                              "executed_here", expected_source="literal")
        return i

    with ThreadPoolExecutor(max_workers=len(words)) as pool:
        list(pool.map(worker, range(len(words))))

    final = Ledger(path)
    assert len(final.state["hypotheses"]) == len(words)
    assert len(final.state["evidence"]) == len(words)


def test_a_contested_fork_is_decided_exactly_once(tmp_path):
    path = tmp_path / "L"
    led = Ledger.create(path, "x", [])
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="rev", author="main", steelman="nats is better")

    workers = 8
    gate = threading.Barrier(workers)

    def race(i):
        other = Ledger(path)
        gate.wait()
        try:
            other.decide("F1", "sqs", by="agent", invalidated=[f"US-{i}"])
            return "won"
        except Blocked:
            return "refused"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(race, range(workers)))
    assert outcomes.count("won") == 1, outcomes
    events = [
        json.loads(line)
        for line in (path / "forks.jsonl").read_text().splitlines()
        if line.strip() and json.loads(line)["ev"] == "decide"
    ]
    assert len(events) == 1, "two decisions landed and one silently overwrote the other"


def test_many_processes_write_without_losing_a_single_event(tmp_path):
    """The lock path that only real OS processes exercise: every run_cli is its own
    process, each re-reading the ledger under flock. Interleaved writes must all
    land, and the derived state must equal a clean replay of the logs."""
    path = tmp_path / "L"
    words = ["clock", "socket", "kernel", "cursor", "manifest", "padding",
             "locale", "checksum", "timeout", "segment", "vacuum", "trigger"]
    criteria = [f"criterion-{i:02d} is satisfied" for i in range(len(words))]
    assert run_cli("init", str(path), "--intent", "x", "--criterion", *criteria).returncode == 0

    def worker(i):
        out = run_cli("record-evidence", str(path), "--criterion", criteria[i],
                      "--command", f"pytest -k e{i}", "--tier", "executed_here",
                      "--expected-source", "literal")
        return out.returncode

    with ThreadPoolExecutor(max_workers=len(words)) as pool:
        codes = list(pool.map(worker, range(len(words))))
    assert codes == [0] * len(words), codes

    final = Ledger(path)
    assert len(final.state["evidence"]) == len(words), "a process's write was lost"
    clean = Ledger(path)
    assert clean.state["criteria"] == criteria


def test_many_processes_racing_on_one_hypothesis_have_exactly_one_winner(tmp_path):
    path = tmp_path / "L"
    assert run_cli("init", str(path), "--intent", "x").returncode == 0
    assert run_cli("declare-loop", str(path), "--incident", INCIDENT,
                   "--command", "pytest", "--symptom", "import blows up").returncode == 0
    assert run_cli("run-loop", str(path), "--incident", INCIDENT,
                   "--red", "true").returncode == 0

    def worker(i):
        out = run_cli("open-hypothesis", str(path), "--id", f"H{i}",
                      "--incident", INCIDENT, "--claim", "the null guard is absent",
                      "--prediction", "adding it turns the loop green")
        return out.returncode

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(worker, range(8)))
    assert codes.count(0) == 1, f"exactly one process may win the id race: {codes}"
    assert len(Ledger(path).state["hypotheses"]) == 1


def test_compact_after_multiprocess_churn_replays_to_the_same_state(tmp_path):
    """Compaction is only honest if the snapshot derives the same state as the
    logs it replaces — checked here after real multi-process interleaving."""
    path = tmp_path / "L"
    criteria = [f"criterion-{i:02d} is satisfied" for i in range(8)]
    assert run_cli("init", str(path), "--intent", "x", "--criterion", *criteria).returncode == 0

    def worker(i):
        return run_cli("record-evidence", str(path), "--criterion", criteria[i],
                       "--command", f"pytest -k e{i}", "--tier", "executed_here",
                       "--expected-source", "literal").returncode

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(worker, range(8)))
    assert codes == [0] * 8, codes

    stale = Ledger(path)
    before = {k: v for k, v in stale.state.items() if k != "events"}
    reopened = stale.compact()
    after = {k: v for k, v in reopened.state.items() if k != "events"}
    assert after == before, "the compacted ledger does not derive the same state"
    assert len(reopened.state["evidence"]) == 8


# --------------------------------------------------------------------------- #
# found by the first real agent to use the skill
# --------------------------------------------------------------------------- #


def test_evidence_against_a_criterion_that_does_not_exist_is_refused(led):
    """A near-miss used to open a second criterion that could never be closed."""
    with pytest.raises(Blocked, match="not a criterion of this run") as exc:
        led.record_evidence("handles crlf", "pytest -k crlf", "executed_here")
    assert "handles CRLF" in str(exc.value), "the refusal should name the real one"
    assert led.state["evidence"] == []


def test_a_single_route_block_needs_to_say_why_and_is_disclosed(led):
    with pytest.raises(Blocked, match="only-route"):
        led.classify("missing-binary", "information_missing",
                     missing_fact="the container runtime binary",
                     smallest_input="the operator installs it",
                     tried=["which docker returned nothing"])
    led.classify("missing-binary", "information_missing",
                 missing_fact="the container runtime binary",
                 smallest_input="the operator installs it",
                 tried=["which docker returned nothing"],
                 only_route="there is no container runtime on this host to try")
    status, blocking, disclosed = led.outcome()
    assert status == "NOT REACHED"
    assert any("missing-binary" in r for r in blocking)
    assert any("no other route" in d for d in disclosed)


def test_two_distinct_routes_still_satisfy_the_block_without_only_route(led):
    led.classify("needs-token", "information_missing",
                 missing_fact="a Vault token",
                 smallest_input="the operator runs vault login",
                 tried=["checked the environment", "asked the secrets endpoint"])
    assert led.state["classifications"][0].get("only_route") is None
    _, _, disclosed = led.outcome()
    assert not any("no other route" in d for d in disclosed)


# --------------------------------------------------------------------------- #
# work is not a fact: the anti-park guard
# --------------------------------------------------------------------------- #


def test_a_smallest_input_describing_local_work_is_refused_as_a_park(led):
    with pytest.raises(Blocked, match="work this session"):
        led.classify(
            "sandbox-e2e-unavailable",
            "information_missing",
            missing_fact="A running Next.js instance serving the generated project",
            smallest_input=(
                "Fix sandbox-server.ts port binding or sync generated files into a "
                "servable directory and start next dev on a free port"
            ),
            tried=["next dev on 4321 refused", "lsof shows no listener"],
            park="C2 F4",
        )
    assert led.state["classifications"] == [], "the park must never reach the ledger"


def test_the_guard_message_names_the_arms_that_do_the_work(led):
    with pytest.raises(Blocked, match="boot-runtime"):
        led.classify("sig", "information_missing",
                     missing_fact="a running server",
                     smallest_input="start npm run dev in the project directory",
                     tried=["tried once", "tried twice"])


def test_only_route_discloses_the_way_out_instead_of_refusing(led):
    led.classify("needs-vault-token", "information_missing",
                 missing_fact="a Vault token",
                 smallest_input="start the vault dev server and read the token",
                 tried=["checked the env", "asked the endpoint"],
                 only_route="requires an operator account we do not hold")
    assert led.state["classifications"][0]["only_route"]


def test_a_delegate_actor_still_parks_legitimately(led):
    earned_block(led)
    assert led.state["classifications"][0]["signature"] == "needs-vault-token"


def test_a_fact_without_an_action_verb_is_never_guarded(led):
    led.classify("needs-quota", "information_missing",
                 missing_fact="paid API quota",
                 smallest_input="a billing account with remaining quota",
                 tried=["no key in env", "status endpoint shows zero quota"])
    assert led.state["classifications"][0]["signature"] == "needs-quota"


# --------------------------------------------------------------------------- #
# boot-runtime: a running server instead of a parked path
# --------------------------------------------------------------------------- #


def _port_open(port):
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def test_boot_runtime_picks_a_free_port_and_stop_runtime_retires_it(tmp_path):
    import time

    led = Ledger.create(tmp_path / "L", "serve e2e", ["serve the page"])
    led.boot_runtime("e2e-sandbox", "python3 -m http.server {port} --bind 127.0.0.1")
    rt = led.state["infra"]["runtimes"][0]
    assert rt["name"] == "e2e-sandbox" and not rt["stopped"]
    assert rt["url"].startswith("http://127.0.0.1:")
    assert _port_open(rt["port"]), "the listener must actually be up"
    led.stop_runtime("e2e-sandbox")
    assert led.state["infra"]["runtimes"][0]["stopped"]
    for _ in range(30):
        if not _port_open(rt["port"]):
            break
        time.sleep(0.1)
    assert not _port_open(rt["port"]), "stop-runtime must actually kill the server"


def test_stop_runtime_reaps_a_listener_that_respawned_in_a_new_group(tmp_path):
    """next dev may respawn its server child in a fresh process group; the
    recorded pid's killpg then misses it and the port stays bound. stop-runtime
    must notice and reap whatever still listens."""
    import textwrap
    import time

    detached = textwrap.dedent(
        """
        import socket, sys, time
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", int(sys.argv[1])))
        srv.listen(5)
        open(sys.argv[2], "w").write("ready")
        while True:
            time.sleep(1)
        """
    )
    script = tmp_path / "detached_server.py"
    marker = tmp_path / "ready"
    script.write_text(detached)
    # Parent (the "npx" role) spawns the real server detached, then idles.
    cmd = (
        "python3 -c \"import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,{str(script)!r},'{{port}}',"
        f"{str(marker)!r}],start_new_session=True);time.sleep(60)\""
    )
    led = Ledger.create(tmp_path / "L", "serve e2e", ["serve the page"])
    led.boot_runtime("respawner", cmd, timeout=15)
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.1)
    assert marker.exists(), "the detached listener never came up"
    rt = led.state["infra"]["runtimes"][0]
    led.stop_runtime("respawner")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _port_open(rt["port"]):
        time.sleep(0.2)
    assert not _port_open(rt["port"]), (
        "stop-runtime must free the port even when the server lives in another "
        "process group"
    )


def test_boot_runtime_releases_a_parked_block_and_records_listener_evidence(tmp_path):
    led = Ledger.create(tmp_path / "L", "serve e2e", ["serve the page"])
    led.classify("sandbox-e2e-unavailable", "information_missing",
                 missing_fact="a running server",
                 smallest_input="the operator starts the preview server",
                 tried=["no server on 4321", "no server on 4399"],
                 park="F-e2e")
    led.boot_runtime(
        "e2e-sandbox",
        "python3 -m http.server {port} --bind 127.0.0.1",
        resolve_signature="sandbox-e2e-unavailable",
        criterion="serve the page",
    )
    assert any(b["signature"] == "sandbox-e2e-unavailable"
               for b in led.state["resolved_blocks"])
    ev = led.state["evidence"][-1]
    assert ev["criterion"] == "serve the page"
    assert ev["tier"] == "executed_here"
    assert ev["expected_source"] == "literal"


def test_boot_runtime_refuses_an_already_bound_port(tmp_path):
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        bound = sock.getsockname()[1]
        sock.listen(1)
        led = Ledger.create(tmp_path / "L", "serve e2e", ["serve the page"])
        with pytest.raises(Blocked, match="already in use"):
            led.boot_runtime(
                "e2e-sandbox",
                "python3 -m http.server {port} --bind 127.0.0.1",
                port=bound,
            )
    assert led.state.get("infra", {}).get("runtimes", []) == []


def test_boot_runtime_demands_a_port_placeholder(tmp_path):
    led = Ledger.create(tmp_path / "L", "serve e2e", ["serve the page"])
    with pytest.raises(UsageError, match=r"\{port\}"):
        led.boot_runtime("e2e-sandbox", "python3 -m http.server 8080")


def test_boot_runtime_times_out_when_nothing_listens_and_cleans_up(tmp_path):
    led = Ledger.create(tmp_path / "L", "serve e2e", ["serve the page"])
    with pytest.raises(Blocked, match="did not accept connections"):
        led.boot_runtime(
            "e2e-sandbox",
            "sleep 30  # would listen on {port}",
            timeout=2,
        )
    assert led.state.get("infra", {}).get("runtimes", []) == [], "a failed boot must not record a runtime"
    log = tmp_path / "L" / "runtimes" / "e2e-sandbox.log"
    assert log.exists(), "the failure log must survive for diagnosis"


def test_validate_infra_checks_a_running_runtime_and_ignores_a_stopped_one(tmp_path):
    led = Ledger.create(tmp_path / "L", "serve e2e", ["serve the page"])
    led.boot_runtime("e2e-sandbox", "python3 -m http.server {port} --bind 127.0.0.1")
    result = led.validate_infra()
    runtime_checks = [c for c in result["checks"] if c["type"] == "runtime"]
    assert runtime_checks and runtime_checks[0]["ok"]
    led.stop_runtime("e2e-sandbox")
    result = led.validate_infra()
    assert result["ready"], "a stopped runtime is not a missing prerequisite"
    assert not [c for c in result["checks"] if c["type"] == "runtime"]


# --------------------------------------------------------------------------- #
# (10) auto-critique: the report names the run's own weaknesses first
# --------------------------------------------------------------------------- #


def test_auto_critique_calls_out_an_unfalsified_search(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "universal newlines strip CR", "isort will strip the CR")
    led.close_hypothesis("H1", "pytest -k crlf", "looks plausible", survives=True)
    rows = led.auto_critique()
    crit = "\n".join(rows)
    assert "adversarial search: weak" in crit
    assert "0 refuted" in crit


def test_auto_critique_passes_a_search_that_refuted(led):
    two_refuted(led)
    rows = led.auto_critique()
    crit = "\n".join(rows)
    assert "adversarial search: ok" in crit
    assert "1 of 2 hypotheses refuted" in crit


def test_auto_critique_calls_out_untraceable_evidence(led):
    led.record_evidence("handles CRLF", "pytest", "executed_here", expected_source="literal")
    rows = led.auto_critique()
    crit = "\n".join(rows)
    assert "evidence traceability: weak" in crit
    assert "output-ref" in crit


def test_auto_critique_passes_traceable_evidence(led):
    led.record_evidence("handles CRLF", "pytest", "executed_here",
                        expected_source="literal", output_ref="runs/crlf.log")
    crit = "\n".join(led.auto_critique())
    assert "evidence traceability: ok" in crit


def test_auto_critique_calls_out_a_single_shot_verification(led):
    led.record_evidence("handles CRLF", "pytest", "executed_here",
                        expected_source="literal", output_ref="runs/crlf.log")
    crit = "\n".join(led.auto_critique())
    assert "independent reproduction: weak" in crit
    assert "handles CRLF" in crit


def test_auto_critique_calls_out_a_verdictless_dispute(led):
    led.open_fork(
        "F1", "which queue?", "broker gone", "redis",
        [
            {"id": "pg", "cost": "3d", "risk": "med", "reversibility": "reversible"},
            {"id": "sqlite", "cost": "0d", "risk": "low", "reversibility": "reversible"},
        ],
    )
    led.dispute("F1", challenger="reviewer", author="main", steelman="sqlite cannot hold the write volume")
    led.decide("F1", "pg", by="agent", invalidated=["the redis spike"])
    crit = "\n".join(led.auto_critique())
    assert "dispute verdicts: weak" in crit
    assert "F1" in crit


def test_auto_critique_calls_out_overruled_dissent_and_narrow_search(led):
    led.open_fork(
        "F1", "which queue?", "broker gone", "redis",
        [
            {"id": "pg", "cost": "3d", "risk": "med", "reversibility": "reversible"},
            {"id": "sqlite", "cost": "0d", "risk": "low", "reversibility": "reversible"},
        ],
    )
    led.dispute("F1", challenger="reviewer", author="main", steelman="sqlite cannot hold the write volume")
    led.decide("F1", "pg", by="agent", invalidated=["the redis spike"],
               challenger_overruled=True,
               override_reason="the counter-example did not reproduce")
    two_refuted(led)
    led.begin_fix(INCIDENT, "binary mode", search="narrow")
    crit = "\n".join(led.auto_critique())
    assert "overruled dissent: weak" in crit
    assert "search width: weak" in crit


def test_auto_critique_notes_intent_drift(led):
    led.open_fork(
        "F1", "criterion drift", "the BOM fixture matters", "keep",
        [
            {"id": "keep", "cost": "0d", "risk": "low", "reversibility": "reversible"},
            {"id": "revise", "cost": "1d", "risk": "med", "reversibility": "reversible"},
        ],
        criterion_from="handles CRLF",
        criterion_to="handles CRLF and BOM-prefixed fixtures",
        invalidation_rationale="BOM fixtures were never in the original report.",
    )
    led.dispute("F1", challenger="reviewer", author="main", steelman="keep the old line")
    led.decide("F1", "revise", by="agent", invalidated=["AC-1"])
    crit = "\n".join(led.auto_critique())
    assert "intent drift: note" in crit


def test_auto_critique_appears_in_the_report(led):
    report = led.report()
    assert "## Auto-critique" in report
    assert "adversarial search" in report


# --------------------------------------------------------------------------- #
# (4) upper-pipeline routing: one lane to route on, with a fallback
# --------------------------------------------------------------------------- #


def test_route_names_the_investigate_lane_for_an_open_hypothesis(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "universal newlines strip CR", "isort will strip the CR")
    route = led.pipeline_route()
    assert route["lane"] == "investigate"
    assert route["fallback"]["lane"] == "verify"


def test_route_names_the_decide_lane_for_a_disputed_fork(led):
    led.open_fork(
        "F1", "which queue?", "broker gone", "redis",
        [
            {"id": "pg", "cost": "3d", "risk": "med", "reversibility": "reversible"},
            {"id": "sqlite", "cost": "0d", "risk": "low", "reversibility": "reversible"},
        ],
    )
    led.dispute("F1", challenger="reviewer", author="main", steelman="sqlite cannot hold the write volume")
    route = led.pipeline_route()
    assert route["lane"] == "decide"


def test_route_escalates_a_fork_with_no_reversible_option(led):
    led.open_fork(
        "F1", "drop the table or not?", "schema drift", "keep",
        [
            {"id": "drop", "cost": "0d", "risk": "high", "reversibility": "irreversible"},
            {"id": "archive", "cost": "2d", "risk": "med", "reversibility": "irreversible"},
        ],
    )
    led.dispute("F1", challenger="reviewer", author="main", steelman="archiving costs downtime too")
    route = led.pipeline_route()
    assert route["lane"] == "human"
    assert "irreversible" in route["because"]
    # the run still has a fallback lane instead of stalling on the escalation
    assert route["fallback"]["lane"] == "verify"


def test_route_parks_to_unblock_with_a_verify_fallback(led):
    led.classify(
        "flake-1", "information_missing",
        missing_fact="the staging database password",
        smallest_input="the password pasted by the operator",
        tried=["read the vault", "asked in #ops"],
        park="staging is behind an operator-only credential",
    )
    route = led.pipeline_route()
    assert route["lane"] == "unblock"
    assert route["fallback"]["lane"] == "verify"
    assert "work, not a stop" in route["fallback"]["because"]


def test_route_says_close_when_the_outcome_is_reached(led):
    two_refuted(led)
    led.open_hypothesis("H3", INCIDENT, "the parser drops the BOM", "stripping the BOM fixes it")
    led.close_hypothesis("H3", "BOM-strip probe", "went green", survives=True)
    led.begin_fix(INCIDENT, "binary mode")
    led.record_evidence("handles CRLF", "pytest", "executed_here",
                        expected_source="literal", output_ref="runs/crlf.log")
    led.record_evidence("rejects malformed", "pytest", "executed_here",
                        expected_source="literal", output_ref="runs/malformed.log")
    route = led.pipeline_route()
    assert route["lane"] == "close"
    assert route["fallback"] is None


def test_route_through_the_cli_prints_json(tmp_path):
    path = tmp_path / "L"
    run_cli("init", str(path), "--intent", "ship it", "--criterion", "works")
    out = run_cli("route", str(path))
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["lane"] == "verify"
    assert data["fallback"]["lane"] == "close"
    assert "action" in data and "because" in data


# --------------------------------------------------------------------------- #
# spec integration: tickets map onto criteria, merging != earning
# --------------------------------------------------------------------------- #


def write_ticket(dirpath, number, title, criteria, blocked_by="None (can start immediately)",
                 what="the end-to-end behaviour"):
    dirpath.mkdir(parents=True, exist_ok=True)
    body = (f"# {number}: {title}\n\n"
            f"**What to build:** {what}\n\n"
            f"**Blocked by:** {blocked_by}\n\n"
            f"**Status:** ready-for-agent\n\n"
            + "\n".join(f"- [ ] {c}" for c in criteria) + "\n")
    (dirpath / f"{number}-{title.lower().replace(' ', '-')}.md").write_text(body)


def test_import_tickets_maps_criteria_and_keeps_boundaries(tmp_path):
    tickets = tmp_path / "issues"
    write_ticket(tickets, "01", "Parser reads CRLF", ["import survives CRLF input"])
    write_ticket(tickets, "02", "Reject malformed", ["malformed input raises", "error names the line"],
                 blocked_by="01")
    led = Ledger.create(tmp_path / "L", "ship the parser", ["shipped"])
    led.import_tickets(tickets)
    assert [t["id"] for t in led.state["tickets"]] == ["01", "02"]
    assert "import survives CRLF input" in led.state["criteria"]
    assert "error names the line" in led.state["criteria"]
    assert led.state["tickets"][1]["blocked_by"] == ["01"]
    assert led.state["tickets"][0]["status"] == "ready-for-agent"


def test_ticket_with_no_acceptance_criterion_is_refused_at_intake(tmp_path):
    tickets = tmp_path / "issues"
    write_ticket(tickets, "01", "Vague ticket", [])
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    with pytest.raises(IntakeError, match="no acceptance criterion"):
        led.import_tickets(tickets)
    assert led.state["tickets"] == []


def test_ticket_with_unverifiable_criterion_is_refused(tmp_path):
    tickets = tmp_path / "issues"
    write_ticket(tickets, "01", "Short checks", ["ok"])
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    with pytest.raises(IntakeError, match="shorter than 4"):
        led.import_tickets(tickets)


def test_dangling_blocked_by_edge_is_refused(tmp_path):
    tickets = tmp_path / "issues"
    write_ticket(tickets, "01", "Real ticket", ["real criterion"], blocked_by="02")
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    with pytest.raises(UsageError, match="unknown ticket"):
        led.import_tickets(tickets)


def test_duplicated_criterion_across_tickets_is_refused(tmp_path):
    tickets = tmp_path / "issues"
    write_ticket(tickets, "01", "First", ["shared criterion"])
    write_ticket(tickets, "02", "Second", ["shared criterion"])
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    with pytest.raises(UsageError, match="exactly one ticket"):
        led.import_tickets(tickets)


def test_merging_a_ticket_without_evidence_still_blocks_the_close(tmp_path):
    """The point of the mapping: a ticket merged upstream is not earned here.
    Its criteria sit in the flat list, so close refuses until evidence lands."""
    tickets = tmp_path / "issues"
    write_ticket(tickets, "01", "Parser reads CRLF", ["import survives CRLF input"])
    led = Ledger.create(tmp_path / "L", "ship the parser", ["shipped"])
    led.import_tickets(tickets)
    led.record_evidence("shipped", "pytest", "executed_here",
                        expected_source="literal", output_ref="runs/ship.log")
    status, blocking, _ = led.outcome()
    assert status == "NOT REACHED"
    assert any("import survives CRLF input" in b for b in blocking)


def test_ticket_provenance_separates_earned_from_merged(tmp_path):
    tickets = tmp_path / "issues"
    write_ticket(tickets, "01", "Earned ticket", ["criterion a proven"])
    write_ticket(tickets, "02", "Merged only", ["criterion b proven"])
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    led.import_tickets(tickets)
    led.record_evidence("criterion a proven", "pytest", "executed_here",
                        expected_source="literal", output_ref="runs/a.log")
    report = led.report()
    prov = report.split("## Ticket provenance")[1].split("\n## ")[0]
    assert "earned" in prov and "01" in prov
    assert "merged only" in prov and "02" in prov
    assert "criterion b proven" in prov, "the unproven criterion must be named"


def test_import_tickets_through_the_cli(tmp_path):
    tickets = tmp_path / "issues"
    write_ticket(tickets, "01", "Parser reads CRLF", ["import survives CRLF input"])
    path = tmp_path / "L"
    run_cli("init", str(path), "--intent", "ship it", "--criterion", "works")
    out = run_cli("import-tickets", str(path), "--tickets", str(tickets))
    assert out.returncode == 0, out.stderr
    assert "ticket 01" in out.stdout
    assert "import survives CRLF input" in Ledger(path).state["criteria"]


def test_import_tickets_cli_refuses_a_criterionless_ticket(tmp_path):
    tickets = tmp_path / "issues"
    write_ticket(tickets, "01", "Vague ticket", [])
    path = tmp_path / "L"
    run_cli("init", str(path), "--intent", "ship it", "--criterion", "works")
    out = run_cli("import-tickets", str(path), "--tickets", str(tickets))
    assert out.returncode == 2
    assert "INTAKE" in out.stderr
    assert Ledger(path).state["tickets"] == []


# --- real-tracker exports: native ids and the issue template ---------------- #


def write_issue_ticket(dirpath, filename, title, criteria, blocked_by=None,
                       what="the end-to-end behaviour"):
    """The <issue-template> shape used on real trackers: ## sections, native ids."""
    dirpath.mkdir(parents=True, exist_ok=True)
    body = (f"# {title}\n\n"
            f"## What to build\n\n{what}\n\n"
            f"## Acceptance criteria\n\n"
            + "\n".join(f"- [ ] {c}" for c in criteria) + "\n\n")
    if blocked_by is not None:
        body += "## Blocked by\n\n" + "\n".join(f"- {b}" for b in blocked_by) + "\n"
    (dirpath / filename).write_text(body)


def test_native_ids_from_filenames_and_headings(tmp_path):
    tickets = tmp_path / "issues"
    write_issue_ticket(tickets, "ENG-123.md", "Parser reads CRLF", ["import survives CRLF input"])
    write_issue_ticket(tickets, "7.md", "Reject malformed", ["malformed input raises"],
                       blocked_by=["None (can start immediately)"])
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    led.import_tickets(tickets)
    assert [t["id"] for t in led.state["tickets"]] == ["7", "ENG-123"]
    assert led.state["tickets"][1]["id"] == "ENG-123"


def test_issue_template_sections_are_parsed(tmp_path):
    tickets = tmp_path / "issues"
    write_issue_ticket(tickets, "ENG-1.md", "Full template", ["criterion one works"],
                       blocked_by=["None (can start immediately)"], what="a complete slice")
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    led.import_tickets(tickets)
    t = led.state["tickets"][0]
    assert t["what_to_build"] == "a complete slice"
    assert t["criteria"] == ["criterion one works"]
    assert t["blocked_by"] == []


def test_blocked_by_accepts_native_keys_urls_and_padding_insensitive_numbers(tmp_path):
    tickets = tmp_path / "issues"
    write_issue_ticket(tickets, "01-base.md", "Base ticket", ["base criterion works"])
    write_issue_ticket(tickets, "ENG-2.md", "Follow-up", ["follow-up criterion works"],
                       blocked_by=["#1", "ENG-3",
                                   "https://tracker.example/issues/9"])
    write_issue_ticket(tickets, "ENG-3.md", "Third", ["third criterion works"])
    write_issue_ticket(tickets, "9.md", "URL target", ["url target criterion works"])
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    led.import_tickets(tickets)
    follow = next(t for t in led.state["tickets"] if t["id"] == "ENG-2")
    # '#1' finds '01' by value, 'ENG-3' exactly, the URL yields '9'
    assert follow["blocked_by"] == ["01", "ENG-3", "9"]


def test_dangling_native_key_edge_is_refused(tmp_path):
    tickets = tmp_path / "issues"
    write_issue_ticket(tickets, "ENG-1.md", "Orphan", ["orphan criterion works"],
                       blocked_by=["ENG-999"])
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    with pytest.raises(UsageError, match="unknown ticket"):
        led.import_tickets(tickets)


def test_heading_without_id_falls_back_to_filename(tmp_path):
    tickets = tmp_path / "issues"
    write_issue_ticket(tickets, "ENG-42.md", "Title only", ["title-only criterion works"])
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    led.import_tickets(tickets)
    assert led.state["tickets"][0]["id"] == "ENG-42"
    assert led.state["tickets"][0]["title"] == "Title only"


def test_filename_without_id_and_heading_without_id_is_refused(tmp_path):
    tickets = tmp_path / "issues"
    write_issue_ticket(tickets, "no-id-here.md", "Anonymous", ["anonymous criterion works"])
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    with pytest.raises(UsageError, match="does not look like one"):
        led.import_tickets(tickets)


def test_provenance_formats_native_ids(tmp_path):
    tickets = tmp_path / "issues"
    write_issue_ticket(tickets, "ENG-7.md", "Native provenance", ["native criterion works"])
    led = Ledger.create(tmp_path / "L", "ship", ["shipped"])
    led.import_tickets(tickets)
    prov = led.report().split("## Ticket provenance")[1].split("\n## ")[0]
    assert "ticket ENG-7" in prov


# --------------------------------------------------------------------------- #
# prompt intake: every prompt shape, no argv limits, nothing lost
# --------------------------------------------------------------------------- #

from prompt import criteria_from_text, parse_prompt  # noqa: E402


def test_a_complex_multisection_prompt_decomposes_without_loss():
    """A very complex prompt: sections, mixed list syntax, a Goal heading,
    requirement sentences after the last section. Every stated check lands as a
    criterion with provenance; specs and exclusions are reported, not promoted."""
    prompt = """# Payments retry engine

## Goal
ship a retry engine that never drops a payment

## Acceptance criteria
- [ ] failed payments retry with exponential backoff
- [x] retries are capped at five attempts
1. a dead payment raises an alert

## Risks
- the payment provider may rate-limit us

## Out of scope
- refunds

Retries must not double-charge a card.
Ensure the retry state survives a restart.
"""
    shape = parse_prompt(prompt)
    assert shape["intent"] == "ship a retry engine that never drops a payment"
    texts = [c["text"] for c in shape["criteria"]]
    assert texts == [
        "failed payments retry with exponential backoff",
        "retries are capped at five attempts",
        "a dead payment raises an alert",
        "Retries must not double-charge a card.",
        "the retry state survives a restart.",
    ]
    # provenance says which rule extracted each check
    assert shape["criteria"][0]["source"] == "section:Acceptance criteria"
    assert shape["criteria"][3]["source"] == "requirement-sentence"
    # hypotheses are candidates for open-hypothesis, not criteria
    assert [h["text"] for h in shape["hypotheses"]] == [
        "the payment provider may rate-limit us"]
    assert shape["exclusions"] == ["refunds"]


def test_goal_marker_forms_are_all_recognised():
    assert parse_prompt("## Goal: ship the parser\n\n- [ ] handles CRLF\n")["intent"] \
        == "ship the parser"
    assert parse_prompt("Goal: ship the parser\n\nThe parser must handle BOM.\n")["intent"] \
        == "ship the parser"
    assert parse_prompt("## Obiettivo\nspedire il parser\n\n- [ ] il parser gira\n")["intent"] \
        == "spedire il parser"


def test_negated_requirements_are_criteria_not_exclusions():
    shape = parse_prompt("Ship it.\n\nThe gateway must not leak the API key.")
    assert "The gateway must not leak the API key." in [c["text"] for c in shape["criteria"]]


def test_an_imperative_only_prompt_is_refused_at_intake():
    """Task instructions are not checks; the refusal names the missing question."""
    with pytest.raises(IntakeError, match="names no check"):
        parse_prompt("Build a REST API. Make it fast.")


def test_a_one_sentence_prompt_is_both_intent_and_criterion():
    shape = parse_prompt("The parser handles BOM-prefixed files.")
    assert shape["intent"] == "The parser handles BOM-prefixed files."
    assert [c["text"] for c in shape["criteria"]] == ["The parser handles BOM-prefixed files."]


def test_bare_bullets_promote_to_criteria():
    shape = parse_prompt("- handles CRLF\n- rejects malformed input with a clear error")
    assert [c["text"] for c in shape["criteria"]] == [
        "handles CRLF", "rejects malformed input with a clear error"]


def test_duplicated_criteria_collapse_but_others_survive():
    shape = parse_prompt(
        "# Ship the auth flow\n\n## Acceptance criteria\n- [ ] the login works\n"
        "- [ ] the login works\n- [ ] the logout works\n")
    assert [c["text"] for c in shape["criteria"]] == ["the login works", "the logout works"]


def test_an_unnameable_criterion_is_refused_not_silently_kept():
    with pytest.raises(IntakeError, match="shorter than 4"):
        parse_prompt("# Ship it\n\n## Acceptance criteria\n- [ ] ok\n")


def test_a_very_large_prompt_is_decomposed_whole():
    """300 criteria, no truncation: prompts are read whole from a file."""
    prompt = "# Big\n\n## Acceptance criteria\n" + "\n".join(
        f"- [ ] criterion number {i:03d} works" for i in range(300))
    shape = parse_prompt(prompt)
    assert len(shape["criteria"]) == 300
    assert shape["criteria"][0]["text"] == "criterion number 000 works"
    assert shape["criteria"][-1]["text"] == "criterion number 299 works"


def test_two_unindented_lines_in_a_section_are_two_criteria_not_one():
    """Section items fold only when indented: two consecutive plain lines are
    two checks. (Body prose still folds and is sentence-split afterwards.)"""
    shape = parse_prompt(
        "# Billing\n\n## Requirements\n"
        "Il totale deve coincidere con la somma delle righe.\n"
        "Ensure the invoice PDF renders correctly.\n")
    assert [c["text"] for c in shape["criteria"]] == [
        "Il totale deve coincidere con la somma delle righe.",
        "Ensure the invoice PDF renders correctly.",
    ]


def test_criteria_file_unwraps_bullets_checkboxes_and_continuations():
    items = criteria_from_text(
        "# Reqs\n\n- [ ] alpha check here\n* beta check there\n"
        "gamma check continues\n  on the next line\n")
    assert [c["text"] for c in items] == [
        "alpha check here", "beta check there", "gamma check continues on the next line"]


def test_init_from_prompt_file_creates_the_whole_magnet(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("## Goal: ship the parser\n\n## Acceptance criteria\n"
                           "- [ ] handles CRLF\n- [ ] rejects malformed\n")
    path = tmp_path / "L"
    out = run_cli("init", str(path), "--prompt-file", str(prompt_file))
    assert out.returncode == 0, out.stderr
    shape = json.loads(out.stdout)
    assert shape["shape_ok"] is True
    assert shape["intent"] == "ship the parser"
    state = Ledger(path).state
    assert state["criteria"] == ["handles CRLF", "rejects malformed"]


def test_prompt_criteria_still_gate_the_close(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("## Goal: ship the parser\n\n## Acceptance criteria\n"
                           "- [ ] handles CRLF\n")
    path = tmp_path / "L"
    run_cli("init", str(path), "--prompt-file", str(prompt_file))
    assert run_cli("close", str(path)).returncode == 3
    run_cli("record-evidence", str(path), "--criterion", "handles CRLF",
            "--command", "pytest -k crlf", "--tier", "executed_here",
            "--expected-source", "literal")
    assert run_cli("close", str(path)).returncode == 0


def test_intake_mode_previews_the_decomposition_and_writes_nothing(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("## Goal: ship it\n\n## Acceptance criteria\n- [ ] the thing works\n\n"
                           "## Risks\n- flaky network\n")
    path = tmp_path / "L"
    out = run_cli("init", str(path), "--prompt-file", str(prompt_file), "--intake")
    assert out.returncode == 0, out.stderr
    shape = json.loads(out.stdout)
    assert shape["shape_ok"] is True
    assert shape["criteria_provenance"][0]["text"] == "the thing works"
    assert shape["hypotheses"][0]["text"] == "flaky network"
    assert not path.exists()


def test_intent_file_accepts_a_huge_multiline_unicode_intent(tmp_path):
    """No argv limits: a 100 KB multiline intent with quotes and unicode
    round-trips through init untouched."""
    intent = "ship the parser — “quoted”\nsecond line\n" + "x" * 100_000
    intent_file = tmp_path / "intent.txt"
    intent_file.write_text(intent)
    path = tmp_path / "L"
    out = run_cli("init", str(path), "--intent-file", str(intent_file),
                  "--criterion", "works")
    assert out.returncode == 0, out.stderr
    assert Ledger(path).state["intent"] == intent


def test_criteria_file_merges_with_flag_criteria_without_duplicates(tmp_path):
    criteria_file = tmp_path / "criteria.txt"
    criteria_file.write_text("- handles CRLF\nhandles CRLF\nrejects malformed\n")
    path = tmp_path / "L"
    out = run_cli("init", str(path), "--intent", "ship it",
                  "--criteria-file", str(criteria_file),
                  "--criterion", "rejects malformed", "e2e passes")
    assert out.returncode == 0, out.stderr
    assert Ledger(path).state["criteria"] == ["handles CRLF", "rejects malformed", "e2e passes"]


def test_intent_sources_are_mutually_exclusive(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("## Acceptance criteria\n- [ ] the thing works\n")
    out = run_cli("init", str(tmp_path / "L"), "--intent", "x",
                  "--prompt-file", str(prompt_file))
    assert out.returncode == 2
    out = run_cli("init", str(tmp_path / "L"))
    assert out.returncode == 2


def test_prompt_file_reads_stdin_with_dash(tmp_path):
    prompt = "## Goal: ship it\n\n## Acceptance criteria\n- [ ] the thing works\n"
    out = subprocess.run(
        [sys.executable, str(ROOT / "lib" / "convergence.py"),
         "init", str(tmp_path / "L"), "--prompt-file", "-"],
        capture_output=True, text=True, input=prompt, timeout=60)
    assert out.returncode == 0, out.stderr
    assert Ledger(tmp_path / "L").state["criteria"] == ["the thing works"]


def test_prompt_file_missing_is_a_usage_error(tmp_path):
    out = run_cli("init", str(tmp_path / "L"), "--prompt-file", str(tmp_path / "nope.md"))
    assert out.returncode == 2
    assert "ERROR" in out.stderr


def test_bom_prefixed_prompt_imports_without_smuggling_the_bom(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("\ufeff## Goal: ship it\n\n## Acceptance criteria\n"
                           "- [ ] the thing works\n", encoding="utf-8")
    path = tmp_path / "L"
    out = run_cli("init", str(path), "--prompt-file", str(prompt_file))
    assert out.returncode == 0, out.stderr
    assert Ledger(path).state["intent"] == "ship it"
