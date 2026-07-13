"""The chat pane — the whole app, really.

Everything is reachable by talking: ask a question and it answers from your documents; drop
a folder and it proposes how to sort it; describe a photo and it shows you the photo.

The router below is deliberately dumb — a few keyword patterns — and that is a choice, not
a shortcut. Asking the LLM to first classify your intent would add a whole model round-trip
(seconds) before anything visible happens, and would occasionally decide that "find my
invoice" means image search. A boring `if` chain is faster and never surprises anyone.

The transcript itself lives in ``osdc.ui.state``, not in this pane: the pane is rebuilt on
every rail navigation, and a conversation that evaporates when you glance at the Library is
not a conversation.
"""

from __future__ import annotations

import asyncio
import logging
import re
from functools import partial
from pathlib import Path
from typing import Any

from nicegui import ui

from osdc.container import Container
from osdc.services.images import IndexProgress
from osdc.ui import components
from osdc.ui.state import STATE, ChatEntry

logger = logging.getLogger(__name__)

_IMAGE_INTENT = re.compile(
    r"\b(photo|photos|picture|pictures|image|images|pic|pics|screenshot|screenshots)\b",
    re.IGNORECASE,
)
# "file" and "sort" only count as filing verbs with an object — otherwise every question
# containing "that file" or "what sort of" got hijacked into the organize flow.
_ORGANIZE_INTENT = re.compile(
    r"\b(organi[sz]e|tidy|clean\s?up|clear\s?out"
    r"|sort(?:\s+out)?\s+(?:my|the|this|that|these|those|everything|it\b|out\b)"
    r"|file\s+(?:my|the|this|that|these|those|them|it|everything|away))",
    re.IGNORECASE,
)
_INDEX_INTENT = re.compile(r"\b(index|scan|look\s+at|learn|ingest|add)\b", re.IGNORECASE)


