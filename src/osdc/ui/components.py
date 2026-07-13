"""Chat message components. Presentation only — no logic, no service calls."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from collections.abc import Awaitable, Callable
from pathlib import Path

from nicegui import ui

from osdc.desktop import window
from osdc.domain.models import FileRecord, OrganizePlan
from osdc.services.images import ImageHit
from osdc.services.rag import Answer

Handler = Callable[[], Awaitable[None]]

MAX_THUMB_BYTES = 4_000_000


def user_message(text: str) -> None:
    with ui.row().classes("w-full justify-end"):
        ui.label(text).classes("msg-user text-sm")


def thinking(label: str) -> ui.row:
    row = ui.row().classes("items-center gap-3 msg-bot")
    with row:
        ui.spinner(size="sm", color="primary")
        ui.label(label).classes("dim text-sm")
    return row


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
        with ui.grid(columns=4).classes("w-full gap-2"):
            for hit in hits:
                with ui.column().classes("tile gap-0").on("click", lambda p=hit.path: _reveal(p)):
                    src = _thumb(hit.path)
                    if src:
                        ui.image(src).classes("cursor-pointer")
                    else:
                        ui.icon("broken_image").classes("dim text-3xl p-8")
                    ui.label(f"{hit.score:.2f}").classes("score mono")
                    ui.label(hit.path.name).classes("dim text-xs px-2 py-1 truncate w-full")


def plan_preview(plan: OrganizePlan, on_apply: Handler, on_cancel: Handler) -> None:
    """The safety rail made visible.

    The model chose every destination here, but it produced *data*, not commands — so this
    screen can show exactly what will happen before a single byte moves. Nothing is touched
    until Apply, and Apply goes through the logged, reversible organizer.
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

        with ui.row().classes("gap-2"):
            ui.button("Apply", on_click=on_apply).props("unelevated dense")
            ui.button("Cancel", on_click=on_cancel).props("flat dense").classes("dim")

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


def plan_applied(moved: int, errors: list[str], on_undo: Handler) -> None:
    with ui.column().classes("msg-bot w-full gap-2"):
        ui.label(f"Filed {moved} file{'s' if moved != 1 else ''}.").classes("text-sm")
        for error in errors[:5]:
            ui.label(error).classes("dim text-xs")
        if moved:
            ui.button("Undo", on_click=on_undo).props("flat dense size=sm").classes("dim")


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
        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat dense")
            ui.button("OK", on_click=lambda: dialog.submit(field.value)).props("unelevated dense")
    picked = await dialog
    return str(picked).strip() or None if picked else None


def _native_folder_dialog(title: str) -> str | None:
    return window.pick_folder(title)


# --- helpers ----------------------------------------------------------------


def _thumb(path: Path) -> str | None:
    """Inline the image as a data URI.

    NiceGUI can only serve files it has been given a route for, and registering a route per
    photo would leak a growing set of handlers. Inlining keeps it stateless — and keeps the
    user's photos from being exposed on the local HTTP server at all.
    """
    try:
        if path.stat().st_size > MAX_THUMB_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


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
