"""Chat message components. Presentation only — no logic, no service calls."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import mimetypes
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path

from nicegui import background_tasks, run, ui
from PIL import Image

from osdc.desktop import window
from osdc.domain.models import FileRecord, OrganizePlan
from osdc.services.images import ImageHit
from osdc.services.rag import Answer

logger = logging.getLogger(__name__)

Handler = Callable[[], Awaitable[None]]

#: Raw pass-through ceiling for the thumbnail fast path; anything bigger (or in a format
#: browsers can't show, like HEIC) gets decoded and downscaled instead of refused.
MAX_THUMB_BYTES = 300_000
THUMB_EDGE = 480

#: Mime types Chromium will actually render from a data URI. HEIC and TIFF are absent on
#: purpose — those must be transcoded no matter how small they are.
_BROWSER_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}


def user_message(text: str) -> None:
    with ui.row().classes("w-full justify-end"):
        ui.label(text).classes("msg-user text-sm whitespace-pre-wrap")


class Thinking(ui.row):
    """Spinner plus a mutable label, so long jobs can narrate their progress."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.classes("items-center gap-3 msg-bot")
        with self:
            ui.spinner(size="sm", color="primary")
            self.label = ui.label(text).classes("dim text-sm")


def thinking(label: str) -> Thinking:
    return Thinking(label)


def notice(title: str, detail: str) -> None:
    with ui.column().classes("msg-bot gap-1"):
        ui.label(title).classes("text-sm")
        if detail:
            ui.label(detail).classes("dim text-xs")


def answer(result: Answer) -> None:
    with ui.column().classes("msg-bot w-full gap-3"):
        ui.markdown(result.text).classes("msg-bot text-sm")

        if result.sources:
            ui.label("Sources").classes("dim text-xs uppercase tracking-wider mt-1")
            for source in result.sources:
                path = source.file.organized_path or source.file.original_path
                with (
                    ui.row()
                    .classes("src items-center gap-3 px-3 py-2 w-full cursor-pointer")
                    .on("click", lambda p=path: _reveal(p))
                ):
                    ui.label(str(source.index)).classes("dim text-xs mono w-4 text-center")
                    with ui.column().classes("gap-0 flex-grow min-w-0"):
                        ui.label(f"{source.file.filename} · page {source.page}").classes("text-xs")
                        ui.label(str(path)).classes("dim text-xs mono truncate")
                    ui.label(f"{source.score:.2f}").classes("dim text-xs mono")
        elif not result.grounded:
            ui.label(
                "Nothing in your library was relevant, so I didn't ask the model — it "
                "would only have been guessing."
            ).classes("dim text-xs")


def image_results(hits: list[ImageHit], query: str) -> None:
    with ui.column().classes("msg-bot w-full gap-3"):
        if not hits:
            ui.label("No photos matched that.").classes("text-sm")
            ui.label(
                "Try describing what is visible in the picture rather than naming it — "
                '"two people on a beach at sunset" works better than "holiday".'
            ).classes("dim text-xs")
            return

        ui.label(f"{len(hits)} photo{'s' if len(hits) != 1 else ''}").classes("text-sm")
        if query.strip():
            ui.label(f'for "{query.strip()}"').classes("dim text-xs")
        with ui.grid(columns=4).classes("w-full gap-2"):
            for hit in hits:
                with ui.column().classes("tile gap-0").on("click", lambda p=hit.path: _reveal(p)):
                    holder = ui.element("div").classes("thumb w-full")
                    ui.label(f"{hit.score:.2f}").classes("score mono")
                    ui.label(hit.path.name).classes("dim text-xs px-2 py-1 truncate w-full")
                    _fill_thumb_async(holder, hit.path)


