"""The confidence gate — the single place threshold policy lives.

Classifiers produce a score. Only this decides whether that score is good enough to
touch the user's files. Keeping it separate is what lets Phase 3 swap the classifier
without relitigating the safety posture, and what makes the threshold tunable against
the labeled corpus (roadmap.md §6.1) instead of by feel.

Default posture is conservative: when in doubt, Review beats a wrong auto-move
(planning.md §11).
"""

from __future__ import annotations

from osdc.domain.enums import UNCLASSIFIED, CategoryKind, Decision
from osdc.domain.models import Classification


class ConfidenceGate:
    def __init__(
        self,
        academic_threshold: float,
        general_threshold: float,
        auto_approve: bool = True,
    ) -> None:
        self._academic = academic_threshold
        self._general = general_threshold
        self._auto_approve = auto_approve

    def threshold_for(self, kind: CategoryKind) -> float:
        return self._academic if kind is CategoryKind.ACADEMIC else self._general

    def decide(self, classification: Classification) -> Decision:
        if not self._auto_approve:
            return Decision.REVIEW
        if classification.label == UNCLASSIFIED or classification.score <= 0.0:
            return Decision.REVIEW
        if classification.score >= self.threshold_for(classification.kind):
            return Decision.AUTO
        return Decision.REVIEW
