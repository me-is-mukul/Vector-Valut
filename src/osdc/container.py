"""Composition root.

Every swappable implementation is chosen HERE and nowhere else.

Look at what changed between Phase 0 and Phase 3+5: the extractor list, the embedder, and
the classifier — three lines. ``ProcessingService``, ``SearchService``, the organizer, the
repositories and the UI were not touched. That is the payoff the walking skeleton was
built for (roadmap.md §1).

If you ever find yourself importing a concrete pipeline class outside this file, the seam
has leaked.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from osdc.config import paths
from osdc.config.settings import Settings, load_settings
from osdc.domain.ports import Classifier, LlmClient, OcrEngineProtocol, TextEmbedder, VectorStore
from osdc.pipeline.classify.gate import ConfidenceGate
from osdc.pipeline.classify.keyword import KeywordClassifier
from osdc.pipeline.classify.semester_aware import SemesterAwareClassifier
from osdc.pipeline.embed.clip_embedder import ClipEmbedder
from osdc.pipeline.embed.hash_embedder import HashEmbedder
from osdc.pipeline.extract.ocr import NullOcr
from osdc.pipeline.extract.office import DocxExtractor, PptxExtractor
from osdc.pipeline.extract.pdf import PdfExtractor
from osdc.pipeline.extract.plaintext import PlainTextExtractor
from osdc.pipeline.extract.registry import ExtractorRegistry
from osdc.pipeline.ingest.watcher import FolderWatcher
from osdc.pipeline.llm.ollama_client import OllamaClient
from osdc.pipeline.organize.organizer import Organizer
from osdc.services.feedback import FeedbackService
from osdc.services.images import ImageService
from osdc.services.ingestion import IngestionService
from osdc.services.knowledge import KnowledgeBaseService
from osdc.services.library import LibraryService
from osdc.services.planner import PlanningService
from osdc.services.processing import ProcessingService
from osdc.services.queue import AsyncioTaskQueue
from osdc.services.rag import RagService
from osdc.services.search import SearchService
from osdc.storage import vectors as vector_collections
from osdc.storage.db import Database
from osdc.storage.repositories import (
    ChunkRepository,
    CorrectionRepository,
    EmbeddingRepository,
    FileRepository,
    JobRepository,
    MoveLogRepository,
)
from osdc.storage.vectors import build_vector_store

logger = logging.getLogger(__name__)


def _build_embedder(settings: Settings) -> TextEmbedder:
    """Phase 3's swap. Falls back to the Phase 0 stub if the [ml] extra is missing."""
    if not settings.use_real_embeddings:
        logger.warning("Real embeddings disabled — using HashEmbedder (no semantics)")
        return HashEmbedder()
    try:
        from osdc.pipeline.embed.sentence_embedder import SentenceTransformerEmbedder
    except ImportError:
        logger.warning(
            "sentence-transformers not installed — falling back to HashEmbedder. "
            'Install with: pip install -e ".[ml]"'
        )
        return HashEmbedder()
    return SentenceTransformerEmbedder(model_name=settings.embedding_model)


