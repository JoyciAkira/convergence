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
    with pytest.raises(Blocked, match=f"at least {MIN_HYPOTHESES}"):
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
    with pytest.raises(Blocked, match="no-op"):
        led.merge_hypotheses("H1", "H1", "same thing")


def test_supplying_the_missing_fact_clears_the_block(led):
    led.classify(
        "needs-vault-token",
        "information_missing",
        missing_fact="a Vault token",
        smallest_input="vault login",
    )
    assert led.outcome()[0] == "BLOCKED"
    led.resolve_block("needs-vault-token", "operator ran `vault login`; token in env")
    verified(led)
    assert led.outcome() == ("COMPLETE", [])


def test_a_criterion_left_unverified_by_a_block_is_blocked_not_failed(led):
    led.classify("needs-vault-token", "information_missing",
                 missing_fact="a Vault token", smallest_input="vault login")
    status, reasons = led.outcome()
    assert status == "BLOCKED"
    assert not any("never checked" in r for r in reasons)
    assert any("left unverified by the block" in r for r in reasons)


def test_a_criterion_never_checked_with_nothing_blocking_is_a_failure(led):
    status, reasons = led.outcome()
    assert status == "FAILED VERIFICATION"
    assert any("never checked" in r for r in reasons)


def test_a_resolved_block_stays_visible_in_the_report(led):
    led.classify("needs-vault-token", "information_missing",
                 missing_fact="a Vault token", smallest_input="vault login")
    led.resolve_block("needs-vault-token", "token supplied by the operator")
    verified(led)
    report = led.report()
    assert "needs-vault-token" in report
    assert "(resolved)" in report


def test_hypothesis_unresolved_forbids_starting_over(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "shared tmpdir", "a per-run dir removes the flake")
    led.classify("flake-on-ci", "hypothesis_unresolved")
    with pytest.raises(Blocked, match="untested hypothesis blocks"):
        led.open_hypothesis("H2", INCIDENT, "clock skew", "freezing the clock fixes it")
    led.close_hypothesis("H1", "per-run dir probe", "went green", survives=True)
    assert led.state["directive"] is None


def test_information_missing_must_name_the_fact_and_the_input(led):
    with pytest.raises(UsageError, match="--missing-fact"):
        led.classify("needs-vault-token", "information_missing", smallest_input="export VAULT_TOKEN")
    with pytest.raises(UsageError, match="--smallest-input"):
        led.classify("needs-vault-token", "information_missing", missing_fact="a Vault token")
    led.classify(
        "needs-vault-token",
        "information_missing",
        missing_fact="a Vault token with read access",
        smallest_input="the operator running `vault login`",
    )
    status, reasons = led.outcome()
    assert status == "BLOCKED"
    assert any("needs-vault-token" in r for r in reasons)




# --------------------------------------------------------------------------- #
# entry B: forks, disputes and delegated authority
# --------------------------------------------------------------------------- #


def fork_options(*ids):
    return [
        {"id": i, "cost": "1d", "risk": "low", "reversibility": "reversible"} for i in ids
    ]


def test_a_fork_needs_options(led):
    with pytest.raises(UsageError, match="at least one --option"):
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
    assert led.outcome() == ("COMPLETE", [])


def test_a_criterion_with_no_evidence_at_all_fails_verification(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here")
    assert led.outcome()[0] == "FAILED VERIFICATION"


def test_evidence_that_could_not_be_gathered_never_counts_as_a_pass(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here")
    led.record_evidence("rejects malformed", "pytest -k malformed", "unavailable", reason="no network")
    assert led.outcome()[0] == "COMPLETE WITH RISKS"


def test_an_untested_hypothesis_outranks_an_undecided_fork(led):
    armed(led)
    led.open_hypothesis("H1", INCIDENT, "newline mode", "mode would be 'r'")
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    status, reasons = led.outcome()
    assert status == "FAILED VERIFICATION"
    assert any("H1" in r for r in reasons)


def test_an_undecided_fork_blocks_an_otherwise_complete_run(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here")
    led.record_evidence("rejects malformed", "pytest -k malformed", "executed_here")
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    assert led.outcome()[0] == "BLOCKED"


def test_an_agent_decided_fork_downgrades_the_final_verdict(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here")
    led.record_evidence("rejects malformed", "pytest -k malformed", "executed_here")
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="reviewer", author="main", steelman="nats is better but unstaffed")
    led.decide("F1", "sqs", by="agent", invalidated=["US-004"])
    status, reasons = led.outcome()
    assert status == "COMPLETE WITH RISKS"
    assert any("F1" in r for r in reasons)


def test_a_blocking_risk_prevents_completion(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here")
    led.record_evidence("rejects malformed", "pytest -k malformed", "executed_here")
    led.record_risk("the public schema changed", blocking=True)
    assert led.outcome()[0] == "BLOCKED"


def test_the_report_carries_every_section_a_reviewer_needs(led):
    settled(led)
    led.record_evidence("handles CRLF", "pytest -k crlf", "executed_here")
    led.open_fork("F1", "which queue?", "broker gone", "redis", fork_options("sqs", "nats"))
    led.dispute("F1", challenger="reviewer", author="main", steelman="nats handles fan-out natively")
    report = led.report()
    for section in (
        "## Intent", "## Acceptance criteria", "## Work performed", "## Verification",
        "## Hypothesis ledger", "## Challenger findings", "## Conflicts and tradeoffs",
        "## Decay risks", "## Human anchors", "## Residual uncertainty", "## Final status",
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
    assert "FAILED VERIFICATION" in result.stdout
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
    assert "**COMPLETE**" in result.stdout


def test_main_is_importable_for_in_process_gating(tmp_path):
    path = tmp_path / "L"
    assert main(["init", str(path), "--intent", "x"]) == 0
    assert main(["open-hypothesis", str(path), "--id", "H1", "--incident", "I",
                 "--claim", "c", "--prediction", "p"]) == 3