class ChatPane:
    def __init__(self, container: Container) -> None:
        self.c = container
        self.thread: ui.column
        self.composer: ui.textarea
        self._send_button: ui.button | None = None
        self._busy = False
        self._welcome_showing = False

    # ------------------------------------------------------------------
    def build(self) -> None:
        with ui.column().classes("w-full h-full items-center gap-0"):
            self.thread = ui.column().classes(
                "chat-thread w-full max-w-3xl flex-grow gap-6 px-6 py-8 overflow-y-auto"
            )
            with self.thread:
                if STATE.chat.entries:
                    # Coming back from another page: the conversation is still here.
                    for entry in STATE.chat.entries:
                        self._render(entry)
                    ui.timer(0.05, self._scroll_when_connected, once=True)
                else:
                    self._welcome_showing = True
                    self._welcome()

            with ui.column().classes("w-full max-w-3xl px-6 pb-6 gap-2"):
                self._composer()

    def _welcome(self) -> None:
        """The four chips launch their skill directly — a folder picker opens, the
        composer focuses, recent files render. They never type a canned prompt into the
        chat on the user's behalf; watching the app talk to itself is uncanny, and the
        example queries were meaningless against a real library anyway."""
        with ui.column().classes("w-full items-center gap-8 mt-24"):
            ui.label("What can I help you find?").classes("text-3xl font-semibold")
            ui.label("Everything stays on your machine.").classes("dim text-sm -mt-6")

            with ui.grid(columns=2).classes("w-full gap-3 mt-4"):
                for icon, title, sub, action in (
                    (
                        "folder_open",
                        "Organize a folder",
                        "Read every file and propose where it goes",
                        self._pick_folder,
                    ),
                    (
                        "search",
                        "Ask your documents",
                        "Answers with the file and page cited",
                        self._focus_composer,
                    ),
                    (
                        "image_search",
                        "Find a photo",
                        "Describe what is in it",
                        self._photo_skill,
                    ),
                    (
                        "history",
                        "Recent activity",
                        "What got filed while you were away",
                        self._recent_activity,
                    ),
                ):
                    with ui.column().classes("chip gap-1").on("click", action):
                        ui.icon(icon).classes("text-lg").style("color: var(--accent)")
                        ui.label(title).classes("text-sm font-medium")
                        ui.label(sub).classes("dim text-xs")

    def _composer(self) -> None:
        with ui.column().classes("composer w-full px-4 py-3 gap-2"):
            self.composer = (
                ui.textarea(
                    placeholder="Ask anything, or give me a folder to organize…",
                    value=STATE.chat.draft,
                    on_change=lambda e: setattr(STATE.chat, "draft", e.value or ""),
                )
                .props("borderless autogrow input-class=text-sm dense")
                .classes("w-full")
                .mark("composer")
            )
            # Enter sends, Shift+Enter makes a newline. NiceGUI's modifier set has no
            # `.exact`, so the shift check has to happen client-side.
            self.composer.on(
                "keydown.enter",
                self._on_enter,
                js_handler="(e) => { if (!e.shiftKey) { e.preventDefault(); emit(e); } }",
            )

            with ui.row().classes("w-full items-center justify-between"):
                with ui.row().classes("items-center gap-1"):
                    ui.button(icon="folder_open", on_click=self._pick_folder).props(
                        "flat dense round size=sm"
                    ).classes("dim").tooltip("Choose a folder to organize")
                    ui.button(icon="image", on_click=self._pick_image_folder).props(
                        "flat dense round size=sm"
                    ).classes("dim").tooltip("Index a folder of photos")
                    ui.button(icon="delete_sweep", on_click=self._clear_chat).props(
                        "flat dense round size=sm"
                    ).classes("dim").tooltip("Clear the conversation").mark("clear-chat")
                self._send_button = (
                    ui.button(icon="arrow_upward", on_click=self._on_enter)
                    .props("round dense unelevated size=sm")
                    .mark("send")
                )

    # --- transcript: record once, render anywhere -------------------------
    def _begin(self) -> None:
        """First real interaction clears the welcome hero so the conversation owns
        the pane. (0.1.0 had this condition inverted, so the hero never left.)"""
        if self._welcome_showing:
            self.thread.clear()
            self._welcome_showing = False

    def _record(self, kind: str, **data: Any) -> ChatEntry:
        """Append to the durable transcript, then draw it. Recording comes first so the
        conversation survives even if this page has been navigated away from by the time
        a slow answer arrives — it will simply be there when the user comes back."""
        entry = ChatEntry(kind=kind, data=data)
        STATE.chat.entries.append(entry)
        # A day of heavy use must not grow into an unbounded replay; image results in
        # particular hold real hit lists.
        if len(STATE.chat.entries) > 400:
            del STATE.chat.entries[:-400]
        try:
            with self.thread:
                self._render(entry)
        except Exception:
            logger.debug("Could not draw a chat entry (page likely navigated away)")
        return entry

    def _render(self, entry: ChatEntry) -> None:
        d = entry.data
        if entry.kind == "user":
            components.user_message(d["text"])
        elif entry.kind == "notice":
            components.notice(d["title"], d.get("detail", ""))
        elif entry.kind == "answer":
            components.answer(d["answer"])
        elif entry.kind == "images":
            components.image_results(d["hits"], d["query"])
        elif entry.kind == "recent":
            components.recent_files(d["files"], review_count=d["review_count"])
        elif entry.kind == "plan":
            components.plan_preview(
                d["plan"],
                on_apply=partial(self._apply_plan, entry),
                on_cancel=partial(self._cancel_plan, entry),
                pending=d.get("state") == "pending",
            )
        elif entry.kind == "applied":
            components.plan_applied(
                d["moved"],
                d["errors"],
                on_undo=partial(self._undo_plan, entry),
                undoable=not d.get("undone", False),
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
        self._set_busy(True)

        try:
            self._begin()
            self._record("user", text=text)
            await self._route(text)
        finally:
            self._busy = False
            self._set_busy(False)
        await self._scroll()

    async def _route(self, text: str) -> None:
        folder = _folder_in(text)
        wants_organize = _ORGANIZE_INTENT.search(text) is not None
        wants_images = _IMAGE_INTENT.search(text) is not None

        if wants_organize:
            await self._organize(text, folder)
        elif wants_images and folder and _INDEX_INTENT.search(text):
            # "index the photos in C:\…" — a filesystem path is useless as a CLIP
            # caption, so a path plus photo words plus an indexing verb means indexing.
            await self._index_images(folder)
        elif wants_images:
            await self._image_search(text)
        elif folder:
            await self._organize(text, folder)
        else:
            await self._ask(text)

    # --- the things it can do ------------------------------------------
    async def _ask(self, question: str) -> None:
        with self.thread:
            thinking = components.thinking("Reading your documents…")
        try:
            answer = await self.c.rag.ask(question)
        except Exception as exc:
            logger.exception("RAG failed")
            self._record("notice", title="That didn't work.", detail=str(exc))
            return
        finally:
            _dismiss(thinking)
        self._record("answer", answer=answer)

    async def _image_search(self, query: str) -> None:
        if self.c.images.count() == 0:
            self._record(
                "notice",
                title="No photos indexed yet.",
                detail=(
                    "Pick a folder of images first — the picture button below, or Images in "
                    "the sidebar. I look at every photo once, then you can find them by "
                    "describing what is in them."
                ),
            )
            return

        with self.thread:
            thinking = components.thinking("Looking at your photos…")
        cleaned = _strip_intent(query)
        try:
            hits = await self.c.images.search(cleaned)
        except Exception as exc:
            logger.exception("Image search failed")
            self._record("notice", title="Image search failed.", detail=str(exc))
            return
        finally:
            _dismiss(thinking)
        self._record("images", hits=hits, query=cleaned)

    async def _index_images(self, folder: Path) -> None:
        with self.thread:
            thinking = components.thinking("Looking at every photo… (this takes a moment)")

        # index_folder reports progress from a worker thread; a timer polls it from the
        # UI loop, because touching elements from another thread is a race.
        latest: dict[str, IndexProgress] = {}

        def on_progress(p: IndexProgress) -> None:
            latest["p"] = p

        def paint() -> None:
            p = latest.get("p")
            if p is not None:
                thinking.label.text = f"Looking at every photo… {p.done}/{p.total} · {p.current}"

        timer = ui.timer(0.3, paint)
        try:
            result = await self.c.images.index_folder(folder, on_progress)
        except Exception as exc:
            logger.exception("Image indexing failed")
            self._record("notice", title="Couldn't index that folder.", detail=str(exc))
            return
        finally:
            timer.cancel()
            _dismiss(thinking)

        if result.found == 0:
            self._record(
                "notice",
                title="No images in that folder.",
                detail=(
                    f"I looked through {folder} and its subfolders for photos "
                    "(JPG, PNG, HEIC, WebP, GIF, BMP, TIFF) and found none."
                ),
            )
        elif result.indexed == 0:
            self._record(
                "notice",
                title=f"Found {result.found} image{'s' if result.found != 1 else ''}, "
                "but couldn't read any of them.",
                detail="They may be corrupt or in a format this build can't decode. "
                f"First few: {', '.join(result.failed[:3])}",
            )
        else:
            skipped = f" ({len(result.failed)} unreadable, skipped)" if result.failed else ""
            self._record(
                "notice",
                title=f"Looked at {result.indexed} photo"
                f"{'s' if result.indexed != 1 else ''}.{skipped}",
                detail='Now describe one — "a man holding a baby", "sunset on a beach".',
            )
        await self._scroll()

    async def _organize(self, text: str, folder: Path | None) -> None:
        if folder is None:
            self._record(
                "notice",
                title="Which folder?",
                detail=(
                    "Use the folder button below, or name a path — "
                    'for example "organize C:\\Users\\me\\Downloads".'
                ),
            )
            return

        if not folder.is_dir():
            self._record("notice", title="Can't find that folder.", detail=str(folder))
            return

        with self.thread:
            thinking = components.thinking(f"Reading everything in {folder.name}…")

        try:
            await self.c.planner.index_folder(folder)
            await self._drain()

            thinking.label.text = f"Working out where {folder.name} should go…"
            plan = await self.c.planner.build_plan(folder)
        except Exception as exc:
            logger.exception("Planning failed")
            self._record("notice", title="That didn't work.", detail=str(exc))
            return
        finally:
            _dismiss(thinking)

        self._record("plan", plan=plan, state="pending")
        await self._scroll()

    # --- plan actions ---------------------------------------------------
    async def _apply_plan(self, entry: ChatEntry) -> None:
        if entry.data.get("state") != "pending":
            return
        entry.data["state"] = "applied"
        plan = entry.data["plan"]

        with self.thread:
            thinking = components.thinking("Filing…")
        try:
            moved, errors = await self.c.planner.apply_plan(plan)
        except Exception as exc:
            logger.exception("Applying the plan failed")
            entry.data["state"] = "failed"
            self._record("notice", title="Filing failed.", detail=str(exc))
            return
        finally:
            self.c.planner.release(plan.source_folder)
            _dismiss(thinking)

        self._record("applied", moved=moved, errors=errors, plan=plan, undone=False)
        await self._scroll()

    async def _cancel_plan(self, entry: ChatEntry) -> None:
        if entry.data.get("state") != "pending":
            return
        entry.data["state"] = "cancelled"
        self.c.planner.release(entry.data["plan"].source_folder)
        self._record("notice", title="Left everything where it was.", detail="")
        await self._scroll()

    async def _undo_plan(self, entry: ChatEntry) -> None:
        if entry.data.get("undone"):
            return
        entry.data["undone"] = True
        plan = entry.data["plan"]

        with self.thread:
            thinking = components.thinking("Putting everything back…")
        try:
            undone = await self.c.planner.undo_plan(plan)
        except Exception as exc:
            logger.exception("Undo failed")
            self._record("notice", title="Couldn't undo that.", detail=str(exc))
            return
        finally:
            _dismiss(thinking)
        self._record(
            "notice",
            title=f"Put {undone} file{'s' if undone != 1 else ''} back.",
            detail="Every move was logged before it happened, so undo is always available.",
        )
        await self._scroll()

    async def _clear_chat(self) -> None:
        """Wipe the transcript and bring the welcome hero back.

        Plans still waiting on a decision hold a planner lock on their folder; clearing
        the conversation is a decision — leave everything where it is — so release them.
        """
        if self._busy:
            ui.notify("Wait for the current answer to finish", type="warning")
            return
        for entry in STATE.chat.entries:
            if entry.kind == "plan" and entry.data.get("state") == "pending":
                entry.data["state"] = "cancelled"
                self.c.planner.release(entry.data["plan"].source_folder)

        STATE.chat.entries.clear()
        STATE.chat.draft = ""
        self.composer.value = ""
        self.thread.clear()
        with self.thread:
            self._welcome_showing = True
            self._welcome()

    # --- skills, launched directly from the welcome chips ----------------
    async def _pick_folder(self) -> None:
        folder = await components.choose_folder("Choose a folder to organize")
        if folder:
            await self._submit(f"organize {folder}")

    def _focus_composer(self) -> None:
        self.composer.run_method("focus")

    async def _photo_skill(self) -> None:
        """Photos indexed → put the cursor where the description goes. None yet →
        the only useful first step is picking the folder, so open that dialog."""
        if self.c.images.count() > 0:
            self.composer.value = "find photos of "
            self.composer.run_method("focus")
            return
        await self._pick_image_folder()

    async def _recent_activity(self) -> None:
        """Deterministic — straight from the database, no LLM in the loop. (In 0.1.0
        this chip typed a canned question that retrieval could never answer.)"""
        self._begin()
        files = await self.c.library.list_files(10)
        stats = await self.c.library.stats()
        self._record("recent", files=files, review_count=stats.review_count)
        await self._scroll()

    async def _pick_image_folder(self) -> None:
        folder = await components.choose_folder("Choose a folder of photos")
        if not folder:
            return
        path = Path(folder)
        self._begin()
        self._record("user", text=f"index the photos in {path}")
        if not path.is_dir():
            self._record("notice", title="Can't find that folder.", detail=str(path))
            return
        await self._index_images(path)

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
            "const t = document.querySelector('.chat-thread');if (t) t.scrollTop = t.scrollHeight;"
        )

    async def _scroll_when_connected(self) -> None:
        """On page (re)build the websocket may not be up yet; scrolling into a void
        does nothing, so wait for the client first."""
        try:
            await ui.context.client.connected(timeout=5.0)
        except TimeoutError:
            return
        await self._scroll()

    def _set_busy(self, busy: bool) -> None:
        if self._send_button is None:
            return
        if busy:
            self._send_button.disable()
            self.composer.disable()
        else:
            self._send_button.enable()
            self.composer.enable()


def _dismiss(element: ui.element) -> None:
    """Delete a transient element (a thinking spinner) that may already be gone.

    "Clear the conversation" or a navigation can sweep the thread while an answer is
    still in flight; the in-flight handler must not crash on its own cleanup."""
    try:
        if not element.is_deleted:
            element.delete()
    except (ValueError, KeyError):
        pass


def _folder_in(text: str) -> Path | None:
    """Pull a path out of "organize C:\\Users\\me\\Downloads"."""
    match = re.search(r'([A-Za-z]:[\\/][^<>:"|?*\n]+|/(?:[^/\0"\'\n]+/?)+)', text)
    if match:
        raw = match.group(1).strip().strip("\"'")
        candidate = Path(raw.rstrip(".,;)"))
        # Paths may contain spaces, so the match greedily ate any words after the path
        # ("organize C:\Users\me\Downloads please"). Trim words off the end until
        # something real appears.
        while True:
            if candidate.is_dir():
                return candidate
            trimmed = str(candidate)
            if " " not in trimmed:
                break
            candidate = Path(trimmed.rsplit(" ", 1)[0].rstrip(".,;)"))

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
