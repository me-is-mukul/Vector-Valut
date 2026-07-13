"""Phase 3's embedder: Sentence-Transformers. This is where the app stops being dumb.

``bge-small-en-v1.5`` — 384 dimensions, ~130 MB, runs comfortably on CPU and takes the
GPU if there is one. Chosen as the default because it is the strongest retrieval model in
its size class, but the model name is a setting: roadmap.md §6.1 wants this benchmarked
against ``e5-small`` and ``MiniLM`` on a labeled corpus rather than picked on reputation.

**The query prefix is not optional.** ``bge`` is an asymmetric encoder: it was trained
with an instruction prefix on the query side and none on the passage side. Embedding a
question the same way you embed a passage measurably degrades recall, and it is a silent
failure — retrieval simply gets worse and nothing tells you. Hence ``embed_query``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from osdc.domain.models import Vector

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

#: The instruction bge was trained with. Applied to queries only, never to passages.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class SentenceTransformerEmbedder:
    """Loads lazily, on first use, and exactly once."""

    def __init__(self, model_name: str = DEFAULT_MODEL, device: str | None = None) -> None:
        self._model_name = model_name
        self._device = device
        self._model: SentenceTransformer | None = None
        self._lock = threading.Lock()
        # Known ahead of the model load so the vector store can be sized without paying
        # for a model download at import time.
        self.dim = 384 if "small" in model_name or "MiniLM" in model_name else 768

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            with self._lock:
                if self._model is None:  # re-check: two workers can race here
                    from sentence_transformers import SentenceTransformer

                    logger.info("Loading embedding model %s…", self._model_name)
                    model = SentenceTransformer(self._model_name, device=self._device)
                    self.dim = model.get_sentence_embedding_dimension() or self.dim
                    self._model = model
                    logger.info("Embedding model ready (dim=%d, device=%s)", self.dim, model.device)
        return self._model

    def _encode(self, texts: Sequence[str]) -> list[Vector]:
        # normalize_embeddings=True means cosine similarity is a plain dot product, and
        # every score lands in a comparable range.
        result: Any = self.model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        return [[float(x) for x in row] for row in result]

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """Passages: stored documents, chunks, subject descriptions. No prefix."""
        return self._encode(texts)

    def embed_query(self, texts: Sequence[str]) -> list[Vector]:
        """Questions and search terms. Prefixed, because bge is asymmetric."""
        if "bge" in self._model_name.lower():
            return self._encode([f"{BGE_QUERY_PREFIX}{t}" for t in texts])
        if "e5" in self._model_name.lower():
            return self._encode([f"query: {t}" for t in texts])
        return self._encode(texts)
