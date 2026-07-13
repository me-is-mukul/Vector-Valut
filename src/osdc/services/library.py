"""Read-side service for the UI: what's in the library, what needs review, how's the queue.

The UI never touches a repository directly — it comes through here, and everything is
wrapped in ``to_thread`` so a slow query cannot stall the event loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from osdc.domain.enums import Decision, JobStatus
from osdc.domain.models import FileRecord, JobRecord
from osdc.storage.repositories import FileRepository, JobRepository


@dataclass(frozen=True)
class QueueStats:
    queued: int
    running: int
    done: int
    failed: int
    total_files: int
    review_count: int


class LibraryService:
    def __init__(self, files: FileRepository, jobs: JobRepository) -> None:
        self._files = files
        self._jobs = jobs

    async def list_files(self, limit: int = 500) -> list[FileRecord]:
        return await asyncio.to_thread(self._files.list_all, limit)

    async def review_queue(self, limit: int = 500) -> list[FileRecord]:
        return await asyncio.to_thread(self._files.list_by_decision, Decision.REVIEW, limit)

    async def recent_jobs(self, limit: int = 100) -> list[JobRecord]:
        return await asyncio.to_thread(self._jobs.list_recent, limit)

    async def stats(self) -> QueueStats:
        return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> QueueStats:
        return QueueStats(
            queued=self._jobs.count_by_status(JobStatus.QUEUED),
            running=self._jobs.count_by_status(JobStatus.RUNNING),
            done=self._jobs.count_by_status(JobStatus.DONE),
            failed=self._jobs.count_by_status(JobStatus.FAILED),
            total_files=self._files.count(),
            review_count=len(self._files.list_by_decision(Decision.REVIEW)),
        )
