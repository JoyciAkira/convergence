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
import json
import sys
import time
from pathlib import Path

LEDGER_VERSION = 1

OK = 0
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

#: After a non-convergence classification, which actions satisfy it.
DIRECTIVE_REQUIRES = {
    "loop_defective": ("hyp_open",),
    "hypothesis_unresolved": ("hyp_refute", "hyp_survive", "hyp_merge"),
    "information_missing": (),
}

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

FINAL_STATUSES = ("FAILED VERIFICATION", "BLOCKED", "COMPLETE WITH RISKS", "COMPLETE")


class Blocked(Exception):
    """A gate refused the action. Exit code 3."""


class UsageError(Exception):
    """Malformed input. Exit code 2."""


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


def empty_state():
    return {
        "version": LEDGER_VERSION,
        "intent": None,
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
        "events": 0,
    }


def apply_event(state, ev):
    """Fold one event into `state` in place. Unknown events are a corrupt log."""
    kind = ev.get("ev")
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise UsageError(f"corrupt ledger: unknown event {kind!r}")
    handler(state, ev)
    state["events"] += 1


def _ev_init(s, ev):
    s["intent"] = ev["intent"]
    s["criteria"] = list(ev.get("criteria") or [])


def _ev_loop_declare(s, ev):
    s["loops"][ev["incident"]] = {
        "command": ev["command"],
        "symptom": ev["symptom"],
        "ran": False,
        "red": False,
        "declared_at": ev["at"],
    }


def _ev_loop_run(s, ev):
    loop = s["loops"].get(ev["incident"])
    if loop is None:
        raise UsageError(f"no loop declared for incident {ev['incident']!r}")
    loop["ran"] = True
    loop["red"] = ev["red"]
    loop["ran_at"] = ev["at"]


def _ev_hyp_open(s, ev):
    s["hypotheses"][ev["id"]] = {
        "id": ev["id"],
        "incident": ev["incident"],
        "claim": ev["claim"],
        "prediction": ev["prediction"],
        "rank": ev.get("rank"),
        "status": "open",
        "probe": None,
        "observed": None,
        "opened_at": ev["at"],
        "closed_at": None,
    }


def _ev_hyp_refute(s, ev):
    _close_hypothesis(s, ev, "refuted")


def _ev_hyp_survive(s, ev):
    _close_hypothesis(s, ev, "surviving")


def _close_hypothesis(s, ev, status):
    hyp = s["hypotheses"].get(ev["id"])
    if hyp is None:
        raise UsageError(f"unknown hypothesis {ev['id']!r}")
    if hyp["status"] != "open":
        raise Blocked(f"hypothesis {ev['id']!r} is already {hyp['status']}")
    hyp["status"] = status
    hyp["probe"] = ev["probe"]
    hyp["observed"] = ev["observed"]
    hyp["closed_at"] = ev["at"]


def _ev_hyp_merge(s, ev):
    target = s["hypotheses"].get(ev["into"])
    source = s["hypotheses"].get(ev["from"])
    if target is None or source is None:
        raise UsageError("merge needs an existing --into and --from hypothesis")
    if source["status"] != "open":
        raise Blocked(f"cannot merge {ev['from']!r}: it is already {source['status']}")
    if source["claim"].strip().lower() == target["claim"].strip().lower():
        raise Blocked(
            f"merge is a no-op: {ev['from']!r} and {ev['into']!r} state the same claim"
        )
    target["merged_from"] = target.get("merged_from", []) + [ev["from"]]
    target["merge_note"] = ev["note"]
    source["status"] = "merged"
    source["merged_into"] = ev["into"]
    source["closed_at"] = ev["at"]


def _ev_fix_begin(s, ev):
    s["fixes"][ev["incident"]] = {"note": ev["note"], "at": ev["at"]}


def _ev_classify(s, ev):
    s["classifications"].append(
        {
            "signature": ev["signature"],
            "kind": ev["kind"],
            "missing_fact": ev.get("missing_fact"),
            "smallest_input": ev.get("smallest_input"),
            "note": ev.get("note"),
            "at": ev["at"],
        }
    )
    if ev["kind"] == "information_missing":
        s["directive"] = None
    else:
        s["directive"] = {"kind": ev["kind"], "set_at": ev["at"]}


