"""ClaimComparator: interface at the similarity seam for paraphrase detection.

Two adapters justify this seam:
- JaccardComparator: production adapter using word-set overlap (current behavior)
- ExactMatchComparator: test adapter returning 1.0 for identical strings, 0.0 otherwise

The interface returns a float so callers can apply their own thresholds:
the paraphrase gate uses 0.7, did-you-mean suggestions use 0.4.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod

_STOPWORDS = frozenset(
    "a an the is are was were be been being in of to it its and or for on that this "
    "with from by not has have had does do did when if at as we you they he she".split()
)


class ClaimComparator(ABC):
    """Interface at the similarity seam.

    Measures how similar two claim strings are. Returns a float in [0.0, 1.0].
    Callers decide what threshold constitutes a duplicate or a suggestion.
    """

    @abstractmethod
    def similarity(self, a: str, b: str) -> float:
        """Return similarity score between two claims. 0.0 = unrelated, 1.0 = identical."""


class JaccardComparator(ClaimComparator):
    """Production adapter: Jaccard overlap of content words, ignoring stopwords."""

    def similarity(self, a: str, b: str) -> float:
        ka = {w for w in re.findall(r"[a-z0-9]+", a.lower()) if len(w) > 2 and w not in _STOPWORDS}
        kb = {w for w in re.findall(r"[a-z0-9]+", b.lower()) if len(w) > 2 and w not in _STOPWORDS}
        if not ka or not kb:
            return 0.0
        return len(ka & kb) / len(ka | kb)


class ExactMatchComparator(ClaimComparator):
    """Test adapter: 1.0 for identical strings (case-insensitive), 0.0 otherwise."""

    def similarity(self, a: str, b: str) -> float:
        return 1.0 if a.strip().lower() == b.strip().lower() else 0.0