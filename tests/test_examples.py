"""Both example scripts run from zero in an isolated ledger and close REACHED.

The examples are the contract in executable form; if they rot, the skill's own
promise rots with them. Each run gets a fresh ledger under pytest's tmp_path, so
nothing lands in the repository's `.omc/` and a second run cannot collide.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = {
    "minimal-narrow": ROOT / "examples" / "minimal-narrow" / "run.sh",
    "clean-room-re": ROOT / "examples" / "clean-room-re" / "run.sh",
}


@pytest.mark.parametrize("attempt", (1, 2))
@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_example_runs_clean_and_closes_reached(tmp_path, name, attempt):
    ledger = tmp_path / f"{name}-{attempt}"
    result = subprocess.run(
        ["bash", str(EXAMPLES[name]), str(ledger)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, (name, attempt, result.stdout, result.stderr)
    report = ledger / "report.md"
    assert report.exists(), (name, result.stdout, result.stderr)
    text = report.read_text(encoding="utf-8")
    assert "**REACHED**" in text
    assert "**NOT REACHED**" not in text
    assert (ledger / "control.jsonl").exists()


def test_examples_do_not_touch_the_repository_ledger(tmp_path):
    omc = ROOT / ".omc"
    before = set(omc.iterdir()) if omc.exists() else set()
    for name, script in EXAMPLES.items():
        result = subprocess.run(
            ["bash", str(script), str(tmp_path / name)],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
            env=os.environ.copy(),
        )
        assert result.returncode == 0, (name, result.stdout, result.stderr)
    after = set(omc.iterdir()) if omc.exists() else set()
    assert after == before
