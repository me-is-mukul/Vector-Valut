"""The LLM segregation planner, and the guard rails around it.

This is the feature where a language model decides where the user's files go. The whole
design rests on one constraint: **the model emits data, never instructions.** These tests
are what hold that line.

The most important one is ``test_a_hallucinated_path_cannot_escape_the_library`` — a model
that emits ``../../../Windows/System32`` must be stopped by code, not by hoping it doesn't.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from osdc.domain.enums import CategoryKind, FileAction
from osdc.domain.models import Classification, OrganizePlan, PlanItem
from osdc.pipeline.organize.organizer import Organizer
from osdc.services.planner import PLAN_SCHEMA
from osdc.storage.db import Database
from osdc.storage.repositories import MoveLogRepository


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path / 'plan.sqlite3'}")
    database.create_all()
    return database


@pytest.fixture
def moves(db: Database) -> MoveLogRepository:
    return MoveLogRepository(db)


@pytest.fixture
def organizer(library_root: Path, moves: MoveLogRepository) -> Organizer:
    return Organizer(library_root=library_root, action=FileAction.COPY, move_log=moves)


@pytest.fixture
def file_id(db: Database, watched: Path) -> str:
    """A real file row — the move log has a foreign key onto it, and that FK is deliberate:
    an orphaned move record would be an un-undoable move."""
    from osdc.domain.enums import FileType
    from osdc.storage.repositories import FileRepository

    source = watched / "notes.txt"
    source.write_text("hello", encoding="utf-8")
    return FileRepository(db).create(
        path=source, content_hash="abc", file_type=FileType.TXT, size_bytes=5
    )


# --- THE guard rail ---------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../Windows/System32",
        "..\\..\\..\\Users\\someone\\Desktop",
        "/etc/passwd",
        "Academics/../../../../tmp",
        "C:\\Windows\\Temp",
    ],
)
def test_a_hallucinated_path_cannot_escape_the_library(
    organizer: Organizer, library_root: Path, hostile: str
) -> None:
    """Destinations come from an LLM. Treat every one as hostile input.

    Either the traversal is neutralised into a plain folder name inside the library, or it
    raises — but under no circumstances does it resolve to somewhere outside the library
    root. This is the difference between an organizer and a remote file-write primitive.
    """
    try:
        resolved = organizer.resolve_relative(hostile)
    except ValueError:
        return  # refused outright — also fine

    root = library_root.resolve()
    assert root == resolved.resolve() or root in resolved.resolve().parents, (
        f"{hostile!r} escaped the library to {resolved}"
    )


def test_an_ordinary_destination_resolves_normally(
    organizer: Organizer, library_root: Path
) -> None:
    assert organizer.resolve_relative("Academics/Semester 5/Operating Systems") == (
        library_root / "Academics" / "Semester 5" / "Operating Systems"
    )


def test_destination_components_are_sanitised(organizer: Organizer, library_root: Path) -> None:
    """A model that emits a colon or a pipe would otherwise produce an unopenable path
    on Windows."""
    assert organizer.resolve_relative("General/Bills: 2024 | Q1") == (
        library_root / "General" / "Bills_ 2024 _ Q1"
    )


def test_organize_to_files_into_an_explicit_folder(
    organizer: Organizer, library_root: Path, watched: Path, file_id: str
) -> None:
    source = watched / "notes.txt"

    dest, move_id = organizer.organize_to(file_id, source, "General/Recipes")

    assert dest == library_root / "General" / "Recipes" / "notes.txt"
    assert dest.exists()
    assert source.exists(), "copy must leave the original"
    assert move_id, "the move must still be logged and undoable"


def test_an_explicit_move_is_still_undoable(
    organizer: Organizer, moves: MoveLogRepository, watched: Path, file_id: str
) -> None:
    """The LLM path goes through exactly the same reversible engine as everything else."""
    source = watched / "notes.txt"

    dest, move_id = organizer.organize_to(file_id, source, "General/Recipes")
    assert dest.exists()

    organizer.undo(move_id)
    assert not dest.exists()
    assert source.exists()


# --- plan shape -------------------------------------------------------------


def test_the_schema_forces_the_fields_we_depend_on() -> None:
    """Ollama constrains the sampler to this schema, which is what turns "please reply with
    JSON" from a hope into a guarantee."""
    item = PLAN_SCHEMA["properties"]["assignments"]["items"]
    assert set(item["required"]) == {"file_id", "folder", "reason", "confidence"}


