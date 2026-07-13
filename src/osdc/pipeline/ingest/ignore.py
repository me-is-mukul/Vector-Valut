"""What never reaches the pipeline.

Browsers, Office and OS indexers all litter watched folders with files that are
either half-written or none of our business. Filtering them here is cheaper than
discovering them three stages later with a corrupt hash.
"""

from __future__ import annotations

from pathlib import Path

# Partial downloads and editor swap files. Hashing one of these gets you a hash of
# a file that no longer exists a second later.
IGNORED_SUFFIXES: frozenset[str] = frozenset(
    {
        ".crdownload",  # Chrome / Edge
        ".part",  # Firefox
        ".partial",
        ".download",  # Safari
        ".tmp",
        ".temp",
        ".swp",
        ".swx",
        ".bak",
        ".lock",
        ".opdownload",  # Opera
    }
)

IGNORED_PREFIXES: tuple[str, ...] = (
    "~$",  # Word / Excel / PowerPoint lock files
    ".~lock.",  # LibreOffice
    ".",  # dotfiles, incl. .DS_Store
)

IGNORED_NAMES: frozenset[str] = frozenset({"thumbs.db", "desktop.ini", ".ds_store", "vectors.json"})

IGNORED_DIR_NAMES: frozenset[str] = frozenset(
    {"$recycle.bin", "system volume information", "__pycache__", ".git", "node_modules"}
)


def should_ignore(path: Path, library_root: Path | None = None) -> bool:
    """True if this path must never enter the pipeline."""
    name = path.name
    lowered = name.lower()

    if lowered in IGNORED_NAMES:
        return True
    if path.suffix.lower() in IGNORED_SUFFIXES:
        return True
    if any(name.startswith(prefix) for prefix in IGNORED_PREFIXES):
        return True
    if any(part.lower() in IGNORED_DIR_NAMES for part in path.parts):
        return True

    # Never re-ingest what we ourselves just filed, or the watcher and the organizer
    # will feed each other in a loop.
    return library_root is not None and _is_within(path, library_root)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True
