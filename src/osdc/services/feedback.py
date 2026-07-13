"""Undo and corrections.

Phase 0 implements ``undo`` in full, because roadmap.md §2.6 makes reversibility a
foundation requirement rather than a feature — an organizer that can move files but
not un-move them is not safe to point at someone's Downloads folder.

Corrections are recorded here too; Phase 4 adds the UI actions and the prototype
updates on top. The rows are immutable by design, so prototypes can always be rebuilt
from scratch if the update rule turns out to be wrong.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from osdc.domain.enums import CategoryKind
from osdc.pipeline.organize.organizer import Organizer
from osdc.storage.repositories import (
    CorrectionRepository,
    FileRepository,
    MoveLogRepository,
)

logger = logging.getLogger(__name__)


class FeedbackService:
    def __init__(
        self,
        files: FileRepository,
        moves: MoveLogRepository,
        corrections: CorrectionRepository,
        organizer: Organizer,
    ) -> None:
        self._files = files
        self._moves = moves
        self._corrections = corrections
        self._organizer = organizer

    async def undo(self, move_id: str) -> Path:
        """Reverse one filing operation and forget where the file was put."""
        restored = await asyncio.to_thread(self._organizer.undo, move_id)
        record = self._moves.get(move_id)
        if record is not None:
            await asyncio.to_thread(self._files.set_organized_path, record.file_id, None)
        return restored

    async def undo_last_for_file(self, file_id: str) -> Path:
        record = await asyncio.to_thread(self._moves.latest_for_file, file_id)
        if record is None:
            raise ValueError(f"No filing operation recorded for file {file_id}")
        return await self.undo(record.id)

    async def record_correction(self, file_id: str, to_label: str, to_kind: CategoryKind) -> str:
        """Persist a labeled example. Phase 4 feeds these into prototype updates."""
        file_record = await asyncio.to_thread(self._files.get, file_id)
        from_label = file_record.label if file_record else None
        return await asyncio.to_thread(
            self._corrections.record, file_id, from_label, to_label, to_kind
        )

    async def pending_moves(self) -> int:
        """Rows stuck in ``pending`` mean we died mid-operation. Surfaced in the UI."""
        records = await asyncio.to_thread(self._moves.list_pending)
        return len(records)
