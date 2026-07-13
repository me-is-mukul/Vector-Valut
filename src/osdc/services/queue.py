"""In-process asyncio task queue.

Carries job *ids* only, never job state — the ``jobs`` table is the source of truth
(roadmap.md §2.5). If this queue evaporates (crash, restart, OOM), no work is lost:
``JobRepository.recover_orphans()`` rebuilds it from SQLite on the next boot.

That property is also what makes the Celery/RQ upgrade a drop-in: swap the transport,
keep the table.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

JobHandler = Callable[[str], Awaitable[None]]


class AsyncioTaskQueue:
    def __init__(self, handler: JobHandler, worker_count: int = 2) -> None:
        self._handler = handler
        self._worker_count = worker_count
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def start(self) -> None:
        if self._workers:
            return
        self._workers = [asyncio.create_task(self._worker(i)) for i in range(self._worker_count)]
        logger.info("Started %d pipeline worker(s)", self._worker_count)

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    async def _worker(self, index: int) -> None:
        while True:
            try:
                job_id = await self._queue.get()
            except asyncio.CancelledError:
                return
            try:
                await self._handler(job_id)
            except asyncio.CancelledError:
                return
            except Exception:
                # A poisoned job must never take a worker down with it. The handler is
                # responsible for marking the job failed; we just keep the loop alive.
                logger.exception("Worker %d: job %s raised", index, job_id)
            finally:
                self._queue.task_done()
