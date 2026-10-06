"""Convergence ledger — an append-only state machine that refuses unearned progress.

This module is the enforcement half of the `convergence` skill. The SKILL.md tells the
agent what to do; this file makes the dishonest paths impossible to execute. Every gate
below exists because the surrounding skill library states the rule in prose and then
lets the agent skip it.

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

import argparse
import copy
import fcntl
import functools
import json
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from runtime import FakeRuntimeManager, RealOSRuntimeManager, RuntimeManager
from events import HANDLERS, apply_event
from derivation import (
    auto_critique as derive_auto_critique,
    next_action as derive_next_action,
    outcome as derive_outcome,
    pipeline_route as derive_pipeline_route,
    report as derive_report,
)
from exceptions import Blocked, IntakeError, UsageError
from comparator import ClaimComparator, JaccardComparator

import hashlib

LEDGER_VERSION = 2

OK = 0

#: Advisory lock held across the whole read-validate-write section. Several agents are
#: expected to drive one ledger; without this, two of them racing to record the same
#: hypothesis both validate against the same pre-write state and both land, and the
#: padding guard becomes advisory.
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
EXPECTED_SOURCES = ("spec", "literal", "worked_example", "independent_oracle", "code")

#: A fix may not be written before this many distinct hypotheses have been tested.
#: One plausible idea anchoring the whole investigation is the failure mode.
MIN_HYPOTHESES = 3

#: Two claims in the SAME incident whose content words overlap at least this much are
#: treated as the same hypothesis. String equality cannot stop paraphrase, and paraphrase
#: is how a ledger gets padded to satisfy a minimum. The check is scoped to one incident
#: because padding happens inside one investigation; unrelated incidents legitimately
#: share a defect class and a phrasing.
CLAIM_OVERLAP_THRESHOLD = 0.7

_STOPWORDS = frozenset(
    "a an the is are was were be been being in of to it its and or for on that this "
    "with from by not has have had does do did when if at as we you they he she".split()
)


def _claim_words(claim):
    return {w for w in re.findall(r"[a-z0-9]+", claim.lower()) if len(w) > 2 and w not in _STOPWORDS}


def _overlap(a, b):
    ka, kb = _claim_words(a), _claim_words(b)
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / len(ka | kb)


def _closest(needle, haystack):
    """The nearest option above a similarity floor, or None. Used only to say
    'did you mean' inside a refusal — never to accept a near-miss."""
    scored = [(_overlap(needle, h), h) for h in haystack]
    scored = [pair for pair in scored if pair[0] >= 0.4]
    return max(scored)[1] if scored else None


#: Rival explanations that must be settled before a fix, by declared search width.
#: `narrow` is not a bypass: it is recorded on the fix and disclosed in the report, so the
#: choice survives even though the verdict is unaffected. For a small, fully reproduced
#: defect whose mechanism is unambiguous, demanding three rivals is theatre.
SEARCH_WIDTH = {"narrow": 1, "full": MIN_HYPOTHESES}

#: Rows rendered per report section before the report points at the ledger instead.
REPORT_ROW_CAP = 12


def rows(items, pointer="the ledger logs", cap=REPORT_ROW_CAP):
    """Cap a section so a 3,000-event run does not produce an unreadable document."""
    items = list(items)
    if not items:
        return ["- None"]
    if len(items) <= cap:
        return items
    return items[:cap] + [f"- …and {len(items) - cap} more; the full record is in {pointer}"]

#: After a non-convergence classification, which actions satisfy it.
#: `loop_defective` must permit the loop itself, or the directive is unsatisfiable:
#: `hyp_open` is gated on a loop that has been declared and has run red.
DIRECTIVE_REQUIRES = {
    "loop_defective": ("declare_loop", "run_loop", "hyp_open"),
    "hypothesis_unresolved": ("hyp_refute", "hyp_survive", "hyp_merge"),
    "information_missing": (),
}

# --------------------------------------------------------------------------- #
# closed conditions: explicit close bar (9)
# --------------------------------------------------------------------------- #

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
#: These are not loopholes — they are the mechanical arms that let the ledger act on
#: the world instead of waiting for a human to paste values, copy node_modules, or
#: provision a database by hand. Each one records its action in the ledger so the
#: audit trail survives.
INFRA_COMMANDS = (
    "provision-env", "inject-build-context", "store-secret",
    "boot-runtime", "stop-runtime",
)

#: The magnet has two poles and one question: was the outcome reached? Everything else is
#: a reason it was not, not a lesser kind of success. A run that ends any other way is
#: still going, and `next-action` says what it is waiting on.
FINAL_STATUSES = ("REACHED", "NOT REACHED")


def transactional(fn):
    """Run a mutator against a freshly replayed state, under the ledger lock."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self.transaction():
            return fn(self, *args, **kwargs)

    return wrapper

#: A block must be earned: this many distinct routes must have been tried and recorded
#: before a missing fact counts as unobtainable rather than merely unlooked-for. Without
#: it, "never stop" becomes "never try", and the block is a give-up button.
MIN_BLOCK_ROUTES = 2

#: Words indicating the smallest input names an external actor, not an action the run
#: can take. When one of these is present the fact genuinely lives outside the session
#: and parking it is legitimate.
DELEGATE_ACTORS = (
    "operator", "human", "user", "admin", "someone with", "permission",
    "credentials", "approval", "invite", "purchase", "paid plan", "account",
    "pasted", "paste", "email", "support",
)

#: Imperative verbs that signal an executable action rather than an unobtainable fact.
ACTION_VERBS = (
    "start", "run", "launch", "boot", "restart", "execute", "install", "sync",
    "copy", "inject", "generate", "build", "fix", "create", "provision", "seed",
    "migrate", "bind", "serve", "enable", "setup", "set up",
)

#: Common local tool names: their presence means the action could be run in-session.
LOCAL_TOOLS = (
    "next", "next.js", "npm", "npx", "pnpm", "yarn", "node", "python", "python3",
    "pip", "pip3", "pytest", "docker", "docker-compose", "make", "cargo", "go",
    "cp", "mv", "mkdir", "prisma", "vite", "webpack", "tsc", "git", "curl",
)

_FILE_TOKEN_RE = re.compile(
    r"[\w.-]+\.(?:tsx?|jsx?|mjs|cjs|py|json|env|ya?ml|toml|lock|sh|db|sqlite)\b"
)


def _looks_like_local_action(text):
    """True when a smallest-input describes work this session could actually do.

    The flow-parity failure mode: a bootable server described as missing information.
    An input that pairs imperative verbs with local tools or project files is work,
    not a fact. Facts name actors and permissions; work names commands.
    """
    if not text:
        return False
    t = text.lower()
    if any(tok in t for tok in DELEGATE_ACTORS):
        return False
    has_verb = any(
        re.search(rf"\b{re.escape(v)}\b", t) for v in ACTION_VERBS
    )
    has_local = (
        bool({w.strip(".") for w in t.split()} & set(LOCAL_TOOLS))
        or bool(_FILE_TOKEN_RE.search(t))
        or "/" in t
    )
    return has_verb and has_local


def _free_port():
    """Ask the kernel for a port that is free right now."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_open(port, host="127.0.0.1"):
    """True when something already accepts TCP connections on this port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, int(port))) == 0


def _tail(path, lines=10):
    try:
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no output captured)"