class Container:
    """Owns every long-lived object and the app's start/stop lifecycle."""

    def __init__(
        self,
        settings: Settings | None = None,
        db_url: str | None = None,
        embedder: TextEmbedder | None = None,
    ) -> None:
        """``embedder`` is a test seam: loading bge-small takes seconds, and the suite
        would otherwise pay that cost once per Container."""
        paths.ensure_dirs()
        self.settings = settings or load_settings()
        self.settings.library_root.mkdir(parents=True, exist_ok=True)

        # --- infrastructure -------------------------------------------------
        self.db = Database(db_url or paths.db_url())
        self.db.create_all()

        self.files = FileRepository(self.db)
        self.jobs = JobRepository(self.db)
        self.chunks = ChunkRepository(self.db)
        self.moves = MoveLogRepository(self.db)
        self.corrections = CorrectionRepository(self.db)
        self.embeddings = EmbeddingRepository(self.db)

        self.vector_store: VectorStore = build_vector_store(paths.vector_dir())

        # --- the swappable parts --------------------------------------------
        self.extractors = ExtractorRegistry(
            [
                PlainTextExtractor(),
                PdfExtractor(),  # Phase 1
                DocxExtractor(),  # Phase 1
                PptxExtractor(),  # Phase 1
            ]
        )
        self.ocr: OcrEngineProtocol = NullOcr()  # Phase 2: PaddleOcr
        self.embedder: TextEmbedder = embedder or _build_embedder(self.settings)  # Phase 3
        self.llm: LlmClient = OllamaClient(  # Phase 5
            model=self.settings.llm_model, host=self.settings.llm_host
        )

        self.knowledge = KnowledgeBaseService(
            db=self.db, embedder=self.embedder, vector_store=self.vector_store
        )

        # Semester comes from settings if set, else from the curriculum file.
        current_semester = (
            self.settings.current_semester or self.knowledge.curriculum.current_semester
        )

        self.classifier: Classifier = self._build_classifier(current_semester)  # Phase 3

        self.gate = ConfidenceGate(
            academic_threshold=self.settings.academic_threshold,
            general_threshold=self.settings.general_threshold,
            auto_approve=self.settings.auto_approve,
        )
        self.organizer = Organizer(
            library_root=self.settings.library_root,
            action=self.settings.file_action,
            move_log=self.moves,
        )

        # --- services -------------------------------------------------------
        self.processing = ProcessingService(
            settings=self.settings,
            files=self.files,
            jobs=self.jobs,
            chunks=self.chunks,
            embeddings=self.embeddings,
            extractors=self.extractors,
            ocr=self.ocr,
            embedder=self.embedder,
            classifier=self.classifier,
            gate=self.gate,
            organizer=self.organizer,
            vector_store=self.vector_store,
        )
        self.queue = AsyncioTaskQueue(
            handler=self.processing.process, worker_count=self.settings.worker_count
        )
        self.ingestion = IngestionService(
            settings=self.settings, files=self.files, jobs=self.jobs, queue=self.queue
        )
        self.library = LibraryService(files=self.files, jobs=self.jobs)
        self.feedback = FeedbackService(
            files=self.files,
            moves=self.moves,
            corrections=self.corrections,
            organizer=self.organizer,
        )
        self.search = SearchService(
            files=self.files,
            chunks=self.chunks,
            embedder=self.embedder,
            vector_store=self.vector_store,
        )
        self.rag = RagService(
            files=self.files,
            chunks=self.chunks,
            embedder=self.embedder,
            vector_store=self.vector_store,
            llm=self.llm,
            relevance_floor=self.settings.rag_relevance_floor,
            top_k=self.settings.rag_top_k,
        )
        self.planner = PlanningService(
            settings=self.settings,
            files=self.files,
            moves=self.moves,
            ingestion=self.ingestion,
            processing=self.processing,
            organizer=self.organizer,
            llm=self.llm,
        )
        self.images = ImageService(
            embedder=ClipEmbedder(model_name=self.settings.clip_model),
            vector_store=self.vector_store,
            floor=self.settings.image_search_floor,
        )

        self.watcher: FolderWatcher | None = None

    def _build_classifier(self, current_semester: int | None) -> Classifier:
        """Semester-aware if we have real embeddings; keyword rules if we do not.

        The fallback is not decoration: a HashEmbedder has no semantics, so cosine
        similarity against subject descriptions would be meaningless and the
        semester-aware classifier would route documents essentially at random. Keyword
        rules are worse in the limit but honest without a model.
        """
        if isinstance(self.embedder, HashEmbedder):
            logger.warning("No real embeddings — falling back to the keyword classifier")
            return KeywordClassifier(current_semester=current_semester)

        return SemesterAwareClassifier(
            vector_store=self.vector_store,
            current_semester=current_semester,
            min_academic_similarity=self.settings.min_academic_similarity,
            min_general_similarity=self.settings.min_general_similarity,
            temperature=self.settings.softmax_temperature,
        )

    # ------------------------------------------------------------------
    async def start(self) -> None:
        await self.queue.start()

        # The knowledge base must exist before any job is processed, or the very first
        # document is classified against an empty subject collection and lands in Review
        # for no reason.
        await self.knowledge.seed(model_name=self.settings.embedding_model)

        # Crash recovery BEFORE new work is accepted, so a file that was mid-flight when
        # we died gets picked up rather than silently lost (roadmap.md §2.5).
        orphans = await asyncio.to_thread(self.jobs.recover_orphans)
        for job_id in orphans:
            await self.queue.enqueue(job_id)
        if orphans:
            logger.info("Recovered %d interrupted job(s)", len(orphans))

        stranded = await asyncio.to_thread(self.moves.list_pending)
        if stranded:
            # A 'pending' move means we died between logging and finishing the copy. We do
            # not auto-repair; we surface it, because guessing is how you lose files.
            logger.warning(
                "%d file operation(s) were interrupted mid-write; see the move log",
                len(stranded),
            )

        if self.settings.watched_folders:
            self.watcher = FolderWatcher(
                folders=self.settings.watched_folders,
                on_file=self._on_file,
                loop=asyncio.get_running_loop(),
                debounce_seconds=self.settings.debounce_seconds,
            )
            self.watcher.start()

        if self.settings.scan_on_startup:
            await self.ingestion.scan_folders()

    async def stop(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None
        await self.queue.stop()
        self.db.dispose()

    # ------------------------------------------------------------------
    async def reset_data(self) -> None:
        """Forget everything the app has read: file records, chunks, jobs, the search
        index, the photo index, and the undo log. Settings survive, and the user's
        actual files — originals and the organized library — are never touched.

        Refuses while the pipeline is busy: dropping tables under a worker mid-job
        would strand half-written rows in the new schema.
        """
        stats = await self.library.stats()
        busy = self.queue.depth + stats.queued + stats.running
        if busy:
            raise RuntimeError(
                f"Still processing {busy} item(s) — wait for the queue to empty, then retry."
            )

        logger.warning("Resetting all indexed data at the user's request")
        await asyncio.to_thread(self._reset_data_sync)
        # The Subject Knowledge Base must come back immediately, or the next document
        # would be classified against an empty collection and land in Review for no reason.
        await self.knowledge.seed(model_name=self.settings.embedding_model)
        logger.info("Reset complete; knowledge base re-seeded")

    def _reset_data_sync(self) -> None:
        self.db.drop_all()
        self.db.create_all()
        for name in vector_collections.COLLECTIONS:
            self.vector_store.clear(name)

    async def _on_file(self, path: Path) -> None:
        await self.ingestion.handle_path(path)
