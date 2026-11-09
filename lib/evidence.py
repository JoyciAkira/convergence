"""Evidence tiers: a claim is only a claim once a named command produced output."""
from __future__ import annotations

from claims import _closest
from constants import EVIDENCE_TIERS, EXPECTED_SOURCES
from exceptions import Blocked
from state import one_of, req, transactional

class EvidenceMixin:
    """Three tiers, one anti-tautology control."""

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
                    "independent_oracle, binary_oracle, or rea_evidence."
                )
        critic_flagged = {
            f["criterion"]: f["flag"]
            for f in self.state.get("meta", {}).get("critic", [])
        }
        if criterion in critic_flagged and expected_source is None:
            raise Blocked(
                f"criterion {criterion!r} was flagged by the intake critic "
                f"({critic_flagged[criterion]}): vague phrasing cannot lean on an "
                f"implicit oracle. Pass --expected-source explicitly (spec, literal, "
                f"worked_example, independent_oracle, binary_oracle, rea_evidence), or "
                f"move the criterion via a disputed fork to wording a command can check."
            )
        if tier == "reproduced_independently":
            recorder = req(recorder, "--recorder")
            reproduced_by = req(reproduced_by, "--reproduced-by")
            if str(reproduced_by).strip().casefold() == str(recorder).strip().casefold():
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
