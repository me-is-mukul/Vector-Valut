"""Semantic search over the document chunks.

Phase 3 makes this genuinely semantic: with Sentence-Transformers behind the embedder
port, "virtual memory management" now finds the paging notes even though the phrase never
appears in them. That is the whole difference between the hashing stub and a real model,
and none of the code here changed to get it — only ``container.py`` did.

Results are deduped by file (Phase 7 will merge image collections in alongside).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from osdc.domain.models import FileRecord
from osdc.domain.ports import TextEmbedder, VectorStore
from osdc.storage import vectors
from osdc.storage.repositories import ChunkRepository, FileRepository

#: Below this, a "match" is just the embedding-space floor rather than a real hit.
DEFAULT_FLOOR = 0.35


@dataclass(frozen=True)
class SearchResult:
    file: FileRecord
    score: float
    page: int | None
    excerpt: str


class SearchService:
    def __init__(
        self,
        files: FileRepository,
        chunks: ChunkRepository,
        embedder: TextEmbedder,
        vector_store: VectorStore,
        floor: float = DEFAULT_FLOOR,
    ) -> None:
        self._files = files
        self._chunks = chunks
        self._embedder = embedder
        self._vectors = vector_store
        self._floor = floor

    async def search(self, query: str, k: int = 10) -> list[SearchResult]:
        if not query.strip():
            return []
        return await asyncio.to_thread(self._search_sync, query, k)

    def _search_sync(self, query: str, k: int) -> list[SearchResult]:
        # embed_query, not embed: bge is asymmetric (see sentence_embedder.py).
        query_vector = self._embedder.embed_query([query])[0]
        # Over-fetch, because several hits will collapse onto the same file.
        hits = self._vectors.query(vectors.DOC_CHUNKS, query_vector, k=k * 3)

        results: list[SearchResult] = []
        seen: set[str] = set()
        for hit in hits:
            if hit.score < self._floor or len(results) >= k:
                continue

            chunk = self._chunks.get(hit.id)
            if chunk is None or chunk.file_id in seen:
                continue
            record = self._files.get(chunk.file_id)
            if record is None:
                continue

            seen.add(chunk.file_id)
            results.append(
                SearchResult(
                    file=record,
                    score=hit.score,
                    page=chunk.page,
                    excerpt=_excerpt(chunk.content),
                )
            )
        return results


def _excerpt(text: str, limit: int = 220) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit].rsplit(" ", 1)[0] + "…"
