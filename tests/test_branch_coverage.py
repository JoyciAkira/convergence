"""The coverage runner's CI split: trace once, gate the report separately.

Running the suite twice (once for pytest, once under the tracer) doubles wall
time and can disagree with itself; these tests pin the evaluate mode that lets
CI gate coverage from the single traced run's JSON.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import branch_coverage  # noqa: E402


def test_suite_failure_is_not_masked_by_full_coverage(monkeypatch, capsys):
    monkeypatch.setattr(branch_coverage, "collect_arms", lambda path: ([], 0))
    monkeypatch.setattr(
        branch_coverage, "trace_suite",
        lambda targets: (3, defaultdict(set), defaultdict(set)),
    )
    assert branch_coverage.main(["--modules", "state"]) == 3
    assert "OK: every module is at or above 80%" in capsys.readouterr().out


def test_json_out_writes_the_report(monkeypatch, tmp_path):
    monkeypatch.setattr(branch_coverage, "collect_arms", lambda path: ([], 0))
    monkeypatch.setattr(
        branch_coverage, "trace_suite",
        lambda targets: (0, defaultdict(set), defaultdict(set)),
    )
    report = tmp_path / "report.json"
    assert branch_coverage.main(
        ["--modules", "state", "--json-out", str(report)]
    ) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["pytest"] == 0
    assert payload["modules"][0]["percent"] == 100.0


def _report(path, percent, module="gates"):
    path.write_text(
        json.dumps({
            "threshold": 80,
            "pytest": 0,
            "modules": [{
                "module": module,
                "arms": 10,
                "covered": int(percent // 10),
                "percent": percent,
                "unscored": 0,
                "missing": [],
            }],
        }),
        encoding="utf-8",
    )
    return path


def test_evaluation_passes_at_or_above_the_threshold(tmp_path, capsys):
    report = _report(tmp_path / "report.json", 88.6)
    assert branch_coverage.main(["--evaluate", str(report), "--fail-under", "80"]) == 0
    out = capsys.readouterr().out
    assert "gates" in out
    assert "OK: every module is at or above 80%" in out


def test_evaluation_fails_below_the_threshold(tmp_path, capsys):
    report = _report(tmp_path / "report.json", 67.1)
    assert branch_coverage.main(["--evaluate", str(report), "--fail-under", "80"]) == 1
    out = capsys.readouterr().out
    assert "FAIL: below 80% -> gates" in out


def test_evaluation_threshold_is_the_callers(tmp_path):
    report = _report(tmp_path / "report.json", 85.0)
    assert branch_coverage.main(["--evaluate", str(report), "--fail-under", "90"]) == 1


def test_a_missing_report_is_a_tool_error(tmp_path, capsys):
    assert branch_coverage.main(["--evaluate", str(tmp_path / "absent.json")]) == 2
    assert "cannot evaluate" in capsys.readouterr().err
