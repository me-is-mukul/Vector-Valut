"""Phase 0 classifier: keyword rules, scored by share of evidence.

The scoring rule is the interesting part, because it has to produce a number the
confidence gate can meaningfully threshold on.

    score(label) = hits(label) / total_hits_across_all_labels

So a document that only ever matches Operating Systems keywords scores 1.0 and is
auto-filed. A document matching two Operating Systems keywords and one Finance
keyword scores 0.67 for OS — below the 0.85 gate — and lands in the Review Queue.
That is semantically the behaviour we want: *ambiguous evidence means ask the user*,
which is the conservative posture planning.md §11 asks for.

Note this is a share, not a confidence. Phase 3 replaces it with a genuine cosine
similarity against subject embeddings; the gate above it does not change, because it
only ever consumes a 0..1 score.
"""

from __future__ import annotations

from osdc.domain.enums import UNCLASSIFIED, CategoryKind
from osdc.domain.models import Classification, EmbeddedDocument, LabelScore
from osdc.pipeline.classify.rules import ALL_RULES, KeywordRule

MAX_ALTERNATIVES = 4


class KeywordClassifier:
    def __init__(
        self,
        rules: tuple[KeywordRule, ...] = ALL_RULES,
        current_semester: int | None = None,
    ) -> None:
        self._rules = rules
        self._current_semester = current_semester

    def classify(self, doc: EmbeddedDocument) -> Classification:
        text = doc.text
        if not text.strip():
            return self._unclassified()

        raw: list[tuple[KeywordRule, int]] = [
            (rule, hits) for rule in self._rules if (hits := rule.hits(text)) > 0
        ]
        if not raw:
            return self._unclassified()

        total = sum(hits for _, hits in raw)
        scored = sorted(
            (
                LabelScore(label=rule.label, kind=rule.kind, score=hits / total)
                for rule, hits in raw
            ),
            key=lambda ls: ls.score,
            reverse=True,
        )
        best = scored[0]

        return Classification(
            label=best.label,
            kind=best.kind,
            score=best.score,
            # Phase 0 has no per-subject semester map; Phase 3's KB supplies it. The
            # organizer already honours this field, so the folder layout gains its
            # "Semester N" level for free once it is populated.
            semester=self._current_semester if best.kind is CategoryKind.ACADEMIC else None,
            alternatives=scored[1 : MAX_ALTERNATIVES + 1],
        )

    @staticmethod
    def _unclassified() -> Classification:
        return Classification(
            label=UNCLASSIFIED,
            kind=CategoryKind.GENERAL,
            score=0.0,
            alternatives=[],
        )
