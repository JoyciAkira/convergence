"""Entry B: forks, adversarial dispute and delegated authority."""
from __future__ import annotations

from constants import HUMAN_ANCHORS, REVERSIBILITIES
from exceptions import Blocked, UsageError
from state import one_of, req, transactional

class DecideMixin:
    """Forks disputed from a context that is not the author's."""

    @staticmethod
    def decision_weight(kind, anchors, reversibility):
        """Public alias for the derivation weight heuristic, surfaced on Ledger for
        tests and CLI gating without importing derivation in consumers."""
        from derivation import decision_weight as _dw
        return _dw(kind, anchors, reversibility)

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
        # Validate the edge move before writing: a refusal must not leave a
        # decision event behind, or the fork wedges as already decided.
        if record.get("criterion_to") is not None and chosen not in ("revise", "keep"):
            raise UsageError(
                f"fork {fork!r} moves the right edge, so it must be decided as "
                f"'revise' or 'keep', not {chosen!r}"
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
        if record.get("criterion_to") is not None and chosen == "revise":
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

    @transactional
    def record_risk(self, text, blocking=False):
        self._append(
            "control",
            {"ev": "risk", "text": req(text, "--text"), "blocking": bool(blocking)},
        )
        return self
