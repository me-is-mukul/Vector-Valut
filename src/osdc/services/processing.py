"""The processing orchestrator — architecture.md §5, stage for stage.

    detect → extract (or OCR) → enough text? → clean → chunk → embed → classify
           → gate → organize (auto only) → persist

Every stage talks to a port, never to a concrete implementation. This file was written in
Phase 0 against stubs and did not change when Phase 1 added real extractors or Phase 3
swapped ``HashEmbedder`` for Sentence-Transformers — only ``container.py`` changed. That
is the walking skeleton paying off exactly as intended.

The whole job runs in a worker thread (``asyncio.to_thread``): extraction, embedding and
(later) OCR are blocking CPU work. Keeping that boundary here means the UI never stutters
and the repositories stay comfortably synchronous.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from osdc.config.settings import Settings
from osdc.domain.enums import UNCLASSIFIED, CategoryKind, Decision, FileType, JobStage
from osdc.domain.models import Classification, EmbeddedDocument, ExtractedText
from osdc.domain.ports import Classifier, OcrEngineProtocol, TextEmbedder, VectorStore
from osdc.pipeline.chunk.chunker import Chunk, chunk_extracted
from osdc.pipeline.classify.gate import ConfidenceGate
from osdc.pipeline.extract.cleaner import clean_extracted
from osdc.pipeline.extract.registry import ExtractorRegistry
from osdc.pipeline.organize.organizer import Organizer
from osdc.storage import vectors
from osdc.storage.repositories import (
    ChunkRepository,
    EmbeddingRepository,
    FileRepository,
    JobRepository,
)

logger = logging.getLogger(__name__)

#: Below this many characters we do not trust the extraction enough to classify on it.
#: These become honest Review Queue items rather than guesses. Phase 2's OCR is what
#: rescues the scanned ones.
MIN_TEXT_CHARS = 20

#: Classification reads the head of the document, not all of it. A 300-page textbook
#: embedded whole averages out into a vector that means nothing in particular; the first
#: couple of pages are where the subject actually announces itself.
CLASSIFY_HEAD_CHARS = 4000


class ProcessingService:
    def __init__(
        self,
        settings: Settings,
        files: FileRepository,
        jobs: JobRepository,
        chunks: ChunkRepository,
        embeddings: EmbeddingRepository,
        extractors: ExtractorRegistry,
        ocr: OcrEngineProtocol,
        embedder: TextEmbedder,
        classifier: Classifier,
        gate: ConfidenceGate,
        organizer: Organizer,
        vector_store: VectorStore,
    ) -> None:
        self._settings = settings
        self._files = files
        self._jobs = jobs
        self._chunks = chunks
        self._embeddings = embeddings
        self._extractors = extractors
        self._ocr = ocr
        self._embedder = embedder
        self._classifier = classifier
        self._gate = gate
        self._organizer = organizer
        self._vectors = vector_store

        #: Folders currently being planned. Files under these are indexed and classified as
        #: normal but NOT filed — the whole promise of the bulk-organize flow is that the
        #: user sees a plan before anything moves. The background watcher keeps auto-filing
        #: everything else, so a download arriving mid-plan is unaffected.
        self.planning_roots: set[Path] = set()

    def _is_being_planned(self, path: Path) -> bool:
        return any(root == path or root in path.parents for root in self.planning_roots)

    async def process(self, job_id: str) -> None:
        """Queue handler. Runs the blocking pipeline off the event loop."""
        await asyncio.to_thread(self._process_sync, job_id)

    def _process_sync(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            logger.warning("Job %s vanished", job_id)
            return

        file_record = self._files.get(job.file_id)
        if file_record is None:
            self._jobs.fail(job_id, "File record missing")
            return

        self._jobs.start(job_id)

        try:
            # --- extract ---------------------------------------------------
            self._jobs.set_stage(job_id, JobStage.EXTRACTING)
            extracted = self._extract(file_record.file_type, file_record.original_path)

            # --- clean -----------------------------------------------------
            # Strips running headers/footers. Without this, a header repeated on every
            # page appears in every chunk and every chunk's embedding drifts toward it.
            extracted = clean_extracted(extracted)
            self._files.save_extraction(job.file_id, extracted)

            if extracted.char_count < MIN_TEXT_CHARS:
                self._route_to_review(job_id, job.file_id, reason="low text")
                return

            # --- chunk -----------------------------------------------------
            self._jobs.set_stage(job_id, JobStage.EMBEDDING)
            chunks = chunk_extracted(extracted)

            # --- embed -----------------------------------------------------
            # One vector per chunk (retrieval + citations), plus one for the document
            # head (classification). Two different jobs, two different granularities.
            chunk_vectors = self._embedder.embed([c.text for c in chunks]) if chunks else []
            doc_vector = self._embedder.embed([extracted.text[:CLASSIFY_HEAD_CHARS]])[0]

            # --- classify --------------------------------------------------
            self._jobs.set_stage(job_id, JobStage.CLASSIFYING)
            doc = EmbeddedDocument(
                file_id=job.file_id,
                file_type=file_record.file_type,
                text=extracted.text,
                vector=doc_vector,
            )
            classification = self._classifier.classify(doc)
            decision = self._gate.decide(classification)
            self._files.save_classification(job.file_id, classification, decision)

            logger.info(
                "%s → %s (%.2f, %s) [%d chunks]",
                file_record.filename,
                classification.label,
                classification.score,
                decision.value,
                len(chunks),
            )

            # --- organize (auto only, and never while a plan is pending) ----
            if decision is Decision.AUTO and not self._is_being_planned(file_record.original_path):
                self._jobs.set_stage(job_id, JobStage.ORGANIZING)
                dest, _ = self._organizer.organize(
                    job.file_id, file_record.original_path, classification
                )
                self._files.set_organized_path(job.file_id, dest)

            # --- persist ----------------------------------------------------
            # Chunks are indexed even for Review Queue items: a file you have not filed
            # yet is still a file you should be able to search and ask about.
            self._jobs.set_stage(job_id, JobStage.PERSISTING)
            self._persist_chunks(
                job.file_id, file_record.filename, chunks, chunk_vectors, classification.label
            )

            self._files.mark_processed(job.file_id)
            self._jobs.finish(job_id)

        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            self._jobs.fail(job_id, f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    def _extract(self, file_type: FileType, path: Path) -> ExtractedText:
        extractor = self._extractors.find(path, file_type)
        if extractor is not None:
            extracted = extractor.extract(path)
            if extracted.char_count >= MIN_TEXT_CHARS:
                return extracted
            # Fall through: a PDF that yields no selectable text is a scan.

        # Image-based, or an extractor that came up empty → the OCR seam.
        if self._settings.ocr_enabled and self._ocr.available():
            result = self._ocr.ocr(path)
            return ExtractedText(
                pages=result.pages,
                is_image_based=True,
                ocr_used=True,
                ocr_engine=result.engine,
                ocr_confidence=result.confidence,
            )

        return ExtractedText(
            pages=[],
            is_image_based=file_type in (FileType.IMAGE, FileType.PDF),
            ocr_used=False,
        )

    def _route_to_review(self, job_id: str, file_id: str, reason: str) -> None:
        logger.info("Routing to Review Queue (%s): file %s", reason, file_id)
        self._files.save_classification(
            file_id,
            Classification(label=UNCLASSIFIED, kind=CategoryKind.GENERAL, score=0.0),
            Decision.REVIEW,
        )
        self._files.mark_processed(file_id)
        self._jobs.finish(job_id)

    def _persist_chunks(
        self,
        file_id: str,
        filename: str,
        chunks: list[Chunk],
        chunk_vectors: list[list[float]],
        label: str,
    ) -> None:
        if not chunks:
            return

        chunk_ids = self._chunks.replace_for_file(
            file_id, [(c.ordinal, c.page, c.text) for c in chunks]
        )

        # Stale vectors from a previous run of this file must go, or retrieval will serve
        # both the old and the new chunks.
        self._vectors.delete(vectors.DOC_CHUNKS, chunk_ids)
        self._vectors.upsert(
            collection=vectors.DOC_CHUNKS,
            ids=chunk_ids,
            vectors=chunk_vectors,
            metadata=[
                {
                    "file_id": file_id,
                    "filename": filename,
                    "page": chunk.page,
                    "label": label,
                }
                for chunk in chunks
            ],
        )
        self._embeddings.record(
            file_id=file_id,
            kind="doc",
            collection=vectors.DOC_CHUNKS,
            vector_ref=f"{file_id}::*",
            dim=len(chunk_vectors[0]) if chunk_vectors else 0,
        )
