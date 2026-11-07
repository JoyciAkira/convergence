"""Entry A gates: directives, local-action detection, settled hypotheses.

These drive the public mutators, so a gate that stops refusing (or stops
clearing its directive) fails here rather than living on only in prose.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from convergence import Blocked, Ledger, UsageError  # noqa: E402
from gates import _looks_like_local_action  # noqa: E402

INCIDENT = "INC-1"


@pytest.fixture
def led(tmp_path):
    return Ledger.create(
        tmp_path / "L", "ship the parser", ["handles CRLF", "rejects malformed"]
    )


def test_empty_smallest_input_is_not_local_work():
    assert _looks_like_local_action("") is False
    assert _looks_like_local_action(None) is False


def test_a_command_on_a_local_tool_reads_as_work_not_a_fact():
    assert _looks_like_local_action("run npm install") is True
    assert _looks_like_local_action("operator must paste the API token") is False


def test_parking_local_work_as_missing_information_is_refused(led):
    with pytest.raises(Blocked, match="describes work this session"):
        led.classify(
            "flutter toolchain absent",
            "information_missing",
            missing_fact="a working flutter toolchain",
            smallest_input="run npm install",
            tried=["searched the base images", "read the setup docs"],
        )
    assert led.state["classifications"] == []


def test_a_satisfied_directive_blocks_unrelated_work_then_clears(led):
    led.classify("loop never went red", "loop_defective")
    assert led.state["directive"]["kind"] == "loop_defective"
    with pytest.raises(Blocked, match="loop_defective"):
        led.begin_fix(INCIDENT, "swap the line reader")
    led.declare_loop(INCIDENT, "pytest -k crlf", "import blows up on CRLF")
    with pytest.raises(Blocked, match="loop_defective"):
        led.begin_fix(INCIDENT, "swap the line reader")
    led.run_loop(INCIDENT, red=True)
    led.open_hypothesis("H1", INCIDENT, "text mode eats CR", "binary read keeps it")
    assert led.state["directive"] is None


def test_closing_an_unknown_hypothesis_is_a_usage_error(led):
    with pytest.raises(UsageError, match="unknown hypothesis"):
        led.close_hypothesis("GHOST", "probe", "observed", survives=False)


def test_settling_a_hypothesis_twice_is_refused(led):
    led.declare_loop(INCIDENT, "pytest -k crlf", "import blows up on CRLF")
    led.run_loop(INCIDENT, red=True)
    led.open_hypothesis("H1", INCIDENT, "text mode eats CR", "binary read keeps it")
    led.close_hypothesis("H1", "hexdump the bytes", "CR gone", survives=False)
    with pytest.raises(Blocked, match="already refuted"):
        led.close_hypothesis("H1", "hexdump the bytes", "CR gone", survives=False)
