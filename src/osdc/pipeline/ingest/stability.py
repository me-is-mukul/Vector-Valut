"""Wait until a file has finished being written.

A watcher event means "something happened to this path", not "this file is complete".
A 40 MB PDF still streaming out of Chrome will fire ``created`` immediately, and
hashing it at that moment produces a hash of a prefix — which then never matches
anything, so the file gets indexed twice and dedupe silently fails.

So: poll (size, mtime) until it stops changing, then confirm the file is actually
readable. On Windows the second check does real work — the writer may still hold an
exclusive lock long after the size has settled.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path


async def wait_until_stable(
    path: Path,
    *,
    poll_seconds: float = 0.4,
    required_checks: int = 2,
    timeout_seconds: float = 120.0,
) -> bool:
    """True once size+mtime hold steady across ``required_checks`` consecutive polls."""
    deadline = time.monotonic() + timeout_seconds
    last_signature: tuple[int, int] | None = None
    stable_count = 0

    while time.monotonic() < deadline:
        try:
            stat = path.stat()
        except (FileNotFoundError, PermissionError):
            return False  # vanished mid-write, or not ours to read

        signature = (stat.st_size, stat.st_mtime_ns)

        if signature == last_signature and stat.st_size > 0:
            stable_count += 1
            if stable_count >= required_checks and _is_readable(path):
                return True
        else:
            stable_count = 0
            last_signature = signature

        await asyncio.sleep(poll_seconds)

    return False


def _is_readable(path: Path) -> bool:
    """Can we actually open it? Catches Windows writers still holding the handle."""
    try:
        with path.open("rb") as fh:
            fh.read(1)
    except OSError:
        return False
    return True
