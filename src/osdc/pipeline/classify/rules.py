"""Phase 0's hardcoded keyword rules.

This is a placeholder for the Subject Knowledge Base (roadmap.md §7) and the general
category prototypes. Phase 3 replaces the whole file with embeddings; until then
these rules are enough to prove the routing, the confidence gate, and the Review
Queue all work.

Keep the academic labels aligned with real subjects — they become the seed data for
the Phase 3 KB.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from osdc.domain.enums import CategoryKind


@dataclass(frozen=True)
class KeywordRule:
    label: str
    kind: CategoryKind
    keywords: tuple[str, ...]

    def hits(self, text: str) -> int:
        """Number of *distinct* keywords present. Repetition is not evidence."""
        return sum(1 for kw in self.keywords if _pattern(kw).search(text))


_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _pattern(keyword: str) -> re.Pattern[str]:
    """Word-boundary match, so 'os' does not fire inside 'closed'."""
    cached = _PATTERN_CACHE.get(keyword)
    if cached is None:
        cached = re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
        _PATTERN_CACHE[keyword] = cached
    return cached


ACADEMIC_RULES: tuple[KeywordRule, ...] = (
    KeywordRule(
        "Operating Systems",
        CategoryKind.ACADEMIC,
        (
            "paging",
            "deadlock",
            "semaphore",
            "mutex",
            "scheduler",
            "virtual memory",
            "context switch",
            "thrashing",
            "page fault",
            "kernel",
            "process scheduling",
        ),
    ),
    KeywordRule(
        "Computer Networks",
        CategoryKind.ACADEMIC,
        (
            "tcp",
            "udp",
            "subnet",
            "osi model",
            "routing",
            "packet",
            "ethernet",
            "dns",
            "congestion control",
            "three-way handshake",
        ),
    ),
    KeywordRule(
        "Data Structures",
        CategoryKind.ACADEMIC,
        (
            "binary tree",
            "linked list",
            "hash table",
            "traversal",
            "heap",
            "graph traversal",
            "time complexity",
            "big o",
            "recursion",
            "quicksort",
        ),
    ),
    KeywordRule(
        "Database Systems",
        CategoryKind.ACADEMIC,
        (
            "normalization",
            "sql",
            "primary key",
            "foreign key",
            "transaction",
            "acid",
            "indexing",
            "relational algebra",
            "query optimization",
        ),
    ),
)

GENERAL_RULES: tuple[KeywordRule, ...] = (
    KeywordRule(
        "Finance",
        CategoryKind.GENERAL,
        ("invoice", "receipt", "payment", "gst", "tax", "amount due", "billing", "salary"),
    ),
    KeywordRule(
        "Identity",
        CategoryKind.GENERAL,
        ("aadhaar", "passport", "driving licence", "driving license", "pan card", "date of birth"),
    ),
    KeywordRule(
        "Medical",
        CategoryKind.GENERAL,
        ("prescription", "diagnosis", "patient", "dosage", "medical report", "blood test"),
    ),
    KeywordRule(
        "Legal",
        CategoryKind.GENERAL,
        ("agreement", "hereby", "whereas", "indemnity", "jurisdiction", "terms and conditions"),
    ),
)

ALL_RULES: tuple[KeywordRule, ...] = ACADEMIC_RULES + GENERAL_RULES
