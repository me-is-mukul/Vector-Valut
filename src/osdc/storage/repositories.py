"""Repositories — the only place that speaks SQLAlchemy.

All methods are synchronous. Callers in the event loop wrap them in
``asyncio.to_thread``; the processing worker already runs off-loop, because OCR and
embedding are blocking CPU work and always will be.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update

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
from osdc.domain.models import (
    ChunkRecord,
    Classification,
    ExtractedText,
    FileRecord,
    JobRecord,
    MoveRecord,
)
from osdc.storage.db import Database
from osdc.storage.schema import (
    ChunkRow,
    CorrectionRow,
    EmbeddingRow,
    FileRow,
    JobRow,
    MoveLogRow,
    utcnow,
)


def _new_id() -> str:
    return str(uuid.uuid4())


def _to_file_record(row: FileRow) -> FileRecord:
    return FileRecord(
        id=row.id,
        filename=row.filename,
        original_path=Path(row.original_path),
        organized_path=Path(row.organized_path) if row.organized_path else None,
        content_hash=row.content_hash,
        file_type=FileType(row.file_type),
        size_bytes=row.size_bytes,
        is_image_based=row.is_image_based,
        ocr_used=row.ocr_used,
        ocr_engine=OcrEngine(row.ocr_engine),
        extracted_text=row.extracted_text,
        label=row.label,
        kind=CategoryKind(row.kind) if row.kind else None,
        semester=row.semester,
        keywords=row.keywords.split(",") if row.keywords else [],
        confidence_score=row.confidence_score,
        decision=Decision(row.decision) if row.decision else None,
        user_corrected=row.user_corrected,
        created_at=row.created_at,
        processed_at=row.processed_at,
    )


def _to_job_record(row: JobRow) -> JobRecord:
    return JobRecord(
        id=row.id,
        file_id=row.file_id,
        status=JobStatus(row.status),
        stage=JobStage(row.stage),
        error=row.error,
        attempts=row.attempts,
        queued_at=row.queued_at,
        finished_at=row.finished_at,
    )


def _to_move_record(row: MoveLogRow) -> MoveRecord:
    return MoveRecord(
        id=row.id,
        file_id=row.file_id,
        source_path=Path(row.source_path),
        dest_path=Path(row.dest_path),
        action=FileAction(row.action),
        status=MoveStatus(row.status),
        error=row.error,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


class FileRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_by_hash(self, content_hash: str) -> FileRecord | None:
        with self.db.session() as s:
            row = s.scalar(select(FileRow).where(FileRow.content_hash == content_hash))
            return _to_file_record(row) if row else None

    def get(self, file_id: str) -> FileRecord | None:
        with self.db.session() as s:
            row = s.get(FileRow, file_id)
            return _to_file_record(row) if row else None

    def create(
        self,
        *,
        path: Path,
        content_hash: str,
        file_type: FileType,
        size_bytes: int,
    ) -> str:
        file_id = _new_id()
        with self.db.session() as s:
            s.add(
                FileRow(
                    id=file_id,
                    filename=path.name,
                    original_path=str(path),
                    content_hash=content_hash,
                    file_type=file_type,
                    size_bytes=size_bytes,
                )
            )
        return file_id

    def save_extraction(self, file_id: str, extracted: ExtractedText) -> None:
        with self.db.session() as s:
            s.execute(
                update(FileRow)
                .where(FileRow.id == file_id)
                .values(
                    extracted_text=extracted.text,
                    is_image_based=extracted.is_image_based,
                    ocr_used=extracted.ocr_used,
                    ocr_engine=extracted.ocr_engine,
                )
            )

    def save_classification(
        self,
        file_id: str,
        classification: Classification,
        decision: Decision,
        keywords: list[str] | None = None,
    ) -> None:
        with self.db.session() as s:
            s.execute(
                update(FileRow)
                .where(FileRow.id == file_id)
                .values(
                    label=classification.label,
                    kind=classification.kind,
                    semester=classification.semester,
                    confidence_score=classification.score,
                    decision=decision,
                    keywords=",".join(keywords) if keywords else None,
                )
            )

    def set_organized_path(self, file_id: str, dest: Path | None) -> None:
        with self.db.session() as s:
            s.execute(
                update(FileRow)
                .where(FileRow.id == file_id)
                .values(organized_path=str(dest) if dest else None)
            )

    def mark_processed(self, file_id: str, when: datetime | None = None) -> None:
        with self.db.session() as s:
            s.execute(
                update(FileRow).where(FileRow.id == file_id).values(processed_at=when or utcnow())
            )

    def list_all(self, limit: int = 500) -> list[FileRecord]:
        with self.db.session() as s:
            rows = s.scalars(select(FileRow).order_by(FileRow.created_at.desc()).limit(limit)).all()
            return [_to_file_record(r) for r in rows]

    def list_by_decision(self, decision: Decision, limit: int = 500) -> list[FileRecord]:
        with self.db.session() as s:
            rows = s.scalars(
                select(FileRow)
                .where(FileRow.decision == decision)
                .order_by(FileRow.created_at.desc())
                .limit(limit)
            ).all()
            return [_to_file_record(r) for r in rows]

    def list_under(self, folder: Path, limit: int = 2000) -> list[FileRecord]:
        """Every indexed file whose original path lives under this folder."""
        prefix = str(folder)
        with self.db.session() as s:
            rows = s.scalars(
                select(FileRow)
                .where(FileRow.original_path.startswith(prefix))
                .order_by(FileRow.filename)
                .limit(limit)
            ).all()
            return [_to_file_record(r) for r in rows]

    def count(self) -> int:
        with self.db.session() as s:
            return len(s.scalars(select(FileRow.id)).all())


class JobRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, file_id: str) -> str:
        job_id = _new_id()
        with self.db.session() as s:
            s.add(JobRow(id=job_id, file_id=file_id, status=JobStatus.QUEUED))
        return job_id

    def get(self, job_id: str) -> JobRecord | None:
        with self.db.session() as s:
            row = s.get(JobRow, job_id)
            return _to_job_record(row) if row else None

    def start(self, job_id: str) -> None:
        with self.db.session() as s:
            row = s.get(JobRow, job_id)
            if row is None:
                return
            row.status = JobStatus.RUNNING
            row.attempts += 1

    def set_stage(self, job_id: str, stage: JobStage) -> None:
        with self.db.session() as s:
            s.execute(update(JobRow).where(JobRow.id == job_id).values(stage=stage))

    def finish(self, job_id: str) -> None:
        with self.db.session() as s:
            s.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(status=JobStatus.DONE, stage=JobStage.COMPLETE, finished_at=utcnow())
            )

    def fail(self, job_id: str, error: str) -> None:
        with self.db.session() as s:
            s.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(status=JobStatus.FAILED, error=error[:2000], finished_at=utcnow())
            )

    def recover_orphans(self) -> list[str]:
        """Crash recovery (roadmap.md §2.5).

        Anything left ``running`` when the process died goes back to ``queued``, and
        every ``queued`` job id is handed back so the caller can re-enqueue it. Without
        this, a crash mid-pipeline silently drops the user's file.
        """
        with self.db.session() as s:
            s.execute(
                update(JobRow)
                .where(JobRow.status == JobStatus.RUNNING)
                .values(status=JobStatus.QUEUED, stage=JobStage.QUEUED)
            )
            s.flush()
            return list(
                s.scalars(
                    select(JobRow.id)
                    .where(JobRow.status == JobStatus.QUEUED)
                    .order_by(JobRow.queued_at)
                ).all()
            )

    def list_recent(self, limit: int = 100) -> list[JobRecord]:
        with self.db.session() as s:
            rows = s.scalars(select(JobRow).order_by(JobRow.queued_at.desc()).limit(limit)).all()
            return [_to_job_record(r) for r in rows]

    def count_by_status(self, status: JobStatus) -> int:
        with self.db.session() as s:
            return len(s.scalars(select(JobRow.id).where(JobRow.status == status)).all())


class MoveLogRepository:
    """Implements the ``MoveLog`` port (domain/ports.py).

    ``begin`` writes the row *before* the file is touched. That ordering is the
    entire mitigation for "destructive moves lose files" (planning.md §11).
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def begin(self, file_id: str, source: Path, dest: Path, action: FileAction) -> str:
        move_id = _new_id()
        with self.db.session() as s:
            s.add(
                MoveLogRow(
                    id=move_id,
                    file_id=file_id,
                    source_path=str(source),
                    dest_path=str(dest),
                    action=action,
                    status=MoveStatus.PENDING,
                )
            )
        return move_id

    def complete(self, move_id: str) -> None:
        with self.db.session() as s:
            s.execute(
                update(MoveLogRow)
                .where(MoveLogRow.id == move_id)
                .values(status=MoveStatus.COMPLETE, completed_at=utcnow())
            )

    def fail(self, move_id: str, error: str) -> None:
        with self.db.session() as s:
            s.execute(
                update(MoveLogRow)
                .where(MoveLogRow.id == move_id)
                .values(status=MoveStatus.FAILED, error=error[:2000])
            )

    def mark_reverted(self, move_id: str) -> None:
        with self.db.session() as s:
            s.execute(
                update(MoveLogRow)
                .where(MoveLogRow.id == move_id)
                .values(status=MoveStatus.REVERTED, completed_at=utcnow())
            )

    def get(self, move_id: str) -> MoveRecord | None:
        with self.db.session() as s:
            row = s.get(MoveLogRow, move_id)
            return _to_move_record(row) if row else None

    def latest_for_file(self, file_id: str) -> MoveRecord | None:
        with self.db.session() as s:
            row = s.scalar(
                select(MoveLogRow)
                .where(MoveLogRow.file_id == file_id)
                .order_by(MoveLogRow.created_at.desc())
                .limit(1)
            )
            return _to_move_record(row) if row else None

    def list_pending(self) -> list[MoveRecord]:
        """Rows stuck in ``pending`` mean the process died mid-operation."""
        with self.db.session() as s:
            rows = s.scalars(
                select(MoveLogRow).where(MoveLogRow.status == MoveStatus.PENDING)
            ).all()
            return [_to_move_record(r) for r in rows]


class CorrectionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self, file_id: str, from_label: str | None, to_label: str, to_kind: CategoryKind
    ) -> str:
        correction_id = _new_id()
        with self.db.session() as s:
            s.add(
                CorrectionRow(
                    id=correction_id,
                    file_id=file_id,
                    from_label=from_label,
                    to_label=to_label,
                    to_kind=to_kind,
                )
            )
            s.execute(update(FileRow).where(FileRow.id == file_id).values(user_corrected=True))
        return correction_id

    def count(self) -> int:
        with self.db.session() as s:
            return len(s.scalars(select(CorrectionRow.id)).all())


class ChunkRepository:
    """Chunks are the unit of retrieval, and therefore the unit of citation.

    The chunk id doubles as the vector-store id (``<file_id>::<ordinal>``), so a vector
    hit resolves straight back to its page without a second lookup.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def make_id(file_id: str, ordinal: int) -> str:
        return f"{file_id}::{ordinal}"

    def replace_for_file(
        self, file_id: str, chunks: list[tuple[int, int | None, str]]
    ) -> list[str]:
        """Write this file's chunks, replacing any previous ones. Returns the chunk ids.

        Replace rather than append: a file reprocessed with a better OCR engine or a new
        chunking strategy must not leave its stale chunks behind to be retrieved
        alongside the fresh ones.
        """
        ids: list[str] = []
        with self.db.session() as s:
            s.query(ChunkRow).filter(ChunkRow.file_id == file_id).delete()
            s.flush()
            for ordinal, page, content in chunks:
                chunk_id = self.make_id(file_id, ordinal)
                s.add(
                    ChunkRow(
                        id=chunk_id,
                        file_id=file_id,
                        page=page,
                        ordinal=ordinal,
                        content=content,
                        embedding_ref=chunk_id,
                    )
                )
                ids.append(chunk_id)
        return ids

    def get(self, chunk_id: str) -> ChunkRecord | None:
        with self.db.session() as s:
            row = s.get(ChunkRow, chunk_id)
            if row is None:
                return None
            return ChunkRecord(
                id=row.id,
                file_id=row.file_id,
                page=row.page,
                ordinal=row.ordinal,
                content=row.content,
            )

    def count(self) -> int:
        with self.db.session() as s:
            return len(s.scalars(select(ChunkRow.id)).all())


class EmbeddingRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, file_id: str, kind: str, collection: str, vector_ref: str, dim: int) -> str:
        embedding_id = _new_id()
        with self.db.session() as s:
            s.add(
                EmbeddingRow(
                    id=embedding_id,
                    file_id=file_id,
                    kind=kind,
                    collection=collection,
                    vector_ref=vector_ref,
                    dim=dim,
                )
            )
        return embedding_id
