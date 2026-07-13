"""Seeds the Subject Knowledge Base into SQLite and the vector store.

Runs on startup. It is idempotent and keyed on a fingerprint of (curriculum content +
embedding model), so it re-seeds automatically when you edit ``curriculum.yaml`` or
switch models — and does nothing at all on every other boot.

That fingerprint matters: subject prototypes embedded with ``bge-small`` are meaningless
to ``e5-small``, and silently mixing the two would degrade classification with no error
anywhere. Changing the model must invalidate the prototypes.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid

from sqlalchemy import select

from osdc.domain.enums import CategoryKind
from osdc.domain.ports import TextEmbedder, VectorStore
from osdc.pipeline.classify.curriculum import Curriculum, load_curriculum
from osdc.storage import vectors
from osdc.storage.db import Database
from osdc.storage.schema import AppMetaRow, CategoryRow, SemesterRow, SubjectRow, utcnow

logger = logging.getLogger(__name__)

FINGERPRINT_KEY = "kb_fingerprint"


class KnowledgeBaseService:
    def __init__(
        self,
        db: Database,
        embedder: TextEmbedder,
        vector_store: VectorStore,
        curriculum: Curriculum | None = None,
    ) -> None:
        self._db = db
        self._embedder = embedder
        self._vectors = vector_store
        self._curriculum = curriculum or load_curriculum()

    @property
    def curriculum(self) -> Curriculum:
        return self._curriculum

    def fingerprint(self, model_name: str) -> str:
        payload = "|".join(
            [
                model_name,
                *(s.embedding_text for s in self._curriculum.subjects),
                *(c.embedding_text for c in self._curriculum.categories),
            ]
        )
        return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()

    async def seed(self, model_name: str) -> bool:
        """Returns True if it (re)built the knowledge base."""
        return await asyncio.to_thread(self._seed_sync, model_name)

    def _seed_sync(self, model_name: str) -> bool:
        want = self.fingerprint(model_name)
        have = self._read_fingerprint()
        if have == want and self._vectors.count(vectors.SUBJECT_KB) > 0:
            logger.info(
                "Knowledge base up to date (%d subjects, %d categories)",
                len(self._curriculum.subjects),
                len(self._curriculum.categories),
            )
            return False

        if have is not None:
            logger.info("Curriculum or embedding model changed — rebuilding knowledge base")

        self._rebuild(model_name)
        return True

    def _rebuild(self, model_name: str) -> None:
        curriculum = self._curriculum

        # --- subjects -> subject_kb ---------------------------------------
        subject_texts = [s.embedding_text for s in curriculum.subjects]
        subject_vectors = self._embedder.embed(subject_texts) if subject_texts else []

        ids, metas = [], []
        for subject in curriculum.subjects:
            ids.append(f"subject::{subject.semester}::{subject.name}")
            metas.append(
                {
                    "label": subject.name,
                    "semester": subject.semester,
                    "code": subject.code or "",
                }
            )
        if ids:
            self._vectors.upsert(vectors.SUBJECT_KB, ids, subject_vectors, metas)

        # --- general categories -> category_prototypes ---------------------
        category_texts = [c.embedding_text for c in curriculum.categories]
        category_vectors = self._embedder.embed(category_texts) if category_texts else []

        cat_ids = [f"category::{c.name}" for c in curriculum.categories]
        cat_metas: list[dict[str, object]] = [
            {"label": c.name, "is_custom": False} for c in curriculum.categories
        ]
        if cat_ids:
            self._vectors.upsert(vectors.CATEGORY_PROTOTYPES, cat_ids, category_vectors, cat_metas)

        # --- mirror into SQLite --------------------------------------------
        with self._db.session() as session:
            session.query(SubjectRow).delete()
            session.query(SemesterRow).delete()
            session.flush()

            semesters = sorted({s.semester for s in curriculum.subjects})
            for number in semesters:
                session.add(
                    SemesterRow(number=number, is_current=(number == curriculum.current_semester))
                )
            session.flush()

            for subject in curriculum.subjects:
                session.add(
                    SubjectRow(
                        id=str(uuid.uuid4()),
                        name=subject.name,
                        code=subject.code,
                        description=subject.description,
                        topics=",".join(subject.topics),
                        credits=subject.credits,
                        semester=subject.semester,
                        prototype_ref=f"subject::{subject.semester}::{subject.name}",
                    )
                )

            existing = {name for (name,) in session.execute(select(CategoryRow.name)).all()}
            for category in curriculum.categories:
                if category.name in existing:
                    continue
                session.add(
                    CategoryRow(
                        id=str(uuid.uuid4()),
                        name=category.name,
                        kind=CategoryKind.GENERAL,
                        is_custom=False,
                        prototype_ref=f"category::{category.name}",
                    )
                )

        self._write_fingerprint(self.fingerprint(model_name))
        logger.info(
            "Knowledge base built: %d subjects across %d semesters, %d general categories",
            len(curriculum.subjects),
            len({s.semester for s in curriculum.subjects}),
            len(curriculum.categories),
        )

    # --- fingerprint storage ------------------------------------------------
    def _read_fingerprint(self) -> str | None:
        with self._db.session() as session:
            row = session.get(AppMetaRow, FINGERPRINT_KEY)
            return row.value if row else None

    def _write_fingerprint(self, value: str) -> None:
        with self._db.session() as session:
            row = session.get(AppMetaRow, FINGERPRINT_KEY)
            if row is None:
                session.add(AppMetaRow(key=FINGERPRINT_KEY, value=value))
            else:
                row.value = value
                row.updated_at = utcnow()