def _item(name: str, dest: str, *, skipped: bool = False) -> PlanItem:
    return PlanItem(
        file_id=name,
        filename=f"{name}.pdf",
        source_path=Path("/tmp") / f"{name}.pdf",
        destination=dest,
        reason="because",
        confidence=0.9,
        skipped=skipped,
    )


def test_review_items_are_excluded_from_the_moves() -> None:
    """ "I don't know" is a valid answer, and it must not turn into a file move."""
    plan = OrganizePlan(
        source_folder=Path("/tmp"),
        items=[
            _item("a", "Academics/Semester 5/Operating Systems"),
            _item("b", "General/Finance"),
            _item("c", "Review", skipped=True),
        ],
    )

    assert [i.file_id for i in plan.movable] == ["a", "b"]
    assert plan.folders == ["Academics/Semester 5/Operating Systems", "General/Finance"]


def test_an_empty_plan_moves_nothing() -> None:
    plan = OrganizePlan(source_folder=Path("/tmp"), items=[])
    assert plan.movable == []
    assert plan.folders == []


# --- the model's output is untrusted ---------------------------------------


class DroppingLlm:
    """A model that silently omits half the files it was asked about. They do this."""

    model = "dropping"

    def available(self) -> bool:
        return True

    def generate(self, prompt: str, system: str | None = None) -> str:
        return ""

    def generate_json(
        self, prompt: str, schema: dict[str, Any], system: str | None = None
    ) -> dict[str, Any]:
        return {"assignments": []}  # answered nothing at all


class BrokenLlm(DroppingLlm):
    def generate_json(
        self, prompt: str, schema: dict[str, Any], system: str | None = None
    ) -> dict[str, Any]:
        raise RuntimeError("model exploded")


@pytest.fixture
def planning(data_dir: Path, library_root: Path, watched: Path, tmp_path: Path) -> Any:
    """A container wired with the cheap hashing embedder — these tests are about the
    planner's handling of a misbehaving model, not about embedding quality."""
    from osdc.config.settings import Settings
    from osdc.container import Container
    from osdc.pipeline.embed.hash_embedder import HashEmbedder

    return Container(
        settings=Settings(
            library_root=library_root, use_real_embeddings=False, scan_on_startup=False
        ),
        db_url=f"sqlite:///{tmp_path / 'drop.sqlite3'}",
        embedder=HashEmbedder(),
    )


def _seed_file(container: Any, source: Path) -> Any:
    from osdc.domain.enums import Decision, FileType
    from osdc.domain.models import ExtractedText, PageSpan

    source.write_text("paging and deadlock", encoding="utf-8")
    file_id = container.files.create(
        path=source, content_hash="h1", file_type=FileType.TXT, size_bytes=19
    )
    container.files.save_extraction(
        file_id, ExtractedText(pages=[PageSpan(page=1, text="paging and deadlock")])
    )
    container.files.save_classification(
        file_id,
        Classification(label="Operating Systems", kind=CategoryKind.ACADEMIC, score=0.9),
        Decision.AUTO,
    )
    return container.files.get(file_id)


def test_a_file_the_model_forgot_still_gets_an_assignment(planning: Any, watched: Path) -> None:
    """Models drop entries from the middle of long lists. When that happens the file must
    NOT silently vanish from the plan — the user asked for the folder to be organised, and a
    file quietly skipped is a bug they only discover months later."""
    planning.planner._llm = DroppingLlm()
    record = _seed_file(planning, watched / "notes.txt")

    items = planning.planner._plan_batch([record], [])

    assert len(items) == 1, "the dropped file must still be accounted for"
    assert items[0].file_id == record.id
    assert items[0].destination, "it needs somewhere to go, even if that is Review"


def test_a_model_that_crashes_falls_back_to_the_classifier(planning: Any, watched: Path) -> None:
    """The LLM is an enhancement, not a dependency. If it dies, the embedding classifier's
    answer is still a perfectly good plan."""
    planning.planner._llm = BrokenLlm()
    record = _seed_file(planning, watched / "notes.txt")

    items = planning.planner._plan_batch([record], [])

    assert len(items) == 1
    assert items[0].classifier_label == "Operating Systems"
    assert "Operating Systems" in items[0].destination
