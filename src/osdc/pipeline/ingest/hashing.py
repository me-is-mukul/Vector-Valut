"""Content hashing — the dedupe key.

BLAKE2b, streamed in chunks so a 2 GB file does not become a 2 GB allocation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK_SIZE = 1 << 20  # 1 MiB
DIGEST_SIZE = 16  # 32 hex chars — ample for dedupe, half the storage of a full digest


def hash_file(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=DIGEST_SIZE)
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def hash_bytes(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=DIGEST_SIZE).hexdigest()
