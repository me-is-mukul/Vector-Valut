"""RAG grounding — the non-negotiable from roadmap.md Phase 5.

The single most important property of this app's chatbot is that it **cannot answer from
the model's own weights**. The user's library holds their medical records, bank statements
and identity documents; an assistant that confidently invents their contents is worse than
no assistant at all.

So the test that matters most here is not "does it give a good answer". It is
``test_the_llm_is_never_called_when_nothing_is_relevant`` — proof that when retrieval comes
up empty, the model is never even asked.

These use a fake LLM. Whether the real qwen2.5 writes a *good* answer is a separate
question, verified by running it; whether we *let* it answer is a property of this code,
and that is what gets pinned here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from osdc.config.settings import Settings
from osdc.container import Container
from osdc.domain.ports import TextEmbedder
from osdc.services.rag import NO_CONTEXT, RagService


class FakeLlm:
    """Records whether it was called, and with what."""

    model = "fake"

    def __init__(self, reply: str = "Paging is a memory management scheme [1].") -> None:
        self.reply = reply
        self.calls: list[str] = []
        self.systems: list[str | None] = []

    def available(self) -> bool:
        return True

    def generate(self, prompt: str, system: str | None = None) -> str:
        self.calls.append(prompt)
        self.systems.append(system)
        return self.reply


class OfflineLlm(FakeLlm):
    def available(self) -> bool:
        return False


@pytest.fixture
async def indexed(
    data_dir: Path, library_root: Path, watched: Path, tmp_path: Path, embedder: TextEmbedder
) -> Container:
    """A container with one Operating Systems document already through the pipeline."""
    container = Container(
        settings=Settings(
            library_root=library_root,
            watched_folders=[watched],
            scan_on_startup=False,
            worker_count=1,
            stability_poll_seconds=0.01,
            stability_checks=1,
        ),
        db_url=f"sqlite:///{tmp_path / 'rag.sqlite3'}",
        embedder=embedder,
    )
    await container.start()

    source = watched / "os_notes.txt"
    source.write_text(
        "Paging divides memory into fixed-size frames. A page fault occurs when the "
        "requested page is not resident in physical memory, and the operating system must "
        "fetch it from disk. The translation lookaside buffer caches recent translations. "
        "Thrashing happens when the working set exceeds the frames available.",
        encoding="utf-8",
    )
    await container.ingestion.handle_path(source)

    import asyncio

    for _ in range(200):
        stats = await container.library.stats()
        if stats.queued == 0 and stats.running == 0 and container.queue.depth == 0:
            break
        await asyncio.sleep(0.02)

    return container


def _rag(container: Container, llm: FakeLlm) -> RagService:
    return RagService(
        files=container.files,
        chunks=container.chunks,
        embedder=container.embedder,
        vector_store=container.vector_store,
        llm=llm,
        relevance_floor=container.settings.rag_relevance_floor,
    )


# --- THE grounding invariant ------------------------------------------------


async def test_the_llm_is_never_called_when_nothing_is_relevant(indexed: Container) -> None:
    """The library is about operating systems. Ask about something else entirely.

    Nothing clears the relevance floor, so the model must not be invoked at all — not
    invoked and then ignored, not invoked with an empty context. Never asked.
    """
    llm = FakeLlm()
    try:
        answer = await _rag(indexed, llm).ask(
            "What was the winning constructor of the 1987 Formula One season?"
        )

        assert llm.calls == [], "the LLM was asked despite having no grounding"
        assert answer.grounded is False
        assert answer.sources == []
        assert answer.text == NO_CONTEXT
    finally:
        await indexed.stop()


async def test_an_answerable_question_reaches_the_llm_with_sources(indexed: Container) -> None:
    llm = FakeLlm()
    try:
        answer = await _rag(indexed, llm).ask("What happens on a page fault?")

        assert len(llm.calls) == 1, "should have asked the model exactly once"
        assert answer.grounded is True
        assert answer.sources, "an answer with no sources is not grounded"
        assert answer.text == llm.reply
    finally:
        await indexed.stop()


async def test_the_prompt_carries_the_sources_and_the_citation_rule(indexed: Container) -> None:
    llm = FakeLlm()
    try:
        await _rag(indexed, llm).ask("What is thrashing?")

        (prompt,) = llm.calls
        (system,) = llm.systems

        assert "os_notes.txt" in prompt, "the model must see which file it is reading"
        assert "page 1" in prompt, "and which page, or it cannot cite one"
        assert "[1]" in prompt, "sources must be numbered for citation"
        assert "thrashing" in prompt.lower()

        assert system is not None
        assert "ONLY from the numbered sources" in system
        assert "The documents don't cover that." in system
    finally:
        await indexed.stop()


# --- citations --------------------------------------------------------------


async def test_sources_carry_file_and_page(indexed: Container) -> None:
    try:
        answer = await _rag(indexed, FakeLlm()).ask("What does the TLB cache?")

        source = answer.sources[0]
        assert source.index == 1
        assert source.file.filename == "os_notes.txt"
        assert source.page == 1
        assert source.score >= indexed.settings.rag_relevance_floor
        assert "translation" in source.excerpt.lower()
    finally:
        await indexed.stop()


async def test_sources_are_ordered_by_relevance(indexed: Container) -> None:
    try:
        answer = await _rag(indexed, FakeLlm()).ask("page fault")
        scores = [s.score for s in answer.sources]
        assert scores == sorted(scores, reverse=True)
    finally:
        await indexed.stop()


# --- degraded modes ---------------------------------------------------------


async def test_it_says_so_when_ollama_is_down(indexed: Container) -> None:
    """Retrieval worked but the model is unreachable. Say that; do not pretend."""
    llm = OfflineLlm()
    try:
        answer = await _rag(indexed, llm).ask("What is paging?")

        assert llm.calls == []
        assert answer.grounded is False
        assert "Ollama" in answer.text
        assert answer.sources, "we still show what we found, even without the model"
    finally:
        await indexed.stop()


async def test_an_empty_question_is_not_sent_anywhere(indexed: Container) -> None:
    llm = FakeLlm()
    try:
        answer = await _rag(indexed, llm).ask("   ")
        assert llm.calls == []
        assert answer.grounded is False
    finally:
        await indexed.stop()
