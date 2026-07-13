"""Image indexing and natural-language image search.

You point it at a folder, it looks at every picture in it, and then you can find them by
describing what is in them.

Images are indexed **in place** — they are never moved or copied into the library. Photos
are not documents: people already have them organised the way they want (by trip, by year),
and an app that rearranged someone's photo library on their behalf would be a menace. This
feature is search, not filing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from osdc.pipeline.embed.clip_embedder import ClipEmbedder, is_image
from osdc.pipeline.ingest.hashing import hash_file
from osdc.pipeline.ingest.ignore import should_ignore
from osdc.storage import vectors
from osdc.storage.vectors import VectorStore

logger = logging.getLogger(__name__)

#: Encoded together. Larger batches are faster but hold more decoded bitmaps in RAM at once,
#: and a folder of 24-megapixel photos will happily eat several GB.
BATCH_SIZE = 16

#: CLIP similarities are compressed into a narrow band — a *good* match is ~0.25-0.35 and
#: an unrelated one ~0.15. They look nothing like sentence-embedding scores, so this floor
#: is far lower than the document one and that is expected, not a bug.
DEFAULT_FLOOR = 0.20


@dataclass(frozen=True)
class ImageHit:
    path: Path
    score: float


@dataclass(frozen=True)
class IndexProgress:
    done: int
    total: int
    current: str


ProgressFn = Callable[[IndexProgress], None]


class ImageService:
    def __init__(
        self,
        embedder: ClipEmbedder,
        vector_store: VectorStore,
        floor: float = DEFAULT_FLOOR,
    ) -> None:
        self._clip = embedder
        self._vectors = vector_store
        self._floor = floor

    async def index_folder(self, folder: Path, on_progress: ProgressFn | None = None) -> int:
        if not folder.is_dir():
            raise ValueError(f"Not a folder: {folder}")
        return await asyncio.to_thread(self._index_sync, folder, on_progress)

    def _index_sync(self, folder: Path, on_progress: ProgressFn | None) -> int:
        paths = [
            p
            for p in sorted(folder.rglob("*"))
            if p.is_file() and is_image(p) and not should_ignore(p)
        ]
        if not paths:
            return 0

        logger.info("Indexing %d image(s) from %s", len(paths), folder)
        indexed = 0

        for start in range(0, len(paths), BATCH_SIZE):
            batch = paths[start : start + BATCH_SIZE]

            usable: list[Path] = []
            ids: list[str] = []
            for path in batch:
                try:
                    # Hash-keyed, so re-indexing a folder is idempotent and moving a photo
                    # does not create a duplicate entry.
                    ids.append(hash_file(path))
                    usable.append(path)
                except OSError as exc:
                    logger.warning("Skipping unreadable image %s: %s", path.name, exc)

            if not usable:
                continue

            try:
                embeddings = self._clip.embed_images(usable)
            except Exception:
                # One corrupt JPEG must not abort a 5,000-photo index.
                logger.exception("Failed to encode a batch; skipping it")
                continue

            self._vectors.upsert(
                collection=vectors.IMAGE_VISUAL,
                ids=ids,
                vectors=embeddings,
                metadata=[
                    {"path": str(p), "filename": p.name, "folder": str(folder)} for p in usable
                ],
            )
            indexed += len(usable)

            if on_progress:
                on_progress(IndexProgress(done=indexed, total=len(paths), current=usable[-1].name))

        logger.info("Indexed %d image(s)", indexed)
        return indexed

    async def search(self, query: str, k: int = 24) -> list[ImageHit]:
        if not query.strip():
            return []
        return await asyncio.to_thread(self._search_sync, query, k)

    def _search_sync(self, query: str, k: int) -> list[ImageHit]:
        query_vector = self._clip.embed_text([query])[0]
        hits = self._vectors.query(vectors.IMAGE_VISUAL, query_vector, k=k)

        results: list[ImageHit] = []
        for hit in hits:
            if hit.score < self._floor:
                continue
            raw = hit.metadata.get("path")
            if not raw:
                continue
            path = Path(str(raw))
            if not path.exists():
                continue  # the user deleted or moved it since we indexed
            results.append(ImageHit(path=path, score=hit.score))
        return results

    def count(self) -> int:
        return self._vectors.count(vectors.IMAGE_VISUAL)
