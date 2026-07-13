"""The seams.

Everything swappable is a Protocol here. Phase 0 ships trivial implementations of
every one of them (``PlainTextExtractor``, ``NullOcr``, ``HashEmbedder``,
``KeywordClassifier``, ``InMemoryVectorStore``, ``AsyncioTaskQueue``); later phases
swap in the real thing and *nothing above these interfaces changes*.

That is the whole bet of the walking skeleton (roadmap.md §1). This file is the
most important one in the repo.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from osdc.domain.enums import FileAction, FileType
from osdc.domain.models import (
    Classification,
    EmbeddedDocument,
    ExtractedText,
    Hit,
    MoveRecord,
    OcrResult,
    Vector,
)


@runtime_checkable
class TextExtractor(Protocol):
    """Pulls text out of one family of file formats.

    Phase 0: plain text only. Phase 1: PyMuPDF, pdfplumber, python-docx, python-pptx.
    """

    name: str

    def supports(self, path: Path, file_type: FileType) -> bool: ...

    def extract(self, path: Path) -> ExtractedText: ...


@runtime_checkable
class OcrEngineProtocol(Protocol):
    """Phase 0: NullOcr. Phase 2: PaddleOCR, with Tesseract as fallback."""

    name: str

    def available(self) -> bool: ...

    def ocr(self, path: Path) -> OcrResult: ...


@runtime_checkable
class TextEmbedder(Protocol):
    """Phase 0: HashEmbedder (deterministic, no model). Phase 3: Sentence-Transformers."""

    dim: int

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """Embed passages — the things being stored and searched over."""
        ...

    def embed_query(self, texts: Sequence[str]) -> list[Vector]:
        """Embed questions — the things doing the searching.

        Separate from ``embed`` because retrieval encoders are asymmetric: ``bge``
        wants a "Represent this sentence for searching relevant passages:" prefix on
        the *query* side only, and using the wrong one measurably degrades recall.
        Symmetric embedders just alias this to ``embed``.
        """
        ...


@runtime_checkable
class ImageEmbedder(Protocol):
    """Phase 6: CLIP/SigLIP. Both methods land in the same vector space."""

    dim: int

    def embed_images(self, paths: Sequence[Path]) -> list[Vector]: ...

    def embed_text(self, texts: Sequence[str]) -> list[Vector]: ...


@runtime_checkable
class VectorStore(Protocol):
    """Phase 0: in-process. Later: ChromaDB, then Qdrant at scale.

    Collection names are the contracts in architecture.md §12.
    """

    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        vectors: Sequence[Vector],
        metadata: Sequence[dict[str, Any]],
    ) -> None: ...

    def query(
        self,
        collection: str,
        vector: Vector,
        k: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[Hit]: ...

    def delete(self, collection: str, ids: Sequence[str]) -> None: ...

    def count(self, collection: str) -> int: ...


@runtime_checkable
class Classifier(Protocol):
    """Phase 0: keyword rules. Phase 3: semester-aware academic + general fallback.

    Returns the label and a raw score. It does *not* decide auto-vs-review — that is
    the ConfidenceGate's job, so the threshold policy lives in exactly one place.
    """

    def classify(self, doc: EmbeddedDocument) -> Classification: ...


@runtime_checkable
class LlmClient(Protocol):
    """The local LLM. Ollama (qwen2.5 / llama / mistral / gemma).

    Deliberately tiny: the RAG service owns prompt construction and grounding policy, so a
    different runtime (llama.cpp, vLLM) is a drop-in.
    """

    model: str

    def available(self) -> bool: ...

    def generate(self, prompt: str, system: str | None = None) -> str: ...

    def generate_json(
        self, prompt: str, schema: dict[str, Any], system: str | None = None
    ) -> dict[str, Any]:
        """Constrained decoding against a JSON schema.

        The bulk-organize planner needs a machine-readable plan, and "please reply with
        JSON" in the prompt is not a contract — a 7B model will eventually wrap it in
        prose or a markdown fence. Ollama can constrain the sampler to the schema, which
        turns a parsing gamble into a guarantee.
        """
        ...


@runtime_checkable
class MoveLog(Protocol):
    """Write-ahead log for every file operation (roadmap.md §2.6).

    The organizer lives in ``pipeline/`` and must not reach into ``storage/``, so it
    talks to the log through this port. ``begin`` is called *before* the file is
    touched; that ordering is the entire point.
    """

    def begin(self, file_id: str, source: Path, dest: Path, action: FileAction) -> str: ...

    def complete(self, move_id: str) -> None: ...

    def fail(self, move_id: str, error: str) -> None: ...

    def mark_reverted(self, move_id: str) -> None: ...

    def get(self, move_id: str) -> MoveRecord | None: ...


@runtime_checkable
class TaskQueue(Protocol):
    """Phase 0: asyncio in-process. Upgrade path: Celery/RQ + Redis.

    Only ever carries a job id. The durable state lives in the ``jobs`` table
    (roadmap.md §2.5) — the queue is a dispatcher, not a source of truth.
    """

    async def enqueue(self, job_id: str) -> None: ...
