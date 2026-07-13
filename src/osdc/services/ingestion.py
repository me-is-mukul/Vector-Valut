"""The ingestion front door (architecture.md §4).

Everything expensive happens downstream, so this stage exists to make sure nothing
reaches the pipeline that shouldn't: temp files, half-written downloads, things we
have already indexed, things too large to bother with.

Order matters, and it is the order in architecture.md §4:
    ignore → stability → size → hash → dedupe → job row → enqueue

The job row is written *before* the enqueue, always.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from osdc.config.settings import Settings
from osdc.domain.enums import FileType
from osdc.domain.ports import TaskQueue
from osdc.pipeline.extract.detector import detect_type
from osdc.pipeline.ingest.hashing import hash_file
from osdc.pipeline.ingest.ignore import should_ignore
from osdc.pipeline.ingest.stability import wait_until_stable
from osdc.storage.repositories import FileRepository, JobRepository

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(
        self,
        settings: Settings,
        files: FileRepository,
        jobs: JobRepository,
        queue: TaskQueue,
    ) -> None:
        self._settings = settings
        self._files = files
        self._jobs = jobs
        self._queue = queue

    async def handle_path(self, path: Path) -> str | None:
        """Process one candidate path. Returns the job id, or None if skipped."""
        settings = self._settings

        if should_ignore(path, library_root=settings.library_root):
            return None
        if not path.is_file():
            return None

        stable = await wait_until_stable(
            path,
            poll_seconds=settings.stability_poll_seconds,
            required_checks=settings.stability_checks,
            timeout_seconds=settings.stability_timeout_seconds,
        )
        if not stable:
            logger.warning("Never settled, skipping: %s", path)
            return None

        try:
            size = path.stat().st_size
        except OSError:
            return None

        if size > settings.max_file_size_bytes:
            logger.info(
                "Too large (%.1f MB > %d MB), skipping: %s",
                size / 1024 / 1024,
                settings.max_file_size_mb,
                path,
            )
            return None

        content_hash = await asyncio.to_thread(hash_file, path)

        existing = await asyncio.to_thread(self._files.get_by_hash, content_hash)
        if existing is not None:
            logger.info("Duplicate of %s, skipping: %s", existing.filename, path.name)
            return None

        file_type = detect_type(path)
        job_id = await asyncio.to_thread(self._create_records, path, content_hash, file_type, size)

        # Job row is durable before the queue ever sees it.
        await self._queue.enqueue(job_id)
        logger.info("Queued %s (%s)", path.name, file_type.value)
        return job_id

    def _create_records(self, path: Path, content_hash: str, file_type: FileType, size: int) -> str:
        file_id = self._files.create(
            path=path,
            content_hash=content_hash,
            file_type=file_type,
            size_bytes=size,
        )
        return self._jobs.create(file_id)

    async def scan_folders(self) -> int:
        """Catch up on files that appeared while the app was not running."""
        queued = 0
        for folder in self._settings.watched_folders:
            if not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if not path.is_file():
                    continue
                if await self.handle_path(path) is not None:
                    queued += 1
        if queued:
            logger.info("Startup scan queued %d file(s)", queued)
        return queued