def _kill_process(pid):
    """SIGTERM a process group started with start_new_session, then SIGKILL as fallback."""
    import os
    import signal
    import time

    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            # EPERM on signal 0 means a zombie: dead, sockets released, un-reaped.
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _listener_pids(port):
    """PIDs with a LISTEN socket on port, via lsof. Dev servers like `next dev`
    may respawn their actual server in a fresh process group, so the recorded
    pid's group is not always the group holding the port."""
    import os
    import subprocess

    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({
        int(line) for line in out.split() if line.strip().isdigit()
    })


def _retire_port(port, attempts=5):
    """Kill every process still listening on port until the port is clear.
    Returns True when nothing listens on the port anymore."""
    import os
    import signal

    for _ in range(attempts):
        pids = _listener_pids(port)
        if not pids:
            return True
        for pid in pids:
            try:
                pgid = os.getpgid(pid)
            except (ProcessLookupError, PermissionError):
                pgid = None
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    if pgid is not None:
                        os.killpg(pgid, sig)
                    else:
                        os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
        time.sleep(0.5)
    return not _listener_pids(port)

#: The right edge is the criteria list. It is set once and moves only through a disputed,
#: recorded fork — never by editing, and never silently.


# --------------------------------------------------------------------------- #
# input validation
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #


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




# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #


