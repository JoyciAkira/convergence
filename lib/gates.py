"""Entry A and C: the red-capable loop, rival hypotheses, the fix gate.

A fix chosen before its rivals are settled is a guess; the gates here make that
guess unexecutable instead of merely discouraged.
"""
from __future__ import annotations

import re

from claims import _claim_words
from constants import (
    CLAIM_OVERLAP_THRESHOLD,
    DIRECTIVE_MESSAGE,
    DIRECTIVE_REQUIRES,
    MIN_BLOCK_ROUTES,
    SEARCH_WIDTH,
)
from exceptions import Blocked, UsageError
from state import one_of, req, transactional

#: Words indicating the smallest input names an external actor, not an action the run
#: can take. When one of these is present the fact genuinely lives outside the session.
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

class GateMixin:
    """Loop declaration, hypothesis elimination, the fix gate and classification."""

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

    @transactional
    def record_meta_fail(self, signature, failure_kind):
        """Record a failed run signature for the meta want-rewind dataset (10)."""
        signature = req(signature, "--signature")
        failure_kind = req(failure_kind, "--failure-kind")
        self._append("control", {"ev": "meta_record", "signature": signature, "failure_kind": failure_kind})
        return self

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
        meta = self.state.get("meta", {})
        if meta.get("exploratory") and not any(
            c.get("selected") for c in self.state.get("candidates", [])
        ):
            raise Blocked(
                "this run is exploratory (init --exploratory), so a fix may not be "
                "chosen before candidate solutions exist and one has been selected: "
                "run the exploration (arena --quick, parallel agents, manual drafts), "
                "record it with record-candidates, choose with select-candidate, then "
                "begin-fix. An exploratory run that skips selection is a preference "
                "wearing a verdict."
            )
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
                f"rivals, and the report discloses that choice."
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


    def _resolved_signatures(self):
        return {r["signature"] for r in self.state["resolved_blocks"]}
