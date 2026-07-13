"""Watchdog adapter, with debouncing.

Watchdog runs its observer on its own thread; everything downstream is asyncio. The
bridge is ``loop.call_soon_threadsafe`` — do not be tempted to call the async
callback directly from the observer thread.

Debouncing matters more than it looks: a single browser download can emit
``created`` + several ``modified`` events, and an editor "save" is often a
write-to-temp-then-rename that fires two or three more. Without a debounce each of
those becomes a pipeline job for a file that is still moving.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from watchdog.events import (
    FileCreatedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

FileCallback = Callable[[Path], Awaitable[None]]


class _EventHandler(FileSystemEventHandler):
    def __init__(self, submit: Callable[[Path], None]) -> None:
        self._submit = submit

    def _handle(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        raw = event.dest_path if isinstance(event, FileMovedEvent) else event.src_path
        if not raw:
            return
        self._submit(Path(str(raw)))

    def on_created(self, event: FileSystemEvent) -> None:
        if isinstance(event, FileCreatedEvent):
            self._handle(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._handle(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        # Noisy by design; the debounce collapses the storm.
        self._handle(event)


class FolderWatcher:
    """Watches folders and calls ``on_file`` once per settled path."""

    def __init__(
        self,
        folders: list[Path],
        on_file: FileCallback,
        loop: asyncio.AbstractEventLoop,
        debounce_seconds: float = 1.0,
    ) -> None:
        self._folders = folders
        self._on_file = on_file
        self._loop = loop
        self._debounce = debounce_seconds
        self._observer: Observer | None = None  # type: ignore[valid-type]
        self._pending: dict[Path, asyncio.Task[None]] = {}

    def start(self) -> None:
        handler = _EventHandler(self._submit_threadsafe)
        observer = Observer()
        watched = 0
        for folder in self._folders:
            if not folder.is_dir():
                logger.warning("Watched folder does not exist, skipping: %s", folder)
                continue
            observer.schedule(handler, str(folder), recursive=True)
            watched += 1
        if watched == 0:
            logger.warning("No valid folders to watch")
        observer.start()
        self._observer = observer
        logger.info("Watching %d folder(s)", watched)

    def stop(self) -> None:
        for task in list(self._pending.values()):
            task.cancel()
        self._pending.clear()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    # --- observer thread -> event loop ------------------------------------
    def _submit_threadsafe(self, path: Path) -> None:
        self._loop.call_soon_threadsafe(self._schedule, path)

    def _schedule(self, path: Path) -> None:
        existing = self._pending.pop(path, None)
        if existing is not None:
            existing.cancel()  # reset the timer; the file is still being written
        self._pending[path] = self._loop.create_task(self._fire_after_debounce(path))

    async def _fire_after_debounce(self, path: Path) -> None:
        try:
            await asyncio.sleep(self._debounce)
        except asyncio.CancelledError:
            return
        self._pending.pop(path, None)
        try:
            await self._on_file(path)
        except Exception:  # a bad file must never kill the watcher
            logger.exception("Ingestion failed for %s", path)