class Ledger:
    def __init__(self, root, runtime_manager: RuntimeManager | None = None, claim_comparator: ClaimComparator | None = None):
        self.root = Path(root)
        self.state = empty_state()
        self.runtime_manager = runtime_manager or RealOSRuntimeManager()
        self.claim_comparator = claim_comparator or JaccardComparator()
        if not self.root.exists():
            raise UsageError(f"ledger directory does not exist: {self.root}")
        self._replay_all()

    # -- plumbing ----------------------------------------------------------- #

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
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                self._replay_all()
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    # -- multi-agent + compact (5) ------------------------------------------- #

    def compact(self):
        """Compact the logs into a single snapshot event, losslessly.

        Compaction is only honest if the compacted ledger derives the exact same
        state. So: replay the logs first and refuse if they disagree with the state
        this instance holds (someone wrote after we loaded — snapshotting a stale
        view would be silent data loss), archive the originals, write one snapshot
        event, and prove the compacted ledger replays to the same state before
        returning it. On any divergence the originals are restored and the call is
        refused.
        """
        import shutil

        clean = empty_state()
        for name in ("control", "hypotheses", "forks", "evidence"):
            self._replay(self.root / LOGS[name], clean)
        if _comparable(clean) != _comparable(self.state):
            raise Blocked(
                "compact refused: replayed state diverges from current state — the "
                "ledger changed under this process. Re-open and retry."
            )
        snapshot = {"ev": "snapshot", "at": time.time(), "state": copy.deepcopy(self.state)}
        archive = self.root / "archive" / f"{int(time.time() * 1000)}"
        archive.mkdir(parents=True, exist_ok=True)
        for name in LOGS.values():
            path = self.root / name
            if path.exists():
                shutil.copy2(path, archive / name)
                path.write_text("", encoding="utf-8")
        with (self.root / LOGS["control"]).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, sort_keys=True) + "\n")
        reopened = Ledger(
            self.root,
            runtime_manager=self.runtime_manager,
            claim_comparator=self.claim_comparator,
        )
        if _comparable(reopened.state) != _comparable(self.state):
            for name in LOGS.values():
                shutil.copy2(archive / name, self.root / name)
            raise Blocked(
                "compact refused: the compacted ledger diverges from the original; "
                "originals restored"
            )
        return reopened

    # -- plumbing ----------------------------------------------------------- #

    def _replay(self, path, state=None):
        target = self.state if state is None else state
        if not path.exists():
            return
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError as exc:
                raise UsageError(f"corrupt ledger: {path.name}:{lineno} is not JSON") from exc
            try:
                apply_event(target, ev)
            except (Blocked, UsageError) as exc:
                raise UsageError(f"corrupt ledger: {path.name}:{lineno}: {exc}") from exc

    def _append(self, name, event):
        """Validate against a copy first, so a refusal never reaches the disk."""
        event = dict(event, at=time.time())
        trial = copy.deepcopy(self.state)
        apply_event(trial, event)
        path = self.root / LOGS[name]
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        self.state = trial

    # -- gates -------------------------------------------------------------- #

    def _enforce_directive(self, action):
        directive = self.state["directive"]
        if directive is None:
            return
        allowed = DIRECTIVE_REQUIRES[directive["kind"]]
        if action not in allowed:
            raise Blocked(
                f"non-convergence classification '{directive['kind']}' is outstanding. "
                f"{DIRECTIVE_MESSAGE[directive['kind']]} "
                f"Allowed now: {', '.join(allowed) or '(none — resolve the missing fact first)'}."
            )

    def _clear_directive(self, action):
        """Satisfied directives are cleared by event, not by mutating memory.

        The CLI runs one command per process, so an in-memory clear would be discarded
        when the process exits and the directive would reappear on replay — leaving the
        run permanently wedged on a directive it had already discharged.
        """
        directive = self.state["directive"]
        if directive and action in DIRECTIVE_REQUIRES[directive["kind"]]:
            self._append("control", {"ev": "directive_clear", "kind": directive["kind"]})

    def _require_ran_red_loop(self, incident):
        loop = self.state["loops"].get(incident)
        if loop is None:
            raise Blocked(
                f"incident {incident!r} has no declared feedback loop. Declare a command "
                f"that can go red on this exact symptom, and run it, before hypothesising."
            )
        if not loop["ran"]:
            raise Blocked(
                f"the feedback loop for {incident!r} was declared but never run: "
                f"{loop['command']}. Run it and record the result."
            )
        if not loop["red"]:
            raise Blocked(
                f"the feedback loop for {incident!r} did not go red ({loop['command']}). "
                f"It is not red-capable, so it cannot discriminate between hypotheses."
            )

    def _unsettled_incidents(self):
        return {h["incident"] for h in self.state["hypotheses"].values() if h["status"] == "open"}

    def _resolved_signatures(self):
        return {r["signature"] for r in self.state["resolved_blocks"]}


    # -- init --------------------------------------------------------------- #

    @classmethod
    def create(cls, root, intent, criteria=(), freeze=False):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        with (root / LOCK).open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                if (root / LOGS["control"]).exists():
                    raise Blocked(f"ledger already initialised: {root}")
                for name in LOGS.values():
                    (root / name).touch()
                ledger = cls(root)
                ledger._append(
                    "control",
                    {
                        "ev": "init",
                        "intent": req(intent, "--intent"),
                        "criteria": list(criteria or []),
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
                fcntl.flock(handle, fcntl.LOCK_UN)

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

    # -- intake + validation helpers (1) ------------------------------------- #

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
        return {"intent": intent, "criteria": criteria}

    @transactional
    def record_meta_fail(self, signature, failure_kind):
        """Record a failed run signature for the meta want-rewind dataset (10)."""
        signature = req(signature, "--signature")
        failure_kind = req(failure_kind, "--failure-kind")
        self._append("control", {"ev": "meta_record", "signature": signature, "failure_kind": failure_kind})
        return self

    @staticmethod
    def decision_weight(kind, anchors, reversibility):
        """Public alias for the derivation weight heuristic, surfaced on Ledger for
        tests and CLI gating without importing derivation in consumers."""
        from derivation import decision_weight as _dw
        return _dw(kind, anchors, reversibility)

    # -- spec integration: tickets -> criteria ------------------------------- #

    @transactional
    def import_tickets(self, directory):
        """Map a to-tickets issue directory onto acceptance criteria.

        The upstream workflow (/to-spec -> /to-tickets -> /implement-spec) closes
        a ticket when it is merged. This ledger closes a run when every criterion
        carries evidence. Importing tickets keeps the boundary visible: every
        criterion remembers its ticket, so the report can name which ticket
        *earned* its close and which was merely merged. A criterion no command
        can close is refused here, at intake — not silently at close.
        """
        from tickets import parse_tickets

        tickets = parse_tickets(directory)
        self._append("control", {"ev": "tickets_imported", "tickets": tickets})
        return self

    # -- entry A: the feedback loop ----------------------------------------- #

    @transactional
    def declare_loop(self, incident, command, symptom):
        self._enforce_directive("declare_loop")
        self._append(
            "control",
            {
                "ev": "loop_declare",
                "incident": req(incident, "--incident"),
                "command": req(command, "--command"),
                "symptom": req(symptom, "--symptom"),
            },
        )
        return self

    @transactional
    def run_loop(self, incident, red):
        self._enforce_directive("run_loop")
        incident = req(incident, "--incident")
        if red not in (True, False):
            raise UsageError("--red must be true or false; state the observed result")
        self._append("control", {"ev": "loop_run", "incident": incident, "red": bool(red)})
        return self

    @transactional
    def open_hypothesis(self, ident, incident, claim, prediction, rank=None):
        self._enforce_directive("hyp_open")
        incident = req(incident, "--incident")
        claim = req(claim, "--claim")
        prediction = req(prediction, "--prediction")
        self._require_ran_red_loop(incident)
        for existing in self.state["hypotheses"].values():
            if existing["incident"] != incident:
                continue
            if existing["claim"].strip().lower() == claim.strip().lower():
                raise Blocked(
                    f"hypothesis already recorded: {existing['id']!r} states the same claim. "
                    f"Padding the ledger with restatements defeats the search."
                )
            overlap = self.claim_comparator.similarity(existing["claim"], claim)
            if overlap >= CLAIM_OVERLAP_THRESHOLD:
                shared = sorted(_claim_words(existing["claim"]) & _claim_words(claim))
                raise Blocked(
                    f"hypothesis {existing['id']!r} is {overlap:.0%} the same claim as this one "
                    f"(shared terms: {', '.join(shared)}). String equality does not stop "
                    f"paraphrase, so content overlap is checked instead. If these are really "
                    f"different mechanisms, say what distinguishes them."
                )
        self._append(
            "hypotheses",
            {
                "ev": "hyp_open",
                "id": req(ident, "--id"),
                "incident": incident,
                "claim": claim,
                "prediction": prediction,
                "rank": rank,
            },
        )
        self._clear_directive("hyp_open")
        return self

    @transactional
    def close_hypothesis(self, ident, probe, observed, survives):
        action = "hyp_survive" if survives else "hyp_refute"
        self._enforce_directive(action)
        hyp = self.state["hypotheses"].get(req(ident, "--id"))
        if hyp is None:
            raise UsageError(f"unknown hypothesis {ident!r}")
        if hyp["status"] != "open":
            raise Blocked(f"hypothesis {ident!r} is already {hyp['status']}")
        self._append(
            "hypotheses",
            {
                "ev": action,
                "id": hyp["id"],
                "probe": req(probe, "--probe"),
                "observed": req(observed, "--observed"),
            },
        )
        self._clear_directive(action)
        return self

    @transactional
    def merge_hypotheses(self, into, source, note):
        self._enforce_directive("hyp_merge")
        self._append(
            "hypotheses",
            {
                "ev": "hyp_merge",
                "into": req(into, "--into"),
                "from": req(source, "--from"),
                "note": req(note, "--note"),
            },
        )
        self._clear_directive("hyp_merge")
        return self

    @transactional
    def begin_fix(self, incident, note, search="full"):
        self._enforce_directive("begin_fix")
        search = one_of(search, tuple(SEARCH_WIDTH), "--search")
        required = SEARCH_WIDTH[search]
        tested = [h for h in self.state["hypotheses"].values() if h["incident"] == incident]
        if not tested:
            # Report the first missing step, not the arithmetic at the end of the ladder.
            self._require_ran_red_loop(incident)
        unsettled = self._unsettled_incidents()
        if incident in unsettled:
            open_ids = sorted(
                h["id"] for h in self.state["hypotheses"].values()
                if h["incident"] == incident and h["status"] == "open"
            )
            raise Blocked(
                f"cannot write a fix while {incident!r} still has open hypotheses: {open_ids}. "
                f"Test them first — a fix chosen before its rivals are refuted is a guess."
            )
        if len(tested) < required:
            raise Blocked(
                f"only {len(tested)} hypothesis/hypotheses settled for {incident!r}; "
                f"--search {search} requires {required}. Test more rivals, or re-run with "
                f"--search narrow if the defect is fully reproduced and its mechanism is "
                f"unambiguous: the fix is then recorded as chosen without a search over "
                f"rivals, and the run can no longer close as a clean COMPLETE."
            )
        self._append(
            "control",
            {
                "ev": "fix_begin",
                "incident": incident,
                "note": req(note, "--note"),
                "search": search,
            },
        )
        return self

    # -- entry C: non-convergence ------------------------------------------ #

    @transactional
    def classify(
        self, signature, kind, missing_fact=None, smallest_input=None,
        tried=(), park=None, note=None, only_route=None,
    ):
        self._enforce_directive("classify")
        kind = one_of(kind, tuple(DIRECTIVE_REQUIRES), "--kind")
        signature = req(signature, "--signature")
        tried = [str(t).strip() for t in (tried or []) if str(t).strip()]
        if kind == "information_missing":
            req(missing_fact, "--missing-fact")
            req(smallest_input, "--smallest-input")
            if _looks_like_local_action(smallest_input) and not str(only_route or "").strip():
                raise Blocked(
                    f"the smallest input for {signature!r} describes work this session "
                    f"can do, not a fact outside its reach: {smallest_input!r}. Do the "
                    f"work: boot-runtime for a server the criteria need, provision-env "
                    f"for a dependency, inject-build-context for build artifacts — then "
                    f"release the block with resolve-block. A park is for facts nobody "
                    f"here can create. If an external actor or permission is genuinely "
                    f"required, say so with --only-route and it is disclosed in the report."
                )
            distinct = {t.lower() for t in tried}
            if len(distinct) < MIN_BLOCK_ROUTES and not str(only_route or "").strip():
                raise Blocked(
                    f"a block parks one path; it does not end the run. But it must be "
                    f"earned: record at least {MIN_BLOCK_ROUTES} distinct routes you "
                    f"already took to obtain {missing_fact!r} with --tried. A fact you "
                    f"merely stopped looking for is not an unavailable fact. If there is "
                    f"genuinely only one way to reach this fact — a missing binary, a "
                    f"permission you do not hold, a capability that does not exist here — "
                    f"say so with --only-route, and it is disclosed in the report."
                )
        self._append(
            "control",
            {
                "ev": "classify",
                "signature": signature,
                "kind": kind,
                "missing_fact": missing_fact,
                "smallest_input": smallest_input,
                "tried": tried,
                "park": park,
                "note": note,
                "only_route": only_route,
            },
        )
        return self

    @transactional
    def resolve_block(self, signature, provided):
        self._append(
            "control",
            {
                "ev": "resolve_block",
                "signature": req(signature, "--signature"),
                "input": req(provided, "--provided"),
            },
        )
        return self

    # -- entry B: forks and disputes ---------------------------------------- #

    @transactional
    def open_fork(
        self, ident, question, trigger, assumed, options,
        invalidation_rationale=None, criterion_from=None, criterion_to=None,
    ):
        if criterion_from is not None or criterion_to is not None:
            criterion_from = req(criterion_from, "--criterion-from")
            criterion_to = req(criterion_to, "--criterion-to")
            if criterion_from not in self.state["criteria"]:
                raise UsageError(
                    f"--criterion-from {criterion_from!r} is not a criterion of this run. "
                    f"The right edge is fixed at init; it moves only by naming an edge that "
                    f"exists. Current: {self.state['criteria']}"
                )
            if criterion_to in self.state["criteria"]:
                raise Blocked(
                    f"--criterion-to {criterion_to!r} is already a criterion. Moving the "
                    f"right edge onto an edge the run already holds would merge two "
                    f"promises into one and quietly drop the other."
                )
        options = list(options or [])
        if not options:
            raise UsageError("a fork needs at least one option (a dict per option)")
        seen = set()
        for option in options:
            # The gate is the validator. It must hold on the library path too, or a
            # malformed option escapes as an AttributeError instead of a refusal.
            if not isinstance(option, dict):
                raise UsageError(f"each option must be a dict, got {type(option).__name__}: {option!r}")
            for field in ("id", "cost", "risk", "reversibility"):
                if not str(option.get(field, "")).strip():
                    raise UsageError(f"option is missing {field!r}: {option!r}")
            if option["reversibility"] not in REVERSIBILITIES:
                raise UsageError(
                    f"option {option['id']!r} reversibility must be one of "
                    f"{list(REVERSIBILITIES)}, got {option['reversibility']!r}"
                )
            if option["id"] in seen:
                raise Blocked(f"duplicate option id {option['id']!r}")
            seen.add(option["id"])
        if len(options) == 1 and not str(invalidation_rationale or "").strip():
            raise Blocked(
                "a single-option fork must carry --invalidation-rationale naming what was "
                "ruled out and why. Silence here is indistinguishable from not having looked."
            )
        self._append(
            "forks",
            {
                "ev": "fork_open",
                "id": req(ident, "--id"),
                "question": req(question, "--question"),
                "trigger": req(trigger, "--trigger"),
                "assumed": req(assumed, "--assumed"),
                "options": options,
                "criterion_from": criterion_from,
                "criterion_to": criterion_to,
                "invalidation_rationale": invalidation_rationale,
            },
        )
        return self

    @transactional
    def dispute(self, fork, challenger, author, steelman, verdict=None):
        fork = req(fork, "--fork")
        challenger = req(challenger, "--challenger")
        author = req(author, "--author")
        steelman = req(steelman, "--steelman")
        if fork not in self.state["forks"]:
            raise UsageError(f"unknown fork {fork!r}")
        if challenger == author:
            raise Blocked(
                f"challenger and author are the same actor ({author!r}). A dispute you write "
                f"against your own conclusion is not a dispute. Use a separate context."
            )
        self._append(
            "forks",
            {
                "ev": "dispute",
                "fork": fork,
                "challenger": challenger,
                "author": author,
                "steelman": steelman,
                "verdict": verdict,
            },
        )
        return self

    @transactional
    def decide(
        self,
        fork,
        chosen,
        by,
        human_ack=False,
        reversibility=None,
        anchors=(),
        invalidated=(),
        rationale=None,
        challenger_overruled=False,
        override_reason=None,
    ):
        fork = req(fork, "--fork")
        record = self.state["forks"].get(fork)
        if record is None:
            raise UsageError(f"unknown fork {fork!r}")
        if fork in self.state["decisions"]:
            raise Blocked(f"fork {fork!r} is already decided")
        by = one_of(by, ("agent", "human"), "--by")
        option_ids = {o["id"] for o in record["options"]}
        if chosen not in option_ids:
            raise UsageError(
                f"--chosen {chosen!r} is not an option of fork {fork!r}: {sorted(option_ids)}"
            )
        disputes = [d for d in self.state["disputes"] if d["fork"] == fork]
        if not disputes:
            raise Blocked(
                f"fork {fork!r} has no dispute. A decision taken without an adversarial "
                f"pass is an assertion. Run `dispute` with a separate challenger first."
            )
        if reversibility is None:
            reversibility = next(
                o["reversibility"] for o in record["options"] if o["id"] == chosen
            )
        else:
            reversibility = one_of(reversibility, REVERSIBILITIES, "--reversibility")
        anchors = [str(a).strip() for a in anchors if str(a).strip()]
        if by == "agent":
            if reversibility == "irreversible":
                raise Blocked(
                    "an irreversible decision is outside delegated authority: decide as "
                    "--by human with --human-ack"
                )
            touched = sorted(set(anchors) & set(HUMAN_ANCHORS))
            if touched:
                raise Blocked(
                    f"the decision touches human anchor(s) {touched}; an agent may not "
                    f"decide it alone. Decide as --by human with --human-ack."
                )
        else:
            if not human_ack:
                raise Blocked("--by human requires --human-ack; record that a person decided")
        if chosen != record["assumed"] and not list(invalidated):
            raise Blocked(
                f"the decision departs from the assumed path ({record['assumed']!r} -> "
                f"{chosen!r}) but records nothing invalidated. Name the work, tests, "
                f"acceptance criteria and PRD stories that the old assumption made stale."
            )
        if challenger_overruled and not str(override_reason or "").strip():
            raise UsageError(
                "--challenger-overruled requires --override-reason saying why the "
                "challenger's objection does not hold. Overriding a dispute is allowed; "
                "overriding it silently is the thing this ledger exists to prevent."
            )
        self._append(
            "forks",
            {
                "ev": "decide",
                "fork": fork,
                "chosen": chosen,
                "by": by,
                "human_ack": bool(human_ack),
                "reversibility": reversibility,
                "anchors": anchors,
                "invalidated": [str(i) for i in invalidated if str(i).strip()],
                "rationale": rationale,
                "challenger_overruled": bool(challenger_overruled),
                "override_reason": override_reason,
            },
        )
        if record.get("criterion_to") is not None:
            if chosen not in ("revise", "keep"):
                raise UsageError(
                    f"fork {fork!r} moves the right edge, so it must be decided as "
                    f"'revise' or 'keep', not {chosen!r}"
                )
            if chosen == "revise":
                self._append(
                    "control",
                    {
                        "ev": "criterion_revised",
                        "from": record["criterion_from"],
                        "to": record["criterion_to"],
                        "fork": fork,
                        "invalidated": [str(i) for i in invalidated if str(i).strip()],
                    },
                )
        return self

    # -- evidence ----------------------------------------------------------- #

    @transactional
    def record_evidence(
        self,
        criterion,
        command,
        tier,
        recorder=None,
        reproduced_by=None,
        expected_source=None,
        reason=None,
        output_ref=None,
    ):
        criterion = req(criterion, "--criterion")
        if criterion not in self.state["criteria"]:
            near = _closest(criterion, self.state["criteria"])
            raise Blocked(
                f"{criterion!r} is not a criterion of this run, so evidence against it could "
                f"never close anything and would fail silently as NOT REACHED."
                + (f" Did you mean {near!r}? The text must match exactly." if near else "")
                + f" Current: {self.state['criteria']}"
            )
        command = req(command, "--command")
        tier = one_of(tier, EVIDENCE_TIERS, "--tier")
        if expected_source is not None:
            expected_source = one_of(expected_source, EXPECTED_SOURCES, "--expected-source")
            if expected_source == "code":
                raise Blocked(
                    "expected_source=code means the expectation was recomputed from the "
                    "implementation. Such a check passes by construction and can never "
                    "disagree with the code. Use spec, literal, worked_example or "
                    "independent_oracle."
                )
        if tier == "reproduced_independently":
            recorder = req(recorder, "--recorder")
            reproduced_by = req(reproduced_by, "--reproduced-by")
            if reproduced_by == recorder:
                raise Blocked(
                    f"--reproduced-by equals --recorder ({recorder!r}). Re-reading your own "
                    f"summary is not independent reproduction; run it in another context."
                )
        if tier == "unavailable":
            req(reason, "--reason")
        self._append(
            "evidence",
            {
                "ev": "evidence",
                "criterion": criterion,
                "command": command,
                "tier": tier,
                "reproduced_by": reproduced_by,
                "expected_source": expected_source,
                "reason": reason,
                "output_ref": output_ref,
            },
        )
        return self

    @transactional
    def record_risk(self, text, blocking=False):
        self._append(
            "control",
            {"ev": "risk", "text": req(text, "--text"), "blocking": bool(blocking)},
        )
        return self

    # -- infra catalog + stub first-class (6) ------------------------------- #

    CATALOG_SPECS = {
        "postgres": {
            "driver": "postgresql",
            "placeholder": "postgresql://user:pass@host:5432/db",
        },
        "redis": {
            "driver": "redis",
            "placeholder": "redis://127.0.0.1:6379",
        },
        "sqlite": {
            "driver": "sqlite",
            "placeholder": "sqlite:///./dev.db",
        },
        "s3": {
            "driver": "s3",
            "placeholder": "s3://bucket/key",
        },
        "mail": {
            "driver": "mail",
            "placeholder": "smtp://localhost:1025",
        },
    }

    @staticmethod
    def resolve_catalog_spec(spec):
        """Turn a known spec id into a concrete placeholder the run can provision against.

        Unknown specs are accepted as-is: the catalog is guidance, not a cage.
        """
        if not isinstance(spec, str) or not spec.strip():
            raise UsageError("spec must be a non-empty string")
        return Ledger.CATALOG_SPECS.get(
            spec.strip(), {"driver": spec.strip(), "placeholder": spec.strip()}
        )

    def provision_env_catalog(self, spec, output_var, command=None):
        """Provision from catalog: record the spec and a placeholder command if none given.

        This reduces the friction of turning a missing dependency into a runnable
        environment without inventing a command from nothing. Not @transactional:
        provision_env already takes the lock, and flock is not re-entrant across
        separate open()s of the same file in one process.
        """
        spec_info = self.resolve_catalog_spec(spec)
        if command is None:
            command = f"echo {spec_info['placeholder']}"
        return self.provision_env(spec_info["driver"], output_var, command)
    # -- infrastructure ----------------------------------------------------- #

    @transactional
    def provision_env(self, spec, output_var, command):
        """Record that an environment dependency was provisioned on-demand.

        This turns an `information_missing` block into a resolved fact: the ledger
        now knows the value exists and how it was obtained, so criteria that depend
        on it can proceed to evidence collection without human paste.
        """
        spec = req(spec, "--spec")
        output_var = req(output_var, "--output-var")
        command = req(command, "--command")
        self._append(
            "control",
            {
                "ev": "provision_env",
                "spec": spec,
                "output_var": output_var,
                "command": command,
                "resolved": True,
            },
        )
        return self

    @transactional
    def inject_build_context(self, target, source, artifacts, command):
        """Record that build context was copied into an isolated test directory.

        Without this, typechecks and builds on generated code produce false negatives
        because dependencies live only in the host project. The injection is recorded
        so the evidence tier can reference it and the report can disclose it.
        """
        target = req(target, "--target")
        source = req(source, "--source")
        command = req(command, "--command")
        artifacts = [str(a).strip() for a in (artifacts or []) if str(a).strip()]
        if not artifacts:
            raise UsageError(
                "--artifacts must list at least one injected item (node_modules, tsconfig.json, etc.)"
            )
        self._append(
            "control",
            {
                "ev": "inject_build_context",
                "target": target,
                "source": source,
                "artifacts": artifacts,
                "command": command,
            },
        )
        return self

    @transactional
    def store_secret(self, name, command):
        """Record that a secret was stored securely for criterion verification.

        The actual value never touches the ledger or the transcript. Only the name
        and the retrieval command are recorded, so evidence commands can reference
        the secret without leaking it. This eliminates the redaction round-trip
        that previously blocked autonomous verification.
        """
        name = req(name, "--name")
        command = req(command, "--command")
        self._append(
            "control",
            {"ev": "store_secret", "name": name, "command": command},
        )
        return self

    # -- runtimes ----------------------------------------------------------- #

    def boot_runtime(
        self, name, command, cwd=None, port=None, path="/", timeout=30.0,
        resolve_signature=None, criterion=None,
    ):
        """Start a local runtime for a generated project and wait for its listener.

        This is the arm that turns "the sandbox is not startable" parks into a
        running URL: pick a free port, start the command (a {port} placeholder in
        the command is substituted), poll until something accepts connections, then
        record the runtime and optionally release a parked block and record
        listener evidence. The server keeps running after this returns so e2e
        criteria can be verified against it; `stop-runtime` retires it.
        """
        name = req(name, "--name")
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
        command = req(command, "--command")
        timeout = max(float(timeout or 30.0), 1.0)
        path = path or "/"

        if "{port}" not in command:
            raise UsageError(
                'the command must contain a {port} placeholder, e.g. '
                '"npx next dev -p {port}", so boot-runtime knows where the '
                'listener will come up'
            )
        if port is not None:
            port = int(port)
            if not 0 < port < 65536:
                raise UsageError(f"--port must be between 1 and 65535, got {port}")
            if self.runtime_manager.is_port_open(port):
                raise Blocked(
                    f"--port {port} is already in use; boot-runtime never starts a "
                    f"second server on a bound port. Omit --port to have a free "
                    f"one chosen."
                )
        else:
            port = self.runtime_manager.allocate_free_port()
        actual_command = command.replace("{port}", str(port))

        runtimes_dir = self.root / "runtimes"
        log_path = runtimes_dir / f"{safe_name}.log"
        try:
            handle = self.runtime_manager.start_listener(
                command=actual_command,
                port=port,
                log_path=log_path,
                cwd=str(cwd) if cwd else None,
                timeout=timeout,
            )
        except RuntimeError as exc:
            raise Blocked(str(exc)) from exc

        url = f"http://127.0.0.1:{port}{path}"
        with self.transaction():
            self._append(
                "control",
                {
                    "ev": "boot_runtime",
                    "name": name,
                    "command": actual_command,
                    "port": port,
                    "url": url,
                    "pid": handle.pid,
                    "log": str(handle.log_path),
                },
            )
        if resolve_signature:
            self.resolve_block(
                resolve_signature,
                f"boot-runtime started {name!r} listening on {url} (pid {handle.pid}); "
                f"log at {handle.log_path}",
            )
        if criterion:
            self.record_evidence(
                criterion,
                f"curl -s -o /dev/null -w '%{{http_code}}' {url}",
                "executed_here",
                expected_source="literal",
                reason=f"runtime {name!r} accepted connections on {url}",
            )
        print(f"boot-runtime: {name} listening on {url} (pid {handle.pid})")
        return self

    @transactional
    def stop_runtime(self, name):
        """Retire a runtime previously started with boot-runtime."""
        name = req(name, "--name")
        runtimes = self.state.get("infra", {}).get("runtimes", [])
        target = next(
            (rt for rt in runtimes if rt["name"] == name and not rt.get("stopped")),
            None,
        )
        if target is None:
            running = [rt["name"] for rt in runtimes if not rt.get("stopped")]
            raise Blocked(
                f"no running runtime named {name!r} in this ledger; "
                f"running: {running or 'none'}"
            )
        port = target.get("port")
        handle = type("H", (), {"pid": target["pid"], "port": port})()
        try:
            self.runtime_manager.stop_listener(handle)
        except RuntimeError as exc:
            raise Blocked(str(exc)) from exc
        self._append("control", {"ev": "stop_runtime", "name": name})
        return self

    # -- preflight ---------------------------------------------------------- #

    def validate_infra(self, fix=False):
        """Check that all infrastructure prerequisites are available before
        starting or resuming a run.

        Returns a dict with `ready` (bool), `checks` (list of per-item results),
        and `summary` (human-readable). When `fix=True`, attempts to restore
        missing prerequisites by re-running the recorded provision/injection
        commands. Fixes are best-effort and recorded in the check detail.
        Exit code 0 means all checks passed; exit code 3 means at least one
        prerequisite is missing and could not be restored.
        """
        import os
        import subprocess
        import shutil

        infra = self.state.get("infra", {})
        checks = []

        # Check provisioned environments: verify the output var is set
        for prov in infra.get("provisions", []):
            var = prov["output_var"]
            spec = prov["spec"]
            cmd = f"printenv {var}"
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=10
                )
                ok = result.returncode == 0 and result.stdout.strip() != ""
                checks.append({
                    "type": "provision",
                    "spec": spec,
                    "var": var,
                    "ok": ok,
                    "detail": "set" if ok else f"{var} not in environment",
                })
            except Exception as exc:
                checks.append({
                    "type": "provision",
                    "spec": spec,
                    "var": var,
                    "ok": False,
                    "detail": str(exc),
                })

        # Check injected build contexts: verify artifacts exist at target
        for inj in infra.get("injections", []):
            target = inj["target"]
            artifacts = inj.get("artifacts", [])
            missing = []
            for art in artifacts:
                path = Path(target) / art
                if not path.exists():
                    missing.append(art)
            ok = len(missing) == 0
            checks.append({
                "type": "injection",
                "target": target,
                "artifacts": artifacts,
                "ok": ok,
                "detail": "all present" if ok else f"missing: {', '.join(missing)}",
            })

        # Check stored secrets: verify the retrieval command executes
        for sec in infra.get("secrets", []):
            name = sec["name"]
            cmd = sec["command"]
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=10
                )
                ok = result.returncode == 0 and result.stdout.strip() != ""
                checks.append({
                    "type": "secret",
                    "name": name,
                    "ok": ok,
                    "detail": "retrievable" if ok else "command failed or empty",
                })
            except Exception as exc:
                checks.append({
                    "type": "secret",
                    "name": name,
                    "ok": False,
                    "detail": str(exc),
                })

        # Check booted runtimes: verify the recorded listener still accepts connections
        for rt in infra.get("runtimes", []):
            if rt.get("stopped"):
                continue
            ok = _port_open(rt["port"])
            checks.append({
                "type": "runtime",
                "name": rt["name"],
                "url": rt["url"],
                "ok": ok,
                "detail": (
                    "listening"
                    if ok
                    else f"not listening on port {rt['port']} (pid {rt.get('pid')})"
                ),
            })

        # Check external tools referenced in loops and evidence commands.
        # Commands may start with env-var assignments (VAR=val cmd) or command
        # substitutions (START=$(date ...) cmd); skip tokens that are not bare
        # binary names so we do not report "START=$(date" as a missing tool.
        _SHELL_OPS = frozenset(("&&", "||", "|", ";", ">>", ">", "<", "2>&1", "&"))

        def _extract_tool_name(cmd):
            for token in cmd.split():
                if token in _SHELL_OPS:
                    continue
                if any(c in token for c in "=$(){}><|;&"):
                    continue
                return token
            return None

        tool_names = set()
        for loop in self.state.get("loops", {}).values():
            name = _extract_tool_name(loop.get("command", ""))
            if name:
                tool_names.add(name)
        for ev in self.state.get("evidence", []):
            name = _extract_tool_name(ev.get("command", ""))
            if name:
                tool_names.add(name)
        # Filter to only external binaries (not builtins)
        builtins = {"echo", "cat", "ls", "grep", "test", "true", "false", "cd", "pwd"}
        externals = tool_names - builtins
        for tool in sorted(externals):
            found = shutil.which(tool) is not None
            checks.append({
                "type": "tool",
                "name": tool,
                "ok": found,
                "detail": "found" if found else "not in PATH",
            })

        # Auto-fix pass: attempt to restore failed checks using recorded commands
        if fix:
            for c in checks:
                if c["ok"]:
                    continue
                if c["type"] == "provision":
                    prov = next(
                        (p for p in infra.get("provisions", []) if p["output_var"] == c["var"]),
                        None,
                    )
                    if prov:
                        try:
                            result = subprocess.run(
                                prov["command"], shell=True, capture_output=True,
                                text=True, timeout=60,
                            )
                            if result.returncode == 0 and result.stdout.strip():
                                # Export the var into current env for subsequent checks
                                os.environ[c["var"]] = result.stdout.strip()
                                c["ok"] = True
                                c["detail"] = "restored via --fix"
                            else:
                                c["detail"] += f"; fix failed: {result.stderr.strip()[:120]}"
                        except Exception as exc:
                            c["detail"] += f"; fix error: {exc}"
                elif c["type"] == "injection":
                    inj = next(
                        (i for i in infra.get("injections", []) if i["target"] == c["target"]),
                        None,
                    )
                    if inj:
                        try:
                            result = subprocess.run(
                                inj["command"], shell=True, capture_output=True,
                                text=True, timeout=60,
                            )
                            if result.returncode == 0:
                                # Re-check artifacts
                                missing_after = [
                                    a for a in inj.get("artifacts", [])
                                    if not (Path(inj["target"]) / a).exists()
                                ]
                                if not missing_after:
                                    c["ok"] = True
                                    c["detail"] = "restored via --fix"
                                else:
                                    c["detail"] = f"fix ran but still missing: {', '.join(missing_after)}"
                            else:
                                c["detail"] += f"; fix failed: {result.stderr.strip()[:120]}"
                        except Exception as exc:
                            c["detail"] += f"; fix error: {exc}"
                # secrets and tools cannot be auto-fixed reliably

        ready = all(c["ok"] for c in checks) if checks else True
        failed = [c for c in checks if not c["ok"]]
        fixed = [c for c in checks if c.get("detail") == "restored via --fix"]
        summary = (
            f"{len(checks) - len(failed)}/{len(checks)} checks passed"
            if checks
            else "no infrastructure registered"
        )
        if fixed:
            summary += f"; {len(fixed)} restored via --fix"
        if failed:
            summary += "; failing: " + ", ".join(
                f"{c['type']}:{c.get('spec') or c.get('name') or c.get('target')} ({c['detail']})"
                for c in failed
            )

        return {"ready": ready, "checks": checks, "summary": summary}

    # -- derivation --------------------------------------------------------- #

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

# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _options(values):
    parsed = []
    for raw in values or []:
        try:
            option = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UsageError(
                f"--option must be a JSON object with id, cost, risk, reversibility: {raw!r}"
            ) from exc
        if not isinstance(option, dict):
            raise UsageError(f"--option must be a JSON object, got {type(option).__name__}")
        parsed.append(option)
    return parsed


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="convergence",
        description="Append-only ledger enforcing hypothesis elimination, fork disputes, "
        "non-convergence classification and evidence tiers.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    def add(name, help_text):
        node = sub.add_parser(name, help=help_text)
        node.add_argument("ledger")
        return node

    node = add("init", "create a ledger")
    node.add_argument("--intent", required=True)
    node.add_argument("--criterion", action="extend", nargs="+", default=[])
    node.add_argument("--intake", action="store_true",
                      help="validate the requested magnet without creating a ledger")

    node = add("freeze-intent", "seal the right edge after init")

    node = add("declare-loop", "declare the feedback loop for an incident")
    node.add_argument("--incident", required=True)
    node.add_argument("--command", required=True)
    node.add_argument("--symptom", required=True)

    node = add("run-loop", "record that the declared loop was actually run")
    node.add_argument("--incident", required=True)
    node.add_argument("--red", required=True, choices=("true", "false"))

    node = add("open-hypothesis", "record a falsifiable hypothesis")
    node.add_argument("--id", required=True)
    node.add_argument("--incident", required=True)
    node.add_argument("--claim", required=True)
    node.add_argument("--prediction", required=True)
    node.add_argument("--rank", type=int)

    for name in ("refute-hypothesis", "survive-hypothesis"):
        node = add(name, "close a hypothesis with an executed probe")
        node.add_argument("--id", required=True)
        node.add_argument("--probe", required=True)
        node.add_argument("--observed", required=True)

    node = add("merge-hypotheses", "two lanes reduced to one mechanism")
    node.add_argument("--into", required=True)
    node.add_argument("--from", dest="source", required=True)
    node.add_argument("--note", required=True)

    node = add("begin-fix", "open the fix gate for an incident")
    node.add_argument("--incident", required=True)
    node.add_argument("--note", required=True)
    node.add_argument("--search", default="full", choices=tuple(SEARCH_WIDTH),
                      help="full: search over rival explanations (default). "
                           "narrow: the defect is fully reproduced and its mechanism is "
                           "unambiguous; the run then cannot close as a clean COMPLETE.")

    node = add("classify", "classify a repeated failure signature")
    node.add_argument("--signature", required=True)
    node.add_argument(
        "--kind", required=True, choices=tuple(sorted(DIRECTIVE_REQUIRES))
    )
    node.add_argument("--missing-fact")
    node.add_argument("--smallest-input")
    node.add_argument("--tried", action="extend", nargs="+", default=[],
                      help="distinct routes already attempted; a block must be earned")
    node.add_argument("--park",
                      help="the path this block parks (a fork id, incident id, or tag). "
                           "The run continues elsewhere.")
    node.add_argument("--only-route",
                      help="when there is genuinely one way to reach the fact — a missing "
                           "binary, a permission you do not hold — say why here instead of "
                           "listing routes that do not exist. Disclosed in the report.")
    node.add_argument("--note")

    node = add("resolve-block", "clear a missing-fact block once the input arrives")
    node.add_argument("--signature", required=True)
    node.add_argument("--provided", required=True)

    node = add("open-fork", "record a decision fork discovered mid-execution")
    node.add_argument("--id", required=True)
    node.add_argument("--question", required=True)
    node.add_argument("--trigger", required=True)
    node.add_argument("--assumed", required=True)
    node.add_argument("--option", action="extend", nargs="+", default=[])
    node.add_argument("--invalidation-rationale")
    node.add_argument("--criterion-from",
                      help="move the right edge: the criterion being revised")
    node.add_argument("--criterion-to", help="move the right edge: its replacement")

    node = add("dispute", "record an adversarial pass against the favoured option")
    node.add_argument("--fork", required=True)
    node.add_argument("--challenger", required=True)
    node.add_argument("--author", required=True)
    node.add_argument("--steelman", required=True)
    node.add_argument("--verdict")

    node = add("decide", "close a fork")
    node.add_argument("--fork", required=True)
    node.add_argument("--chosen", required=True)
    node.add_argument("--by", required=True, choices=("agent", "human"))
    node.add_argument("--human-ack", action="store_true")
    node.add_argument("--reversibility", choices=REVERSIBILITIES)
    node.add_argument("--anchor", action="extend", nargs="+", default=[])
    node.add_argument("--invalidated", action="extend", nargs="+", default=[])
    node.add_argument("--rationale")
    node.add_argument("--challenger-overruled", action="store_true",
                      help="the dispute's objection did not hold; requires --override-reason "
                           "and downgrades the final verdict to COMPLETE WITH RISKS")
    node.add_argument("--override-reason")

    node = add("record-evidence", "record a verification result")
    node.add_argument("--criterion", required=True)
    node.add_argument("--command", required=True)
    node.add_argument("--tier", required=True, choices=EVIDENCE_TIERS)
    node.add_argument("--recorder")
    node.add_argument("--reproduced-by")
    node.add_argument("--expected-source", choices=EXPECTED_SOURCES)
    node.add_argument("--reason")
    node.add_argument("--output-ref")

    node = add("record-risk", "disclose residual or blocking risk")
    node.add_argument("--text", required=True)
    node.add_argument("--blocking", action="store_true")

    node = add("provision-env", "provision an external dependency on-demand")
    node.add_argument("--spec", required=True,
                      help="what was provisioned (e.g. neon-db, vercel-project)")
    node.add_argument("--output-var", required=True,
                      help="environment variable name holding the result")
    node.add_argument("--command", required=True,
                      help="the command that created the resource")

    node = add("provision-env-catalog", "provision from the built-in spec catalog")
    node.add_argument("--spec", required=True,
                      help="catalog spec id (e.g. postgres, redis, sqlite, s3, mail)")
    node.add_argument("--output-var", required=True,
                      help="environment variable name holding the result")
    node.add_argument("--command",
                      help="if omitted, uses the catalog placeholder command")

    node = add("inject-build-context", "copy host build deps into isolated test dir")
    node.add_argument("--target", required=True,
                      help="directory receiving the injected context")
    node.add_argument("--source", required=True,
                      help="host project directory providing the context")
    node.add_argument("--artifacts", action="extend", nargs="+", default=[],
                      help="items injected (node_modules, tsconfig.json, type defs)")
    node.add_argument("--command", required=True,
                      help="the command that performed the injection")

    node = add("store-secret", "store a secret for secure criterion verification")
    node.add_argument("--name", required=True,
                      help="secret identifier referenced in evidence commands")
    node.add_argument("--command", required=True,
                      help="retrieval command (value never touches ledger or transcript)")

    node = add("boot-runtime", "start a local runtime on a free port and wait for its listener")
    node.add_argument("--name", required=True,
                      help="runtime identifier (e2e-sandbox, preview-server)")
    node.add_argument("--command", required=True,
                      help='startup command; a {port} placeholder is substituted, '
                           'e.g. "npx next dev -p {port}"')
    node.add_argument("--cwd", help="working directory for the command")
    node.add_argument("--port", type=int,
                      help="pin the port (must be free); default: pick a free one")
    node.add_argument("--path", default="/",
                      help="URL path the readiness evidence points at (default /)")
    node.add_argument("--timeout", type=float, default=30.0,
                      help="seconds to wait for the listener (default 30)")
    node.add_argument("--resolve-signature",
                      help="also resolve-block this signature once the listener is up")
    node.add_argument("--criterion",
                      help="also record executed_here listener evidence for this criterion")

    node = add("stop-runtime", "stop a runtime previously started with boot-runtime")
    node.add_argument("--name", required=True,
                      help="the runtime identifier given to boot-runtime")

    node = add("validate-infra", "preflight check: verify all infra prerequisites are available")
    node.add_argument("--fix", action="store_true",
                      help="attempt to restore missing prerequisites by re-running "
                           "recorded provision/injection commands")

    add("status", "print the derived state as JSON")
    add("next-action", "print what the ledger requires next")
    add("route", "print the pipeline lane and its fallback as JSON")
    add("close", "compute the final status and print the report")

    node = add("import-tickets", "map a to-tickets issue directory onto criteria")
    node.add_argument("--tickets", required=True, help="directory of ticket files")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except Blocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return BLOCKED
    except IntakeError as exc:
        print(f"INTAKE: {exc}", file=sys.stderr)
        return USAGE
    except UsageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return USAGE


