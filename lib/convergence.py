"""Convergence ledger - an append-only state machine that refuses unearned progress.

This module is the public face of the `convergence` skill. The SKILL.md tells the
agent what to do; the modules below make the dishonest paths impossible to execute.
Every gate exists because the surrounding skill library states the rule in prose and
then lets the agent skip it.

Layout:
  * `state`      - replay, transactions, compaction, the frozen right edge
  * `gates`      - Entry A/C: red loop, hypotheses, fix gate, classification
  * `decide`     - Entry B: forks, adversarial dispute, authority
  * `evidence`   - three tiers, anti-tautology control
  * `intake`     - tickets, reconstruction obligations, candidates
  * `infra`      - provisioning, secrets, runtime lifecycle
  * `preflight`  - are the prerequisites actually available
  * `cli_parser` / `cli` - the argument surface and dispatch
  * `ledger`     - the mixins composed into one `Ledger`

Design constraints:
  * Append-only. All four logs are JSONL, never rewritten. State is derived by replay,
    so a run is resumable across compaction, crash, and session change.
  * No counters terminate a run. Repetition of a failure signature is classified, not
    counted. The only terminal stop is a named missing fact.
  * Malformed input is a UsageError (exit 2). A refused action is Blocked (exit 3).
    Success is exit 0. A caller can gate on the exit code without parsing prose.

See SKILL.md for the operational contract these gates implement.
"""
from __future__ import annotations

import sys

from claims import _claim_words, _closest, _overlap
from cli import _dispatch, _one_shot, main
from cli_parser import _build_parser, _options
from comparator import ClaimComparator, JaccardComparator
from constants import (
    BLOCKED,
    CANDIDATE_SOURCES,
    CLAIM_OVERLAP_THRESHOLD,
    CLOSED_CONDITIONS,
    DIRECTIVE_MESSAGE,
    DIRECTIVE_REQUIRES,
    EVIDENCE_TIERS,
    EXPECTED_SOURCES,
    FINAL_STATUSES,
    HUMAN_ANCHORS,
    INFRA_COMMANDS,
    LEDGER_VERSION,
    LOCK,
    LOGS,
    MIN_BLOCK_ROUTES,
    MIN_HYPOTHESES,
    OK,
    REPORT_ROW_CAP,
    REVERSIBILITIES,
    SEARCH_WIDTH,
    USAGE,
)
from decide import DecideMixin
from evidence import EvidenceMixin
from events import HANDLERS, apply_event
from exceptions import Blocked, IntakeError, UsageError
from gates import (
    ACTION_VERBS,
    DELEGATE_ACTORS,
    LOCAL_TOOLS,
    GateMixin,
    _looks_like_local_action,
)
from infra import (
    CATALOG_SPECS,
    InfraMixin,
    _free_port,
    _kill_process,
    _listener_pids,
    _port_open,
    _retire_port,
    _tail,
)
from intake import IntakeMixin
from ledger import Ledger
from preflight import PreflightMixin
from rendering import rows
from runtime import FakeRuntimeManager, RealOSRuntimeManager, RuntimeManager
from state import (
    StateMixin,
    _comparable,
    _lock_exclusive,
    _unlock,
    empty_state,
    one_of,
    req,
    req_list,
    transactional,
)

if __name__ == "__main__":
    sys.exit(main())
