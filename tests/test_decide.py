"""Entry B: forks, disputes and delegated authority.

The right edge may move only through a disputed, decided fork; these tests pin
each refusal on that path and the event it writes when the move is legitimate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from convergence import Blocked, Ledger, UsageError  # noqa: E402

OPTIONS = [
    {"id": "a", "cost": "low", "risk": "low", "reversibility": "reversible"},
    {"id": "b", "cost": "high", "risk": "low", "reversibility": "reversible"},
]


@pytest.fixture
def led(tmp_path):
    return Ledger.create(
        tmp_path / "L", "ship the parser", ["handles CRLF", "rejects malformed"]
    )


def test_moving_the_edge_requires_a_criterion_the_run_holds(led):
    with pytest.raises(UsageError, match="not a criterion"):
        led.open_fork(
            "F1", "widen the edge?", "a new compliance requirement", "a",
            OPTIONS, criterion_from="ghost criterion", criterion_to="also handles BOM",
        )


def test_the_edge_cannot_move_onto_a_criterion_already_held(led):
    with pytest.raises(Blocked, match="already a criterion"):
        led.open_fork(
            "F1", "widen the edge?", "a new compliance requirement", "a",
            OPTIONS, criterion_from="handles CRLF", criterion_to="rejects malformed",
        )


def test_a_fork_needs_options(led):
    with pytest.raises(UsageError, match="at least one option"):
        led.open_fork("F1", "which db?", "scale", "a", [])


def test_a_single_option_fork_must_name_what_it_ruled_out(led):
    with pytest.raises(Blocked, match="single-option fork"):
        led.open_fork("F1", "which db?", "scale", "a", [OPTIONS[0]])


def test_an_unknown_reversibility_is_refused(led):
    with pytest.raises(UsageError, match="reversibility must be one of"):
        led.open_fork(
            "F1", "which db?", "scale", "a",
            [{"id": "a", "cost": "low", "risk": "low", "reversibility": "maybe"}],
        )


def test_duplicate_option_ids_are_refused(led):
    with pytest.raises(Blocked, match="duplicate option id"):
        led.open_fork("F1", "which db?", "scale", "a", [OPTIONS[0], dict(OPTIONS[0])])


def test_disputing_an_unknown_fork_is_a_usage_error(led):
    with pytest.raises(UsageError, match="unknown fork"):
        led.dispute("GHOST", "reviewer", "author", "the cost argument holds")


def test_deciding_an_unknown_fork_is_a_usage_error(led):
    with pytest.raises(UsageError, match="unknown fork"):
        led.decide("GHOST", "a", "agent")


def test_an_edge_move_must_be_decided_as_revise_or_keep(led):
    led.open_fork(
        "F1", "widen the edge?", "a new requirement", "a", OPTIONS,
        criterion_from="handles CRLF", criterion_to="handles CRLF and BOM",
    )
    led.dispute("F1", "reviewer", "author", "BOM files occur in the wild")
    with pytest.raises(UsageError, match="must be decided as"):
        led.decide("F1", "a", "agent")
    assert led.state["decisions"] == {}


def test_a_dispute_needs_a_separate_challenger(led):
    led.open_fork("F1", "which db?", "scale", "a", OPTIONS)
    with pytest.raises(Blocked, match="same actor"):
        led.dispute("F1", "me", "me", "the cost argument still holds")


def test_a_decision_without_a_dispute_is_an_assertion(led):
    led.open_fork("F1", "which db?", "scale", "a", OPTIONS)
    with pytest.raises(Blocked, match="no dispute"):
        led.decide("F1", "a", "agent")


def test_explicit_reversibility_is_validated_and_recorded(led):
    led.open_fork("F1", "which db?", "scale", "a", OPTIONS)
    led.dispute("F1", "reviewer", "author", "the cost argument still holds")
    led.decide("F1", "a", "agent", reversibility="reversible")
    assert led.state["decisions"]["F1"]["reversibility"] == "reversible"


def test_departing_from_the_assumed_path_must_name_what_it_invalidates(led):
    led.open_fork("F1", "which db?", "scale", "a", OPTIONS)
    led.dispute("F1", "reviewer", "author", "the cost argument still holds")
    with pytest.raises(Blocked, match="records nothing invalidated"):
        led.decide("F1", "b", "agent")


def test_revising_the_edge_records_the_revision(led):
    led.open_fork(
        "F1", "widen the edge?", "a new compliance requirement", "keep",
        [{"id": "revise", "cost": "low", "risk": "low", "reversibility": "reversible"},
         {"id": "keep", "cost": "none", "risk": "medium", "reversibility": "reversible"}],
        criterion_from="handles CRLF", criterion_to="handles CRLF and BOM",
    )
    led.dispute("F1", "reviewer", "author", "BOM files occur in the wild")
    led.decide("F1", "revise", "agent", invalidated=["handles CRLF"])
    assert "handles CRLF and BOM" in led.state["criteria"]
    assert "handles CRLF" not in led.state["criteria"]
    revision = led.state["criterion_revisions"][0]
    assert revision["fork"] == "F1"
    assert revision["invalidated"] == ["handles CRLF"]
