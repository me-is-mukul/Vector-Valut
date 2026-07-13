"""Domain models — the data contracts every layer is written against.

Pure: Pydantic and the stdlib only, nothing from elsewhere in the project.
Mirrors the ER diagram in architecture.md §11.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from osdc.domain.enums import (
    CategoryKind,
    Decision,
    FileAction,
    FileType,
    JobStage,
    JobStatus,
    MoveStatus,
    OcrEngine,
)

Vector = list[float]


class PageSpan(BaseModel):
    """Text belonging to one page.

    Extraction returns spans, never a flat string. Phase 5's citations
    ("file · page 12") are impossible to add later if page provenance is thrown
    away here (roadmap.md, Phase 1).
    """

    model_config = ConfigDict(frozen=True)

    page: int
    text: str


class ExtractedText(BaseModel):
    model_config = ConfigDict(frozen=True)

    pages: list[PageSpan] = Field(default_factory=list)
    is_image_based: bool = False
    ocr_used: bool = False
    ocr_engine: OcrEngine = OcrEngine.NONE
    ocr_confidence: float | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages).strip()

    @property
    def char_count(self) -> int:
        return len(self.text)


class OcrResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    pages: list[PageSpan] = Field(default_factory=list)
    engine: OcrEngine = OcrEngine.NONE
    confidence: float = 0.0

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages).strip()


class EmbeddedDocument(BaseModel):
    """What the classifier receives.

    Phase 0's KeywordClassifier reads ``text`` and ignores ``vector``; Phase 3's
    classifier does the opposite. The signature does not change between them.
    """

    model_config = ConfigDict(frozen=True)

    file_id: str
    file_type: FileType
    text: str
    vector: Vector


class LabelScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    kind: CategoryKind
    score: float


class Classification(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    kind: CategoryKind
    score: float
    semester: int | None = None
    alternatives: list[LabelScore] = Field(default_factory=list)


class ChunkRecord(BaseModel):
    """One retrievable unit. Carries its page so citations can be exact."""

    model_config = ConfigDict(frozen=True)

    id: str
    file_id: str
    page: int | None
    ordinal: int
    content: str


class Hit(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanItem(BaseModel):
    """One proposed move, before anything has touched the disk."""

    model_config = ConfigDict(frozen=True)

    file_id: str
    filename: str
    source_path: Path
    #: Library-relative, e.g. "Academics/Semester 5/Operating Systems".
    destination: str
    reason: str
    confidence: float
    #: What the embedding classifier thought, so the UI can show where the LLM overruled it.
    classifier_label: str | None = None
    skipped: bool = False


class OrganizePlan(BaseModel):
    """A complete, reviewable proposal. Nothing is moved until the user says so.

    This is the safe substitute for letting the model emit `mv` commands: the LLM still
    decides every destination, but it can only ever produce data, never an instruction —
    so it cannot invent an `rm`, mangle a filename containing quotes, or escape the library
    root. Applying it goes through the Organizer, which is logged and reversible.
    """

    model_config = ConfigDict(frozen=True)

    source_folder: Path
    items: list[PlanItem] = Field(default_factory=list)
    unreadable: list[str] = Field(default_factory=list)

    @property
    def movable(self) -> list[PlanItem]:
        return [i for i in self.items if not i.skipped]

    @property
    def folders(self) -> list[str]:
        return sorted({i.destination for i in self.movable})


class MoveRecord(BaseModel):
    """One row of the write-ahead undo log (roadmap.md §2.6)."""

    model_config = ConfigDict(frozen=True)

    id: str
    file_id: str
    source_path: Path
    dest_path: Path
    action: FileAction
    status: MoveStatus
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class FileRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    filename: str
    original_path: Path
    organized_path: Path | None = None
    content_hash: str
    file_type: FileType
    size_bytes: int
    is_image_based: bool = False
    ocr_used: bool = False
    ocr_engine: OcrEngine = OcrEngine.NONE
    extracted_text: str | None = None
    label: str | None = None
    kind: CategoryKind | None = None
    semester: int | None = None
    keywords: list[str] = Field(default_factory=list)
    confidence_score: float | None = None
    decision: Decision | None = None
    user_corrected: bool = False
    created_at: datetime
    processed_at: datetime | None = None


class JobRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    file_id: str
    status: JobStatus
    stage: JobStage
    error: str | None = None
    attempts: int = 0
    queued_at: datetime
    finished_at: datetime | None = None