def _ev_resolve_block(s, ev):
    s["resolved_blocks"].append(
        {"signature": ev["signature"], "input": ev["input"], "at": ev["at"]}
    )


def _ev_fork_open(s, ev):
    s["forks"][ev["id"]] = {
        "id": ev["id"],
        "question": ev["question"],
        "trigger": ev["trigger"],
        "assumed": ev["assumed"],
        "options": ev["options"],
        "invalidation_rationale": ev.get("invalidation_rationale"),
        "opened_at": ev["at"],
    }


def _ev_dispute(s, ev):
    s["disputes"].append(
        {
            "fork": ev["fork"],
            "challenger": ev["challenger"],
            "author": ev["author"],
            "steelman": ev["steelman"],
            "verdict": ev.get("verdict"),
            "at": ev["at"],
        }
    )


def _ev_decide(s, ev):
    s["decisions"][ev["fork"]] = {
        "chosen": ev["chosen"],
        "by": ev["by"],
        "human_ack": ev.get("human_ack", False),
        "reversibility": ev["reversibility"],
        "anchors": ev.get("anchors") or [],
        "invalidated": ev.get("invalidated") or [],
        "rationale": ev.get("rationale"),
        "at": ev["at"],
    }


def _ev_evidence(s, ev):
    s["evidence"].append(
        {
            "criterion": ev["criterion"],
            "command": ev["command"],
            "tier": ev["tier"],
            "reproduced_by": ev.get("reproduced_by"),
            "expected_source": ev.get("expected_source"),
            "reason": ev.get("reason"),
            "output_ref": ev.get("output_ref"),
            "at": ev["at"],
        }
    )


def _ev_risk(s, ev):
    s["risks"].append({"text": ev["text"], "blocking": ev.get("blocking", False), "at": ev["at"]})


_HANDLERS = {
    "init": _ev_init,
    "loop_declare": _ev_loop_declare,
    "loop_run": _ev_loop_run,
    "hyp_open": _ev_hyp_open,
    "hyp_refute": _ev_hyp_refute,
    "hyp_survive": _ev_hyp_survive,
    "hyp_merge": _ev_hyp_merge,
    "fix_begin": _ev_fix_begin,
    "classify": _ev_classify,
    "resolve_block": _ev_resolve_block,
    "fork_open": _ev_fork_open,
    "dispute": _ev_dispute,
    "decide": _ev_decide,
    "evidence": _ev_evidence,
    "risk": _ev_risk,
}


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #


class Ledger:
    def __init__(self, root):
        self.root = Path(root)
        self.state = empty_state()
        if not self.root.exists():
            raise UsageError(f"ledger directory does not exist: {self.root}")
        for name in ("control", "hypotheses", "forks", "evidence"):
            self._replay(self.root / LOGS[name])

    # -- plumbing ----------------------------------------------------------- #

    def _replay(self, path):
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
                apply_event(self.state, ev)
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
        directive = self.state["directive"]
        if directive and action in DIRECTIVE_REQUIRES[directive["kind"]]:
            self.state["directive"] = None

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
    def create(cls, root, intent, criteria=()):
        root = Path(root)
        if (root / LOGS["control"]).exists():
            raise Blocked(f"ledger already initialised: {root}")
        root.mkdir(parents=True, exist_ok=True)
        for name in LOGS.values():
            (root / name).touch()
        ledger = cls(root)
        ledger._append(
            "control",
            {"ev": "init", "intent": req(intent, "--intent"), "criteria": list(criteria or [])},
        )
        return ledger

    # -- entry A: the feedback loop ----------------------------------------- #

    def declare_loop(self, incident, command, symptom):
        incident = req(incident, "--incident")
        ledger = self
        ledger._enforce_directive("loop_declare")
        ledger._append(
            "control",
            {
                "ev": "loop_declare",
                "incident": incident,
                "command": req(command, "--command"),
                "symptom": req(symptom, "--symptom"),
            },
        )
        return ledger

    def run_loop(self, incident, red):
        incident = req(incident, "--incident")
        if red not in (True, False):
            raise UsageError("--red must be true or false; state the observed result")
        self._append("control", {"ev": "loop_run", "incident": incident, "red": bool(red)})
        return self

    def open_hypothesis(self, ident, incident, claim, prediction, rank=None):
        self._enforce_directive("hyp_open")
        incident = req(incident, "--incident")
        claim = req(claim, "--claim")
        prediction = req(prediction, "--prediction")
        self._require_ran_red_loop(incident)
        for existing in self.state["hypotheses"].values():
            if existing["claim"].strip().lower() == claim.strip().lower():
                raise Blocked(
                    f"hypothesis already recorded: {existing['id']!r} states the same claim. "
                    f"Padding the ledger with restatements defeats the search."
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

    def begin_fix(self, incident, note):
        self._enforce_directive("begin_fix")
        incident = req(incident, "--incident")
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
        tested = [h for h in self.state["hypotheses"].values() if h["incident"] == incident]
        if len(tested) < MIN_HYPOTHESES:
            raise Blocked(
                f"only {len(tested)} hypothesis/hypotheses recorded for {incident!r}; "
                f"at least {MIN_HYPOTHESES} distinct explanations must be tested before a fix. "
                f"One plausible idea anchoring the whole investigation is the failure mode."
            )
        self._append(
            "control",
            {"ev": "fix_begin", "incident": incident, "note": req(note, "--note")},
        )
        return self

    # -- entry C: non-convergence ------------------------------------------ #

    def classify(self, signature, kind, missing_fact=None, smallest_input=None, note=None):
        self._enforce_directive("classify")
        kind = one_of(kind, tuple(DIRECTIVE_REQUIRES), "--kind")
        signature = req(signature, "--signature")
        if kind == "information_missing":
            req(missing_fact, "--missing-fact")
            req(smallest_input, "--smallest-input")
        self._append(
            "control",
            {
                "ev": "classify",
                "signature": signature,
                "kind": kind,
                "missing_fact": missing_fact,
                "smallest_input": smallest_input,
                "note": note,
            },
        )
        return self

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

    def open_fork(self, ident, question, trigger, assumed, options, invalidation_rationale=None):
        options = list(options or [])
        if not options:
            raise UsageError("a fork needs at least one --option (JSON per option)")
        seen = set()
        for option in options:
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
                "invalidation_rationale": invalidation_rationale,
            },
        )
        return self

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
            },
        )
        return self

    # -- evidence ----------------------------------------------------------- #

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

    def record_risk(self, text, blocking=False):
        self._append(
            "control",
            {"ev": "risk", "text": req(text, "--text"), "blocking": bool(blocking)},
        )
        return self

    # -- derivation --------------------------------------------------------- #

    def outcome(self):
        """Final status, computed by precedence. Never inferred from a flag.

        Precedence is FAILED VERIFICATION > BLOCKED > COMPLETE WITH RISKS > COMPLETE.
        A criterion carrying no evidence is a FAILURE only when nothing prevented it
        from being checked. While a block is outstanding the same criterion is a
        consequence of the block, and reporting it as a failed verification would blame
        the work for a missing credential or an undecided fork.
        """
        s = self.state
        open_hyps = sorted(h["id"] for h in s["hypotheses"].values() if h["status"] == "open")
        undecided = sorted(f for f in s["forks"] if f not in s["decisions"])
        blocked_sigs = [
            c for c in s["classifications"]
            if c["kind"] == "information_missing"
            and c["signature"] not in self._resolved_signatures()
        ]
        blocking_risks = [r for r in s["risks"] if r["blocking"]]
        covered = {e["criterion"] for e in s["evidence"] if e["tier"] != "unavailable"}
        unavailable_only = {
            c for c in s["criteria"]
            if c not in covered and any(e["criterion"] == c for e in s["evidence"])
        }
        uncovered = [c for c in s["criteria"] if c not in covered and c not in unavailable_only]
        autonomous = sorted(f for f, d in s["decisions"].items() if d["by"] == "agent")
        nonterminal = [c for c in s["classifications"] if c["kind"] != "information_missing"]

        blocks = []
        if undecided:
            blocks.append(f"forks never decided: {undecided}")
        if blocked_sigs:
            blocks.append(
                "missing facts never supplied: "
                + ", ".join(
                    f"{c['signature']} (needs {c['missing_fact']} via {c['smallest_input']})"
                    for c in blocked_sigs
                )
            )
        if blocking_risks:
            blocks.append(f"blocking risks recorded: {[r['text'] for r in blocking_risks]}")

        failures = []
        if open_hyps:
            failures.append(f"hypotheses still untested: {open_hyps}")
        if uncovered and not blocks:
            failures.append(f"acceptance criteria never checked: {uncovered}")
        if failures:
            return "FAILED VERIFICATION", failures

        if blocks:
            if uncovered:
                blocks.append(f"acceptance criteria left unverified by the block: {uncovered}")
            return "BLOCKED", blocks

        risks = []
        if unavailable_only:
            risks.append(f"criteria verified only as unavailable: {sorted(unavailable_only)}")
        if autonomous:
            risks.append(f"forks decided autonomously, not by a human: {autonomous}")
        if nonterminal:
            risks.append(
                "repeated non-convergence kept this run alive past its own diagnosis: "
                + ", ".join(sorted({c["signature"] for c in nonterminal}))
            )
        risks.extend(f"risk: {r['text']}" for r in s["risks"] if not r["blocking"])
        if risks:
            return "COMPLETE WITH RISKS", risks
        return "COMPLETE", []

    def report(self):
        s = self.state
        status, reasons = self.outcome()
        covered = {e["criterion"] for e in s["evidence"] if e["tier"] != "unavailable"}
        unavailable = {
            e["criterion"] for e in s["evidence"] if e["tier"] == "unavailable"
        }

        lines = ["# Convergence report", ""]
        lines += ["## Intent", "", s["intent"] or "_(not initialised)_", ""]

        lines += ["## Acceptance criteria", ""]
        if s["criteria"]:
            lines += [f"- `{c}`" for c in s["criteria"]]
        else:
            lines.append("_none recorded_")
        lines.append("")

        lines += ["## Work performed", ""]
        if s["fixes"]:
            for incident, fix in sorted(s["fixes"].items()):
                lines.append(f"- `{incident}`: {fix['note']}")
        else:
            lines.append("_no fix recorded_")
        lines.append("")

        lines += ["## Verification", ""]
        for label, criteria in (
            ("Passed", sorted(covered - unavailable)),
            ("Failed", []),
            ("Not run", [c for c in s["criteria"] if c not in covered and c not in unavailable]),
            ("Unavailable", sorted(unavailable)),
        ):
            lines.append(f"**{label}**")
            lines += [f"- `{c}`" for c in criteria] or ["- None"]
            lines.append("")

        lines += ["## Hypothesis ledger", ""]
        if s["hypotheses"]:
            for hyp in s["hypotheses"].values():
                closed = f" -> {hyp['status']} by `{hyp['probe']}`" if hyp["probe"] else ""
                lines.append(f"- `{hyp['id']}` [{hyp['status']}] {hyp['claim']}{closed}")
        else:
            lines.append("_none recorded_")
        lines.append("")

        lines += ["## Challenger findings", ""]
        if s["disputes"]:
            for dispute in s["disputes"]:
                verdict = f" verdict={dispute['verdict']}" if dispute.get("verdict") else ""
                lines.append(
                    f"- fork `{dispute['fork']}`: {dispute['challenger']} against "
                    f"{dispute['author']}{verdict} — {dispute['steelman']}"
                )
        else:
            lines.append("None found")
        lines.append("")

        lines += ["## Conflicts and tradeoffs", ""]
        if s["forks"]:
            for fork_id, fork in sorted(s["forks"].items()):
                decision = s["decisions"].get(fork_id)
                if decision:
                    lines.append(
                        f"- `{fork_id}`: {fork['question']} -> chose "
                        f"`{decision['chosen']}` ({decision['by']}"
                        f"{', human-acknowledged' if decision['human_ack'] else ''})"
                    )
                    if decision["invalidated"]:
                        lines.append(f"  invalidated: {', '.join(decision['invalidated'])}")
                else:
                    lines.append(f"- `{fork_id}`: {fork['question']} -> UNDECIDED")
        else:
            lines.append("None found")
        lines.append("")

        lines += ["## Decay risks", ""]
        if s["classifications"]:
            for item in s["classifications"]:
                mark = " (resolved)" if item["signature"] in self._resolved_signatures() else ""
                lines.append(
                    f"- signature `{item['signature']}` classified `{item['kind']}`{mark}"
                )
        else:
            lines.append("None found")
        lines.append("")

        lines += ["## Human anchors", ""]
        anchored = [
            f"`{f}` touches {sorted(set(d['anchors']) & set(HUMAN_ANCHORS))}"
            for f, d in sorted(s["decisions"].items())
            if set(d["anchors"]) & set(HUMAN_ANCHORS)
        ]
        lines += anchored or ["None touched"]
        lines.append("")

        lines += ["## Residual uncertainty", ""]
        lines += [f"- {r}" for r in reasons] or ["None"]
        lines.append("")

        lines += ["## Final status", "", f"**{status}**", ""]
        return "\n".join(lines)

    def next_action(self):
        s = self.state
        if s["directive"]:
            allowed = DIRECTIVE_REQUIRES[s["directive"]["kind"]]
            return {
                "required": allowed[0] if allowed else "resolve_block",
                "because": DIRECTIVE_MESSAGE[s["directive"]["kind"]],
            }
        for fork_id, fork in sorted(s["forks"].items()):
            if fork_id not in s["decisions"]:
                if not any(d["fork"] == fork_id for d in s["disputes"]):
                    return {
                        "required": "dispute",
                        "because": f"fork {fork_id!r} is open and undisputed",
                    }
                return {"required": "decide", "because": f"fork {fork_id!r} is disputed but undecided"}
        for item in s["classifications"]:
            if item["kind"] == "information_missing" and item["signature"] not in self._resolved_signatures():
                return {
                    "required": "resolve_block",
                    "because": f"blocked on {item['missing_fact']}",
                }
        unsettled = sorted(
            {h["incident"] for h in s["hypotheses"].values() if h["status"] == "open"}
        )
        if unsettled:
            return {
                "required": "hyp_refute",
                "because": f"open hypotheses on incident(s) {unsettled}",
            }
        return {"required": "close", "because": "no outstanding gate"}


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
    node.add_argument("--criterion", action="append", default=[])

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

    node = add("classify", "classify a repeated failure signature")
    node.add_argument("--signature", required=True)
    node.add_argument(
        "--kind", required=True, choices=tuple(sorted(DIRECTIVE_REQUIRES))
    )
    node.add_argument("--missing-fact")
    node.add_argument("--smallest-input")
    node.add_argument("--note")

    node = add("resolve-block", "clear a missing-fact block once the input arrives")
    node.add_argument("--signature", required=True)
    node.add_argument("--provided", required=True)

    node = add("open-fork", "record a decision fork discovered mid-execution")
    node.add_argument("--id", required=True)
    node.add_argument("--question", required=True)
    node.add_argument("--trigger", required=True)
    node.add_argument("--assumed", required=True)
    node.add_argument("--option", action="append", default=[])
    node.add_argument("--invalidation-rationale")

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
    node.add_argument("--anchor", action="append", default=[])
    node.add_argument("--invalidated", action="append", default=[])
    node.add_argument("--rationale")

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

    add("status", "print the derived state as JSON")
    add("next-action", "print what the ledger requires next")
    add("close", "compute the final status and print the report")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except Blocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return BLOCKED
    except UsageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return USAGE


def _dispatch(args):
    if args.subcommand == "init":
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
        ledger.begin_fix(args.incident, args.note)
    elif command == "classify":
        ledger.classify(
            args.signature, args.kind, args.missing_fact, args.smallest_input, args.note
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
    elif command == "status":
        print(json.dumps(ledger.state, indent=2, sort_keys=True, default=str))
        return OK
    elif command == "next-action":
        print(json.dumps(ledger.next_action(), indent=2, sort_keys=True))
        return OK
    elif command == "close":
        report = ledger.report()
        (ledger.root / "report.md").write_text(report, encoding="utf-8")
        print(report)
        status, _ = ledger.outcome()
        return OK if status in ("COMPLETE", "COMPLETE WITH RISKS") else BLOCKED
    else:  # pragma: no cover - argparse already constrains this
        raise UsageError(f"unknown command {command!r}")

    print(f"{command}: ok")
    return OK


if __name__ == "__main__":
    sys.exit(main())
