"""RAG over the document collection — architecture.md §8.

    embed question → retrieve chunks → relevance floor → prompt → local LLM → cited answer

**The non-negotiable, from roadmap.md Phase 5:** if retrieval returns nothing above the
relevance floor, we answer "I don't have that" and never call the model. We do not let it
fall back on its own weights.

This is not pedantry. The user's library contains their medical records, their bank
statements and their identity documents. An assistant that confidently invents the
contents of those is worse than no assistant — the whole product promise is "ask your
documents", and an answer that did not come from a document is a lie about the one thing
this app exists to do.

Everything below is in service of that: the floor, the source-numbered prompt, the
instruction to cite, and returning the sources so the user can check the answer against
the actual page.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from osdc.domain.models import FileRecord
from osdc.domain.ports import LlmClient, TextEmbedder, VectorStore
from osdc.storage import vectors
from osdc.storage.repositories import ChunkRepository, FileRepository

logger = logging.getLogger(__name__)

#: Chunks below this cosine similarity are not evidence. Tuned for bge-small, where
#: unrelated text still scores ~0.3-0.5 — see the note in semester_aware.py.
DEFAULT_RELEVANCE_FLOOR = 0.45

DEFAULT_TOP_K = 6

SYSTEM_PROMPT = """You are a careful assistant answering questions about a user's \
personal document library.

Rules you must follow:
1. Answer ONLY from the numbered sources provided. They are the sole source of truth.
2. Cite the sources you used inline, like [1] or [2]. Every factual claim needs a citation.
3. If the sources do not contain the answer, say exactly: "The documents don't cover that."
   Do not guess, and do not use knowledge from outside the sources.
4. Be concise and direct. Do not preamble.
"""


@dataclass(frozen=True)
class Source:
    index: int
    file: FileRecord
    page: int
    score: float
    excerpt: str


@dataclass(frozen=True)
class Answer:
    text: str
    sources: list[Source] = field(default_factory=list)
    grounded: bool = True


NO_CONTEXT = (
    "I couldn't find anything in your library about that. "
    "Either the documents haven't been indexed yet, or they don't cover it."
)

NO_LLM = (
    "The local model isn't available. Start Ollama and make sure the model is pulled "
    "(`ollama pull qwen2.5:7b`), then try again."
)


class RagService:
    def __init__(
        self,
        files: FileRepository,
        chunks: ChunkRepository,
        embedder: TextEmbedder,
        vector_store: VectorStore,
        llm: LlmClient,
        relevance_floor: float = DEFAULT_RELEVANCE_FLOOR,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        self._files = files
        self._chunks = chunks
        self._embedder = embedder
        self._vectors = vector_store
        self._llm = llm
        self._floor = relevance_floor
        self._top_k = top_k

    async def ask(self, question: str) -> Answer:
        if not question.strip():
            return Answer(text="Ask me something about your documents.", grounded=False)
        return await asyncio.to_thread(self._ask_sync, question)

    def _ask_sync(self, question: str) -> Answer:
        sources = self._retrieve(question)

        # The floor did its job — nothing relevant. Do NOT call the model.
        if not sources:
            logger.info(
                "No chunk cleared the relevance floor (%.2f) for: %s", self._floor, question
            )
            return Answer(text=NO_CONTEXT, sources=[], grounded=False)

        if not self._llm.available():
            return Answer(text=NO_LLM, sources=sources, grounded=False)

        prompt = self._build_prompt(question, sources)
        logger.info("Asking %s with %d source(s)", self._llm.model, len(sources))
        answer = self._llm.generate(prompt, system=SYSTEM_PROMPT)

        return Answer(text=answer or NO_CONTEXT, sources=sources, grounded=bool(answer))

    def _retrieve(self, question: str) -> list[Source]:
        # embed_query, not embed — bge is asymmetric and using the wrong side quietly
        # costs recall (see sentence_embedder.py).
        query_vector = self._embedder.embed_query([question])[0]
        hits = self._vectors.query(vectors.DOC_CHUNKS, query_vector, k=self._top_k)

        sources: list[Source] = []
        for hit in hits:
            if hit.score < self._floor:
                continue

            chunk = self._chunks.get(hit.id)
            if chunk is None:
                continue
            record = self._files.get(chunk.file_id)
            if record is None:
                continue

            sources.append(
                Source(
                    index=len(sources) + 1,
                    file=record,
                    page=chunk.page or 1,
                    score=hit.score,
                    excerpt=chunk.content,
                )
            )
        return sources

    @staticmethod
    def _build_prompt(question: str, sources: list[Source]) -> str:
        blocks = [f"[{s.index}] {s.file.filename} (page {s.page}):\n{s.excerpt}" for s in sources]
        joined = "\n\n".join(blocks)
        return f"Sources:\n\n{joined}\n\n---\n\nQuestion: {question}\n\nAnswer (with citations):"
