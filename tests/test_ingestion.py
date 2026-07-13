"""Ingestion front door: ignore rules, stability, hashing, dedupe.

roadmap.md calls this "the component most likely to embarrass you later". These are the
embarrassments, written down.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from osdc.pipeline.ingest.hashing import hash_bytes, hash_file
from osdc.pipeline.ingest.ignore import should_ignore
from osdc.pipeline.ingest.stability import wait_until_stable

# --- ignore rules -----------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "report.pdf.crdownload",  # Chrome, mid-download
        "video.part",  # Firefox, mid-download
        "~$budget.xlsx",  # Excel lock file
        ".DS_Store",
        "Thumbs.db",
        "notes.txt.tmp",
        ".hidden",
    ],
)
def test_junk_never_enters_the_pipeline(watched: Path, name: str) -> None:
    assert should_ignore(watched / name) is True


@pytest.mark.parametrize("name", ["notes.txt", "lecture.pdf", "slides.pptx", "photo.jpg"])
def test_real_files_are_not_ignored(watched: Path, name: str) -> None:
    assert should_ignore(watched / name) is False


def test_the_library_is_never_re_ingested(tmp_path: Path) -> None:
    """Without this the organizer's output lands back in a watched folder and the
    watcher feeds itself forever."""
    watched = tmp_path / "Downloads"
    library = watched / "AI Library"  # library nested inside a watched folder
    library.mkdir(parents=True)

    filed = library / "Academics" / "Operating Systems" / "notes.txt"
    filed.parent.mkdir(parents=True)
    filed.write_text("paging", encoding="utf-8")

    assert should_ignore(filed, library_root=library) is True
    assert should_ignore(watched / "fresh.txt", library_root=library) is False


# --- hashing ----------------------------------------------------------------


def test_identical_bytes_hash_identically(watched: Path) -> None:
    a, b = watched / "a.txt", watched / "b.txt"
    a.write_bytes(b"same content")
    b.write_bytes(b"same content")
    assert hash_file(a) == hash_file(b) == hash_bytes(b"same content")


def test_different_bytes_hash_differently(watched: Path) -> None:
    a, b = watched / "a.txt", watched / "b.txt"
    a.write_bytes(b"content one")
    b.write_bytes(b"content two")
    assert hash_file(a) != hash_file(b)


def test_hashing_streams_a_file_larger_than_one_chunk(watched: Path) -> None:
    big = watched / "big.bin"
    payload = b"x" * (3 * (1 << 20) + 17)  # > 3 MiB, deliberately not chunk-aligned
    big.write_bytes(payload)
    assert hash_file(big) == hash_bytes(payload)


# --- stability --------------------------------------------------------------


async def test_a_settled_file_is_stable(watched: Path) -> None:
    path = watched / "done.txt"
    path.write_text("finished writing", encoding="utf-8")
    assert await wait_until_stable(path, poll_seconds=0.01, required_checks=2) is True


async def test_an_empty_file_is_never_stable(watched: Path) -> None:
    """A zero-byte file is almost always a file that has been created but not yet written."""
    path = watched / "empty.txt"
    path.touch()
    assert (
        await wait_until_stable(path, poll_seconds=0.01, required_checks=2, timeout_seconds=0.1)
        is False
    )


async def test_a_missing_file_is_not_stable(watched: Path) -> None:
    assert await wait_until_stable(watched / "ghost.txt", poll_seconds=0.01) is False


async def test_a_file_still_growing_is_not_stable_until_it_stops(watched: Path) -> None:
    """The actual bug this guards: hashing a half-written download."""
    path = watched / "downloading.bin"
    path.write_bytes(b"start")

    async def keep_writing() -> None:
        for _ in range(5):
            await asyncio.sleep(0.02)
            with path.open("ab") as fh:
                fh.write(b"more")

    writer = asyncio.create_task(keep_writing())
    stable = await wait_until_stable(
        path, poll_seconds=0.01, required_checks=2, timeout_seconds=0.08
    )
    assert stable is False, "declared stable while the file was still being written"

    await writer
    assert await wait_until_stable(path, poll_seconds=0.01, required_checks=2) is True
