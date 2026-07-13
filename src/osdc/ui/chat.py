"""The chat pane — the whole app, really.

Everything is reachable by talking: ask a question and it answers from your documents; drop
a folder and it proposes how to sort it; describe a photo and it shows you the photo.

The router below is deliberately dumb — a few keyword patterns — and that is a choice, not
a shortcut. Asking the LLM to first classify your intent would add a whole model round-trip
(seconds) before anything visible happens, and would occasionally decide that "find my
invoice" means image search. A boring `if` chain is faster and never surprises anyone.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from nicegui import ui

from osdc.container import Container
from osdc.domain.models import OrganizePlan
from osdc.ui import components

logger = logging.getLogger(__name__)

_IMAGE_INTENT = re.compile(
    r"\b(photo|photos|picture|pictures|image|images|pic|pics|screenshot|screenshots)\b",
    re.IGNORECASE,
)
_ORGANIZE_INTENT = re.compile(r"\b(organi[sz]e|sort|tidy|clean up|clear out|file)\b", re.IGNORECASE)

Emit = Callable[[], Awaitable[None]]


class ChatPane:
    def __init__(self, container: Container) -> None:
        self.c = container
        self.thread: ui.column
        self.composer: ui.textarea
        self._plan: OrganizePlan | None = None
        self._busy = False

    # ------------------------------------------------------------------
    def build(self) -> None:
        with ui.column().classes("w-full h-full items-center gap-0"):
            self.thread = ui.column().classes(
                "w-full max-w-3xl flex-grow gap-6 px-6 py-8 overflow-y-auto"
            )
            with self.thread:
                self._welcome()

            with ui.column().classes("w-full max-w-3xl px-6 pb-6 gap-2"):
                self._composer()

    def _welcome(self) -> None:
        with ui.column().classes("w-full items-center gap-8 mt-24"):
            ui.label("What can I help you find?").classes("text-3xl font-semibold")
            ui.label("Everything stays on your machine.").classes("dim text-sm -mt-6")

            with ui.grid(columns=2).classes("w-full gap-3 mt-4"):
                for icon, title, sub, prompt in (
                    (
                        "folder_open",
                        "Organize a folder",
                        "Read every file and propose where it goes",
                        "organize my Downloads folder",
                    ),
                    (
                        "search",
                        "Ask your documents",
                        "Answers with the file and page cited",
                        "what are my notes on paging?",
                    ),
                    (
                        "image_search",
                        "Find a photo",
                        "Describe what is in it",
                        "photos of a man holding a baby",
                    ),
                    (
                        "history",
                        "Recent activity",
                        "What got filed while you were away",
                        "what did you file recently?",
                    ),
                ):
                    with (
                        ui.column()
                        .classes("chip gap-1")
                        .on("click", lambda p=prompt: self._submit(p))
                    ):
                        ui.icon(icon).classes("text-lg").style("color: var(--accent)")
                        ui.label(title).classes("text-sm font-medium")
                        ui.label(sub).classes("dim text-xs")

    def _composer(self) -> None:
        with ui.column().classes("composer w-full px-4 py-3 gap-2"):
            self.composer = (
                ui.textarea(placeholder="Ask anything, or give me a folder to organize…")
                .props("borderless autogrow input-class=text-sm dense")
                .classes("w-full")
            )
            self.composer.on("keydown.enter.prevent", self._on_enter)

            with ui.row().classes("w-full items-center justify-between"):
                with ui.row().classes("items-center gap-1"):
                    ui.button(icon="folder_open", on_click=self._pick_folder).props(
                        "flat dense round size=sm"
                    ).classes("dim").tooltip("Choose a folder to organize")
                    ui.button(icon="image", on_click=self._pick_image_folder).props(
                        "flat dense round size=sm"
                    ).classes("dim").tooltip("Index a folder of photos")
                ui.button(icon="arrow_upward", on_click=self._on_enter).props(
                    "round dense unelevated size=sm"
                )

    # ------------------------------------------------------------------
    async def _on_enter(self) -> None:
        text = (self.composer.value or "").strip()
        if text:
            await self._submit(text)

    async def _submit(self, text: str) -> None:
        if self._busy:
            return
        self._busy = True
        self.composer.value = ""

        try:
            self.thread.clear() if not self.thread.default_slot.children else None
            with self.thread:
                components.user_message(text)
            await self._route(text)
        finally:
            self._busy = False
        await self._scroll()

    async def _route(self, text: str) -> None:
        folder = _folder_in(text)

        if _IMAGE_INTENT.search(text):
            await self._image_search(text)
        elif _ORGANIZE_INTENT.search(text) or folder:
            await self._organize(text, folder)
        else:
            await self._ask(text)

    # --- the three things it can do ------------------------------------
    async def _ask(self, question: str) -> None:
        with self.thread:
            thinking = components.thinking("Reading your documents…")
        answer = await self.c.rag.ask(question)
        thinking.delete()
        with self.thread:
            components.answer(answer)

    async def _image_search(self, query: str) -> None:
        if self.c.images.count() == 0:
            with self.thread:
                components.notice(
                    "No photos indexed yet.",
                    "Pick a folder of images first — the picture button below, or Images in "
                    "the sidebar. I look at every photo once, then you can find them by "
                    "describing what is in them.",
                )
            return

        with self.thread:
            thinking = components.thinking("Looking at your photos…")
        hits = await self.c.images.search(_strip_intent(query))
        thinking.delete()
        with self.thread:
            components.image_results(hits, query)

    async def _organize(self, text: str, folder: Path | None) -> None:
        if folder is None:
            with self.thread:
                components.notice(
                    "Which folder?",
                    "Use the folder button below, or name a path — "
                    'for example "organize C:\\Users\\me\\Downloads".',
                )
            return

        if not folder.is_dir():
            with self.thread:
                components.notice("Can't find that folder.", str(folder))
            return

        with self.thread:
            thinking = components.thinking(f"Reading everything in {folder.name}…")

        try:
            await self.c.planner.index_folder(folder)
            await self._drain()

            thinking.delete()
            with self.thread:
                thinking = components.thinking(f"Working out where {folder.name} should go…")
            plan = await self.c.planner.build_plan(folder)
        except Exception as exc:
            thinking.delete()
            logger.exception("Planning failed")
            with self.thread:
                components.notice("That didn't work.", str(exc))
            return

        thinking.delete()
        self._plan = plan
        with self.thread:
            components.plan_preview(plan, on_apply=self._apply_plan, on_cancel=self._cancel_plan)
        await self._scroll()

    # --- plan actions ---------------------------------------------------
    async def _apply_plan(self) -> None:
        plan = self._plan
        if plan is None:
            return
        self._plan = None

        with self.thread:
            thinking = components.thinking("Filing…")
        moved, errors = await self.c.planner.apply_plan(plan)
        self.c.planner.release(plan.source_folder)
        thinking.delete()

        with self.thread:
            components.plan_applied(moved, errors, on_undo=lambda: self._undo_plan(plan))
        await self._scroll()

    async def _cancel_plan(self) -> None:
        plan, self._plan = self._plan, None
        if plan is not None:
            self.c.planner.release(plan.source_folder)
        with self.thread:
            components.notice("Left everything where it was.", "")
        await self._scroll()

    async def _undo_plan(self, plan: OrganizePlan) -> None:
        with self.thread:
            thinking = components.thinking("Putting everything back…")
        undone = await self.c.planner.undo_plan(plan)
        thinking.delete()
        with self.thread:
            components.notice(
                f"Put {undone} file{'s' if undone != 1 else ''} back.",
                "Every move was logged before it happened, so undo is always available.",
            )
        await self._scroll()

    # --- pickers --------------------------------------------------------
    async def _pick_folder(self) -> None:
        folder = await components.choose_folder("Choose a folder to organize")
        if folder:
            await self._submit(f"organize {folder}")

    async def _pick_image_folder(self) -> None:
        folder = await components.choose_folder("Choose a folder of photos")
        if not folder:
            return
        with self.thread:
            components.user_message(f"index the photos in {folder}")
            thinking = components.thinking("Looking at every photo… (this takes a moment)")

        count = await self.c.images.index_folder(Path(folder))
        thinking.delete()
        with self.thread:
            if count:
                components.notice(
                    f"Looked at {count} photo{'s' if count != 1 else ''}.",
                    'Now describe one — "a man holding a baby", "sunset on a beach".',
                )
            else:
                components.notice("No images in that folder.", str(folder))
        await self._scroll()

    # --- plumbing -------------------------------------------------------
    async def _drain(self, timeout: float = 600.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            stats = await self.c.library.stats()
            if stats.queued == 0 and stats.running == 0 and self.c.queue.depth == 0:
                return
            await asyncio.sleep(0.15)

    async def _scroll(self) -> None:
        await asyncio.sleep(0.05)
        ui.run_javascript(
            "const t = document.querySelector('.overflow-y-auto');"
            "if (t) t.scrollTop = t.scrollHeight;"
        )


def _folder_in(text: str) -> Path | None:
    """Pull a path out of "organize C:\\Users\\me\\Downloads"."""
    match = re.search(r"([A-Za-z]:\\[^\"'\n]+|/(?:[^/\0\"'\n]+/?)+)", text)
    if match:
        candidate = Path(match.group(1).strip().rstrip("."))
        if candidate.is_dir():
            return candidate

    # "organize my Downloads folder" — resolve a bare name against the home directory.
    named = re.search(r"\b(downloads|desktop|documents|pictures)\b", text, re.IGNORECASE)
    if named:
        candidate = Path.home() / named.group(1).capitalize()
        if candidate.is_dir():
            return candidate
    return None


def _strip_intent(query: str) -> str:
    """ "show me photos of a man lifting a baby" → "a man lifting a baby".

    CLIP was trained on captions, not on requests. Leaving "show me photos of" in front
    measurably shifts the vector away from the thing actually being described.
    """
    cleaned = re.sub(
        r"^\s*(show|find|get|search|give)\s+(me\s+)?(all\s+)?"
        r"(the\s+)?(photos?|pictures?|images?|pics?|screenshots?)\s*(of|with|showing|that\s+have)?\s*",
        "",
        query,
        flags=re.IGNORECASE,
    )
    return cleaned.strip() or query
