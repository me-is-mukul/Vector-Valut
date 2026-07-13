"""OCR stub.

Phase 0 does no OCR. ``NullOcr`` exists so the pipeline can *call* the OCR seam and
handle its "no text" answer correctly — which means Phase 2 swaps in PaddleOCR
(Tesseract fallback) and the orchestrator does not change at all.

Reporting ``available() -> False`` is deliberate: the pipeline flags image-based
files as low-text and routes them to the Review Queue, rather than pretending the
OCR ran and classifying on an empty string.
"""

from __future__ import annotations

from pathlib import Path

from osdc.domain.enums import OcrEngine
from osdc.domain.models import OcrResult


class NullOcr:
    name = "null"

    def available(self) -> bool:
        return False

    def ocr(self, path: Path) -> OcrResult:
        return OcrResult(pages=[], engine=OcrEngine.NONE, confidence=0.0)
