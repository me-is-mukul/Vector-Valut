"""The only component that touches the user's files.

Three invariants, each of which is a test in ``tests/test_organizer.py``:

1. **The move log is written before the file is touched** (roadmap.md §2.6). A crash
   between "log" and "copy" leaves a ``pending`` row we can reconcile. A crash after a
   copy with no row at all leaves a mystery file and no undo.
2. **Never overwrite.** A colliding destination gets a ``(1)`` suffix, always.
3. **Copy is the default.** Move is opt-in, after the user trusts the classifier.

It talks to the log through the ``MoveLog`` port rather than a repository, so
``pipeline/`` never imports ``storage/`` and the layering contract holds.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from osdc.domain.enums import CategoryKind, FileAction
from osdc.domain.models import Classification
from osdc.domain.ports import MoveLog

logger = logging.getLogger(__name__)

ACADEMIC_ROOT = "Academics"
GENERAL_ROOT = "General"

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_component(name: str) -> str:
    """Make a label safe as a single path component on Windows and POSIX."""
    cleaned = _UNSAFE.sub("_", name).strip().rstrip(".")
    return cleaned or "Unnamed"


def unique_destination(dest: Path) -> Path:
    """``notes.txt`` → ``notes (1).txt`` → ``notes (2).txt``. Never overwrites."""
    if not dest.exists():
        return dest
    stem, suffix, parent = dest.stem, dest.suffix, dest.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


class Organizer:
    def __init__(
        self,
        library_root: Path,
        action: FileAction,
        move_log: MoveLog,
    ) -> None:
        self._library_root = library_root
        self._action = action
        self._move_log = move_log

    def destination_dir(self, classification: Classification) -> Path:
        if classification.kind is CategoryKind.ACADEMIC:
            base = self._library_root / ACADEMIC_ROOT
            if classification.semester is not None:
                base = base / f"Semester {classification.semester}"
        else:
            base = self._library_root / GENERAL_ROOT
        return base / sanitize_component(classification.label)

    def resolve_relative(self, relative: str) -> Path:
        """Turn a library-relative folder like "Academics/Semester 5/OS" into a real path.

        This is the only place an *externally supplied* destination enters the organizer —
        the bulk-organize planner's destinations come from an LLM. So every component is
        sanitised, and the result is checked to still live under the library root. Without
        that check a hallucinated ``../../..`` would walk straight out of the library and
        start writing into the user's home directory.
        """
        parts = [
            sanitize_component(part)
            for part in relative.replace("\\", "/").split("/")
            if part.strip() and part.strip() not in {".", ".."}
        ]
        target = self._library_root.joinpath(*parts) if parts else self._library_root

        root = self._library_root.resolve()
        resolved = target.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"Destination escapes the library root: {relative!r}")
        return target

    def organize_to(self, file_id: str, source: Path, relative: str) -> tuple[Path, str]:
        """File one document into an explicitly named folder. Returns (destination, move_id)."""
        return self._place(file_id, source, self.resolve_relative(relative))

    def organize(
        self, file_id: str, source: Path, classification: Classification
    ) -> tuple[Path, str]:
        """File one document by its classification. Returns (destination, move_id)."""
        return self._place(file_id, source, self.destination_dir(classification))

    def _place(self, file_id: str, source: Path, target_dir: Path) -> tuple[Path, str]:
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = unique_destination(target_dir / source.name)

        # Write-ahead: the log entry exists before the filesystem is touched.
        move_id = self._move_log.begin(file_id, source, dest, self._action)

        try:
            if self._action is FileAction.MOVE:
                shutil.move(str(source), str(dest))
            else:
                shutil.copy2(str(source), str(dest))
        except OSError as exc:
            self._move_log.fail(move_id, str(exc))
            logger.exception("Failed to %s %s -> %s", self._action, source, dest)
            raise

        self._move_log.complete(move_id)
        logger.info("%s %s -> %s", self._action.value, source.name, dest)
        return dest, move_id

    def undo(self, move_id: str) -> Path:
        """Reverse a completed operation. Returns the restored original path.

        The COPY branch deletes a file, so it checks first that the original still
        exists. If it does not, the "copy" in the library is the user's only copy and
        deleting it would be data loss — so we refuse.
        """
        record = self._move_log.get(move_id)
        if record is None:
            raise ValueError(f"No such move: {move_id}")

        source, dest = record.source_path, record.dest_path

        if record.action is FileAction.MOVE:
            if not dest.exists():
                raise FileNotFoundError(f"Cannot undo: {dest} is gone")
            if source.exists():
                raise FileExistsError(f"Cannot undo: {source} already exists")
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(source))
        else:
            if not source.exists():
                raise FileNotFoundError(
                    f"Refusing to undo: original {source} is missing, so {dest} is the "
                    "only remaining copy"
                )
            if dest.exists():
                dest.unlink()

        self._move_log.mark_reverted(move_id)
        logger.info("Undid %s: restored %s", record.action.value, source)
        return source
