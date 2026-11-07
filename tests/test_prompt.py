"""Unit tests for lib/prompt.py - isolated, no ledger side effects."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from prompt import load_text, parse_prompt
from exceptions import IntakeError, UsageError


def _texts(items):
    """Extract text field from structured criterion/hypothesis/exclusion dicts."""
    return [i["text"] if isinstance(i, dict) else i for i in items]


class TestLoadText:
    def test_load_existing_file(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("hello world")
        assert load_text(str(p)) == "hello world"

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(UsageError, match="not a readable file"):
            load_text(str(tmp_path / "nope.txt"))

    def test_load_stdin_dash(self, monkeypatch):
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO("from stdin"))
        assert load_text("-") == "from stdin"


class TestParsePrompt:
    def test_empty_raises_intake_error(self):
        with pytest.raises(IntakeError):
            parse_prompt("")

    def test_intent_from_marker(self):
        text = "Goal: ship the parser\n- [ ] handles CRLF\n"
        result = parse_prompt(text)
        assert result["intent"] == "ship the parser"
        assert "handles CRLF" in _texts(result["criteria"])

    def test_intent_from_heading_and_paragraph(self):
        text = "# Ship the parser\n\nThe parser must handle CRLF.\n"
        result = parse_prompt(text)
        assert "Ship the parser" in result["intent"]

    def test_checkboxes_extracted_as_criteria(self):
        text = "# Task\n## Acceptance criteria\n- [x] first check ok\n- [ ] second check ok\n"
        result = parse_prompt(text)
        texts = _texts(result["criteria"])
        assert "first check ok" in texts
        assert "second check ok" in texts

    def test_deontic_sentences_extracted(self):
        text = "# Task\nThe system must reject malformed input.\nIt shall log errors.\n"
        result = parse_prompt(text)
        texts = _texts(result["criteria"])
        assert any("reject malformed" in c.lower() for c in texts)

    def test_hypotheses_extracted(self):
        text = "# Task\n## Hypotheses\n- latency is under 100ms\n## Criteria\n- [ ] responds fast enough\n"
        result = parse_prompt(text)
        hyp_texts = _texts(result.get("hypotheses", []))
        assert any("latency" in h.lower() for h in hyp_texts)

    def test_exclusions_extracted(self):
        text = "# Task\n## Out of scope\n- mobile support\n## Criteria\n- [ ] desktop works ok\n"
        result = parse_prompt(text)
        exc_texts = _texts(result.get("exclusions", []))
        assert any("mobile" in e.lower() for e in exc_texts)

    def test_imperative_only_refused(self):
        text = "Build a REST API.\nAdd authentication.\n"
        with pytest.raises(IntakeError):
            parse_prompt(text)

    def test_ensure_prefix_promoted(self):
        text = "# Task\nEnsure that the output is valid JSON.\n"
        result = parse_prompt(text)
        texts = _texts(result["criteria"])
        assert any("valid json" in c.lower() for c in texts)

    def test_italian_markers(self):
        text = "Obiettivo: spedire il parser\n## Criteri di accettazione\n- [ ] gestisce CRLF\n"
        result = parse_prompt(text)
        assert "spedire il parser" in result["intent"]
        assert "gestisce CRLF" in _texts(result["criteria"])

    def test_heading_intent_when_next_line_is_a_section(self):
        text = "# Ship the parser\n## Acceptance criteria\n- [ ] handles CRLF ok\n"
        result = parse_prompt(text)
        assert result["intent"] == "Ship the parser"

    def test_body_bullets_are_promoted_when_no_requirement_sentence_exists(self):
        text = "Goal: ship the importer\n- imports legacy files\n- keeps timestamps\n"
        result = parse_prompt(text)
        texts = _texts(result["criteria"])
        assert "imports legacy files" in texts
        assert "keeps timestamps" in texts

    def test_bare_prompt_with_only_an_imperative_is_refused(self):
        text = "Goal: ship the importer\nBuild the whole thing\n"
        with pytest.raises(IntakeError):
            parse_prompt(text)


def test_question_shaped_criterion_is_interviewed():
    from prompt import interview_questions

    questions = interview_questions("ship the parser safely", ["does it handle BOM?"])
    assert any(q.get("criterion") == "does it handle BOM?" for q in questions)


def test_missing_intent_is_interviewed():
    from prompt import interview_questions

    questions = interview_questions("", ["handles CRLF"])
    assert any(q["edge"] == "intent" and "no outcome" in q["question"] for q in questions)


def test_finalise_drops_blank_text_before_deduping():
    from prompt import KIND_CRITERIA, _finalise

    out = _finalise(
        [{"text": "   ", "source": "x"}, {"text": "real check", "source": "y"}],
        KIND_CRITERIA,
    )
    assert out == [{"text": "real check", "source": "y"}]


def test_sentence_items_skips_empty_segments():
    from prompt import _sentence_items

    items = _sentence_items([{"text": "It must log.   It must retry."}], "s")
    assert [i["text"] for i in items] == ["It must log.", "It must retry."]


def test_critic_flags_an_intent_restatement():
    from prompt import critic_flags

    intent = "ship the parser that handles CRLF and rejects malformed input"
    flags = critic_flags(intent, [intent])
    assert flags and flags[0]["flag"] == "restates_intent"


def test_critic_asks_for_a_bound_on_quality_words():
    from prompt import critic_flags

    flags = critic_flags("ship the parser", ["the parser is fast"])
    assert any(f["flag"] == "unbounded_quality" for f in flags)


def test_critic_asks_for_an_oracle_without_a_verb():
    from prompt import critic_flags

    flags = critic_flags("ship the parser", ["the parser is resilient"])
    assert any(f["flag"] == "no_oracle" for f in flags)
