"""Unit tests for lib/obligations.py - isolated, no ledger side effects."""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from obligations import (
    parse_obligation_markdown,
    parse_obligation_file,
    validate_obligations,
    parse_obligations,
    obligations_to_criteria,
)
from exceptions import IntakeError, UsageError


class TestParseObligationMarkdown:
    def test_empty_returns_empty(self):
        assert parse_obligation_markdown("") == []

    def test_minimal_heading_and_criteria(self):
        md = "# OBL-1: Parse input\n## Acceptance criteria\n- [ ] handles CRLF\n"
        result = parse_obligation_markdown(md)
        assert len(result) == 1
        assert result[0]["id"] == "OBL-1"
        assert result[0]["title"] == "Parse input"
        assert "handles CRLF" in result[0]["criteria"]

    def test_target_and_category_extracted(self):
        md = "# OBL-2: Decode\n**Target:** binary.exe\n**Category:** codec\n## Criteria\n- [x] decodes UTF-8\n"
        result = parse_obligation_markdown(md)
        assert result[0]["target"] == "binary.exe"
        assert result[0]["category"] == "codec"

    def test_blocked_by_parsed(self):
        md = "# OBL-3: Link\n**Blocked by:** OBL-1, OBL-2\n## Criteria\n- [ ] links symbols\n"
        result = parse_obligation_markdown(md)
        assert "OBL-1" in result[0]["blocked_by"]
        assert "OBL-2" in result[0]["blocked_by"]

    def test_id_from_filename_fallback(self):
        md = "# Just a title\n## Criteria\n- [ ] does something useful\n"
        result = parse_obligation_markdown(md, filename="REQ-42_extra.md")
        assert result[0]["id"] == "REQ-42"

    def test_no_criteria_no_title_returns_empty(self):
        md = "Some random text without structure.\n"
        assert parse_obligation_markdown(md) == []

    def test_multiple_criteria_sections(self):
        md = "# T-1: Multi\n## Acceptance criteria\n- [ ] first check here\n## Requirements\n- [ ] second check here\n"
        result = parse_obligation_markdown(md)
        assert len(result[0]["criteria"]) == 2


