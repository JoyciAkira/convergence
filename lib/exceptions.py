"""Shared exception types for the convergence ledger.

Extracted to avoid circular imports: events.py raises these, convergence.py
catches and re-raises them, and tests assert on them by identity. All three
import from here instead of defining or cross-importing their own copies.
"""


class Blocked(Exception):
    """A gate refused the action. Exit code 3."""
    pass


class UsageError(Exception):
    """Malformed input or corrupt log. Exit code 2."""
    pass


class IntakeError(Exception):
    """The requested intent could not be stabilised into a verifiable magnet."""
    pass