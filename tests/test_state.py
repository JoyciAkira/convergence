"""Storage: locks, transactions, compaction, replay and the frozen edge.

Compaction is the lossy-looking operation that must be lossless, so its tests
compare derived state before and after rather than trusting the copy.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import state as state_module  # noqa: E402
from convergence import Blocked, IntakeError, Ledger, UsageError  # noqa: E402
from state import req_list  # noqa: E402


@pytest.fixture
def led(tmp_path):
    return Ledger.create(
        tmp_path / "L", "ship the parser", ["handles CRLF", "rejects malformed"]
    )


def test_req_list_rejects_empty_and_strips_entries():
    with pytest.raises(UsageError, match="at least one entry"):
        req_list([], "--route")
    assert req_list([" alpha ", "", "beta"], "--route") == ["alpha", "beta"]


def test_a_missing_ledger_directory_is_a_usage_error(tmp_path):
    with pytest.raises(UsageError, match="does not exist"):
        Ledger(tmp_path / "missing")


def test_compact_round_trips_the_derived_state(led):
    before = led.outcome()
    reopened = led.compact()
    assert reopened.outcome() == before
    assert reopened.state["criteria"] == led.state["criteria"]
    assert (led.root / "archive").is_dir()
    control = (led.root / "control.jsonl").read_text(encoding="utf-8")
    assert '"snapshot"' in control


def test_replay_skips_blank_lines(led):
    with (led.root / "control.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n\n")
    assert Ledger(led.root).state["criteria"] == led.state["criteria"]


def test_replay_names_the_corrupt_line(led):
    (led.root / "hypotheses.jsonl").write_text("not json\n", encoding="utf-8")
    with pytest.raises(UsageError, match="is not JSON"):
        Ledger(led.root)


def test_create_refuses_a_second_initialisation(led):
    with pytest.raises(Blocked, match="already initialised"):
        Ledger.create(led.root, "ship it again", ["another criterion"])


def test_create_accepts_a_precomputed_critic(tmp_path):
    created = Ledger.create(
        tmp_path / "C", "ship the parser", ["handles CRLF", "rejects malformed"],
        critic=[],
    )
    assert created.state["meta"]["critic"] == []


def test_validate_intent_requires_a_callable_hook():
    with pytest.raises(IntakeError, match="state_hook must be callable"):
        Ledger.validate_intent(
            "ship the parser", ["handles CRLF"], state_hook="not callable"
        )


def test_create_refuses_to_freeze_without_criteria(tmp_path):
    with pytest.raises(Blocked, match="no acceptance criteria"):
        Ledger.create(tmp_path / "F", "ship the parser", [], freeze=True)


def test_create_can_freeze_an_intent_with_criteria(tmp_path):
    created = Ledger.create(
        tmp_path / "G", "ship the parser", ["handles CRLF"], freeze=True
    )
    assert created.state["intent_frozen"] is True


def test_compact_tolerates_a_missing_log_file(led):
    (led.root / "hypotheses.jsonl").unlink()
    compacted = led.compact()
    assert compacted.state["criteria"] == led.state["criteria"]
    assert not (led.root / "hypotheses.jsonl").exists()


def test_validate_intent_rejects_a_criterion_that_restates_the_intent():
    intent = "ship the parser that handles CRLF and rejects malformed input"
    with pytest.raises(IntakeError, match="restates the intent"):
        Ledger.validate_intent(intent, [intent])


def test_validate_intent_rejects_an_unverifiable_short_criterion():
    with pytest.raises(IntakeError, match="shorter than 4"):
        Ledger.validate_intent("ship the parser", ["ok"])


def test_lock_helpers_degrade_without_fcntl(tmp_path, monkeypatch):
    monkeypatch.setattr(state_module, "_HAS_FCNTL", False)
    with (tmp_path / "lock").open("a") as handle:
        state_module._lock_exclusive(handle)
        state_module._unlock(handle)
