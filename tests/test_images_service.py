from __future__ import annotations

from pathlib import Path

from PIL import Image

from osdc.services.images import ImageService
from osdc.storage.vectors import IMAGE_VISUAL, InMemoryVectorStore


class _FakeModel:
    def encode(self, images, normalize_embeddings=True, show_progress_bar=False):
        return [[1.0, 0.0] for _ in images]


class _ExplodingModel:
    """Fails on batches, works one-by-one — but never for images wider than 100px."""

    def encode(self, images, normalize_embeddings=True, show_progress_bar=False):
        if len(images) > 1:
            raise RuntimeError("batch failed")
        if images[0].width > 100:
            raise RuntimeError("corrupt")
        return [[1.0, 0.0] for _ in images]


class _FakeEmbedder:
    def __init__(self, model=None) -> None:
        self.model = model or _FakeModel()


def test_index_folder_skips_unsupported_images(tmp_path: Path) -> None:
    folder = tmp_path / "photos"
    folder.mkdir()

    good = folder / "good.png"
    Image.new("RGB", (8, 8), color="red").save(good)

    bad = folder / "bad.heic"
    bad.write_text("not actually an image", encoding="utf-8")

    store = InMemoryVectorStore()
    service = ImageService(embedder=_FakeEmbedder(), vector_store=store)

    result = service._index_sync(folder, None)

    assert result.indexed == 1
    assert store.count(IMAGE_VISUAL) == 1


def test_unreadable_images_are_reported_not_hidden(tmp_path: Path) -> None:
    """A folder full of images the decoder cannot open must NOT report "no images"."""
    folder = tmp_path / "photos"
    folder.mkdir()

    # detect_type calls this an image (correctly), but PIL cannot decode the truncated body.
    fake_heic = folder / "IMG_0001.heic"
    fake_heic.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32)

    store = InMemoryVectorStore()
    service = ImageService(embedder=_FakeEmbedder(), vector_store=store)

    result = service._index_sync(folder, None)

    assert result.found == 1
    assert result.indexed == 0
    assert result.failed == ["IMG_0001.heic"]


def test_one_bad_encode_does_not_sink_the_batch(tmp_path: Path) -> None:
    folder = tmp_path / "photos"
    folder.mkdir()
    Image.new("RGB", (8, 8), color="red").save(folder / "small.png")
    Image.new("RGB", (200, 8), color="red").save(folder / "wide.png")

    store = InMemoryVectorStore()
    service = ImageService(embedder=_FakeEmbedder(_ExplodingModel()), vector_store=store)

    result = service._index_sync(folder, None)

    assert result.found == 2
    assert result.indexed == 1
    assert result.failed == ["wide.png"]
    assert store.count(IMAGE_VISUAL) == 1


def test_empty_folder_is_genuinely_empty(tmp_path: Path) -> None:
    folder = tmp_path / "nothing"
    folder.mkdir()
    (folder / "notes.txt").write_text("no pictures here", encoding="utf-8")

    service = ImageService(embedder=_FakeEmbedder(), vector_store=InMemoryVectorStore())
    result = service._index_sync(folder, None)

    assert result.found == 0
    assert result.indexed == 0
