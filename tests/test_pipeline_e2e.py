"""The Phase 0 exit criterion, as a test.

roadmap.md §4:

    Drop os_notes.txt containing the word "paging" into a watched folder. Within two
    seconds it appears in AI Library/Academics/Operating Systems/, and a row shows up
    with its hash, category, confidence and decision. Kill the app mid-processing and
    restart it — the job resumes.

Both halves are here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from osdc.config.settings import Settings
from osdc.container import Container
from osdc.domain.enums import CategoryKind, Decision, FileAction, JobStatus
from osdc.domain.ports import TextEmbedder
from osdc.storage import vectors


def _settings(library_root: Path, watched: Path) -> Settings:
    return Settings(
        library_root=library_root,
        watched_folders=[watched],
        file_action=FileAction.COPY,
        debounce_seconds=0.05,
        stability_poll_seconds=0.01,
        stability_checks=1,
        scan_on_startup=False,
        worker_count=1,
    )


@pytest.fixture
def container(
    data_dir: Path,
    library_root: Path,
    watched: Path,
    tmp_path: Path,
    embedder: TextEmbedder,
) -> Container:
    return Container(
        settings=_settings(library_root, watched),
        db_url=f"sqlite:///{tmp_path / 'e2e.sqlite3'}",
        embedder=embedder,
    )


#: Long enough and specific enough to clear the calibrated academic floor (0.62).
_OS_TEXT = (
    "Paging and virtual memory. A page fault occurs when the requested page is not "
    "resident. The TLB caches recent address translations, and thrashing follows when the "
    "working set exceeds the frames available."
)


async def _drain(container: Container, timeout: float = 5.0) -> None:
    """Wait until no job is queued or running."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        stats = await container.library.stats()
        if stats.queued == 0 and stats.running == 0 and container.queue.depth == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("pipeline did not drain in time")


# --- the happy path ---------------------------------------------------------


async def test_a_dropped_file_is_classified_and_filed(
    container: Container, watched: Path, library_root: Path
) -> None:
    """planning.md §4's signature use case, end to end.

    Note the destination now carries the semester — ``Academics/Semester 5/Operating
    Systems/``. Phase 0 filed to ``Academics/Operating Systems/`` because the keyword
    classifier had no idea which semester a subject belonged to. The organizer always
    honoured ``Classification.semester``; Phase 3's knowledge base simply started
    populating it, and the folder layout deepened with no change to the organizer.
    """
    await container.start()
    try:
        source = watched / "os_notes.txt"
        source.write_text(
            "Lecture 7: paging and virtual memory. A page fault occurs when the requested "
            "page is not resident in physical memory. The TLB caches recent address "
            "translations. Thrashing happens when the working set exceeds available frames.",
            encoding="utf-8",
        )

        job_id = await container.ingestion.handle_path(source)
        assert job_id is not None
        await _drain(container)

        expected = library_root / "Academics" / "Semester 5" / "Operating Systems" / "os_notes.txt"
        assert expected.exists(), f"not filed; library contains {list(library_root.rglob('*'))}"
        assert source.exists(), "COPY must leave the original alone"

        (record,) = await container.library.list_files()
        assert record.filename == "os_notes.txt"
        assert record.label == "Operating Systems"
        assert record.kind is CategoryKind.ACADEMIC
        assert record.semester == 5
        assert record.decision is Decision.AUTO
        assert record.confidence_score is not None and record.confidence_score >= 0.85
        assert record.organized_path == expected
        assert len(record.content_hash) == 32
        assert record.processed_at is not None

        job = container.jobs.get(job_id)
        assert job is not None and job.status is JobStatus.DONE

        assert container.vector_store.count(vectors.DOC_CHUNKS) >= 1
        assert container.chunks.count() >= 1
    finally:
        await container.stop()


async def test_semantic_search_finds_a_document_by_meaning_not_words(
    container: Container, watched: Path
) -> None:
    """The whole point of Phase 3.

    The query "virtual memory management" does not appear anywhere in the document. A
    keyword search — or Phase 0's hashing embedder — returns nothing. A real embedder
    finds it.
    """
    await container.start()
    try:
        source = watched / "os_notes.txt"
        source.write_text(
            "Lecture 7: paging and page faults. The translation lookaside buffer caches "
            "recent translations. Thrashing occurs when the working set exceeds the frames "
            "available, and the page replacement algorithm evicts the least recently used.",
            encoding="utf-8",
        )
        await container.ingestion.handle_path(source)
        await _drain(container)

        hits = await container.search.search("virtual memory management")
        assert [h.file.filename for h in hits] == ["os_notes.txt"]
        assert hits[0].page == 1, "a citation needs a page"
        assert hits[0].excerpt
    finally:
        await container.stop()


async def test_a_document_matching_nothing_goes_to_review_and_is_not_moved(
    container: Container, watched: Path, library_root: Path
) -> None:
    """The similarity floor doing its job.

    An article about birds belongs to no subject and no category. It must land in Review,
    NOT be filed under whichever subject happens to score highest — which is what would
    happen without an absolute floor (see semester_aware.py). Before calibration this
    exact text scored 0.549 against Data Structures.
    """
    await container.start()
    try:
        source = watched / "birds.txt"
        source.write_text(
            "The migratory route of the Arctic tern spans pole to pole, the longest of any "
            "bird. They breed in the northern summer and follow the sun southward, seeing "
            "more daylight than any other creature on earth.",
            encoding="utf-8",
        )

        await container.ingestion.handle_path(source)
        await _drain(container)

        (record,) = await container.library.list_files()
        assert record.decision is Decision.REVIEW
        assert record.organized_path is None, "an unmatched file must not be filed"
        assert list(library_root.rglob("*.txt")) == [], "nothing should have been written"

        queue = await container.library.review_queue()
        assert [r.filename for r in queue] == ["birds.txt"]
    finally:
        await container.stop()


