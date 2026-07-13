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
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from osdc.pipeline.embed.clip_embedder import ClipEmbedder, is_image
from osdc.pipeline.ingest.hashing import hash_file
from osdc.pipeline.ingest.ignore import should_ignore
from osdc.storage import vectors
from osdc.storage.vectors import VectorStore

# Phone cameras shoot HEIC; without this, an iPhone photo folder indexes as zero images
# even though every file in it is a picture.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - the wheel ships it; belt and braces
    pass

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


@dataclass(frozen=True)
class IndexResult:
    """Honest accounting, so the UI never says "no images" about a folder full of them.

    ``found`` is how many image files were discovered; ``indexed`` how many actually made
    it into the vector store. The difference is ``failed`` — unreadable or corrupt files,
    named so the user can be told *which* ones.
    """

    found: int = 0
    indexed: int = 0
    failed: list[str] = field(default_factory=list)


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

    async def index_folder(
        self, folder: Path, on_progress: ProgressFn | None = None
    ) -> IndexResult:
        if not folder.is_dir():
            raise ValueError(f"Not a folder: {folder}")
        return await asyncio.to_thread(self._index_sync, folder, on_progress)

    def _index_sync(self, folder: Path, on_progress: ProgressFn | None) -> IndexResult:
        paths = self._discover(folder)
        if not paths:
            return IndexResult()

        logger.info("Indexing %d image(s) from %s", len(paths), folder)
        indexed = 0
        failed: list[str] = []

        # Load the model BEFORE the loop. Inside it, a load failure (no weights yet, no
        # disk space) would be swallowed by the per-batch guard and misreported to the
        # user as "that folder has no images".
        model = self._clip.model

        for start in range(0, len(paths), BATCH_SIZE):
            batch = paths[start : start + BATCH_SIZE]

            loaded: list[tuple[Path, str, Image.Image]] = []
            for path in batch:
                try:
                    # Decode and hash together, so the three lists below can never drift
                    # out of step — a half-appended file would attach this photo's vector
                    # to another photo's path.
                    with Image.open(path) as handle:
                        image = handle.convert("RGB")
                    # Hash-keyed, so re-indexing a folder is idempotent and moving a photo
                    # does not create a duplicate entry.
                    loaded.append((path, hash_file(path), image))
                except OSError as exc:
                    logger.warning("Skipping unreadable image %s: %s", path.name, exc)
                    failed.append(path.name)

            if not loaded:
                continue

            embeddings, kept = self._encode(model, loaded)
            if kept:
                self._vectors.upsert(
                    collection=vectors.IMAGE_VISUAL,
                    ids=[item[1] for item in kept],
                    vectors=embeddings,
                    metadata=[
                        {"path": str(p), "filename": p.name, "folder": str(folder)}
                        for p, _, _ in kept
                    ],
                )
                indexed += len(kept)
            kept_paths = {item[0] for item in kept}
            failed.extend(p.name for p, _, _ in loaded if p not in kept_paths)

            if on_progress:
                done = min(start + BATCH_SIZE, len(paths))
                current = kept[-1][0].name if kept else batch[-1].name
                on_progress(IndexProgress(done=done, total=len(paths), current=current))

        logger.info("Indexed %d of %d image(s)", indexed, len(paths))
        return IndexResult(found=len(paths), indexed=indexed, failed=failed)

    @staticmethod
    def _discover(folder: Path) -> list[Path]:
        """Every indexable image under ``folder``.

        Each candidate is guarded individually: one locked or vanishing file (cloud-sync
        placeholders love doing this) must not abort discovery of the other five thousand.
        """
        paths: list[Path] = []
        for p in sorted(folder.rglob("*")):
            try:
                if p.is_file() and is_image(p) and not should_ignore(p):
                    paths.append(p)
            except OSError as exc:
                logger.warning("Skipping unreadable path %s: %s", p, exc)
        return paths

    @staticmethod
    def _encode(
        model: object, loaded: list[tuple[Path, str, Image.Image]]
    ) -> tuple[list[list[float]], list[tuple[Path, str, Image.Image]]]:
        """Encode a batch; on failure retry one-by-one so a single corrupt file costs
        itself, not the fifteen good photos that happened to share its batch."""
        images = [item[2] for item in loaded]
        try:
            rows = model.encode(images, normalize_embeddings=True, show_progress_bar=False)  # type: ignore[attr-defined]
            return [[float(x) for x in row] for row in rows], loaded
        except Exception:
            logger.exception("Batch encode failed; retrying images individually")

        embeddings: list[list[float]] = []
        kept: list[tuple[Path, str, Image.Image]] = []
        for item in loaded:
            try:
                rows = model.encode([item[2]], normalize_embeddings=True, show_progress_bar=False)  # type: ignore[attr-defined]
                embeddings.append([float(x) for x in rows[0]])
                kept.append(item)
            except Exception:
                logger.exception("Could not encode %s; skipping it", item[0].name)
        return embeddings, kept

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
