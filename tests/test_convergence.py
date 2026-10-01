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
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from convergence import (  # noqa: E402
    MIN_HYPOTHESES,
    Blocked,
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
