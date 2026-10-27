"""Drift + hardening tests: prose promises that must hold as machine behaviour."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from convergence import Blocked, Ledger, UsageError  # noqa: E402


@pytest.fixture
def led(tmp_path):
    return Ledger.create(tmp_path / "L", "ship the parser", ["handles CRLF", "rejects malformed"])


def test_frontmatter_has_triggers_and_hint():
    head = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    assert "name:" in head
    assert "description:" in head
    assert "triggers:" in head
    assert "argument-hint:" in head


def test_skill_drift_guard_passes():
    r = subprocess.run([sys.executable, "scripts/skill_drift.py"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_self_reproduction_is_case_insensitive(led):
    with pytest.raises(Blocked, match="Re-reading your own"):
        led.record_evidence("handles CRLF", "pytest -k crlf", "reproduced_independently",
                            recorder="Main", reproduced_by="main",
                            expected_source="literal")


def test_store_secret_requires_name_reference(led):
    with pytest.raises(UsageError, match="UPPER_SNAKE"):
        led.store_secret("llm-key", "echo secret-value")
    with pytest.raises(UsageError, match="must reference the secret by name"):
        led.store_secret("LLM_API_KEY", "echo hardcoded-value-123")
    led.store_secret("LLM_API_KEY", "echo $LLM_API_KEY")
    assert led.state["infra"]["secrets"][-1]["name"] == "LLM_API_KEY"


def test_import_tickets_is_idempotent(tmp_path, led):
    d = tmp_path / "tickets"
    d.mkdir()
    (d / "01.md").write_text("# 01: Parser\n\n## Acceptance criteria\n\n- [ ] handles tabs\n", encoding="utf-8")
    led.import_tickets(str(d))
    assert "handles tabs" in led.state["criteria"]
    led.import_tickets(str(d))  # identical re-import must not raise
    assert led.state["criteria"].count("handles tabs") == 1


def test_status_filter_flags(tmp_path):
    led = Ledger.create(tmp_path / "L2", "ship it", ["handles CRLF"])
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, 'lib');"
                        "from convergence import main;"
                        f"raise SystemExit(main(['status', '{tmp_path / 'L2'}', '--criterion', 'handles CRLF']))"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["criterion"] == "handles CRLF"


def test_portable_lock_helpers_exist():
    import convergence as conv
    assert callable(conv._lock_exclusive) and callable(conv._unlock)
    assert conv.LEDGER_VERSION == 2


def test_interview_flags_task_shaped_criteria():
    from prompt import interview_questions
    qs = interview_questions("ship it", ["build a REST API"])
    assert any(q["edge"] == "intent" for q in qs)
    assert any(q["edge"] == "criterion" and "build a REST API" in q["question"] for q in qs)


def test_interview_is_clean_for_verifiable_magnet():
    from prompt import interview_questions
    assert interview_questions("ship the parser that handles CRLF",
                               ["handles CRLF", "rejects malformed"]) == []


def test_intake_reports_questions_and_needs_interview(tmp_path):
    r = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(ROOT / 'lib')!r});"
         "from convergence import main;"
         "raise SystemExit(main(['init', 'L', '--intent', 'ship it',"
         " '--criterion', 'build a REST API', '--intake']))"],
        cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    shape = json.loads(r.stdout)
    assert shape["shape_ok"] is True
    assert shape["needs_interview"] is True
    assert any(q["edge"] == "criterion" for q in shape["questions"])
