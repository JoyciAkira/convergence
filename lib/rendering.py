"""Report rendering helpers: cap a section, point at the log for the rest."""
from __future__ import annotations

from constants import REPORT_ROW_CAP  # noqa: F401  (re-exported)

def rows(items, pointer="the ledger logs", cap=REPORT_ROW_CAP):
    """Cap a section so a 3,000-event run does not produce an unreadable document."""
    items = list(items)
    if not items:
        return ["- None"]
    if len(items) <= cap:
        return items
    return items[:cap] + [f"- …and {len(items) - cap} more; the full record is in {pointer}"]
