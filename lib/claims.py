"""Claim comparison: paraphrase and near-miss detection.

Padding a ledger to three hypotheses is the failure mode this guards; exact string
equality cannot catch a restatement.
"""
from __future__ import annotations

import re

from constants import CLAIM_OVERLAP_THRESHOLD  # noqa: F401  (re-exported)

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
