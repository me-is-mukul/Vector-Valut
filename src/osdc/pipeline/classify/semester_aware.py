"""Phase 3 classifier — architecture.md §6, for real.

    current-semester subjects → all subjects → general categories → Review Queue

The hard part is not the search. It is turning a cosine similarity into a number the
confidence gate can threshold on, without silently changing what the threshold *means*.

**Why raw cosine will not do.** Sentence embeddings have a high, non-zero similarity
floor: with bge, two totally unrelated texts still score ~0.3-0.5, and a good match is
~0.6-0.8. Feed that to a 0.85 gate and nothing is ever auto-filed. Lower the gate to 0.6
and now "sourdough recipe" scores 0.62 against Machine Learning and gets filed there,
because it is the *best* of a uniformly bad set. Raw cosine measures "how close", never
"how sure".

**What we do instead — two independent questions.**

1. *Is this document about any subject at all?* An absolute floor (``min_similarity``) on
   the raw cosine. Nothing below it is a candidate. This is what stops the recipe.

2. *Given that it is, which one?* A softmax over the surviving candidates. One clear
   winner → ~1.0. Two plausible subjects → ~0.5 each → under the gate → Review Queue.

That second number is a genuine confidence, and it is on the *same scale as Phase 0's
share-of-evidence score* — so the 0.85 threshold survives the swap from keyword rules to
embeddings without being re-tuned or re-interpreted. The seam holds.

Both constants are settings, because roadmap.md §6.1 is right that they should be
calibrated against a labeled corpus rather than picked by feel. See ``scripts/calibrate.py``.
"""

from __future__ import annotations

import logging
import math

from osdc.domain.enums import UNCLASSIFIED, CategoryKind
from osdc.domain.models import Classification, EmbeddedDocument, Hit, LabelScore, Vector
from osdc.domain.ports import VectorStore

logger = logging.getLogger(__name__)

MAX_ALTERNATIVES = 4

#: Candidates are pooled from the top this-many neighbours before the softmax.
TOP_K = 5

# Collection names, duplicated here rather than imported from storage/ — pipeline must
# not depend on storage (the layering contract). They are string contracts, not code.
SUBJECT_KB = "subject_kb"
CATEGORY_PROTOTYPES = "category_prototypes"


def softmax(scores: list[float], temperature: float) -> list[float]:
    """Sharpen a set of similarities into a probability distribution.

    Temperature matters: cosines live in a narrow band (say 0.55-0.75), so a temperature
    of 1.0 would flatten everything to a near-uniform distribution and no document would
    ever clear the gate. A low temperature (~0.05) restores the resolution.
    """
    if not scores:
        return []
    scaled = [s / temperature for s in scores]
    peak = max(scaled)  # subtract the max for numerical stability
    exps = [math.exp(s - peak) for s in scaled]
    total = sum(exps)
    return [e / total for e in exps] if total else [0.0] * len(scores)


class SemesterAwareClassifier:
    def __init__(
        self,
        vector_store: VectorStore,
        current_semester: int | None = None,
        min_academic_similarity: float = 0.45,
        min_general_similarity: float = 0.40,
        temperature: float = 0.05,
    ) -> None:
        self._vectors = vector_store
        self._current_semester = current_semester
        self._min_academic = min_academic_similarity
        self._min_general = min_general_similarity
        self._temperature = temperature

    def classify(self, doc: EmbeddedDocument) -> Classification:
        if not doc.text.strip():
            return _unclassified()

        # 1. Current semester first. This is the whole point of being semester-aware:
        #    it drastically cuts false positives, because the document a student just
        #    downloaded is overwhelmingly likely to be for a course they are taking now.
        if self._current_semester is not None:
            hits = self._search(SUBJECT_KB, doc.vector, where={"semester": self._current_semester})
            result = self._resolve(hits, CategoryKind.ACADEMIC, self._min_academic)
            if result is not None:
                return result

        # 2. Broaden to every semester — revision, backlogs, next term's reading.
        hits = self._search(SUBJECT_KB, doc.vector)
        result = self._resolve(hits, CategoryKind.ACADEMIC, self._min_academic)
        if result is not None:
            return result

        # 3. Not academic at all → the general categories.
        hits = self._search(CATEGORY_PROTOTYPES, doc.vector)
        result = self._resolve(hits, CategoryKind.GENERAL, self._min_general)
        if result is not None:
            return result

        return _unclassified()

    # ------------------------------------------------------------------
    def _search(
        self, collection: str, vector: Vector, where: dict[str, object] | None = None
    ) -> list[Hit]:
        return self._vectors.query(collection, vector, k=TOP_K, where=where)

    def _resolve(self, hits: list[Hit], kind: CategoryKind, floor: float) -> Classification | None:
        """Apply the absolute floor, then softmax the survivors. None = no match here."""
        candidates = [h for h in hits if h.score >= floor]
        if not candidates:
            return None

        confidences = softmax([h.score for h in candidates], self._temperature)
        scored = sorted(
            (
                LabelScore(label=str(hit.metadata.get("label", "?")), kind=kind, score=conf)
                for hit, conf in zip(candidates, confidences, strict=True)
            ),
            key=lambda ls: ls.score,
            reverse=True,
        )
        best = scored[0]
        best_hit = max(candidates, key=lambda h: h.score)

        logger.debug(
            "%s: raw cosine %.3f → confidence %.3f (%d candidates above %.2f)",
            best.label,
            best_hit.score,
            best.score,
            len(candidates),
            floor,
        )

        semester = best_hit.metadata.get("semester") if kind is CategoryKind.ACADEMIC else None
        return Classification(
            label=best.label,
            kind=kind,
            score=best.score,
            semester=int(semester)
            if isinstance(semester, int | float | str) and semester
            else None,
            alternatives=scored[1 : MAX_ALTERNATIVES + 1],
        )


def _unclassified() -> Classification:
    return Classification(label=UNCLASSIFIED, kind=CategoryKind.GENERAL, score=0.0, alternatives=[])