def _fill_thumb_async(holder: ui.element, path: Path) -> None:
    """Decode off the event loop, paint when ready.

    A grid of 24-megapixel photos takes seconds to decode; doing that inline froze the
    whole UI, and skipping big files (the old behaviour) rendered them as broken icons.
    """

    async def load() -> None:
        try:
            src = await run.io_bound(_thumb, path)
        except Exception:
            logger.exception("Thumbnail failed for %s", path.name)
            src = None
        try:
            if holder.is_deleted or holder.client.is_deleted:
                return  # the user already navigated away
            with holder:
                if src:
                    ui.image(src).classes("cursor-pointer")
                else:
                    ui.icon("broken_image").classes("dim text-3xl p-8")
        except Exception:
            # The page can be torn down between the check and the paint; a thumbnail
            # arriving after its page is gone is routine, not an error.
            logger.debug("Discarding a thumbnail for a closed page: %s", path.name)

    background_tasks.create(load(), name=f"thumb:{path.name}")


def plan_preview(
    plan: OrganizePlan,
    on_apply: Handler,
    on_cancel: Handler,
    pending: bool = True,
) -> None:
    """The safety rail made visible.

    The model chose every destination here, but it produced *data*, not commands — so this
    screen can show exactly what will happen before a single byte moves. Nothing is touched
    until Apply, and Apply goes through the logged, reversible organizer.

    ``pending=False`` re-renders a plan that has already been decided (the transcript is
    replayed after navigation) — same preview, no live buttons.
    """
    movable = plan.movable
    review = [i for i in plan.items if i.skipped]

    with ui.column().classes("msg-bot w-full gap-3"):
        if not plan.items:
            ui.label("Nothing readable in that folder.").classes("text-sm")
            return

        ui.label(
            f"Here's what I'd do with {len(movable)} file{'s' if len(movable) != 1 else ''}."
        ).classes("text-sm")

        with ui.card().classes("w-full p-4 gap-4"):
            if review:
                ui.label("Needs your input").classes("dim text-xs uppercase tracking-wider")

            by_folder: dict[str, list] = {}
            for item in movable:
                by_folder.setdefault(item.destination, []).append(item)

            for folder in sorted(by_folder):
                with ui.column().classes("w-full gap-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("folder").classes("text-sm").style("color: var(--accent)")
                        ui.label(folder).classes("plan-folder mono")
                    for item in by_folder[folder]:
                        with ui.column().classes("plan-file pl-3 ml-1 gap-0 py-1"):
                            ui.label(item.filename).classes("text-xs")
                            if item.reason:
                                ui.label(item.reason).classes("dim text-xs")

            if review:
                ui.separator().style("background: var(--border)")
                with ui.row().classes("items-center gap-2"):
                    ui.icon("help_outline").classes("dim text-sm")
                    ui.label(f"{len(review)} left for you to decide").classes("dim text-xs")
                for item in review:
                    with ui.column().classes("plan-file pl-3 ml-1 gap-0 py-1"):
                        ui.label(item.filename).classes("text-xs")
                        if item.reason:
                            ui.label(item.reason).classes("dim text-xs")

            if plan.unreadable:
                ui.label(
                    f"{len(plan.unreadable)} file(s) I couldn't read (scans need OCR)."
                ).classes("dim text-xs")

        if not pending:
            return

        with ui.row().classes("gap-2"):
            apply_btn = ui.button("Apply").props("unelevated dense")
            cancel_btn = ui.button("Cancel").props("flat dense").classes("dim")

        # One decision per plan: both buttons die the moment either is pressed, so a
        # double-click cannot file the same folder twice.
        def _lock() -> None:
            apply_btn.disable()
            cancel_btn.disable()

        async def _apply() -> None:
            _lock()
            await on_apply()

        async def _cancel() -> None:
            _lock()
            await on_cancel()

        apply_btn.on_click(_apply)
        cancel_btn.on_click(_cancel)

        ui.label("Nothing has moved yet. Every move is logged and can be undone.").classes(
            "dim text-xs"
        )


def recent_files(files: list[FileRecord], review_count: int = 0) -> None:
    """What got filed while you were away — straight from the database, no model."""
    with ui.column().classes("msg-bot w-full gap-3"):
        if not files:
            ui.label("Nothing has been filed yet.").classes("text-sm")
            ui.label(
                "Drop a folder into the chat, or add a watched folder in Settings and let "
                "your next download file itself."
            ).classes("dim text-xs")
            return

        ui.label("Recently filed").classes("text-sm")
        with ui.card().classes("w-full p-2 gap-0"):
            for record in files:
                path = record.organized_path or record.original_path
                with (
                    ui.row()
                    .classes("src items-center gap-3 px-3 py-2 w-full cursor-pointer")
                    .on("click", lambda p=path: _reveal(p))
                ):
                    ui.icon("check_circle" if record.organized_path else "help_outline").classes(
                        "text-sm"
                    ).style("color: var(--accent)" if record.organized_path else "opacity:.4")
                    with ui.column().classes("gap-0 flex-grow min-w-0"):
                        ui.label(record.filename).classes("text-xs")
                        ui.label(record.label or "waiting for your decision").classes("dim text-xs")
                    if record.processed_at:
                        ui.label(record.processed_at.strftime("%d %b, %H:%M")).classes(
                            "dim text-xs mono"
                        )
        if review_count:
            ui.label(
                f"{review_count} file{'s' if review_count != 1 else ''} waiting in the "
                "Library for your decision."
            ).classes("dim text-xs")


def plan_applied(moved: int, errors: list[str], on_undo: Handler, undoable: bool = True) -> None:
    with ui.column().classes("msg-bot w-full gap-2"):
        ui.label(f"Filed {moved} file{'s' if moved != 1 else ''}.").classes("text-sm")
        for error in errors[:5]:
            ui.label(error).classes("dim text-xs")
        if moved and undoable:
            undo_btn = ui.button("Undo").props("flat dense size=sm").classes("dim")

            async def _undo() -> None:
                undo_btn.disable()  # undo is idempotent-hostile; one shot only
                await on_undo()

            undo_btn.on_click(_undo)


async def choose_folder(title: str) -> str | None:
    """A real OS folder picker in the desktop app; a typed path in browser mode."""
    if window.current is not None:
        # The native dialog is modal and blocks its thread, so it must not run on the loop.
        return await asyncio.to_thread(_native_folder_dialog, title)

    with ui.dialog() as dialog, ui.card().classes("w-96 p-4 gap-3"):
        ui.label(title).classes("text-sm")
        field = (
            ui.input(placeholder=r"C:\Users\you\Downloads")
            .props("dense outlined")
            .classes("w-full")
        )
        field.on("keydown.enter", lambda: dialog.submit(field.value))
        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat dense")
            ui.button("OK", on_click=lambda: dialog.submit(field.value)).props("unelevated dense")
    picked = await dialog
    if picked is None:
        return None
    # Explorer's "Copy as path" wraps the path in quotes; swallowing them here beats
    # telling the user their real folder doesn't exist.
    return str(picked).strip().strip("\"'").strip() or None


def _native_folder_dialog(title: str) -> str | None:
    return window.pick_folder(title)


# --- helpers ----------------------------------------------------------------


def _thumb(path: Path) -> str | None:
    """A data-URI thumbnail.

    Inlined on purpose: NiceGUI can only serve files it has been given a route for, and
    registering a route per photo would leak a growing set of handlers — and would expose
    the user's photo tree on the local HTTP server. Small browser-friendly files pass
    through untouched; everything else (big JPEGs, HEIC, TIFF) is downscaled to a real
    thumbnail instead of being refused.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    return _thumb_cached(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=256)
def _thumb_cached(path_str: str, mtime_ns: int, size: int) -> str | None:
    path = Path(path_str)
    mime = mimetypes.guess_type(path.name)[0]

    if size <= MAX_THUMB_BYTES and mime in _BROWSER_MIMES:
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    try:
        with Image.open(path) as handle:
            handle.draft("RGB", (THUMB_EDGE, THUMB_EDGE))  # free 8x speedup on JPEGs
            image = handle.convert("RGB")
        image.thumbnail((THUMB_EDGE, THUMB_EDGE))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=72)
    except Exception:
        logger.warning("Could not thumbnail %s", path.name)
        return None
    return f"data:image/jpeg;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


def _reveal(path: Path) -> None:
    """Open the containing folder and select the file."""
    import subprocess
    import sys

    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError:
        ui.notify("Couldn't open that location", type="negative")
