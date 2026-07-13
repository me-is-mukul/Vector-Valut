"""The rail is not a reset button.

Every rail click is a full page navigation and NiceGUI rebuilds the page from scratch —
these tests pin down that the conversation, the composer draft, and the image results all
survive the round trip. Driven through NiceGUI's simulated user: real pages, real event
handlers, no browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nicegui import ui
from nicegui.testing import User

from osdc.config.settings import Settings
from osdc.container import Container
from osdc.domain.enums import FileAction
from osdc.pipeline.embed.hash_embedder import HashEmbedder
from osdc.services.images import ImageHit
from osdc.ui.pages import register_pages
from osdc.ui.state import STATE


@pytest.fixture
def container(data_dir: Path, library_root: Path, tmp_path: Path) -> Container:
    settings = Settings(
        onboarded=True,
        library_root=library_root,
        watched_folders=[],
        file_action=FileAction.COPY,
        scan_on_startup=False,
        use_real_embeddings=False,
        worker_count=1,
    )
    return Container(
        settings=settings,
        db_url=f"sqlite:///{tmp_path / 'ui.sqlite3'}",
        embedder=HashEmbedder(),
    )


@pytest.fixture(autouse=True)
def fresh_state() -> None:
    STATE.reset()


async def test_chat_transcript_survives_switching_tabs(user: User, container: Container) -> None:
    register_pages(container)

    await user.open("/")
    await user.should_see("What can I help you find?")

    user.find(marker="composer").type("what is in my library?")
    user.find(marker="send").click()
    # Empty library → retrieval finds nothing → the grounded refusal, no LLM call.
    await user.should_see("what is in my library?")
    await user.should_see("couldn't find anything")

    await user.open("/library")
    await user.should_see("Library")

    await user.open("/")
    # The conversation is still there — and the welcome hero is not.
    await user.should_see("what is in my library?")
    await user.should_see("couldn't find anything")
    await user.should_not_see("What can I help you find?")


async def test_composer_draft_survives_switching_tabs(user: User, container: Container) -> None:
    register_pages(container)

    await user.open("/")
    user.find(marker="composer").type("half a thought")

    await user.open("/settings")
    await user.should_see("Settings")

    await user.open("/")
    composer = user.find(marker="composer").elements.pop()
    assert composer.value == "half a thought"  # type: ignore[attr-defined]


async def test_clear_chat_brings_the_welcome_back(user: User, container: Container) -> None:
    register_pages(container)

    await user.open("/")
    user.find(marker="composer").type("what is in my library?")
    user.find(marker="send").click()
    # Wait for the answer: clearing is refused while a question is in flight.
    await user.should_see("couldn't find anything")

    user.find(marker="clear-chat").click()
    await user.should_see("What can I help you find?")
    await user.should_not_see("what is in my library?")
    assert STATE.chat.entries == []

    # And the clean slate survives navigation too.
    await user.open("/library")
    await user.open("/")
    await user.should_see("What can I help you find?")


async def test_reset_database_forgets_everything(user: User, container: Container) -> None:
    register_pages(container)

    # Something to forget: a photo in the image index.
    container.vector_store.upsert(
        "image_visual", ["h1"], [[1.0, 0.0]], [{"path": "C:/nowhere/red.jpg"}]
    )
    assert container.images.count() == 1

    await user.open("/settings")
    user.find(marker="reset-db").click()
    await user.should_see("Reset the database?")
    user.find("Reset everything").click()
    # The reset drops tables and re-seeds the knowledge base; give it real time
    # instead of should_see's default three quick attempts.
    await user.should_see("forgotten everything", retries=100)

    assert container.images.count() == 0
    stats = await container.library.stats()
    assert stats.total_files == 0
    # The Subject Knowledge Base came back — classification still has prototypes.
    assert container.vector_store.count("subject_kb") > 0


async def test_image_results_survive_switching_tabs(user: User, container: Container) -> None:
    register_pages(container)
    STATE.images.query = "a red square"
    STATE.images.searched_query = "a red square"
    STATE.images.hits = [ImageHit(path=Path("C:/nowhere/red.jpg"), score=0.31)]

    await user.open("/images")
    await user.should_see("1 photo")
    await user.should_see("red.jpg")

    await user.open("/library")
    await user.open("/images")
    await user.should_see("1 photo")

    box = user.find(kind=ui.input).elements.pop()
    assert box.value == "a red square"
