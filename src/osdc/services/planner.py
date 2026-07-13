"""Folder → knowledge base → LLM segregation plan.

This is the flow behind "drop a folder and it sorts itself out", and it is deliberately
built in two halves:

**1. Embeddings do the reading.** Every file is extracted, chunked, embedded and indexed —
that is the knowledge base, and it is what makes the folder searchable and chattable
immediately. The semester-aware classifier also proposes a label for each file.

**2. The LLM does the judgement.** It never sees raw file bytes; it sees a compact card per
file (name, type, the classifier's guess and its runners-up, a content snippet) plus the
folder taxonomy already in use, and it returns a destination for each one. It can overrule
the classifier, invent a folder the taxonomy is missing, or refuse and send a file to
review.

**What it cannot do is touch the disk.** It emits a `PlanItem` — data, not an instruction.
It cannot produce an `rm`, cannot mangle a filename containing a quote, and cannot escape
the library root (the Organizer re-checks). The user sees the whole plan, approves it, and
the existing write-ahead, undoable Organizer carries it out. Same intelligence as letting
it write shell commands; none of the ways that ends in lost files.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from osdc.config.settings import Settings
from osdc.domain.enums import UNCLASSIFIED, Decision
from osdc.domain.models import OrganizePlan, PlanItem
from osdc.domain.ports import LlmClient
from osdc.pipeline.ingest.ignore import should_ignore
from osdc.pipeline.organize.organizer import Organizer
from osdc.services.ingestion import IngestionService
from osdc.services.processing import ProcessingService
from osdc.storage.repositories import FileRepository, MoveLogRepository

logger = logging.getLogger(__name__)

#: How many files the model is asked to place at once. Too many and a 7B model starts
#: dropping entries from the middle of the list; too few and a 300-file folder takes
#: forever. Twenty is comfortable at 8k context.
BATCH_SIZE = 20

#: The model reads this much of each document. The first page is where a document
#: announces what it is; the other forty pages just cost context.
SNIPPET_CHARS = 700

REVIEW = "Review"

PLANNER_SYSTEM = """You are a meticulous librarian organising a user's files.

For every file you are given, choose the folder it belongs in.

Rules:
1. Prefer a folder that already exists in the taxonomy. Only invent a new one when nothing \
existing fits, and then keep it short and general ("General/Recipes", not \
"General/Sourdough Bread Recipes From March").
2. Academic coursework goes under "Academics/Semester N/<Subject>". Use the subject the \
CONTENT is about, not the filename.
3. Personal and administrative documents go under "General/<Category>".
4. If you genuinely cannot tell what a file is, put it in "Review". That is a valid, \
respectable answer — a wrong folder is worse than an honest one.
5. The classifier's suggestion is a hint, not an order. Overrule it when the snippet \
clearly says otherwise.
6. Give a short, concrete reason quoting what in the content decided it.

