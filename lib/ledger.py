"""The assembled ledger.

Each mixin owns one domain and its gates; this class is only the composition, so
a reader can find a rule by knowing which decision it governs.
"""
from __future__ import annotations

from decide import DecideMixin
from evidence import EvidenceMixin
from gates import GateMixin
from infra import InfraMixin
from intake import IntakeMixin
from preflight import PreflightMixin
from state import StateMixin


class Ledger(
    StateMixin,
    GateMixin,
    DecideMixin,
    EvidenceMixin,
    IntakeMixin,
    InfraMixin,
    PreflightMixin,
):
    """Append-only ledger enforcing hypothesis elimination, fork disputes,
    non-convergence classification and evidence tiers."""
