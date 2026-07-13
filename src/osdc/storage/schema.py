"""SQLAlchemy tables — architecture.md §11, plus ``move_log``.

``move_log`` is not in the ER diagram yet, but roadmap.md §2.6 requires it: every
file operation is written here *before* it happens, so a crash mid-move is always
recoverable and every move is undoable.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

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


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class FileRow(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512))
    original_path: Mapped[str] = mapped_column(Text)
    organized_path: Mapped[str | None] = mapped_column(Text, default=None)

    # Dedupe key. Unique — the same bytes are never indexed twice.
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    file_type: Mapped[FileType] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(Integer)

    is_image_based: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_engine: Mapped[OcrEngine] = mapped_column(String(16), default=OcrEngine.NONE)

    extracted_text: Mapped[str | None] = mapped_column(Text, default=None)

    label: Mapped[str | None] = mapped_column(String(128), index=True, default=None)
    kind: Mapped[CategoryKind | None] = mapped_column(String(16), default=None)
    semester: Mapped[int | None] = mapped_column(Integer, default=None)
    keywords: Mapped[str | None] = mapped_column(Text, default=None)  # comma-separated
    confidence_score: Mapped[float | None] = mapped_column(Float, default=None)
    decision: Mapped[Decision | None] = mapped_column(String(16), index=True, default=None)

    user_corrected: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    chunks: Mapped[list[ChunkRow]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[JobRow]] = relationship(back_populates="file", cascade="all, delete-orphan")


class ChunkRow(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("file_id", "ordinal", name="uq_chunk_file_ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    page: Mapped[int | None] = mapped_column(Integer, default=None)
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding_ref: Mapped[str | None] = mapped_column(String(128), default=None)

    file: Mapped[FileRow] = relationship(back_populates="chunks")


class EmbeddingRow(Base):
    __tablename__ = "embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # doc | image_visual | image_ocr_text
    collection: Mapped[str] = mapped_column(String(64))
    vector_ref: Mapped[str] = mapped_column(String(128))
    dim: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CategoryRow(Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[CategoryKind] = mapped_column(String(16))
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    prototype_ref: Mapped[str | None] = mapped_column(String(128), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SemesterRow(Base):
    __tablename__ = "semesters"

    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)


class SubjectRow(Base):
    """Populated in Phase 3 from the Subject Knowledge Base (roadmap.md §7)."""

    __tablename__ = "subjects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    code: Mapped[str | None] = mapped_column(String(32), default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    topics: Mapped[str | None] = mapped_column(Text, default=None)
    keywords: Mapped[str | None] = mapped_column(Text, default=None)
    credits: Mapped[int | None] = mapped_column(Integer, default=None)
    semester: Mapped[int | None] = mapped_column(
        ForeignKey("semesters.number"), index=True, default=None
    )
    prototype_ref: Mapped[str | None] = mapped_column(String(128), default=None)


class CorrectionRow(Base):
    """Immutable. Prototypes are derived from these and can always be rebuilt
    from scratch if the update rule turns out to be wrong (roadmap.md, Phase 4)."""

    __tablename__ = "corrections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    from_label: Mapped[str | None] = mapped_column(String(128), default=None)
    to_label: Mapped[str] = mapped_column(String(128))
    to_kind: Mapped[CategoryKind] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRow(Base):
    """The source of truth for pipeline work — NOT the asyncio queue (roadmap.md §2.5).

    Rows are written ``queued`` before anything is enqueued, so a crash re-enqueues
    rather than silently dropping the user's file.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    status: Mapped[JobStatus] = mapped_column(String(16), index=True, default=JobStatus.QUEUED)
    stage: Mapped[JobStage] = mapped_column(String(16), default=JobStage.QUEUED)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    file: Mapped[FileRow] = relationship(back_populates="jobs")


class AppMetaRow(Base):
    """Small key-value store for app-level state that is not user data.

    Currently holds the knowledge-base fingerprint (curriculum content + embedding model),
    so prototypes are rebuilt when either changes. Prototypes embedded with one model are
    meaningless to another, and mixing them degrades classification with no visible error.
    """

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MoveLogRow(Base):
    __tablename__ = "move_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    source_path: Mapped[str] = mapped_column(Text)
    dest_path: Mapped[str] = mapped_column(Text)
    action: Mapped[FileAction] = mapped_column(String(16))
    status: Mapped[MoveStatus] = mapped_column(String(16), index=True, default=MoveStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
