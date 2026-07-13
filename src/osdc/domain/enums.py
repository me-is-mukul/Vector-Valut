"""Domain enums. Pure — no imports from anywhere else in the project."""

from __future__ import annotations

from enum import StrEnum


class FileType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    TXT = "txt"
    MD = "md"
    IMAGE = "image"
    OTHER = "other"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobStage(StrEnum):
    QUEUED = "queued"
    DETECTING = "detecting"
    EXTRACTING = "extracting"
    EMBEDDING = "embedding"
    CLASSIFYING = "classifying"
    ORGANIZING = "organizing"
    PERSISTING = "persisting"
    COMPLETE = "complete"


class Decision(StrEnum):
    AUTO = "auto"
    REVIEW = "review"


class CategoryKind(StrEnum):
    ACADEMIC = "academic"
    GENERAL = "general"


class FileAction(StrEnum):
    COPY = "copy"
    MOVE = "move"


class MoveStatus(StrEnum):
    """Write-ahead states for the undo log (roadmap.md §2.6)."""

    PENDING = "pending"
    COMPLETE = "complete"
    REVERTED = "reverted"
    FAILED = "failed"


class OcrEngine(StrEnum):
    NONE = "none"
    PADDLE = "paddle"
    TESSERACT = "tesseract"


#: The bucket for anything the classifier cannot place.
UNCLASSIFIED = "Others"
