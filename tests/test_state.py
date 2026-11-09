"""Storage: locks, transactions, compaction, replay and the frozen edge.

Compaction is the lossy-looking operation that must be lossless, so its tests
compare derived state before and after rather than trusting the copy.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from compaction import MARKER as COMPACT_MARKER  # noqa: E402
import compaction as compaction_module  # noqa: E402
import state as state_module  # noqa: E402
from convergence import Blocked, IntakeError, Ledger, UsageError  # noqa: E402
from state import req_list  # noqa: E402


CRASH_DURING_COMPACT = r"""
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from convergence import Ledger

mode = sys.argv[3]
real_open = Path.open
real_unlink = Path.unlink

if mode == "truncate":
    def crash_open(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        if self.name == "control.jsonl" and mode == "w":
            handle.write("")
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os._exit(37)
        return handle
    Path.open = crash_open
else:
    def crash_unlink(self, *args, **kwargs):
        if self.name == ".compact.pending":
            os._exit(38)
        return real_unlink(self, *args, **kwargs)
    Path.unlink = crash_unlink

Ledger(sys.argv[1]).compact()
"""


CRASH_DURING_RECOVERY = r"""
import os
import sys

sys.path.insert(0, sys.argv[2])
import compaction
from convergence import Ledger

real_restore = compaction._restore_file

def crash_after_first_restore(source, target):
    real_restore(source, target)
    os._exit(39)

compaction._restore_file = crash_after_first_restore
Ledger(sys.argv[1])
"""


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


@pytest.mark.parametrize(("mode", "code"), [("truncate", 37), ("before_unlink", 38)])
def test_a_crash_during_compaction_restores_the_originals(tmp_path, mode, code):
    """The marker is the commit point: without its removal, the archive wins."""
    led = Ledger.create(tmp_path / "L", "ship the parser", ["handles CRLF"])
    led.record_risk("must survive compaction")
    expected = [r["text"] for r in led.state["risks"]]
    marker = led.root / COMPACT_MARKER

    proc = subprocess.run(
        [sys.executable, "-c", CRASH_DURING_COMPACT, str(led.root),
         str(ROOT / "lib"), mode],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == code, (proc.stdout, proc.stderr)
    assert marker.exists(), "the crashed process must leave the transaction marker"
    control = (led.root / "control.jsonl").read_text(encoding="utf-8")
    if mode == "truncate":
        assert control == "", "the crash must land after truncation, before the snapshot"
    else:
        assert '"snapshot"' in control, "the crash must land after the snapshot is written"

    recovered = Ledger(led.root)
    assert [r["text"] for r in recovered.state["risks"]] == expected
    assert not marker.exists(), "recovery must clear the marker only after restoring"
    assert "snapshot" not in (led.root / "control.jsonl").read_text(encoding="utf-8")
    assert not (led.root / "control.jsonl.recover").exists()


def test_a_crash_during_recovery_is_idempotent(tmp_path):
    """A crash halfway through restoring must not consume the archive."""
    led = Ledger.create(tmp_path / "L", "ship the parser", ["handles CRLF"])
    led.record_risk("must survive recovery crashes")
    expected = [r["text"] for r in led.state["risks"]]
    marker = led.root / COMPACT_MARKER

    crashed = subprocess.run(
        [sys.executable, "-c", CRASH_DURING_COMPACT, str(led.root),
         str(ROOT / "lib"), "truncate"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert crashed.returncode == 37, (crashed.stdout, crashed.stderr)
    assert marker.exists()

    recovery_crashed = subprocess.run(
        [sys.executable, "-c", CRASH_DURING_RECOVERY, str(led.root),
         str(ROOT / "lib")],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert recovery_crashed.returncode == 39, (
        recovery_crashed.stdout, recovery_crashed.stderr)
    assert marker.exists(), "an interrupted recovery must keep the marker"
    archives = list((led.root / "archive").iterdir())
    assert len(archives) == 1 and (archives[0] / "control.jsonl").exists(), (
        "the archive must remain available for the next recovery attempt")

    recovered = Ledger(led.root)
    assert [r["text"] for r in recovered.state["risks"]] == expected
    assert not marker.exists()
    assert "snapshot" not in (led.root / "control.jsonl").read_text(encoding="utf-8")
    assert not (led.root / "control.jsonl.recover").exists()


def test_compact_fsyncs_directories_at_commit_points(led, monkeypatch):
    """A power loss must not resurrect the marker or lose the archive rename."""
    calls = []
    original = compaction_module._fsync_dir

    def record(path):
        calls.append(Path(path))
        return original(path)

    monkeypatch.setattr(compaction_module, "_fsync_dir", record)
    led.compact()

    archive_dir = led.root / "archive"
    assert archive_dir in calls
    assert any(path.parent == archive_dir for path in calls), (
        "the timestamped archive directory must be flushed after its files")
    assert calls.count(led.root) >= 2, (
        "the ledger root must be flushed for the marker rename and its removal")


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