async def test_a_general_document_is_filed_under_general(
    container: Container, watched: Path, library_root: Path
) -> None:
    """An invoice must not be filed as coursework — the failure the floors prevent."""
    await container.start()
    try:
        source = watched / "invoice.txt"
        source.write_text(
            "TAX INVOICE. Invoice number 4471. Amount due: Rs 12,400. GST at 18% included. "
            "Payment due within 30 days. Bank account and IFSC code given below.",
            encoding="utf-8",
        )
        await container.ingestion.handle_path(source)
        await _drain(container)

        (record,) = await container.library.list_files()
        assert record.label == "Finance"
        assert record.kind is CategoryKind.GENERAL
        assert record.semester is None
        assert (library_root / "General" / "Finance" / "invoice.txt").exists()
        assert not (library_root / "Academics").exists(), "an invoice is not coursework"
    finally:
        await container.stop()


async def test_an_unreadable_file_lands_in_review_rather_than_failing(
    container: Container, watched: Path
) -> None:
    """No extractor and no OCR in Phase 0 → honest 'I can't read this', not a crash."""
    await container.start()
    try:
        source = watched / "scan.pdf"
        source.write_bytes(b"%PDF-1.4\n" + b"\x00" * 500)

        await container.ingestion.handle_path(source)
        await _drain(container)

        (record,) = await container.library.list_files()
        assert record.decision is Decision.REVIEW
        assert record.is_image_based is True
    finally:
        await container.stop()


# --- dedupe -----------------------------------------------------------------


async def test_the_same_bytes_are_never_indexed_twice(container: Container, watched: Path) -> None:
    await container.start()
    try:
        first = watched / "notes.txt"
        first.write_text(_OS_TEXT, encoding="utf-8")
        assert await container.ingestion.handle_path(first) is not None
        await _drain(container)

        # Same content, different name — the classic "downloaded it twice" case.
        second = watched / "notes (copy).txt"
        second.write_text(_OS_TEXT, encoding="utf-8")
        assert await container.ingestion.handle_path(second) is None

        assert len(await container.library.list_files()) == 1
    finally:
        await container.stop()


async def test_a_temp_file_is_ignored(container: Container, watched: Path) -> None:
    await container.start()
    try:
        partial = watched / "huge.pdf.crdownload"
        partial.write_text("half a download", encoding="utf-8")
        assert await container.ingestion.handle_path(partial) is None
        assert await container.library.list_files() == []
    finally:
        await container.stop()


# --- crash recovery ---------------------------------------------------------


async def test_a_job_interrupted_by_a_crash_is_resumed_on_restart(
    data_dir: Path,
    library_root: Path,
    watched: Path,
    tmp_path: Path,
    embedder: TextEmbedder,
) -> None:
    """The second half of the exit criterion.

    Simulates: the process dies with a job mid-flight. On restart the file must still get
    filed. Without ``recover_orphans`` the job would sit at RUNNING forever and the user's
    file would be silently dropped — which for a file organizer is data loss.
    """
    db_url = f"sqlite:///{tmp_path / 'crash.sqlite3'}"
    settings = _settings(library_root, watched)

    source = watched / "os_notes.txt"
    source.write_text(
        "Paging and page fault handling. The page replacement algorithm evicts the least "
        "recently used frame when physical memory is exhausted, and thrashing follows if "
        "the working set does not fit.",
        encoding="utf-8",
    )

    # --- run 1: enqueue, then "crash" before the worker touches it -----------
    first = Container(settings=settings, db_url=db_url, embedder=embedder)
    job_id = await first.ingestion.handle_path(source)  # note: queue never started
    assert job_id is not None

    first.jobs.start(job_id)  # pretend a worker picked it up...
    assert first.jobs.get(job_id).status is JobStatus.RUNNING  # type: ignore[union-attr]
    first.db.dispose()  # ...and the process died right here.

    assert not (library_root / "Academics").exists(), "precondition: nothing filed yet"

    # --- run 2: restart ------------------------------------------------------
    second = Container(settings=settings, db_url=db_url, embedder=embedder)
    await second.start()
    try:
        await _drain(second)

        job = second.jobs.get(job_id)
        assert job is not None
        assert job.status is JobStatus.DONE, "the interrupted job was never picked back up"
        assert job.attempts == 2, "should record that this was a retry"

        filed = library_root / "Academics" / "Semester 5" / "Operating Systems" / "os_notes.txt"
        assert filed.exists(), "the file was lost across the crash"
    finally:
        await second.stop()


# --- undo -------------------------------------------------------------------


async def test_a_filed_document_can_be_unfiled(
    container: Container, watched: Path, library_root: Path
) -> None:
    await container.start()
    try:
        source = watched / "os_notes.txt"
        source.write_text(_OS_TEXT, encoding="utf-8")
        await container.ingestion.handle_path(source)
        await _drain(container)

        (record,) = await container.library.list_files()
        assert record.organized_path is not None
        filed = record.organized_path
        assert filed.exists()

        await container.feedback.undo_last_for_file(record.id)

        assert not filed.exists()
        assert source.exists()

        (after,) = await container.library.list_files()
        assert after.organized_path is None, "the DB must forget where it was filed"
    finally:
        await container.stop()
