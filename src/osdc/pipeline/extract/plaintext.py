"""Phase 0's only real extractor: plain text and Markdown.

Note that it already returns ``PageSpan``s rather than a flat string. There is only
ever one span here, but the *shape* is the one Phase 1 needs for per-page citations
— the seam has to carry page provenance from the very first commit or Phase 5 can
never add it (roadmap.md, Phase 1).
"""

from __future__ import annotations

from pathlib import Path

from osdc.domain.enums import FileType
from osdc.domain.models import ExtractedText, PageSpan

_ENCODINGS = ("utf-8", "utf-16", "latin-1")


class PlainTextExtractor:
    name = "plaintext"

    def supports(self, path: Path, file_type: FileType) -> bool:
        return file_type in (FileType.TXT, FileType.MD)

    def extract(self, path: Path) -> ExtractedText:
        raw = path.read_bytes()
        text = ""
        for encoding in _ENCODINGS:
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")

        return ExtractedText(
            pages=[PageSpan(page=1, text=text.strip())],
            is_image_based=False,
            ocr_used=False,
        )
