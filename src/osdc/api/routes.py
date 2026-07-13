"""FastAPI routers — thin. No logic here; it all lives in ``services/``.

These exist so the core is drivable without the UI (scripts, tests, the eventual
hosted mode), which is the practical proof that the UI really is swappable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from osdc.container import Container
from osdc.domain.models import OrganizePlan
from osdc.setup import hardware

router = APIRouter(prefix="/api", tags=["osdc"])

_container: Container | None = None


def bind(container: Container) -> None:
    global _container
    _container = container


def _c() -> Container:
    if _container is None:
        raise HTTPException(status_code=503, detail="Application still starting")
    return _container


@router.get("/health")
async def health() -> dict[str, Any]:
    container = _c()
    stats = await container.library.stats()
    return {
        "status": "ok",
        "files": stats.total_files,
        "queued": stats.queued,
        "running": stats.running,
        "failed": stats.failed,
        "review": stats.review_count,
        "queue_depth": container.queue.depth,
    }


@router.get("/files")
async def list_files(limit: int = 200) -> list[dict[str, Any]]:
    records = await _c().library.list_files(limit)
    return [r.model_dump(mode="json") for r in records]


@router.get("/review")
async def review_queue(limit: int = 200) -> list[dict[str, Any]]:
    records = await _c().library.review_queue(limit)
    return [r.model_dump(mode="json") for r in records]


@router.get("/jobs")
async def list_jobs(limit: int = 100) -> list[dict[str, Any]]:
    records = await _c().library.recent_jobs(limit)
    return [r.model_dump(mode="json") for r in records]


@router.get("/search")
async def search(q: str, k: int = 10) -> list[dict[str, Any]]:
    results = await _c().search.search(q, k)
    return [
        {
            "file": r.file.model_dump(mode="json"),
            "score": r.score,
            "page": r.page,
            "excerpt": r.excerpt,
        }
        for r in results
    ]


@router.post("/chat")
async def chat(payload: dict[str, str]) -> dict[str, Any]:
    question = payload.get("question", "")
    answer = await _c().rag.ask(question)
    return {
        "answer": answer.text,
        "grounded": answer.grounded,
        "sources": [
            {
                "index": s.index,
                "filename": s.file.filename,
                "page": s.page,
                "score": s.score,
                "path": str(s.file.organized_path or s.file.original_path),
            }
            for s in answer.sources
        ],
    }


@router.get("/llm")
async def llm_status() -> dict[str, Any]:
    container = _c()
    available = await asyncio.to_thread(container.llm.available)
    return {"model": container.llm.model, "available": available}


@router.post("/organize/plan")
async def build_plan(payload: dict[str, str]) -> dict[str, Any]:
    """Index a folder and propose where everything goes. Moves nothing."""
    container = _c()
    folder = Path(payload["folder"])
    await container.planner.index_folder(folder)
    plan = await container.planner.build_plan(folder)
    return plan.model_dump(mode="json")


@router.post("/organize/apply")
async def apply_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute an approved plan through the logged, reversible organizer."""
    container = _c()
    plan = OrganizePlan.model_validate(payload)
    moved, errors = await container.planner.apply_plan(plan)
    container.planner.release(plan.source_folder)
    return {"moved": moved, "errors": errors}


@router.post("/images/index")
async def index_images(payload: dict[str, str]) -> dict[str, Any]:
    result = await _c().images.index_folder(Path(payload["folder"]))
    return {
        "found": result.found,
        "indexed": result.indexed,
        "failed": result.failed,
        "total": _c().images.count(),
    }


@router.get("/images/search")
async def search_images(q: str, k: int = 24) -> list[dict[str, Any]]:
    hits = await _c().images.search(q, k)
    return [{"path": str(h.path), "filename": h.path.name, "score": h.score} for h in hits]


@router.get("/hardware")
async def hardware_info() -> dict[str, Any]:
    hw = hardware.detect()
    choice = hardware.recommend(hw)
    return {
        "summary": hardware.summary(hw),
        "gpu": hw.gpu_name,
        "vram_gb": hw.vram_gb,
        "ram_gb": hw.ram_gb,
        "recommended_model": choice.name,
        "recommended_size_gb": choice.size_gb,
        "rationale": choice.rationale,
    }


@router.post("/files/{file_id}/undo")
async def undo_filing(file_id: str) -> dict[str, str]:
    try:
        restored = await _c().feedback.undo_last_for_file(file_id)
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"restored_to": str(restored)}


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    return _c().settings.model_dump(mode="json")
