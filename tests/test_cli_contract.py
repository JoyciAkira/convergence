"""CLI-level contract tests: the documented verbs and gates, one process each.

These are deliberately end-to-end through `lib/convergence.py`, not calls into the
ledger object: exit codes (0/2/3), JSONL replay across processes and refusal
messages are part of the skill's contract, and only a real process can prove
them. The examples and the lifecycle suite cover the rest.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONVERGENCE = [sys.executable, str(ROOT / "lib" / "convergence.py")]


def run(*args, env=None):
    extra = os.environ.copy()
    if env:
        extra.update(env)
    return subprocess.run(
        [*CONVERGENCE, *map(str, args)],
        cwd=ROOT, capture_output=True, text=True, timeout=90, env=extra,
    )


def ok(*args, env=None):
    result = run(*args, env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return result


def refused(code, *args, match="", env=None):
    result = run(*args, env=env)
    assert result.returncode == code, (result.returncode, result.stdout, result.stderr)
    assert match in (result.stdout + result.stderr), (match, result.stdout, result.stderr)
    return result


def status(ledger):
    return json.loads(ok("status", ledger).stdout)


@pytest.fixture
def led(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "ship the parser",
       "--criterion", "handles CRLF", "rejects malformed")
    ok("freeze-intent", path)
    return path


# --------------------------------------------------------------------------- #
# intake: any prompt shape, the magnet sealed, nothing invented
# --------------------------------------------------------------------------- #

def test_intake_validates_without_writing(tmp_path):
    path = tmp_path / "L"
    refused(2, "init", path, "--intent", "ship it", "--intake",
            match="no criteria")
    assert not path.exists()

    brief = tmp_path / "brief.md"
    brief.write_text(
        "# Ship the parser\n\n"
        "Acceptance criteria:\n- [ ] handles CRLF\n- [ ] rejects malformed\n\n"
        "## Hypotheses\n- the mode is wrong\n\n"
        "## Out of scope\n- Windows\n",
        encoding="utf-8",
    )
    out = ok("init", path, "--prompt-file", brief, "--intake")
    shape = json.loads(out.stdout)
    assert shape["shape_ok"] is True
    assert "handles CRLF" in shape["criteria"]
    assert shape["hypotheses"] and "mode" in shape["hypotheses"][0]["text"].lower()
    assert "Windows" in json.dumps(shape["exclusions"])
    assert not path.exists()


def test_intent_and_criteria_files_create_the_magnet(tmp_path):
    path = tmp_path / "L"
    intent = tmp_path / "intent.txt"
    criteria = tmp_path / "checks.txt"
    intent.write_text("ship the parser\n", encoding="utf-8")
    criteria.write_text("- handles CRLF\n- rejects malformed\n", encoding="utf-8")
    ok("init", path, "--intent-file", intent, "--criteria-file", criteria)
    state = status(path)
    assert state["intent"] == "ship the parser"
    assert state["criteria"] == ["handles CRLF", "rejects malformed"]
    ok("freeze-intent", path)
    assert status(path)["intent_frozen"] is True
    refused(3, "freeze-intent", path, match="already frozen")


# --------------------------------------------------------------------------- #
# Entry A: red loop, rivals, fix gate, one-shot
# --------------------------------------------------------------------------- #

def test_compact_command_is_lossless_and_repeatable(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "ship the parser", "--criterion", "handles CRLF")
    ok("record-risk", path, "--text", "kept across compaction")
    before = status(path)

    result = ok("compact", path)
    payload = json.loads(result.stdout)
    assert payload["compacted"] is True
    assert payload["ledger"] == str(path)
    assert Path(payload["archive"]).is_dir()

    after = status(path)
    assert after["intent"] == before["intent"]
    assert after["criteria"] == before["criteria"]
    assert after["risks"] == before["risks"]
    control = (Path(path) / "control.jsonl").read_text(encoding="utf-8")
    assert control.count("\n") == 1
    assert '"snapshot"' in control

    second = json.loads(ok("compact", path).stdout)
    assert second["archive"] == payload["archive"]
    assert status(path)["risks"] == before["risks"]
    refused(2, "compact", tmp_path / "missing", match="does not exist")


def test_entry_a_gates_refuse_each_shortcut(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "ship the parser", "--criterion", "handles CRLF")
    refused(3, "open-hypothesis", path, "--id", "H1", "--incident", "I",
            "--claim", "mode", "--prediction", "mode is r",
            match="no declared feedback loop")
    ok("declare-loop", path, "--incident", "I", "--command", "pytest",
       "--symptom", "crash")
    refused(3, "open-hypothesis", path, "--id", "H1", "--incident", "I",
            "--claim", "mode", "--prediction", "mode is r",
            match="declared but never run")
    ok("run-loop", path, "--incident", "I", "--red", "false")
    refused(3, "open-hypothesis", path, "--id", "H1", "--incident", "I",
            "--claim", "mode", "--prediction", "mode is r",
            match="did not go red")
    ok("run-loop", path, "--incident", "I", "--red", "true")
    for hyp, claim in (("H1", "text mode eats CR"), ("H2", "missing cookie"),
                       ("H3", "BOM prefix")):
        ok("open-hypothesis", path, "--id", hyp, "--incident", "I",
           "--claim", claim, "--prediction", "fixing it turns green")
    refused(3, "begin-fix", path, "--incident", "I", "--note", "fix",
            match="open hypotheses")
    ok("refute-hypothesis", path, "--id", "H1", "--probe", "hexdump",
       "--observed", "still red")
    ok("survive-hypothesis", path, "--id", "H2", "--probe", "binary open",
       "--observed", "green")
    ok("merge-hypotheses", path, "--into", "H2", "--from", "H3",
       "--note", "same mechanism")
    ok("begin-fix", path, "--incident", "I", "--note", "binary mode")
    refused(3, "close", path, match="NOT REACHED")
    ok("record-evidence", path, "--criterion", "handles CRLF",
       "--command", "pytest", "--tier", "executed_here",
       "--expected-source", "literal")
    result = ok("close", path)
    assert "**REACHED**" in result.stdout


def test_one_shot_is_idempotent_and_narrow_is_disclosed(tmp_path):
    path = tmp_path / "L"
    args = (
        "one-shot", path, "--incident", "INC-1",
        "--loop-command", "pytest -k null", "--symptom", "crash on null",
        "--red-observed", "true", "--claim", "no null guard",
        "--prediction", "guard turns it green", "--probe", "apply guard",
        "--observed", "green", "--fix-note", "add guard",
        "--criterion", "null input returns empty",
        "--evidence-command", "pytest -k null",
    )
    ok(*args)
    ok(*args)  # a resumed batch must not wedge on its own earlier steps
    state = status(path)
    assert state["fixes"]["INC-1"]["search"] == "narrow"
    assert state["hypotheses"]["INC-1-H1"]["status"] == "surviving"
    result = ok("close", path)
    assert "**REACHED**" in result.stdout
    assert "without searching over rival" in result.stdout


# --------------------------------------------------------------------------- #
# Entry B: fork, dispute, delegated authority, right-edge move
# --------------------------------------------------------------------------- #

def test_fork_requires_dispute_and_respects_authority(led):
    two = [
        {"id": "a", "cost": "1d", "risk": "low", "reversibility": "reversible"},
        {"id": "b", "cost": "2d", "risk": "low", "reversibility": "reversible"},
    ]
    refused(3, "open-fork", led, "--id", "F1", "--question", "db?",
            "--trigger", "scale", "--assumed", "a",
            "--option", json.dumps(two[0]), match="single-option fork")
    ok("open-fork", led, "--id", "F1", "--question", "db?", "--trigger",
       "scale", "--assumed", "a", "--option", *(json.dumps(o) for o in two))
    refused(3, "dispute", led, "--fork", "F1", "--challenger", "me",
            "--author", "me", "--steelman", "cost", match="same actor")
    refused(3, "decide", led, "--fork", "F1", "--chosen", "a",
            "--by", "agent", match="no dispute")

    ok("dispute", led, "--fork", "F1", "--challenger", "reviewer",
       "--author", "author", "--steelman", "cost")
    refused(3, "decide", led, "--fork", "F1", "--chosen", "a", "--by", "agent",
            "--anchor", "money", match="human anchor")
    refused(3, "decide", led, "--fork", "F1", "--chosen", "a", "--by", "human",
            match="requires --human-ack")
    ok("decide", led, "--fork", "F1", "--chosen", "a", "--by", "human",
       "--human-ack")
    assert status(led)["decisions"]["F1"]["chosen"] == "a"


def test_decide_cannot_depart_silently_and_edge_move_restarts_unverified(led):
    options = [
        {"id": "revise", "cost": "1d", "risk": "low", "reversibility": "reversible"},
        {"id": "keep", "cost": "0", "risk": "low", "reversibility": "reversible"},
    ]
    ok("open-fork", led, "--id", "F2", "--question", "widen?",
       "--trigger", "BOM found", "--assumed", "keep",
       "--criterion-from", "handles CRLF",
       "--criterion-to", "handles CRLF and BOM",
       "--option", *(json.dumps(o) for o in options))
    ok("dispute", led, "--fork", "F2", "--challenger", "reviewer",
       "--author", "author", "--steelman", "BOM is common")
    refused(3, "decide", led, "--fork", "F2", "--chosen", "revise",
            "--by", "agent", match="records nothing invalidated")
    ok("decide", led, "--fork", "F2", "--chosen", "revise", "--by", "agent",
       "--invalidated", "handles CRLF")
    state = status(led)
    assert "handles CRLF and BOM" in state["criteria"]
    assert "handles CRLF" not in state["criteria"]
    refused(3, "close", led, match="NOT REACHED")


def test_overruling_a_dissent_needs_a_reason_but_does_not_gate(led):
    options = [
        {"id": "a", "cost": "1d", "risk": "low", "reversibility": "reversible"},
        {"id": "b", "cost": "2d", "risk": "low", "reversibility": "reversible"},
    ]
    ok("open-fork", led, "--id", "F3", "--question", "db?", "--trigger",
       "scale", "--assumed", "a", "--option", *(json.dumps(o) for o in options))
    ok("dispute", led, "--fork", "F3", "--challenger", "reviewer",
       "--author", "author", "--steelman", "b is faster")
    refused(2, "decide", led, "--fork", "F3", "--chosen", "a", "--by", "agent",
            "--challenger-overruled", match="override-reason")
    ok("decide", led, "--fork", "F3", "--chosen", "a", "--by", "agent",
       "--challenger-overruled", "--override-reason", "the counter-example did not reproduce")


# --------------------------------------------------------------------------- #
# Entry C: classification, earned block, anti-park guard, directives
# --------------------------------------------------------------------------- #

def test_park_must_be_earned_and_the_run_continues(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "ship the parser", "--criterion", "handles CRLF")
    refused(2, "classify", path, "--signature", "missing db", "--kind",
            "information_missing", match="missing-fact")
    refused(3, "classify", path, "--signature", "missing db", "--kind",
            "information_missing", "--missing-fact", "a database",
            "--smallest-input", "run npm install", "--tried", "docs", "images",
            match="describes work this session")
    refused(3, "classify", path, "--signature", "missing db", "--kind",
            "information_missing", "--missing-fact", "a database",
            "--smallest-input", "operator creates it", "--tried", "docs",
            match="at least 2 distinct routes")
    ok("classify", path, "--signature", "missing db", "--kind",
       "information_missing", "--missing-fact", "a database",
       "--smallest-input", "operator creates it",
       "--tried", "docs", "support", "--park", "F-deploy")
    action = json.loads(ok("next-action", path).stdout)
    assert len(action["parked_paths"]) == 1
    assert "F-deploy" in action["parked_paths"][0]
    ok("resolve-block", path, "--signature", "missing db", "--provided", "created")
    assert json.loads(ok("next-action", path).stdout).get("parked_paths", []) == []


def test_a_defective_loop_is_a_directive_not_a_cap(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "ship the parser", "--criterion", "handles CRLF")
    ok("classify", path, "--signature", "flake", "--kind", "loop_defective")
    refused(3, "begin-fix", path, "--incident", "I", "--note", "fix",
            match="loop_defective")
    ok("declare-loop", path, "--incident", "I", "--command", "pytest",
       "--symptom", "flake")
    ok("run-loop", path, "--incident", "I", "--red", "true")
    ok("open-hypothesis", path, "--id", "H1", "--incident", "I",
       "--claim", "shared tmpdir", "--prediction", "per-run dir fixes it")
    assert status(path)["directive"] is None


# --------------------------------------------------------------------------- #
# Routing, paging and exploratory provenance
# --------------------------------------------------------------------------- #

def test_route_and_status_views(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "ship the parser", "--criterion", "handles CRLF")
    route = json.loads(ok("route", path).stdout)
    assert route["lane"] == "verify"
    assert route["fallback"]["lane"] == "close"
    assert json.loads(ok("status", path, "--section", "criteria",
                        "--limit", "1").stdout) == {"criteria": ["handles CRLF"]}
    one = json.loads(ok("status", path, "--criterion", "handles CRLF").stdout)
    assert one["verified"] is False and one["latest_tier"] is None
    refused(2, "status", path, "--section", "ghost", match="unknown section")


def test_init_can_take_a_raw_prompt(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--prompt",
       "# Ship the parser\n\n## Acceptance criteria\n- [ ] handles BOM\n")
    state = status(path)
    assert state["criteria"] == ["handles BOM"]
    assert "Ship the parser" in state["intent"]


def test_exploratory_candidates_are_provenanced(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "pick a parser design",
       "--criterion", "selected design passes", "--exploratory")
    assert json.loads(ok("route", path).stdout)["lane"] == "explore"
    ok("record-candidates", path, "--run-id", "R1", "--source", "arena",
       "--count", "2", "--note", "two drafts", "--file", "drafts/a.py")
    assert status(path)["candidates"][0]["count"] == 2
    assert json.loads(ok("route", path).stdout)["lane"] == "explore"
    ok("select-candidate", path, "--run-id", "R1", "--id", "A",
       "--by", "author", "--method", "rubric after review")
    candidate = status(path)["candidates"][0]
    assert candidate["selected"] == "A" and candidate["selected_by"] == "author"
    assert json.loads(ok("route", path).stdout)["lane"] == "verify"
    result = refused(3, "close", path, match="NOT REACHED")
    assert "unopposed self-selection" in result.stdout


def test_catalog_provision_and_only_route_disclosure(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "ship the parser", "--criterion", "handles CRLF")
    ok("provision-env-catalog", path, "--spec", "sqlite", "--output-var", "CAT_DB")
    refused(3, "validate-infra", path, match="not in environment")
    ok("validate-infra", path, env={"CAT_DB": "sqlite:///tmp/cat.db"})
    ok("classify", path, "--signature", "needs token", "--kind",
       "information_missing", "--missing-fact", "an API token",
       "--smallest-input", "run npm install", "--only-route", "operator holds it")
    result = refused(3, "close", path, match="NOT REACHED")
    assert "blocks taken on the word that there was no other route" in result.stdout


# --------------------------------------------------------------------------- #
# Evidence: tiers, anti-tautology, exact criteria
# --------------------------------------------------------------------------- #

def test_every_expected_source_is_accepted(tmp_path):
    path = tmp_path / "L"
    sources = ["spec", "literal", "worked_example", "independent_oracle",
               "binary_oracle", "rea_evidence"]
    criteria = [f"source {source} is accepted" for source in sources]
    ok("init", path, "--intent", "verify every oracle", "--criterion", *criteria)
    for index, (criterion, source) in enumerate(zip(criteria, sources)):
        ok("record-evidence", path, "--criterion", criterion, "--command",
           "pytest", "--tier", "executed_here", "--expected-source", source,
           "--output-ref", f"artifacts/{index}.log")
    for criterion in criteria:
        assert json.loads(ok("status", path, "--criterion", criterion).stdout)["verified"] is True
    ok("close", path)
    ok("record-evidence", path, "--criterion", criteria[0], "--command", "n/a",
       "--tier", "unavailable", "--reason", "no access")
    assert json.loads(ok("status", path, "--criterion", criteria[0]).stdout)["verified"] is False
    ok("record-evidence", path, "--criterion", criteria[0], "--command",
       "pytest", "--tier", "executed_here", "--expected-source", "literal")
    result = ok("close", path)
    assert "**REACHED**" in result.stdout


def test_evidence_tiers_and_refusals(led):
    refused(3, "record-evidence", led, "--criterion", "ghost",
            "--command", "pytest", "--tier", "executed_here",
            match="not a criterion")
    refused(3, "record-evidence", led, "--criterion", "handles CRLF",
            "--command", "pytest", "--tier", "executed_here",
            "--expected-source", "code", match="passes by construction")
    refused(2, "record-evidence", led, "--criterion", "handles CRLF",
            "--command", "pytest", "--tier", "unavailable", match="--reason")
    refused(2, "record-evidence", led, "--criterion", "handles CRLF",
            "--command", "pytest", "--tier", "reproduced_independently",
            match="--recorder")
    refused(3, "record-evidence", led, "--criterion", "handles CRLF",
            "--command", "pytest", "--tier", "reproduced_independently",
            "--recorder", "main", "--reproduced-by", "main",
            match="equals --recorder")
    ok("record-evidence", led, "--criterion", "handles CRLF", "--command",
       "pytest", "--tier", "reproduced_independently", "--recorder", "main",
       "--reproduced-by", "reviewer", "--expected-source", "literal")


def test_flagged_criterion_needs_an_explicit_oracle(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "ship the parser", "--criterion",
       "the parser is fast")
    refused(3, "record-evidence", path, "--criterion", "the parser is fast",
            "--command", "pytest", "--tier", "executed_here",
            match="flagged by the intake critic")
    ok("record-evidence", path, "--criterion", "the parser is fast",
       "--command", "pytest", "--tier", "executed_here",
       "--expected-source", "literal")


# --------------------------------------------------------------------------- #
# Close: derived verdict, report, exit codes, crash replay
# --------------------------------------------------------------------------- #

def test_close_is_derived_from_state_and_writes_the_report(led):
    before = status(led)
    result = refused(3, "close", led, match="NOT REACHED")
    assert "**REACHED**" not in result.stdout
    report = (Path(led) / "report.md").read_text(encoding="utf-8")
    assert "**NOT REACHED**" in report
    after = status(led)
    assert after["intent"] == before["intent"]
    assert after["criteria"] == before["criteria"]
    assert after["meta"]["run_fails"]

    for criterion, source in (("handles CRLF", "literal"),
                              ("rejects malformed", "spec")):
        ok("record-evidence", led, "--criterion", criterion, "--command",
           "pytest", "--tier", "executed_here", "--expected-source", source)
    result = ok("close", led)
    assert "**REACHED**" in result.stdout
    assert "## Auto-critique" in result.stdout
    final = status(led)
    assert final["intent"] == before["intent"]
    assert final["criteria"] == before["criteria"]


def test_a_crash_torn_tail_resumes_and_is_repaired(led):
    path = Path(led) / "control.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"ev": "risk_recorded", "text": "torn')
    # The intact prefix replays; the interrupted append is not a record.
    ok("next-action", led)
    assert not any(r["text"] == "torn" for r in status(led)["risks"])
    ok("record-risk", led, "--text", "after crash")
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert events[-1]["text"] == "after crash"
    assert not any(event.get("text") == "torn" for event in events)
    assert status(led)["risks"][-1]["text"] == "after crash"


def test_a_process_killed_mid_append_is_recovered_by_the_next_process(led):
    ok("record-risk", led, "--text", "before crash")
    worker = textwrap.dedent(
        r"""
        import os
        import sys
        from pathlib import Path

        sys.path.insert(0, sys.argv[2])
        from ledger import Ledger

        real_open = Path.open

        class CrashWriter:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return self.handle.__exit__(*exc)

            def write(self, data):
                cut = max(1, len(data) // 2)
                self.handle.write(data[:cut])
                self.handle.flush()
                os._exit(37)

        def crashing_open(self, mode="r", *args, **kwargs):
            handle = real_open(self, mode, *args, **kwargs)
            if self.name == "control.jsonl" and "a" in mode:
                return CrashWriter(handle)
            return handle

        Path.open = crashing_open
        Ledger(Path(sys.argv[1])).record_risk("torn by a crashing writer")
        """
    ).strip()
    proc = subprocess.run(
        [sys.executable, "-c", worker, str(led), str(ROOT / "lib")],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 37, (proc.stdout, proc.stderr)
    control = Path(led) / "control.jsonl"
    raw = control.read_bytes()
    assert not raw.endswith(b"\n")
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw.decode("utf-8"))

    # Separate CLI processes: the first ignores the torn tail, the second
    # repairs it before appending its own complete record.
    ok("next-action", led)
    assert [r["text"] for r in status(led)["risks"]] == ["before crash"]
    ok("record-risk", led, "--text", "after crash")
    events = [json.loads(line) for line in control.read_text().splitlines()]
    assert all(isinstance(event, dict) for event in events)
    assert [r["text"] for r in status(led)["risks"]] == [
        "before crash", "after crash"]
    assert not any(event.get("text") == "torn by a crashing writer" for event in events)


def test_a_complete_last_record_without_a_newline_is_kept(led):
    ok("record-risk", led, "--text", "complete record")
    path = Path(led) / "control.jsonl"
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    assert status(led)["risks"][-1]["text"] == "complete record"
    ok("record-risk", led, "--text", "second record")
    assert [r["text"] for r in status(led)["risks"]][-2:] == [
        "complete record", "second record"]


def test_malformed_input_and_corrupt_ledger(led):
    refused(2, "open-fork", led, "--id", "F", "--question", "q", "--trigger",
            "t", "--assumed", "a", "--option", "{not json", match="--option")
    refused(2, "open-hypothesis", led, "--id", "H", "--incident", "I",
            "--claim", "c", "--prediction", "p", "--rank", "nan")
    with (Path(led) / "hypotheses.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{broken\n")
    refused(2, "next-action", led, match="not JSON")


# --------------------------------------------------------------------------- #
# Provenance: tickets and obligations imported through the CLI
# --------------------------------------------------------------------------- #

def test_tickets_become_criteria_and_must_be_earned(tmp_path):
    path = tmp_path / "L"
    tickets = tmp_path / "tickets"
    tickets.mkdir()
    (tickets / "01.md").write_text(
        "# 01: Ship parser\n**Blocked by:** none\n## Acceptance criteria\n"
        "- [ ] ticket check ok\n",
        encoding="utf-8",
    )
    ok("init", path, "--intent", "ship the parser", "--criterion", "handles CRLF")
    ok("import-tickets", path, "--tickets", tickets)
    state = status(path)
    assert state["tickets"][0]["id"] == "01"
    assert "ticket check ok" in state["criteria"]
    refused(3, "close", path, match="NOT REACHED")
    ok("record-evidence", path, "--criterion", "ticket check ok", "--command",
       "pytest", "--tier", "executed_here", "--expected-source", "literal")
    ok("record-evidence", path, "--criterion", "handles CRLF", "--command",
       "pytest", "--tier", "executed_here", "--expected-source", "literal")
    result = ok("close", path)
    assert "earned" in result.stdout


def test_dangling_ticket_edge_is_refused(tmp_path):
    path = tmp_path / "L"
    tickets = tmp_path / "tickets"
    tickets.mkdir()
    (tickets / "01.md").write_text(
        "# 01: Ship parser\n**Blocked by:** #99\n## Acceptance criteria\n"
        "- [ ] ticket check ok\n",
        encoding="utf-8",
    )
    ok("init", path, "--intent", "ship the parser", "--criterion", "handles CRLF")
    refused(2, "import-tickets", path, "--tickets", tickets, match="unknown ticket")


def test_obligations_become_criteria_and_report_provenance(tmp_path):
    path = tmp_path / "L"
    manifest = tmp_path / "obligations.json"
    manifest.write_text(json.dumps({
        "target": "libx.so",
        "reconstruction_obligations": [{
            "id": "OBL-1", "title": "Parity", "category": "behavioral",
            "criteria": ["parity check ok"],
        }],
    }), encoding="utf-8")
    ok("init", path, "--intent", "reimplement libx", "--criterion", "abi ok")
    ok("import-obligations", path, "--obligations", manifest)
    assert status(path)["obligations"][0]["id"] == "OBL-1"
    ok("record-evidence", path, "--criterion", "abi ok", "--command", "nm",
       "--tier", "executed_here", "--expected-source", "literal")
    ok("record-evidence", path, "--criterion", "parity check ok", "--command",
       "diff", "--tier", "executed_here", "--expected-source", "binary_oracle")
    result = ok("close", path)
    assert "earned" in result.stdout


# --------------------------------------------------------------------------- #
# Infrastructure: preflight gates, real listener, auto-fix
# --------------------------------------------------------------------------- #

def test_provision_and_preflight_gate_next_action(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "ship the parser", "--criterion", "handles CRLF")
    ok("provision-env", path, "--spec", "sqlite", "--output-var", "E2E_DB",
       "--command", "echo sqlite:///tmp/e2e.db")
    refused(3, "validate-infra", path, match="not in environment")
    action = json.loads(ok("next-action", path).stdout)
    assert action["required"] == "validate-infra"
    ok("validate-infra", path, env={"E2E_DB": "sqlite:///tmp/e2e.db"})


def test_injection_and_secret_are_checked_and_fixable(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "ship the parser", "--criterion", "handles CRLF")
    target = tmp_path / "isolated"
    target.mkdir()
    source = tmp_path / "host"
    source.mkdir()
    (source / "tsconfig.json").write_text("{}", encoding="utf-8")
    command = f"cp {source / 'tsconfig.json'} {target / 'tsconfig.json'}"
    ok("inject-build-context", path, "--target", target, "--source", source,
       "--artifacts", "tsconfig.json", "--command", command)
    refused(3, "validate-infra", path, match="missing: tsconfig.json")
    ok("validate-infra", path, "--fix")
    assert (target / "tsconfig.json").exists()

    refused(2, "store-secret", path, "--name", "lowercase",
            "--command", "echo $lowercase", match="UPPER_SNAKE")
    refused(2, "store-secret", path, "--name", "API_KEY",
            "--command", "echo value", match="reference the secret by name")
    ok("store-secret", path, "--name", "API_KEY", "--command", "echo $API_KEY")
    ok("validate-infra", path, env={"API_KEY": "secret"})


def test_boot_runtime_is_really_listening_and_stops(tmp_path):
    path = tmp_path / "L"
    ok("init", path, "--intent", "serve the page", "--criterion", "page serves")
    try:
        ok("boot-runtime", path, "--name", "e2e-sandbox",
           "--command", "python3 -m http.server {port} --bind 127.0.0.1",
           "--cwd", tmp_path, "--timeout", "20")
        ok("validate-infra", path)
        state = status(path)
        runtime = state["infra"]["runtimes"][0]
        assert runtime["stopped"] is False
        ok("stop-runtime", path, "--name", "e2e-sandbox")
        assert status(path)["infra"]["runtimes"][0]["stopped"] is True
        ok("validate-infra", path)
    finally:
        run("stop-runtime", path, "--name", "e2e-sandbox")
