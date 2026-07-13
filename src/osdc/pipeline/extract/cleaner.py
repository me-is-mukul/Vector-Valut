"""Text cleanup between extraction and embedding.

Raw PDF text is full of artefacts that are invisible to a human reader but actively
poison an embedding: words split across line breaks, and the same running header
repeated on all forty pages.

The header/footer one matters more than it looks. If "CS-501 Operating Systems — Unit 3"
sits at the top of every page, it appears in *every* chunk, and every chunk's embedding
drifts toward it. Retrieval then returns whichever chunk is longest rather than whichever
is relevant, because they all look alike. Stripping the repeats is what makes chunk
embeddings actually about their own content.
"""

from __future__ import annotations

import re

from osdc.domain.models import ExtractedText, PageSpan

#: A line has to appear on at least this share of pages to count as a running header.
HEADER_PAGE_RATIO = 0.6

#: ...and we need enough pages for that ratio to mean anything.
MIN_PAGES_FOR_HEADER_DETECTION = 3

#: Long lines are content, not furniture, however often they repeat.
MAX_HEADER_LINE_CHARS = 80

# "inter-\nnational" → "international". Only when the next line starts lowercase;
# "Operating-\nSystems" is a real hyphen and must survive.
_HYPHEN_BREAK = re.compile(r"(\w)-\n([a-z])")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_MANY_SPACES = re.compile(r"[ \t]{2,}")
_PAGE_NUMBER_LINE = re.compile(r"^\s*(page\s+)?\d+\s*(/\s*\d+)?\s*$", re.IGNORECASE)


def clean_text(text: str) -> str:
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _TRAILING_SPACE.sub("\n", text)
    text = _MANY_SPACES.sub(" ", text)
    text = _MANY_NEWLINES.sub("\n\n", text)
    return text.strip()


def find_repeated_lines(pages: list[PageSpan]) -> set[str]:
    """Lines that appear on most pages — running headers, footers, page numbers."""
    if len(pages) < MIN_PAGES_FOR_HEADER_DETECTION:
        return set()

    counts: dict[str, int] = {}
    for page in pages:
        # A line repeated twice on one page still only votes once.
        seen = {
            line.strip()
            for line in page.text.splitlines()
            if line.strip() and len(line.strip()) <= MAX_HEADER_LINE_CHARS
        }
        for line in seen:
            counts[line] = counts.get(line, 0) + 1

    threshold = len(pages) * HEADER_PAGE_RATIO
    return {line for line, count in counts.items() if count >= threshold}


def clean_extracted(extracted: ExtractedText) -> ExtractedText:
    """Strip running furniture, then normalise each page. Page numbers are preserved."""
    repeated = find_repeated_lines(extracted.pages)

    cleaned: list[PageSpan] = []
    for page in extracted.pages:
        kept = [
            line
            for line in page.text.splitlines()
            if line.strip() not in repeated and not _PAGE_NUMBER_LINE.match(line)
        ]
        body = clean_text("\n".join(kept))
        if body:
            cleaned.append(PageSpan(page=page.page, text=body))

    return extracted.model_copy(update={"pages": cleaned})
