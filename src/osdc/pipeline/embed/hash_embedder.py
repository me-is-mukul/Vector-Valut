"""Phase 0's embedder: the hashing trick. No model, no download, no Torch.

Each token is hashed into one of ``dim`` buckets and the resulting bag-of-words
vector is L2-normalised. This is a real vector with real cosine behaviour — identical
texts score 1.0, texts sharing vocabulary score high, unrelated texts score near 0 —
it simply has no *semantics*. "paging" and "virtual memory" are as unrelated to it as
"paging" and "banana".

That is exactly the right amount of intelligence for a walking skeleton: it exercises
the embedding seam, the vector store, and the similarity maths end to end, at zero
install cost. Phase 3 replaces it with Sentence-Transformers and every caller above
stays byte-for-byte identical.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from osdc.domain.models import Vector

_TOKEN = re.compile(r"[a-z0-9]+")

DEFAULT_DIM = 256


class HashEmbedder:
    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        return [self._embed_one(text) for text in texts]

    def embed_query(self, texts: Sequence[str]) -> list[Vector]:
        """Symmetric: a hashing embedder has no notion of query-vs-passage."""
        return self.embed(texts)

    def _embed_one(self, text: str) -> Vector:
        vector = [0.0] * self.dim
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % self.dim
            vector[index] += 1.0

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            return vector
        return [v / norm for v in vector]
