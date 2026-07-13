"""Page-aware chunking.

planning.md §13 asks whether to chunk by page or semantically. The answer here is
**page-first, then split within the page**, and the reason is citations: a chunk that
straddles a page boundary cannot honestly say which page it came from, and a citation
that points at the wrong page is worse than no citation at all — it looks authoritative
and sends the user to the wrong place.

So a chunk never crosses a page. Within a page we split on paragraph boundaries where we
can and mid-text only when a paragraph is genuinely too long, with a small overlap so a
sentence spanning the split is still retrievable from both sides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from osdc.domain.models import ExtractedText

#: Roughly 200 tokens. Big enough to carry an idea, small enough that a retrieved chunk is
#: mostly signal rather than a whole page of context the LLM has to wade through.
DEFAULT_CHUNK_CHARS = 900

#: Carried from the end of one chunk into the start of the next, so a definition split
#: across the boundary is still findable from either side.
DEFAULT_OVERLAP_CHARS = 120

#: Shorter than this and a chunk is noise — a stray heading, a page number we missed.
MIN_CHUNK_CHARS = 40

_PARAGRAPH = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    page: int
    text: str


def chunk_extracted(
    extracted: ExtractedText,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    ordinal = 0

    for page in extracted.pages:
        for body in _split_page(page.text, chunk_chars, overlap_chars):
            if len(body) < MIN_CHUNK_CHARS:
                continue
            chunks.append(Chunk(ordinal=ordinal, page=page.page, text=body))
            ordinal += 1

    # A document that is all short pages (a slide deck of bare titles) would otherwise
    # produce nothing at all and be unsearchable. Fall back to one chunk per page —
    # emphatically NOT one merged chunk for the whole document, because that would have to
    # claim a single page number for content drawn from several, and a citation pointing at
    # the wrong page is worse than no citation.
    if not chunks:
        for page in extracted.pages:
            body = page.text.strip()
            if body:
                chunks.append(Chunk(ordinal=len(chunks), page=page.page, text=body))

    return chunks


def _split_page(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]

    # Prefer paragraph boundaries — they are real semantic seams.
    paragraphs = [p.strip() for p in _PARAGRAPH.split(text) if p.strip()]

    parts: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if len(paragraph) > chunk_chars:
            if buffer:
                parts.append(buffer)
                buffer = ""
            parts.extend(_hard_split(paragraph, chunk_chars, overlap_chars))
            continue

        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) <= chunk_chars:
            buffer = candidate
        else:
            parts.append(buffer)
            buffer = paragraph

    if buffer:
        parts.append(buffer)
    return parts


def _hard_split(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    """A paragraph too long to keep whole. Break on a sentence end if one is near."""
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))

        if end < len(text):
            window = text[start:end]
            # Look for a sentence end in the last quarter of the window.
            pivot = max(
                window.rfind(". "), window.rfind("? "), window.rfind("! "), window.rfind("\n")
            )
            if pivot > chunk_chars * 0.6:
                end = start + pivot + 1

        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)

    return [p for p in parts if p]
