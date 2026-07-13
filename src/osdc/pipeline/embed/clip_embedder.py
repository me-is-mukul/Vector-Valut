"""CLIP — the model that lets you find a photo by describing it.

The trick that makes "man lifts a baby" work is that CLIP puts **images and text into the
same vector space**. A photograph of a man lifting a baby and the sentence "man lifts a
baby" land close together, because CLIP was trained on hundreds of millions of
image/caption pairs. So image search is just: embed the sentence, cosine against the image
vectors.

No OCR, no tagging, no filenames. It genuinely looks at the picture.

`clip-ViT-B-32` (~600 MB) is the workhorse: fast on CPU, good enough that "sunset on a
beach" and "screenshot of an error message" both work. It is not perfect — it is weak on
counting, on text inside images, and on fine-grained distinctions ("golden retriever" vs
"labrador"). Phase 6 of the roadmap adds an OCR-text index alongside it to cover the
screenshot case properly.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from osdc.domain.models import Vector

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

DEFAULT_CLIP_MODEL = "clip-ViT-B-32"

IMAGE_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}
)


class ClipEmbedder:
    """Loads lazily and exactly once — the model is 600 MB and startup should not pay for
    it if the user never opens image search."""

    def __init__(self, model_name: str = DEFAULT_CLIP_MODEL) -> None:
        self._model_name = model_name
        self._model: SentenceTransformer | None = None
        self._lock = threading.Lock()
        self.dim = 512

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            with self._lock:
                if self._model is None:  # two indexing workers can race here
                    from sentence_transformers import SentenceTransformer

                    logger.info("Loading CLIP (%s)…", self._model_name)
                    self._model = SentenceTransformer(self._model_name)
                    logger.info("CLIP ready")
        return self._model

    def embed_images(self, paths: Sequence[Path]) -> list[Vector]:
        from PIL import Image

        images = []
        for path in paths:
            with Image.open(path) as handle:
                # Convert inside the context manager: PNGs with alpha and 16-bit TIFFs both
                # break the encoder otherwise, and a CMYK JPEG produces garbage vectors.
                images.append(handle.convert("RGB"))

        result: Any = self.model.encode(images, normalize_embeddings=True, show_progress_bar=False)
        return [[float(x) for x in row] for row in result]

    def embed_text(self, texts: Sequence[str]) -> list[Vector]:
        """Same vector space as the images — that is the whole point."""
        result: Any = self.model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        return [[float(x) for x in row] for row in result]


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES
