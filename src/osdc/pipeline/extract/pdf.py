"""PDF extraction — PyMuPDF first, pdfplumber as the fallback.

Two things this must get right:

1. **Per-page spans, never a flat string.** Everything downstream that says "page 12"
   traces back to here. Throw the page number away at extraction time and Phase 5's
   citations become unimplementable.

2. **Honest image-based detection.** A PDF with a text layer must skip OCR; a scan must
   be flagged for it. The heuristic is characters-per-page: a real digital page carries
   hundreds; a scanned page yields a handful of stray ligatures, or nothing.
   Get this wrong in the cheap direction and you OCR thousands of digital PDFs for
   nothing. Wrong in the expensive direction and scans reach the classifier as empty
   strings and get filed on a guess.
"""

from __future__ import annotations

import logging
from pathlib import Path

from osdc.domain.enums import FileType
from osdc.domain.models import ExtractedText, PageSpan

logger = logging.getLogger(__name__)

#: Below this many characters per page, we call the page image-based (a scan).
#: Empirically a digital text page is in the hundreds; a scanned one is near zero.
MIN_CHARS_PER_PAGE = 50

#: If PyMuPDF gets less than this, it is worth paying for a pdfplumber second opinion —
#: some PDFs (odd encodings, unusual layout engines) defeat one library but not the other.
PLUMBER_RETRY_THRESHOLD = 100


class PdfExtractor:
    name = "pdf"

    def supports(self, path: Path, file_type: FileType) -> bool:
        return file_type is FileType.PDF

    def extract(self, path: Path) -> ExtractedText:
        pages = self._with_pymupdf(path)
        total = sum(len(p.text) for p in pages)

        if pages and total < PLUMBER_RETRY_THRESHOLD:
            fallback = self._with_pdfplumber(path)
            if sum(len(p.text) for p in fallback) > total:
                logger.info("pdfplumber beat PyMuPDF on %s", path.name)
                pages = fallback
                total = sum(len(p.text) for p in pages)

        page_count = max(len(pages), 1)
        is_image_based = (total / page_count) < MIN_CHARS_PER_PAGE

        return ExtractedText(pages=pages, is_image_based=is_image_based, ocr_used=False)

    @staticmethod
    def _with_pymupdf(path: Path) -> list[PageSpan]:
        import pymupdf

        try:
            with pymupdf.open(path) as doc:
                return [
                    PageSpan(page=i + 1, text=page.get_text().strip()) for i, page in enumerate(doc)
                ]
        except Exception as exc:  # a corrupt PDF is a Review Queue item, not a crash
            logger.warning("PyMuPDF failed on %s: %s", path.name, exc)
            return []

    @staticmethod
    def _with_pdfplumber(path: Path) -> list[PageSpan]:
        import pdfplumber

        try:
            with pdfplumber.open(path) as doc:
                return [
                    PageSpan(page=i + 1, text=(page.extract_text() or "").strip())
                    for i, page in enumerate(doc.pages)
                ]
        except Exception as exc:
            logger.warning("pdfplumber failed on %s: %s", path.name, exc)
            return []
