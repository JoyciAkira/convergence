#!/usr/bin/env python3
"""Anti-drift guard: prose gates in SKILL.md must exist as machine gates in lib/.

Checks:
  1. SKILL.md frontmatter has name, description, triggers, argument-hint.
  2. Every key refusal theme is implemented somewhere in lib/ and covered
     by at least one behavioural test in tests/.
  3. LEDGER_VERSION is referenced by the prose maintenance section.
  4. The layout holds: convergence.py is a facade, and the domain modules that
     own the gates stay under their line caps.
  5. Every flag the CLI accepts is named in SKILL.md: adding a flag is a change
     to the contract, and a contract nobody can read is not one.

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


# module -> maximum lines. convergence.py must stay a facade so a new gate lands in
# the module that owns it; the other three own the most rule-dense behaviour and are
# the ones that rot back into a monolith first.
LAYOUT = {
    "convergence.py": 150,
    "cli.py": 400,
    "gates.py": 400,
    "state.py": 400,
}


def check_layout() -> list[str]:
    problems: list[str] = []
    for name, cap in LAYOUT.items():
        path = LIB / name
        if not path.exists():
            problems.append(f"layout: {name} is missing; convergence.py is a facade over domain modules")
            continue
        size = len(_read(path).splitlines())
        if size > cap:
            problems.append(
                f"layout: {name} is {size} lines (cap {cap}) — move the new rule into the module that owns it"
            )
    return problems


#: Flags the CLI may expose without prose: argparse's own help.
UNDOCUMENTED_OK = frozenset(("-h", "--help"))


def parser_flags() -> set[str]:
    """Every option string the ledger CLI exposes, subcommands included."""
    sys.path.insert(0, str(LIB))
    from cli_parser import _build_parser

    flags: set[str] = set()
    for action in _build_parser()._actions:
        flags.update(action.option_strings)
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for sub in choices.values():
                for sub_action in sub._actions:
                    flags.update(sub_action.option_strings)
    return flags


def check_flags(flags: set[str], prose: str) -> list[str]:
    """A flag nobody documented is a capability the skill cannot be held to."""
    return [
        f"drift: CLI flag {flag} is not documented in SKILL.md"
        for flag in sorted(flags - UNDOCUMENTED_OK)
        if flag not in prose
    ]


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

    problems.extend(check_layout())
    flags = parser_flags()
    problems.extend(check_flags(flags, prose))

    if "LEDGER_VERSION" not in lib_text:
        problems.append("drift: LEDGER_VERSION missing from lib/")
    if "LEDGER_VERSION" not in prose and "ledger maintenance" not in prose.lower():
        problems.append("drift: ledger maintenance/versioning not documented in SKILL.md")

    if problems:
        print("skill-drift: FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        f"skill-drift: OK ({len(GATES)} refusal themes mapped prose->lib->tests; "
        f"layout {len(LAYOUT)} caps held; {len(flags)} CLI flags documented)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
