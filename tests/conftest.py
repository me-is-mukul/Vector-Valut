from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from osdc.domain.ports import TextEmbedder


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway install per test — no test ever touches the real data directory."""
    root = tmp_path / "appdata"
    root.mkdir()
    monkeypatch.setenv("OSDC_DATA_DIR", str(root))
    yield root
    os.environ.pop("OSDC_DATA_DIR", None)


@pytest.fixture
def library_root(tmp_path: Path) -> Path:
    root = tmp_path / "AI Library"
    root.mkdir()
    return root


@pytest.fixture
def watched(tmp_path: Path) -> Path:
    folder = tmp_path / "Downloads"
    folder.mkdir()
    return folder


@pytest.fixture(scope="session")
def embedder() -> TextEmbedder:
    """The real bge-small model, loaded exactly once for the whole suite.

    Loading it costs a few seconds; without session scope the end-to-end tests would pay
    that per Container and the suite would crawl.

    In CI the [ml] extra (Torch, several GB) is deliberately not installed, so every test
    that depends on this fixture skips there and runs locally instead.
    """
    pytest.importorskip("sentence_transformers", reason="[ml] extra not installed")
    from osdc.pipeline.embed.sentence_embedder import SentenceTransformerEmbedder

    model = SentenceTransformerEmbedder()
    model.embed(["warm up"])  # force the lazy load here, not inside a timed test
    return model
