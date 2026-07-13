"""Extractor registry.

Phase 1 registers PyMuPDF / pdfplumber / python-docx / python-pptx here and nothing
else in the codebase changes — that is the seam working as intended.
"""

from __future__ import annotations

from pathlib import Path

from osdc.domain.enums import FileType
from osdc.domain.ports import TextExtractor


class ExtractorRegistry:
    def __init__(self, extractors: list[TextExtractor] | None = None) -> None:
        self._extractors: list[TextExtractor] = list(extractors or [])

    def register(self, extractor: TextExtractor) -> None:
        self._extractors.append(extractor)

    def find(self, path: Path, file_type: FileType) -> TextExtractor | None:
        for extractor in self._extractors:
            if extractor.supports(path, file_type):
                return extractor
        return None

    @property
    def names(self) -> list[str]:
        return [e.name for e in self._extractors]
