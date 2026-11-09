"""Intake: tickets, reconstruction obligations and exploratory candidates."""
from __future__ import annotations

from constants import CANDIDATE_SOURCES
from exceptions import Blocked, UsageError
from state import one_of, req, transactional

class IntakeMixin:
    """Upstream artefacts become criteria; a merged ticket is not an earned one."""

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
        existing = {t["id"]: t for t in self.state.get("tickets", [])}
        fresh = []
        for t in tickets:
            prev = existing.get(t["id"])
            if prev is not None:
                if prev == t:
                    continue  # idempotent re-import: identical ticket already recorded
                raise UsageError(
                    f"ticket {t['id']} is already imported with different content; "
                    f"refusing ambiguous re-import"
                )
            fresh.append(t)
        if not fresh:
            return self
        self._append("control", {"ev": "tickets_imported", "tickets": fresh})
        return self

    @transactional
    def import_obligations(self, path):
        """Map reverse engineering reconstruction obligations onto acceptance criteria.

        The upstream reverse engineering workflow (REA / Ghidra / clean-room specs)
        produces ReconstructionObligationLedgers with structural, behavioral, or
        control-flow obligations. Importing obligations keeps the boundary visible:
        every criterion remembers its obligation, so the report can name which
        obligation *earned* its close and which remains unproven.
        """
        from obligations import parse_obligations

        obligations = parse_obligations(path)
        existing = {o["id"]: o for o in self.state.get("obligations", [])}
        fresh = []
        for o in obligations:
            prev = existing.get(o["id"])
            if prev is not None:
                if prev == o:
                    continue  # idempotent re-import: identical obligation already recorded
                raise UsageError(
                    f"obligation {o['id']} is already imported with different content; "
                    f"refusing ambiguous re-import"
                )
            fresh.append(o)
        if not fresh:
            return self
        self._append("control", {"ev": "obligations_imported", "obligations": fresh})
        return self

    @transactional
    def record_candidates(self, run_id, source, count, note=None, files=()):
        """Record that an exploration pass produced candidate solutions.

        The ledger does not generate candidates — arena, parallel agents or the
        author do. This records provenance (where they came from, how many,
        where they live) so the later selection can name what it chose among.
        """
        self._enforce_directive("record_candidates")
        run_id = req(run_id, "--run-id")
        source = one_of(source, CANDIDATE_SOURCES, "--source")
        count = int(count)
        if count < 1:
            raise UsageError("--count must be >= 1: an exploration that produced "
                             "nothing is not a recording, it is a finding")
        candidate = {
            "run_id": run_id,
            "source": source,
            "count": count,
            "note": note,
            "files": list(files or []),
        }
        self._append("control", {"ev": "candidates_recorded", "candidates": [candidate]})
        self._clear_directive("record_candidates")
        return self

    @transactional
    def select_candidate(self, run_id, ident, by, method, fork=None):
        """Record which candidate solution this run pursues.

        ``by='disputed'`` requires a recorded dispute on ``--fork``: the choice
        was contested by a separate context before it was made. ``by='author'``
        records an unopposed self-selection; it never blocks, but the report
        carries it under disclosures — selecting your own candidate in silence
        is exactly what this ledger exists to prevent.
        """
        self._enforce_directive("select_candidate")
        run_id = req(run_id, "--run-id")
        ident = req(ident, "--id")
        by = one_of(by, ("disputed", "author"), "--by")
        method = req(method, "--method")
        if by == "disputed":
            fork = req(fork, "--fork (required when --by disputed)")
            if not any(d.get("fork") == fork for d in self.state.get("disputes", [])):
                raise Blocked(
                    f"--by disputed requires a dispute on fork {fork!r}, and none is "
                    f"recorded. Run dispute first — a selection 'disputed' without a "
                    f"dispute launders a preference into a verdict."
                )
        self._append(
            "control",
            {
                "ev": "candidate_selected",
                "run_id": run_id,
                "id": ident,
                "by": by,
                "method": method,
                "fork": fork if by == "disputed" else None,
            },
        )
        self._clear_directive("select_candidate")
        return self