Answer for every file you were given. Do not skip any."""

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string"},
                    "folder": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["file_id", "folder", "reason", "confidence"],
            },
        }
    },
    "required": ["assignments"],
}


class PlanningService:
    def __init__(
        self,
        settings: Settings,
        files: FileRepository,
        moves: MoveLogRepository,
        ingestion: IngestionService,
        processing: ProcessingService,
        organizer: Organizer,
        llm: LlmClient,
    ) -> None:
        self._settings = settings
        self._files = files
        self._moves = moves
        self._ingestion = ingestion
        self._processing = processing
        self._organizer = organizer
        self._llm = llm

    # --- step 1: build the knowledge base ---------------------------------
    async def index_folder(self, folder: Path) -> int:
        """Push every file in the folder through the pipeline. Returns how many were queued.

        Auto-filing is suppressed for this folder while the plan is pending — the whole
        promise of this flow is that the user sees the plan before anything moves.
        """
        if not folder.is_dir():
            raise ValueError(f"Not a folder: {folder}")

        self._processing.planning_roots.add(folder)

        queued = 0
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or should_ignore(path, self._settings.library_root):
                continue
            if await self._ingestion.handle_path(path) is not None:
                queued += 1

        logger.info("Indexed %d file(s) from %s", queued, folder)
        return queued

    def release(self, folder: Path) -> None:
        """Plan applied or abandoned — this folder goes back to normal auto-filing."""
        self._processing.planning_roots.discard(folder)

    # --- step 2: ask the model where everything goes ----------------------
    async def build_plan(self, folder: Path) -> OrganizePlan:
        records = await asyncio.to_thread(self._files.list_under, folder)

        candidates = [r for r in records if r.extracted_text]
        unreadable = [r.filename for r in records if not r.extracted_text]

        if not candidates:
            return OrganizePlan(source_folder=folder, items=[], unreadable=unreadable)

        if not await asyncio.to_thread(self._llm.available):
            logger.warning("LLM unavailable — falling back to the classifier's own labels")
            return OrganizePlan(
                source_folder=folder,
                items=[self._fallback_item(r) for r in candidates],
                unreadable=unreadable,
            )

        taxonomy = await asyncio.to_thread(self._existing_taxonomy)

        items: list[PlanItem] = []
        for start in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[start : start + BATCH_SIZE]
            items.extend(await asyncio.to_thread(self._plan_batch, batch, taxonomy))
            # Folders the model invented become part of the taxonomy for the next batch,
            # or a 300-file folder ends up with "Bills", "Billing" and "Invoices".
            taxonomy = sorted(set(taxonomy) | {i.destination for i in items})

        return OrganizePlan(source_folder=folder, items=items, unreadable=unreadable)

    def _plan_batch(self, batch: list[Any], taxonomy: list[str]) -> list[PlanItem]:
        cards = "\n\n".join(self._card(r) for r in batch)
        known = "\n".join(f"  {t}" for t in taxonomy) or "  (none yet — you are starting fresh)"
        prompt = (
            f"Folders already in use:\n{known}\n\n"
            f"Files to place:\n\n{cards}\n\n"
            f"Return an assignment for each of the {len(batch)} files."
        )

        try:
            raw = self._llm.generate_json(prompt, PLAN_SCHEMA, system=PLANNER_SYSTEM)
        except Exception:
            logger.exception("Planner call failed; falling back to classifier labels")
            return [self._fallback_item(r) for r in batch]

        by_id = {
            str(a.get("file_id")): a for a in raw.get("assignments", []) if isinstance(a, dict)
        }

        items: list[PlanItem] = []
        for record in batch:
            assignment = by_id.get(record.id)
            if assignment is None:
                # The model dropped this file. Do not guess on its behalf.
                logger.warning("Planner omitted %s — falling back", record.filename)
                items.append(self._fallback_item(record))
                continue

            folder = str(assignment.get("folder") or REVIEW).strip() or REVIEW
            items.append(
                PlanItem(
                    file_id=record.id,
                    filename=record.filename,
                    source_path=record.original_path,
                    destination=folder,
                    reason=str(assignment.get("reason") or "").strip(),
                    confidence=_clamp(assignment.get("confidence")),
                    classifier_label=record.label,
                    skipped=folder.strip().lower() == REVIEW.lower(),
                )
            )
        return items

    def _card(self, record: Any) -> str:
        snippet = " ".join((record.extracted_text or "").split())[:SNIPPET_CHARS]
        guess = record.label or UNCLASSIFIED
        score = record.confidence_score or 0.0
        return (
            f"file_id: {record.id}\n"
            f"name: {record.filename}\n"
            f"type: {record.file_type.value}\n"
            f"classifier suggests: {guess} (confidence {score:.2f})\n"
            f"content: {snippet}"
        )

    def _fallback_item(self, record: Any) -> PlanItem:
        """No LLM, or the model dropped this file. Use the classifier's own answer —
        conservatively, so anything it was unsure about goes to Review rather than a guess."""
        unsure = record.decision is not Decision.AUTO or not record.label
        destination = (
            REVIEW
            if unsure
            else self._organizer.destination_dir(_as_classification(record))
            .relative_to(self._settings.library_root)
            .as_posix()
        )
        return PlanItem(
            file_id=record.id,
            filename=record.filename,
            source_path=record.original_path,
            destination=destination,
            reason="Local model unavailable — used the classifier's own label.",
            confidence=record.confidence_score or 0.0,
            classifier_label=record.label,
            skipped=destination == REVIEW,
        )

    def _existing_taxonomy(self) -> list[str]:
        root = self._settings.library_root
        if not root.is_dir():
            return []
        folders = {
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_dir() and any(c.is_file() for c in p.iterdir())
        }
        return sorted(folders)

    # --- step 3: apply, through the safe engine ---------------------------
    async def apply_plan(self, plan: OrganizePlan) -> tuple[int, list[str]]:
        """Execute an approved plan. Returns (moved, errors)."""
        return await asyncio.to_thread(self._apply_sync, plan)

    def _apply_sync(self, plan: OrganizePlan) -> tuple[int, list[str]]:
        moved = 0
        errors: list[str] = []

        for item in plan.movable:
            if not item.source_path.exists():
                errors.append(f"{item.filename}: no longer there")
                continue
            try:
                dest, _ = self._organizer.organize_to(
                    item.file_id, item.source_path, item.destination
                )
            except (OSError, ValueError) as exc:
                logger.exception("Failed to file %s", item.filename)
                errors.append(f"{item.filename}: {exc}")
                continue

            self._files.set_organized_path(item.file_id, dest)
            moved += 1

        logger.info("Applied plan: %d moved, %d error(s)", moved, len(errors))
        return moved, errors

    async def undo_plan(self, plan: OrganizePlan) -> int:
        """Reverse everything the plan did. Every move was logged, so this always works."""
        return await asyncio.to_thread(self._undo_sync, plan)

    def _undo_sync(self, plan: OrganizePlan) -> int:
        undone = 0
        for item in plan.movable:
            record = self._moves.latest_for_file(item.file_id)
            if record is None:
                continue
            try:
                self._organizer.undo(record.id)
            except (OSError, ValueError) as exc:
                logger.warning("Could not undo %s: %s", item.filename, exc)
                continue
            self._files.set_organized_path(item.file_id, None)
            undone += 1
        return undone


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _as_classification(record: Any) -> Any:
    from osdc.domain.enums import CategoryKind
    from osdc.domain.models import Classification

    return Classification(
        label=record.label or UNCLASSIFIED,
        kind=record.kind or CategoryKind.GENERAL,
        score=record.confidence_score or 0.0,
        semester=record.semester,
    )