class TestParseObligationFile:
    def test_json_file(self, tmp_path):
        data = {"obligations": [{"id": "J-1", "title": "Json ob", "criteria": ["passes validation"]}]}
        p = tmp_path / "obs.json"
        p.write_text(json.dumps(data))
        result = parse_obligation_file(p)
        assert result[0]["id"] == "J-1"

    def test_md_file(self, tmp_path):
        md = "# M-1: Md ob\n## Criteria\n- [ ] works correctly\n"
        p = tmp_path / "obs.md"
        p.write_text(md)
        result = parse_obligation_file(p)
        assert result[0]["id"] == "M-1"

    def test_malformed_json_raises_usage_error(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        with pytest.raises(UsageError):
            parse_obligation_file(p)

    def test_fallback_to_markdown_on_non_json(self, tmp_path):
        p = tmp_path / "obs.txt"
        p.write_text("# F-1: Fallback\n## Criteria\n- [ ] fallback works now\n")
        result = parse_obligation_file(p)
        assert result[0]["id"] == "F-1"


class TestValidateObligations:
    def test_empty_raises_intake_error(self):
        with pytest.raises(IntakeError, match="no obligations"):
            validate_obligations([], source_name="test")

    def test_missing_id_raises(self):
        with pytest.raises(IntakeError, match="missing id"):
            validate_obligations([{"title": "No ID", "criteria": ["check this"]}], "t")

    def test_missing_title_raises(self):
        with pytest.raises(IntakeError, match="missing title"):
            validate_obligations([{"id": "X-1", "criteria": ["check this"]}], "t")

    def test_no_criteria_raises(self):
        with pytest.raises(IntakeError, match="no acceptance criteria"):
            validate_obligations([{"id": "X-1", "title": "T"}], "t")

    def test_short_criterion_raises(self):
        with pytest.raises(IntakeError, match="shorter than 4"):
            validate_obligations([{"id": "X-1", "title": "T", "criteria": ["ab"]}], "t")

    def test_duplicate_id_raises(self):
        items = [
            {"id": "D-1", "title": "A", "criteria": ["check one thing"]},
            {"id": "D-1", "title": "B", "criteria": ["check two thing"]},
        ]
        with pytest.raises(UsageError, match="duplicate obligation id"):
            validate_obligations(items, "t")

    def test_duplicate_criterion_across_obligations_raises(self):
        items = [
            {"id": "A-1", "title": "A", "criteria": ["shared criterion"]},
            {"id": "A-2", "title": "B", "criteria": ["shared criterion"]},
        ]
        with pytest.raises(UsageError, match="appears in multiple"):
            validate_obligations(items, "t")

    def test_blocked_by_unknown_raises(self):
        items = [{"id": "B-1", "title": "T", "criteria": ["check it"], "blocked_by": ["GHOST"]}]
        with pytest.raises(UsageError, match="unknown obligation"):
            validate_obligations(items, "t")

    def test_valid_passes(self):
        items = [{"id": "V-1", "title": "Valid", "criteria": ["criterion passes ok"]}]
        result = validate_obligations(items, "t")
        assert result[0]["id"] == "V-1"


class TestParseObligations:
    def test_nonexistent_path_raises(self, tmp_path):
        with pytest.raises(UsageError, match="does not exist"):
            parse_obligations(tmp_path / "nope")

    def test_empty_directory_raises(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(IntakeError, match="no obligation files"):
            parse_obligations(d)

    def test_directory_with_files(self, tmp_path):
        d = tmp_path / "obs"
        d.mkdir()
        (d / "a.md").write_text("# A-1: First\n## Criteria\n- [ ] first criterion ok\n")
        (d / "b.md").write_text("# B-1: Second\n## Criteria\n- [ ] second criterion ok\n")
        result = parse_obligations(d)
        ids = {r["id"] for r in result}
        assert "A-1" in ids and "B-1" in ids


class TestJsonShapes:
    def test_single_obligation_dict_is_promoted(self, tmp_path):
        p = tmp_path / "single.json"
        p.write_text(json.dumps({"id": "S-1", "title": "Single",
                                 "criteria": ["single check works"]}))
        assert parse_obligation_file(p)[0]["id"] == "S-1"

    def test_top_level_target_propagates_to_items(self, tmp_path):
        p = tmp_path / "target.json"
        p.write_text(json.dumps({
            "target": "libfoo.so",
            "obligations": [{"id": "T-1", "title": "T",
                             "criteria": ["propagated check"]}],
        }))
        assert parse_obligation_file(p)[0]["target"] == "libfoo.so"

    def test_item_target_is_not_overwritten(self, tmp_path):
        p = tmp_path / "own.json"
        p.write_text(json.dumps({
            "target": "top.so",
            "obligations": [{"id": "T-2", "title": "T", "target": "own.so",
                             "criteria": ["own target kept"]}],
        }))
        assert parse_obligation_file(p)[0]["target"] == "own.so"

    def test_non_dict_items_are_skipped(self, tmp_path):
        p = tmp_path / "mixed.json"
        p.write_text(json.dumps({"obligations": [
            {"id": "N-1", "title": "N", "criteria": ["valid check here"]},
            "junk", 42,
        ]}))
        assert [o["id"] for o in parse_obligation_file(p)] == ["N-1"]

    def test_blocked_by_string_is_split_and_none_filtered(self, tmp_path):
        p = tmp_path / "deps.json"
        p.write_text(json.dumps({"obligations": [
            {"id": "B-1", "title": "B", "criteria": ["blocker check ok"]},
            {"id": "B-2", "title": "C", "criteria": ["dependent check ok"],
             "blocked_by": "B-1, none"},
        ]}))
        assert parse_obligation_file(p)[1]["blocked_by"] == ["B-1"]

    def test_nested_reconstruction_manifest_is_read(self, tmp_path):
        p = tmp_path / "nested.json"
        p.write_text(json.dumps({"reconstruction_manifest": {"obligations": [
            {"id": "M-1", "title": "M", "criteria": ["manifest check ok"]},
        ]}}))
        assert parse_obligation_file(p)[0]["id"] == "M-1"

    def test_unrelated_json_yields_no_obligations(self, tmp_path):
        p = tmp_path / "unrelated.json"
        p.write_text(json.dumps({"unrelated": True}))
        assert parse_obligation_file(p) == []


def test_markdown_type_sets_category():
    md = "# T-1: Typed\n**Type:** control_flow\n## Criteria\n- [ ] branches reproduce\n"
    assert parse_obligation_markdown(md)[0]["category"] == "control_flow"


def test_blocked_by_none_is_empty():
    md = "# T-1: Free\n**Blocked by:** none\n## Criteria\n- [ ] free check ok\n"
    assert parse_obligation_markdown(md)[0]["blocked_by"] == []


def test_blocked_by_underscore_alias():
    md = "# T-2: Alias\n**Blocked_by:** #T-1\n## Criteria\n- [ ] alias check ok\n"
    assert parse_obligation_markdown(md)[0]["blocked_by"] == ["T-1"]


def test_markdown_criteria_only_uses_filename_id():
    md = "## Criteria\n- [ ] filename identifies me\n"
    result = parse_obligation_markdown(md, filename="OBL-9.md")
    assert result[0]["id"] == "OBL-9"


def test_txt_file_with_valid_json_parses(tmp_path):
    p = tmp_path / "obs.txt"
    p.write_text(json.dumps({"obligations": [
        {"id": "TX-1", "title": "T", "criteria": ["txt json check"]},
    ]}))
    assert parse_obligation_file(p)[0]["id"] == "TX-1"


def test_name_field_used_as_title():
    items = [{"id": "N-1", "name": "From name", "criteria": ["name check works"]}]
    assert validate_obligations(items, "t")[0]["title"] == "From name"


def test_criteria_as_string_is_wrapped():
    items = [{"id": "S-1", "title": "T", "criteria": "one string check"}]
    assert validate_obligations(items, "t")[0]["criteria"] == ["one string check"]


def test_criteria_of_wrong_type_is_refused():
    with pytest.raises(IntakeError, match="no acceptance criteria"):
        validate_obligations([{"id": "X-1", "title": "T", "criteria": 42}], "t")


def test_blocked_by_string_with_none_token():
    items = [
        {"id": "A-1", "title": "A", "criteria": ["first check ok"]},
        {"id": "A-2", "title": "B", "criteria": ["second check ok"],
         "blocked_by": "A-1, none"},
    ]
    assert validate_obligations(items, "t")[1]["blocked_by"] == ["A-1"]


def test_non_dict_item_is_refused():
    with pytest.raises(IntakeError, match="must be an object"):
        validate_obligations(["junk"], "t")


class TestObligationsToCriteria:
    def test_flattens_in_order(self):
        obs = [
            {"criteria": ["alpha check ok", "beta check ok"]},
            {"criteria": ["gamma check ok"]},
        ]
        assert obligations_to_criteria(obs) == ["alpha check ok", "beta check ok", "gamma check ok"]

    def test_empty(self):
        assert obligations_to_criteria([]) == []
