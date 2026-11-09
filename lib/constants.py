"""Frozen vocabulary of the ledger: statuses, widths, anchors, logs.

Kept in one leaf module so gates, derivation and the CLI cannot disagree about a
threshold or a status string. Nothing here imports anything else.
"""
from __future__ import annotations

LEDGER_VERSION = 2

OK = 0

#: Advisory lock held across the whole read-validate-write section. Several agents
#: are expected to drive one ledger; without this, two of them racing to record the
#: same hypothesis both validate against the same pre-write state and both land,
#: and the padding guard becomes advisory.
LOCK = ".lock"
USAGE = 2
BLOCKED = 3

#: Decisions touching any of these are never within delegated agent authority.
HUMAN_ANCHORS = (
    "security",
    "privacy",
    "data_integrity",
    "public_contract",
    "money",
    "legal",
    "irreversible_action",
)

REVERSIBILITIES = ("reversible", "irreversible")
EVIDENCE_TIERS = ("executed_here", "reproduced_independently", "unavailable")

#: `code` means the expectation was recomputed from the implementation. That is a
#: tautology and can never disagree with the code, so it is rejected outright.
EXPECTED_SOURCES = (
    "spec", "literal", "worked_example", "independent_oracle",
    "binary_oracle", "rea_evidence", "code",
)

#: A fix may not be written before this many distinct hypotheses have been tested.
#: One plausible idea anchoring the whole investigation is the failure mode.
MIN_HYPOTHESES = 3

#: Two claims in the SAME incident whose content words overlap at least this much
#: are treated as the same hypothesis. String equality cannot stop paraphrase, and
#: paraphrase is how a ledger gets padded to satisfy a minimum.
CLAIM_OVERLAP_THRESHOLD = 0.7

#: Rival explanations that must be settled before a fix, by declared search width.
#: `narrow` is not a bypass: it is recorded on the fix and disclosed in the report.
SEARCH_WIDTH = {"narrow": 1, "full": MIN_HYPOTHESES}

#: Where candidate solutions may come from. The ledger does not generate candidates;
#: it records provenance so a later selection can name what it chose among.
CANDIDATE_SOURCES = ("arena", "parallel", "manual", "other")

#: Rows rendered per report section before the report points at the ledger instead.
REPORT_ROW_CAP = 12

CLOSED_CONDITIONS = (
    "every criterion verified",
    "no open hypothesis",
    "no undecided fork",
    "no unresolved missing-fact block",
    "no unrebuilt defective loop",
    "no blocking risk",
    "no criterion verified unavailable",
    "no criterion never verified",
    "infra ready or none registered",
)

DIRECTIVE_MESSAGE = {
    "loop_defective": (
        "Rebuild the feedback loop before theorising. Declare a command that is "
        "red-capable, deterministic and fast, and run it. No hypothesis may be "
        "written until that command exists and has gone red on this exact symptom."
    ),
    "hypothesis_unresolved": (
        "An untested hypothesis blocks new lines of attack. Refute it, confirm it, "
        "or merge it. Do not start a fresh investigation while one is untested."
    ),
}

LOGS = {
    "control": "control.jsonl",
    "hypotheses": "hypotheses.jsonl",
    "forks": "forks.jsonl",
    "evidence": "evidence.jsonl",
}

#: Infrastructure capabilities that turn parked paths into executable work.
INFRA_COMMANDS = (
    "provision-env", "inject-build-context", "store-secret",
    "boot-runtime", "stop-runtime",
)

#: The magnet has two poles and one question: was the outcome reached? Everything
#: else is a reason it was not, not a lesser kind of success.
FINAL_STATUSES = ("REACHED", "NOT REACHED")

#: A block must be earned: this many distinct routes must have been tried and
#: recorded before a missing fact counts as unobtainable rather than unlooked-for.
MIN_BLOCK_ROUTES = 2

#: After a non-convergence classification, which actions satisfy it.
DIRECTIVE_REQUIRES = {
    "loop_defective": ("declare_loop", "run_loop", "hyp_open"),
    "hypothesis_unresolved": ("hyp_refute", "hyp_survive", "hyp_merge"),
    "information_missing": (),
}
