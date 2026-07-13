"""The safety invariants (roadmap.md §6.2).

These are the tests that earn the right to point this app at someone's Downloads folder.
Everything else in Phase 0 is a convenience; these are the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from osdc.domain.enums import CategoryKind, FileAction, FileType, MoveStatus
from osdc.domain.models import Classification, MoveRecord
from osdc.pipeline.organize.organizer import Organizer, sanitize_component, unique_destination
from osdc.storage.db import Database
from osdc.storage.repositories import FileRepository, MoveLogRepository


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    database.create_all()
    return database


@pytest.fixture
def moves(db: Database) -> MoveLogRepository:
    return MoveLogRepository(db)


@pytest.fixture
def file_id(db: Database, watched: Path) -> str:
    source = watched / "notes.txt"
    source.write_text("paging and deadlock", encoding="utf-8")
    return FileRepository(db).create(
        path=source, content_hash="abc123", file_type=FileType.TXT, size_bytes=19
    )


ACADEMIC = Classification(label="Operating Systems", kind=CategoryKind.ACADEMIC, score=1.0)
GENERAL = Classification(label="Finance", kind=CategoryKind.GENERAL, score=0.9)


def _organizer(library_root: Path, moves: MoveLogRepository, action: FileAction) -> Organizer:
    return Organizer(library_root=library_root, action=action, move_log=moves)


# --- destination layout -----------------------------------------------------


def test_academic_files_land_under_academics(library_root: Path, moves: MoveLogRepository) -> None:
    org = _organizer(library_root, moves, FileAction.COPY)
    assert org.destination_dir(ACADEMIC) == library_root / "Academics" / "Operating Systems"


def test_semester_adds_a_level_when_known(library_root: Path, moves: MoveLogRepository) -> None:
    """Phase 0 leaves semester None; Phase 3's KB fills it and the layout deepens for free."""
    org = _organizer(library_root, moves, FileAction.COPY)
    with_sem = ACADEMIC.model_copy(update={"semester": 5})
    assert org.destination_dir(with_sem) == (
        library_root / "Academics" / "Semester 5" / "Operating Systems"
    )


def test_general_files_land_under_general(library_root: Path, moves: MoveLogRepository) -> None:
    org = _organizer(library_root, moves, FileAction.COPY)
    assert org.destination_dir(GENERAL) == library_root / "General" / "Finance"


def test_labels_are_sanitised_for_the_filesystem() -> None:
    assert sanitize_component("Data Structures / Algorithms") == "Data Structures _ Algorithms"
    assert sanitize_component("") == "Unnamed"
    assert sanitize_component("trailing.") == "trailing"


# --- INVARIANT: never overwrite ---------------------------------------------


def test_collision_gets_a_suffix_and_never_overwrites(
    library_root: Path, moves: MoveLogRepository, watched: Path, file_id: str
) -> None:
    org = _organizer(library_root, moves, FileAction.COPY)
    source = watched / "notes.txt"

    first, _ = org.organize(file_id, source, ACADEMIC)
    second, _ = org.organize(file_id, source, ACADEMIC)
    third, _ = org.organize(file_id, source, ACADEMIC)

    assert first.name == "notes.txt"
    assert second.name == "notes (1).txt"
    assert third.name == "notes (2).txt"
    assert first.exists() and second.exists() and third.exists()


def test_unique_destination_leaves_a_free_path_alone(tmp_path: Path) -> None:
    target = tmp_path / "free.txt"
    assert unique_destination(target) == target


# --- INVARIANT: the log is written BEFORE the file is touched ---------------


def test_move_log_is_written_before_the_copy(
    library_root: Path, moves: MoveLogRepository, watched: Path, file_id: str, monkeypatch
) -> None:
    """Simulate dying mid-copy: the row must already exist, and be PENDING.

    This is the whole reason ``begin()`` is called before ``shutil.copy2``. If the order
    were reversed, this crash would leave a file in the library with no undo record.
    """
    org = _organizer(library_root, moves, FileAction.COPY)
    source = watched / "notes.txt"

    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("osdc.pipeline.organize.organizer.shutil.copy2", explode)

    with pytest.raises(OSError, match="disk full"):
        org.organize(file_id, source, ACADEMIC)

    record = moves.latest_for_file(file_id)
    assert record is not None, "crash left no audit trail — the file would be unrecoverable"
    assert record.status is MoveStatus.FAILED
    assert record.error is not None and "disk full" in record.error
    assert source.exists(), "the original must still be there"


