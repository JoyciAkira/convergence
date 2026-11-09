"""Ledger state: append-only logs, replay, transactions and the magnet's edges.

`StateMixin` owns everything that turns four JSONL files into a replayed state:
the advisory lock, the transaction, crash recovery, creation, the frozen intent
and the delegation to pure derivation. Atomic compaction lives in `compaction.py`.
Gates live in `gates`, `decide`, `evidence`, `intake`, `infra` and `preflight`.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:  # Windows: no fcntl; fall back to msvcrt or no-op lock
    fcntl = None
    _HAS_FCNTL = False

from comparator import ClaimComparator, JaccardComparator
from compaction import compact_locked, recover_pending
from constants import LEDGER_VERSION, LOCK, LOGS
from derivation import (
    auto_critique as derive_auto_critique,
    next_action as derive_next_action,
    outcome as derive_outcome,
    pipeline_route as derive_pipeline_route,
    report as derive_report,
)
from events import apply_event
from exceptions import Blocked, IntakeError, UsageError
from runtime import RealOSRuntimeManager, RuntimeManager

def _lock_exclusive(handle):
    """Portable exclusive lock: fcntl on POSIX, msvcrt on Windows, no-op otherwise."""
    if _HAS_FCNTL:
        fcntl.flock(handle, fcntl.LOCK_EX)
        return
    try:
        import msvcrt
        import os
        handle.flush()
        try:
            os.fseek(handle.fileno(), 0, os.SEEK_SET)
        except OSError:
            pass
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    except (ImportError, OSError):
        return

def _unlock(handle):
    if _HAS_FCNTL:
        fcntl.flock(handle, fcntl.LOCK_UN)
        return
    try:
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except (ImportError, OSError):
        return

def transactional(fn):
    """Run a mutator against a freshly replayed state, under the ledger lock."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self.transaction():
            return fn(self, *args, **kwargs)

    return wrapper

def req(value, label):
    text = "" if value is None else str(value).strip()
    if not text:
        raise UsageError(f"{label} is required and must be non-empty")
    return text

def req_list(values, label):
    items = [str(v).strip() for v in (values or []) if str(v).strip()]
    if not items:
        raise UsageError(f"{label} is required and must contain at least one entry")
    return items

def one_of(value, allowed, label):
    text = req(value, label)
    if text not in allowed:
        raise UsageError(f"{label} must be one of {list(allowed)}, got {text!r}")
    return text

def _comparable(state):
    """State minus the bookkeeping counter, for snapshot/replay equality checks.

    `events` increments on every applied event, so a snapshot replay necessarily
    counts one more application than the run that produced it; everything else in
    the state must match exactly.
    """
    return {k: v for k, v in state.items() if k != "events"}

def empty_state():
    return {
        "version": LEDGER_VERSION,
        "intent": None,
        "intent_frozen": False,
        "intent_freeze_at": None,
        "criteria": [],
        "loops": {},
        "hypotheses": {},
        "fixes": {},
        "forks": {},
        "disputes": [],
        "decisions": {},
        "evidence": [],
        "classifications": [],
        "resolved_blocks": [],
        "risks": [],
        "directive": None,
        "criterion_revisions": [],
        "events": 0,
        "infra": {},
        "meta": {},
        "tickets": [],
    }

