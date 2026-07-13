"""Vector store — the five collection contracts from architecture.md §12.

Phase 0 deliberately does **not** put ChromaDB in the core dependency group: it
drags in onnxruntime and friends, and the walking skeleton is supposed to install in
seconds (roadmap.md §2.3). So the collections and the ``VectorStore`` port are real
from day one, and the *implementation* is swappable:

- ``InMemoryVectorStore`` — pure stdlib, JSON-persisted. The Phase 0 default.
- ``ChromaVectorStore``  — used automatically once ``pip install -e ".[vector]"``
  has been run. Same contract, no caller changes.

That is the walking-skeleton bet applied to storage.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from osdc.domain.models import Hit, Vector
from osdc.domain.ports import VectorStore

# --- Collection contracts (architecture.md §12) -----------------------------
DOC_CHUNKS = "doc_chunks"
IMAGE_VISUAL = "image_visual"
IMAGE_OCR_TEXT = "image_ocr_text"
SUBJECT_KB = "subject_kb"
CATEGORY_PROTOTYPES = "category_prototypes"

COLLECTIONS: tuple[str, ...] = (
    DOC_CHUNKS,
    IMAGE_VISUAL,
    IMAGE_OCR_TEXT,
    SUBJECT_KB,
    CATEGORY_PROTOTYPES,
)


def cosine(a: Vector, b: Vector) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class InMemoryVectorStore:
    """Phase 0 default. Persists to a JSON file so restarts keep the index."""

    def __init__(self, persist_path: Path | None = None) -> None:
        self._persist_path = persist_path
        self._data: dict[str, dict[str, tuple[Vector, dict[str, Any]]]] = {
            name: {} for name in COLLECTIONS
        }
        self._load()

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for collection, items in raw.items():
            self._data.setdefault(collection, {})
            for item_id, payload in items.items():
                self._data[collection][item_id] = (payload["vector"], payload["metadata"])

    def _flush(self) -> None:
        if not self._persist_path:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        serialisable = {
            collection: {
                item_id: {"vector": vec, "metadata": meta} for item_id, (vec, meta) in items.items()
            }
            for collection, items in self._data.items()
        }
        tmp = self._persist_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(serialisable), encoding="utf-8")
        tmp.replace(self._persist_path)

    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        vectors: Sequence[Vector],
        metadata: Sequence[dict[str, Any]],
    ) -> None:
        bucket = self._data.setdefault(collection, {})
        for item_id, vector, meta in zip(ids, vectors, metadata, strict=True):
            bucket[item_id] = (list(vector), dict(meta))
        self._flush()

    def query(
        self,
        collection: str,
        vector: Vector,
        k: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[Hit]:
        bucket = self._data.get(collection, {})
        hits: list[Hit] = []
        for item_id, (candidate, meta) in bucket.items():
            if where and any(meta.get(key) != value for key, value in where.items()):
                continue
            hits.append(Hit(id=item_id, score=cosine(vector, candidate), metadata=meta))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def delete(self, collection: str, ids: Sequence[str]) -> None:
        bucket = self._data.get(collection, {})
        for item_id in ids:
            bucket.pop(item_id, None)
        self._flush()

    def count(self, collection: str) -> int:
        return len(self._data.get(collection, {}))


class ChromaVectorStore:
    """Same contract, backed by ChromaDB. Requires the ``[vector]`` extra."""

    def __init__(self, persist_dir: Path) -> None:
        import chromadb  # imported lazily: not a core dependency

        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        for name in COLLECTIONS:
            self._client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})

    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        vectors: Sequence[Vector],
        metadata: Sequence[dict[str, Any]],
    ) -> None:
        self._client.get_or_create_collection(collection).upsert(
            ids=list(ids),
            embeddings=[list(v) for v in vectors],
            metadatas=[dict(m) for m in metadata],
        )

    def query(
        self,
        collection: str,
        vector: Vector,
        k: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[Hit]:
        result = self._client.get_or_create_collection(collection).query(
            query_embeddings=[list(vector)], n_results=k, where=where or None
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        return [
            # Chroma returns cosine *distance*; the port's contract is similarity.
            Hit(id=i, score=1.0 - float(d), metadata=dict(m or {}))
            for i, d, m in zip(ids, distances, metadatas, strict=False)
        ]

    def delete(self, collection: str, ids: Sequence[str]) -> None:
        self._client.get_or_create_collection(collection).delete(ids=list(ids))

    def count(self, collection: str) -> int:
        return int(self._client.get_or_create_collection(collection).count())


def build_vector_store(persist_dir: Path) -> VectorStore:
    """Use Chroma if it is installed, otherwise the in-process store.

    The return type is the *port*, not either concrete class — so nothing downstream
    can accidentally depend on which one it got.
    """
    try:
        import chromadb  # noqa: F401
    except ImportError:
        return InMemoryVectorStore(persist_path=persist_dir / "vectors.json")
    return ChromaVectorStore(persist_dir=persist_dir / "chroma")
