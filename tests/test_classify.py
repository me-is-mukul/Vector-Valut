"""Classifier scoring and the confidence gate.

The gate is the safety valve — it is what stands between a mediocre classifier and a
folder full of misfiled documents. These tests pin its behaviour so Phase 3 can swap the
classifier underneath without quietly changing the posture.
"""

from __future__ import annotations

import pytest

from osdc.domain.enums import UNCLASSIFIED, CategoryKind, Decision, FileType
from osdc.domain.models import Classification, EmbeddedDocument
from osdc.pipeline.classify.gate import ConfidenceGate
from osdc.pipeline.classify.keyword import KeywordClassifier
from osdc.pipeline.embed.hash_embedder import HashEmbedder


def doc(text: str) -> EmbeddedDocument:
    return EmbeddedDocument(
        file_id="f1", file_type=FileType.TXT, text=text, vector=HashEmbedder().embed([text])[0]
    )


def gate(auto_approve: bool = True) -> ConfidenceGate:
    return ConfidenceGate(
        academic_threshold=0.85, general_threshold=0.70, auto_approve=auto_approve
    )


# --- classification ---------------------------------------------------------


def test_unambiguous_academic_text_scores_one_and_auto_files() -> None:
    """The roadmap's Phase 0 exit criterion, at the unit level."""
    result = KeywordClassifier().classify(doc("Notes on paging and page fault handling."))

    assert result.label == "Operating Systems"
    assert result.kind is CategoryKind.ACADEMIC
    assert result.score == pytest.approx(1.0)
    assert gate().decide(result) is Decision.AUTO


def test_general_category_is_matched() -> None:
    result = KeywordClassifier().classify(doc("INVOICE — amount due: 4,200. GST included."))
    assert result.label == "Finance"
    assert result.kind is CategoryKind.GENERAL
    assert gate().decide(result) is Decision.AUTO


def test_ambiguous_evidence_goes_to_review_not_a_guess() -> None:
    """Two OS keywords and one Finance keyword → 0.67 for OS, under the 0.85 gate.

    This is the behaviour we actually want: mixed evidence means ask the user, rather
    than confidently filing a document into the wrong folder (planning.md §11).
    """
    result = KeywordClassifier().classify(doc("paging, deadlock, and the invoice for it"))

    assert result.label == "Operating Systems"
    assert result.score == pytest.approx(2 / 3)
    assert gate().decide(result) is Decision.REVIEW


def test_alternatives_are_reported_for_the_review_ui() -> None:
    result = KeywordClassifier().classify(doc("paging, deadlock, and the invoice for it"))
    assert [a.label for a in result.alternatives] == ["Finance"]


def test_text_with_no_known_keywords_is_unclassified() -> None:
    result = KeywordClassifier().classify(doc("A recipe for sourdough bread."))
    assert result.label == UNCLASSIFIED
    assert result.score == 0.0
    assert gate().decide(result) is Decision.REVIEW


def test_empty_text_is_unclassified() -> None:
    assert KeywordClassifier().classify(doc("   ")).label == UNCLASSIFIED


def test_keywords_match_on_word_boundaries() -> None:
    """'sql' must not fire inside 'sqlite'; that is how you get nonsense classifications."""
    result = KeywordClassifier().classify(doc("I used sqlite and postgresql yesterday"))
    assert result.label == UNCLASSIFIED


def test_repetition_is_not_evidence() -> None:
    """Ten mentions of 'paging' is one distinct keyword, not ten. Otherwise a single
    word repeated in a header would dominate every score."""
    once = KeywordClassifier().classify(doc("paging. invoice."))
    many = KeywordClassifier().classify(doc("paging paging paging paging. invoice."))
    assert once.score == many.score == pytest.approx(0.5)


def test_current_semester_is_attached_to_academic_results_only() -> None:
    classifier = KeywordClassifier(current_semester=5)
    assert classifier.classify(doc("paging")).semester == 5
    assert classifier.classify(doc("invoice")).semester is None


# --- the gate ---------------------------------------------------------------


def test_academic_and_general_thresholds_are_independent() -> None:
    g = gate()
    assert g.threshold_for(CategoryKind.ACADEMIC) == 0.85
    assert g.threshold_for(CategoryKind.GENERAL) == 0.70


def test_a_score_between_the_two_thresholds_splits_by_kind() -> None:
    g = gate()
    academic = Classification(label="X", kind=CategoryKind.ACADEMIC, score=0.75)
    general = Classification(label="Y", kind=CategoryKind.GENERAL, score=0.75)

    assert g.decide(academic) is Decision.REVIEW  # 0.75 < 0.85
    assert g.decide(general) is Decision.AUTO  # 0.75 >= 0.70


def test_auto_approve_off_sends_everything_to_review() -> None:
    certain = Classification(label="X", kind=CategoryKind.ACADEMIC, score=1.0)
    assert gate(auto_approve=False).decide(certain) is Decision.REVIEW


def test_a_score_exactly_on_the_threshold_auto_files() -> None:
    on_the_line = Classification(label="X", kind=CategoryKind.ACADEMIC, score=0.85)
    assert gate().decide(on_the_line) is Decision.AUTO


# --- the embedder -----------------------------------------------------------


def test_hash_embedder_is_deterministic() -> None:
    assert HashEmbedder().embed(["paging"]) == HashEmbedder().embed(["paging"])


def test_hash_embedder_produces_unit_vectors() -> None:
    (vector,) = HashEmbedder().embed(["paging and deadlock"])
    assert sum(v * v for v in vector) == pytest.approx(1.0)


def test_hash_embedder_handles_empty_text() -> None:
    (vector,) = HashEmbedder().embed([""])
    assert vector == [0.0] * HashEmbedder().dim