class StateMixin:
    """Storage, replay and the frozen right edge."""

    def __init__(self, root, runtime_manager: RuntimeManager | None = None,
                 claim_comparator: ClaimComparator | None = None,
                 _lock_held=False, _recover=True):
        self.root = Path(root)
        self.state = empty_state()
        self.runtime_manager = runtime_manager or RealOSRuntimeManager()
        self.claim_comparator = claim_comparator or JaccardComparator()
        if not self.root.exists():
            raise UsageError(f"ledger directory does not exist: {self.root}")
        if _lock_held:
            if _recover:
                recover_pending(self.root)
            self._replay_all()
        else:
            # Recovery and replay must see the same locked snapshot as a write.
            with self.transaction():
                pass

    def _replay_all(self):
        self.state = empty_state()
        for name in ("control", "hypotheses", "forks", "evidence"):
            self._replay(self.root / LOGS[name])

    @contextmanager
    def transaction(self):
        """Hold the ledger lock across read-validate-write, against fresh state.

        Several agents are expected to drive one ledger. Validating against a state
        loaded at construction time is not enough: another process may land a write
        between our read and our append, and the gate we just passed would then be
        stale. Everything a mutator checks and writes happens inside this section.
        """
        with (self.root / LOCK).open("a") as handle:
            _lock_exclusive(handle)
            try:
                # A crashed compaction may have left half-truncated logs. Restore
                # its archive before anyone reads or extends them.
                recover_pending(self.root)
                self._replay_all()
                yield
            finally:
                _unlock(handle)

    def compact(self):
        """Compact under the ledger lock; see `compaction.py` for the transaction."""
        with (self.root / LOCK).open("a") as handle:
            _lock_exclusive(handle)
            try:
                recover_pending(self.root)
                return compact_locked(self)
            finally:
                _unlock(handle)

    def _replay(self, path, state=None):
        target = self.state if state is None else state
        if not path.exists():
            return
        text = path.read_text()
        lines = text.splitlines()
        for lineno, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError as exc:
                # A crash can cut the final append before its newline. That
                # tail was never a record; the intact prefix is the ledger.
                # A terminated line was written in full, so corruption there
                # is still refused rather than guessed away.
                if lineno == len(lines) and not text.endswith("\n"):
                    break
                raise UsageError(f"corrupt ledger: {path.name}:{lineno} is not JSON") from exc
            try:
                apply_event(target, ev)
            except (Blocked, UsageError) as exc:
                raise UsageError(f"corrupt ledger: {path.name}:{lineno}: {exc}") from exc

    def _repair_tail(self, path):
        """Repair an interrupted final append before writing after it.

        A torn tail is dropped; a complete record that lost only its newline is
        kept (a newline is added) so its already-replayed event cannot vanish.
        """
        if not path.exists():
            return
        data = path.read_bytes()
        if not data or data.endswith(b"\n"):
            return
        tail = data.rsplit(b"\n", 1)[-1]
        try:
            json.loads(tail.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            cut = data.rfind(b"\n")
            path.write_bytes(data[:cut + 1] if cut >= 0 else b"")
        else:
            path.write_bytes(data + b"\n")

    def _append(self, name, event):
        """Validate against a copy first, so a refusal never reaches the disk."""
        event = dict(event, at=time.time())
        trial = copy.deepcopy(self.state)
        apply_event(trial, event)
        path = self.root / LOGS[name]
        self._repair_tail(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        self.state = trial

    @classmethod
    def create(cls, root, intent, criteria=(), freeze=False, exploratory=False, critic=None):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        with (root / LOCK).open("a") as handle:
            _lock_exclusive(handle)
            try:
                if (root / LOGS["control"]).exists():
                    raise Blocked(f"ledger already initialised: {root}")
                if critic is None:
                    from prompt import critic_flags
                    critic = critic_flags(intent, criteria)
                restatements = [f for f in (critic or []) if f.get("flag") == "restates_intent"]
                if restatements:
                    raise IntakeError(
                        "a criterion restates the intent instead of checking it, so it "
                        "passes by construction and can never fail: "
                        + "; ".join(repr(f["criterion"]) for f in restatements)
                        + ". Name an observable consequence that would be false if the "
                        "intent were not met."
                    )
                for name in LOGS.values():
                    (root / name).touch()
                ledger = cls(root, _lock_held=True)
                ledger._append(
                    "control",
                    {
                        "ev": "init",
                        "intent": req(intent, "--intent"),
                        "criteria": list(criteria or []),
                        "exploratory": bool(exploratory),
                        "critic": list(critic or []),
                    },
                )
                if freeze:
                    if not ledger.state["criteria"]:
                        raise Blocked(
                            "cannot freeze an intent that still has no acceptance criteria"
                        )
                    # Not freeze_intent(): that would re-enter the lock this
                    # classmethod already holds, and flock is not re-entrant across
                    # two open() descriptions of the same file — the second waits
                    # forever. Append the event directly; we are already inside the
                    # read-validate-write section.
                    ledger._append("control", {"ev": "intent_frozen"})
                return ledger
            finally:
                _unlock(handle)

    @transactional
    def freeze_intent(self):
        """Seal the right edge signal: after this, intent/criteria may move only via
        disputed fork. This is the intake rigor (1) and intent freeze (2) gate."""
        if self.state["intent_frozen"]:
            raise Blocked("intent already frozen")
        if not self.state["criteria"]:
            raise Blocked("cannot freeze an intent that still has no acceptance criteria")
        self._append("control", {"ev": "intent_frozen"})
        return self

    @staticmethod
    def validate_intent(intent, criteria, state_hook=None):
        """Intake gate: before init, validate the requested magnet.

        This is the place where an agent should stop and interview the request
        rather than inventing criteria. It enforces the shape that must hold
        before freeze_intent can succeed.
        """
        from exceptions import IntakeError
        intent = req(intent, "--intent")
        criteria = [str(c).strip() for c in (criteria or []) if str(c).strip()]
        if not criteria:
            raise IntakeError(
                "intent has no criteria; the magnet has nothing to pull against. "
                "Each criterion must be closeable by a command someone else could re-run."
            )
        if state_hook is not None and not callable(state_hook):
            raise IntakeError("state_hook must be callable")
        if any(len(c) < 4 for c in criteria):
            raise IntakeError(
                "a criterion shorter than 4 characters is unlikely to be verifiable: "
                f"{criteria}"
            )
        from prompt import critic_flags
        restatements = [f for f in critic_flags(intent, criteria) if f["flag"] == "restates_intent"]
        if restatements:
            raise IntakeError(
                "a criterion restates the intent instead of checking it, so it "
                "passes by construction and can never fail: "
                + "; ".join(repr(f["criterion"]) for f in restatements)
                + ". Name an observable consequence that would be false if the "
                "intent were not met."
            )
        return {"intent": intent, "criteria": criteria}

    def _resolved_signatures(self):
        """Signatures of blocks that have been resolved via input."""
        return {b["signature"] for b in self.state.get("resolved_blocks", [])}

    def outcome(self):
        """Delegate to pure derivation module."""
        return derive_outcome(self.state)

    def report(self):
        """Delegate to pure derivation module."""
        return derive_report(self.state)

    def next_action(self):
        """Delegate to pure derivation module, passing validate_infra as callback."""
        return derive_next_action(self.state, validate_infra_fn=self.validate_infra)

    def auto_critique(self):
        """The run's mechanical self-critique (10), derived from the same state as
        the verdict: what a hostile reviewer would find, named first."""
        return derive_auto_critique(self.state)

    def pipeline_route(self):
        """Lane routing for an upper pipeline (4): one word to route on, plus the
        fallback lane when the primary one is blocked from outside the session."""
        return derive_pipeline_route(self.state, validate_infra_fn=self.validate_infra)
