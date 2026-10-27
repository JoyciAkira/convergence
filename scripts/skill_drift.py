#!/usr/bin/env python3
"""Anti-drift guard: prose gates in SKILL.md must exist as machine gates in lib/.

Checks:
  1. SKILL.md frontmatter has name, description, triggers, argument-hint.
  2. Every key refusal theme is implemented somewhere in lib/ and covered
     by at least one behavioural test in tests/.
  3. LEDGER_VERSION is referenced by the prose maintenance section.

Exit 0 when everything maps, 1 with a report otherwise.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"
LIB = ROOT / "lib"
TESTS = ROOT / "tests"

# refusal theme -> (patterns that must appear in lib, patterns in tests)
GATES = {
    "red-capable loop": (["ran_red_loop", "did not go red"], ["did not go red", "never run"]),
    "paraphrase/padding": (["CLAIM_OVERLAP_THRESHOLD", "already recorded"], ["already recorded", "restat"]),
    "probe+observation": (["--probe", "no probe"], ["probe"]),
    "fix gate": (["begin_fix", "declared `--search`"], ["begin_fix", "requires 3", "requires 1"]),
    "directive": (["DIRECTIVE_REQUIRES", "outstanding"], ["loop_defective", "hypothesis_unresolved"]),
    "information_missing": (["missing_fact", "smallest_input", "MIN_BLOCK_ROUTES"], ["information_missing", "missing-fact"]),
    "single-option fork": (["invalidation_rationale", "invalidation-rationale"], ["invalidation"]),
    "dispute-before-decide": (["dispute", "no dispute"], ["dispute"]),
    "self-dispute": (["challenger is the author", "challenger"], ["challenger"]),
    "human anchors": (["HUMAN_ANCHORS", "human_ack"], ["human", "anchor", "irreversib"]),
    "challenger-overruled": (["challenger_overruled", "override-reason"], ["overruled"]),
    "tautology": (["expected_source", "recomputed from the"], ["tautology", "expected_source", "expected-source"]),
    "self-reproduction": (["reproduced_by", "Re-reading your own"], ["reproduced"]),
    "unavailable": (["unavailable", "--reason"], ["unavailable"]),
    "criterion revision": (["criterion_from", "criterion-to", "invalidates"], ["criterion"]),
    "interview questions": (["interview_questions", "needs_interview"], ["interview", "needs_interview"]),
}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def check_frontmatter(prose: str) -> list[str]:
    problems: list[str] = []
    m = re.match(r"^---\n(.*?)\n---\n", prose, re.DOTALL)
    if not m:
        return ["SKILL.md has no YAML frontmatter block"]
    head = m.group(1)
    for key in ("name:", "description:", "triggers:", "argument-hint:"):
        if key not in head:
            problems.append(f"frontmatter missing {key.rstrip(':')}")
    return problems


def main() -> int:
    problems: list[str] = []
    prose = _read(SKILL)
    problems.extend(check_frontmatter(prose))

    lib_text = "\n".join(_read(p) for p in sorted(LIB.glob("*.py")))
    test_text = "\n".join(_read(p) for p in sorted(TESTS.glob("test_*.py")))

    for theme, (lib_pats, test_pats) in GATES.items():
        if not any(p.lower() in lib_text.lower() for p in lib_pats):
            problems.append(f"drift: '{theme}' in prose but no gate found in lib/")
        if not any(p.lower() in test_text.lower() for p in test_pats):
            problems.append(f"drift: '{theme}' has no behavioural test coverage")

    if "LEDGER_VERSION" not in lib_text:
        problems.append("drift: LEDGER_VERSION missing from lib/")
    if "LEDGER_VERSION" not in prose and "ledger maintenance" not in prose.lower():
        problems.append("drift: ledger maintenance/versioning not documented in SKILL.md")

    if problems:
        print("skill-drift: FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"skill-drift: OK ({len(GATES)} refusal themes mapped prose->lib->tests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