def test_successful_copy_is_logged_complete(
    library_root: Path, moves: MoveLogRepository, watched: Path, file_id: str
) -> None:
    org = _organizer(library_root, moves, FileAction.COPY)
    _, move_id = org.organize(file_id, watched / "notes.txt", ACADEMIC)

    record = moves.get(move_id)
    assert record is not None
    assert record.status is MoveStatus.COMPLETE
    assert record.action is FileAction.COPY


# --- INVARIANT: copy is the default, original survives ----------------------


def test_copy_leaves_the_original_in_place(
    library_root: Path, moves: MoveLogRepository, watched: Path, file_id: str
) -> None:
    org = _organizer(library_root, moves, FileAction.COPY)
    source = watched / "notes.txt"
    dest, _ = org.organize(file_id, source, ACADEMIC)

    assert source.exists()
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_move_removes_the_original(
    library_root: Path, moves: MoveLogRepository, watched: Path, file_id: str
) -> None:
    org = _organizer(library_root, moves, FileAction.MOVE)
    source = watched / "notes.txt"
    dest, _ = org.organize(file_id, source, ACADEMIC)

    assert not source.exists()
    assert dest.exists()


# --- INVARIANT: every filing is reversible ----------------------------------


def test_undo_of_a_move_puts_the_file_back(
    library_root: Path, moves: MoveLogRepository, watched: Path, file_id: str
) -> None:
    org = _organizer(library_root, moves, FileAction.MOVE)
    source = watched / "notes.txt"
    original_text = source.read_text(encoding="utf-8")

    dest, move_id = org.organize(file_id, source, ACADEMIC)
    restored = org.undo(move_id)

    assert restored == source
    assert source.exists()
    assert source.read_text(encoding="utf-8") == original_text
    assert not dest.exists()

    record = moves.get(move_id)
    assert record is not None and record.status is MoveStatus.REVERTED


def test_undo_of_a_copy_removes_the_library_copy(
    library_root: Path, moves: MoveLogRepository, watched: Path, file_id: str
) -> None:
    org = _organizer(library_root, moves, FileAction.COPY)
    source = watched / "notes.txt"

    dest, move_id = org.organize(file_id, source, ACADEMIC)
    org.undo(move_id)

    assert source.exists(), "undo of a copy must never touch the original"
    assert not dest.exists()


def test_undo_of_a_copy_refuses_when_the_original_is_gone(
    library_root: Path, moves: MoveLogRepository, watched: Path, file_id: str
) -> None:
    """The one case where undoing would DESTROY the user's only copy. It must refuse."""
    org = _organizer(library_root, moves, FileAction.COPY)
    source = watched / "notes.txt"

    dest, move_id = org.organize(file_id, source, ACADEMIC)
    source.unlink()  # user deleted the original after we filed it

    with pytest.raises(FileNotFoundError, match="only remaining copy"):
        org.undo(move_id)

    assert dest.exists(), "the last surviving copy must still be there"


def test_undo_of_a_move_refuses_to_clobber_a_new_file_at_the_source(
    library_root: Path, moves: MoveLogRepository, watched: Path, file_id: str
) -> None:
    org = _organizer(library_root, moves, FileAction.MOVE)
    source = watched / "notes.txt"

    _, move_id = org.organize(file_id, source, ACADEMIC)
    source.write_text("a DIFFERENT file now lives here", encoding="utf-8")

    with pytest.raises(FileExistsError):
        org.undo(move_id)

    assert source.read_text(encoding="utf-8") == "a DIFFERENT file now lives here"


def test_undo_of_an_unknown_move_raises(library_root: Path, moves: MoveLogRepository) -> None:
    org = _organizer(library_root, moves, FileAction.COPY)
    with pytest.raises(ValueError, match="No such move"):
        org.undo("does-not-exist")


def test_move_record_round_trips(moves: MoveLogRepository, file_id: str, tmp_path: Path) -> None:
    move_id = moves.begin(file_id, tmp_path / "a.txt", tmp_path / "b.txt", FileAction.COPY)
    record = moves.get(move_id)
    assert isinstance(record, MoveRecord)
    assert record.status is MoveStatus.PENDING
    assert moves.list_pending() == [record]

    moves.complete(move_id)
    assert moves.list_pending() == []