def _dispatch(args):
    if args.subcommand == "init":
        if getattr(args, "intake", False):
            try:
                validated = Ledger.validate_intent(args.intent, args.criterion)
            except IntakeError as exc:
                print(json.dumps({"shape_ok": False, "error": str(exc)}, indent=2))
                return USAGE
            print(json.dumps({
                "intent": validated["intent"],
                "criteria": validated["criteria"],
                "shape_ok": True,
                "next": "freeze the ledger with init, then drive the loop",
            }, indent=2))
            return OK
        Ledger.create(args.ledger, args.intent, args.criterion)
        print(f"initialised {args.ledger}")
        return OK

    ledger = Ledger(args.ledger)
    command = args.subcommand

    if command == "declare-loop":
        ledger.declare_loop(args.incident, args.command, args.symptom)
    elif command == "run-loop":
        ledger.run_loop(args.incident, args.red == "true")
    elif command == "open-hypothesis":
        ledger.open_hypothesis(args.id, args.incident, args.claim, args.prediction, args.rank)
    elif command == "refute-hypothesis":
        ledger.close_hypothesis(args.id, args.probe, args.observed, survives=False)
    elif command == "survive-hypothesis":
        ledger.close_hypothesis(args.id, args.probe, args.observed, survives=True)
    elif command == "merge-hypotheses":
        ledger.merge_hypotheses(args.into, args.source, args.note)
    elif command == "begin-fix":
        ledger.begin_fix(args.incident, args.note, search=args.search)
    elif command == "classify":
        ledger.classify(
            args.signature, args.kind, args.missing_fact, args.smallest_input,
            tried=args.tried, park=args.park, note=args.note, only_route=args.only_route,
        )
    elif command == "resolve-block":
        ledger.resolve_block(args.signature, args.provided)
    elif command == "open-fork":
        ledger.open_fork(
            args.id,
            args.question,
            args.trigger,
            args.assumed,
            _options(args.option),
            args.invalidation_rationale,
            criterion_from=args.criterion_from,
            criterion_to=args.criterion_to,
        )
    elif command == "dispute":
        ledger.dispute(args.fork, args.challenger, args.author, args.steelman, args.verdict)
    elif command == "decide":
        ledger.decide(
            args.fork,
            args.chosen,
            args.by,
            human_ack=args.human_ack,
            reversibility=args.reversibility,
            anchors=args.anchor,
            invalidated=args.invalidated,
            rationale=args.rationale,
            challenger_overruled=args.challenger_overruled,
            override_reason=args.override_reason,
        )
    elif command == "record-evidence":
        ledger.record_evidence(
            args.criterion,
            args.command,
            args.tier,
            recorder=args.recorder,
            reproduced_by=args.reproduced_by,
            expected_source=args.expected_source,
            reason=args.reason,
            output_ref=args.output_ref,
        )
    elif command == "record-risk":
        ledger.record_risk(args.text, args.blocking)
    elif command == "provision-env":
        ledger.provision_env(args.spec, args.output_var, args.command)
    elif command == "provision-env-catalog":
        ledger.provision_env_catalog(args.spec, args.output_var, args.command)
    elif command == "inject-build-context":
        ledger.inject_build_context(
            args.target, args.source, args.artifacts, args.command,
        )
    elif command == "store-secret":
        ledger.store_secret(args.name, args.command)
    elif command == "boot-runtime":
        ledger.boot_runtime(
            args.name, args.command, cwd=args.cwd, port=args.port, path=args.path,
            timeout=args.timeout, resolve_signature=args.resolve_signature,
            criterion=args.criterion,
        )
    elif command == "stop-runtime":
        ledger.stop_runtime(args.name)
    elif command == "validate-infra":
        result = ledger.validate_infra(fix=getattr(args, 'fix', False))
        print(json.dumps(result, indent=2, sort_keys=True))
        return OK if result["ready"] else BLOCKED
    elif command == "status":
        print(json.dumps(ledger.state, indent=2, sort_keys=True, default=str))
        return OK
    elif command == "next-action":
        print(json.dumps(ledger.next_action(), indent=2, sort_keys=True))
        return OK
    elif command == "route":
        print(json.dumps(ledger.pipeline_route(), indent=2, sort_keys=True))
        return OK
    elif command == "import-tickets":
        ledger.import_tickets(args.tickets)
        for t in ledger.state["tickets"]:
            print(f"ticket {t['id']}: {t['title']} -> {len(t['criteria'])} criterion/criteria")
        return OK
    elif command == "freeze-intent":
        ledger.freeze_intent()
        print(f"intent frozen at {args.ledger}")
        return OK
    elif command == "close":
        report = ledger.report()
        (ledger.root / "report.md").write_text(report, encoding="utf-8")
        print(report)
        status = ledger.outcome()[0]
        if status != "REACHED":
            ledger.record_meta_fail(
                signature=f"run-{int(time.time())}",
                failure_kind="not_reached",
            )
        return OK if status == "REACHED" else BLOCKED
    else:  # pragma: no cover - argparse already constrains this
        raise UsageError(f"unknown command {command!r}")

    print(f"{command}: ok")
    return OK


if __name__ == "__main__":
    sys.exit(main())
