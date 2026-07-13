"""Pages. Thin: read a service, render. All logic lives below the UI layer."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from nicegui import ui

from osdc.config.settings import save_settings
from osdc.container import Container
from osdc.services.images import IndexProgress
from osdc.ui import components
from osdc.ui.chat import ChatPane
from osdc.ui.setup import SetupWizard
from osdc.ui.shell import shell
from osdc.ui.state import STATE

logger = logging.getLogger(__name__)


def register_pages(container: Container) -> None:
    @ui.page("/")
    async def index() -> None:
        # A fresh install has no model and no folders. Sending someone straight to a chat
        # box that cannot answer anything is a bad first thirty seconds.
        if not container.settings.onboarded:
            await SetupWizard(container).build()
            return
        with shell(active="/", container=container):
            ChatPane(container).build()

    @ui.page("/setup")
    async def setup() -> None:
        await SetupWizard(container).build()

    # --- library ------------------------------------------------------
    @ui.page("/library")
    async def library() -> None:
        with shell(active="/library", container=container):
            with ui.column().classes("w-full h-full p-8 gap-6 overflow-y-auto"):
                ui.label("Library").classes("text-2xl font-semibold")
                ui.label(
                    "Everything already filed, plus anything still waiting on a decision."
                ).classes("dim text-sm -mt-4 max-w-2xl")

                stats_row = ui.row().classes("gap-3")
                table = ui.column().classes("w-full")

                async def refresh() -> None:
                    try:
                        stats = await container.library.stats()
                        files = await container.library.list_files(300)
                    except Exception as exc:
                        stats_row.clear()
                        table.clear()
                        with table:
                            ui.label("Library unavailable right now.").classes("text-sm")
                            ui.label(str(exc)).classes("dim text-xs")
                        return

                    stats_row.clear()
                    with stats_row:
                        _stat("Files", stats.total_files)
                        _stat("Working", stats.queued + stats.running)
                        _stat("Needs you", stats.review_count)
                        _stat("Failed", stats.failed)

                    table.clear()
                    with table:
                        if not files:
                            ui.label(
                                "Nothing yet. Drop a folder into the chat, or let the "
                                "watcher pick up your next download."
                            ).classes("dim text-sm py-8")
                            return
                        ui.table(
                            columns=[
                                {
                                    "name": "filename",
                                    "label": "File",
                                    "field": "filename",
                                    "align": "left",
                                    "sortable": True,
                                },
                                {
                                    "name": "label",
                                    "label": "Filed as",
                                    "field": "label",
                                    "align": "left",
                                    "sortable": True,
                                },
                                {
                                    "name": "conf",
                                    "label": "Confidence",
                                    "field": "conf",
                                    "sortable": True,
                                },
                                {
                                    "name": "where",
                                    "label": "Location",
                                    "field": "where",
                                    "align": "left",
                                },
                            ],
                            rows=[
                                {
                                    "filename": f.filename,
                                    "label": f.label or "—",
                                    "conf": (
                                        f"{f.confidence_score:.2f}"
                                        if f.confidence_score is not None
                                        else "—"
                                    ),
                                    "where": str(f.organized_path or f.original_path),
                                }
                                for f in files
                            ],
                            # The full path — two "syllabus.pdf"s must not share a row key.
                            row_key="where",
                        ).classes("w-full").props("dense flat")

                await refresh()
                ui.timer(3.0, refresh)

    # --- images -------------------------------------------------------
    @ui.page("/images")
    async def images() -> None:
        with shell(active="/images", container=container):
            with ui.column().classes("w-full h-full p-8 gap-6 overflow-y-auto"):
                ui.label("Images").classes("text-2xl font-semibold")
                ui.label(
                    "Point me at a folder of photos. I look at each one, then you can find "
                    'them by describing what is in them — "a man holding a baby". Your '
                    "photos are never moved or copied."
                ).classes("dim text-sm max-w-2xl")

                status = ui.label().classes("dim text-xs")
                status.text = f"{container.images.count()} photo(s) indexed"

                async def do_index() -> None:
                    folder = await components.choose_folder("Choose a folder of photos")
                    if not folder:
                        return
                    path = Path(folder)

                    latest: dict[str, IndexProgress] = {}

                    def on_progress(p: IndexProgress) -> None:
                        latest["p"] = p

                    def paint() -> None:
                        p = latest.get("p")
                        if p is not None:
                            status.text = f"Looking at every photo… {p.done}/{p.total}"

                    status.text = "Looking at every photo…"
                    timer = ui.timer(0.3, paint)
                    try:
                        result = await container.images.index_folder(path, on_progress)
                    except Exception as exc:
                        status.text = f"Couldn't index that folder: {exc}"
                        return
                    finally:
                        timer.cancel()

                    total = container.images.count()
                    if result.found == 0:
                        status.text = (
                            f"No images found in {path} — I looked for JPG, PNG, HEIC, "
                            "WebP, GIF, BMP and TIFF, including subfolders."
                        )
                    elif result.indexed == 0:
                        status.text = (
                            f"Found {result.found} image(s) but couldn't read any of them "
                            f"(e.g. {result.failed[0]}). They may be corrupt."
                        )
                    else:
                        skipped = (
                            f", {len(result.failed)} unreadable skipped" if result.failed else ""
                        )
                        status.text = f"{total} photo(s) indexed (+{result.indexed}{skipped})"

                results = ui.column().classes("w-full")

                async def do_search(query: str) -> None:
                    query = query.strip()
                    if not query:
                        ui.notify("Describe what you want to find first", type="warning")
                        return
                    results.clear()
                    with results:
                        spinner = ui.spinner(color="primary")
                    try:
                        hits = await container.images.search(query)
                    except Exception as exc:
                        spinner.delete()
                        results.clear()
                        with results:
                            components.notice("Image search failed.", str(exc))
                        return
                    spinner.delete()
                    STATE.images.hits = hits
                    STATE.images.searched_query = query
                    results.clear()
                    with results:
                        components.image_results(hits, query)

                with ui.row().classes("w-full gap-2 items-center"):
                    box = (
                        ui.input(
                            placeholder="Describe a photo…",
                            value=STATE.images.query,
                            on_change=lambda e: setattr(STATE.images, "query", e.value or ""),
                        )
                        .props("outlined dense")
                        .classes("flex-grow")
                    )
                    box.on("keydown.enter", lambda: do_search(box.value or ""))
                    ui.button("Search", on_click=lambda: do_search(box.value or "")).props(
                        "unelevated dense"
                    )
                    ui.button("Index a folder", on_click=do_index).props("flat dense").classes(
                        "dim"
                    )

                # Coming back from another page: the last results are still here.
                if STATE.images.hits is not None:
                    with results:
                        components.image_results(STATE.images.hits, STATE.images.searched_query)

    # --- settings -----------------------------------------------------
    @ui.page("/settings")
    async def settings_page() -> None:
        s = container.settings
        with shell(active="/settings", container=container):
            with ui.column().classes("w-full h-full p-8 gap-6 overflow-y-auto max-w-3xl"):
                ui.label("Settings").classes("text-2xl font-semibold")

                with ui.card().classes("w-full p-5 gap-4"):
                    ui.label("Watched folders").classes("text-sm font-medium")
                    ui.label(
                        "New downloads here get filed automatically, even when the window "
                        "is closed."
                    ).classes("dim text-xs")

                    folders = ui.column().classes("w-full gap-1")

                    def draw_folders() -> None:
                        folders.clear()
                        with folders:
                            if not s.watched_folders:
                                ui.label("None — nothing is being watched.").classes("dim text-xs")
                            for folder in list(s.watched_folders):
                                with ui.row().classes("items-center gap-2 w-full"):
                                    ui.label(str(folder)).classes("mono text-xs flex-grow")
                                    ui.button(
                                        icon="close", on_click=lambda f=folder: remove(f)
                                    ).props("flat dense round size=xs").classes("dim")

                    def remove(folder: Path) -> None:
                        s.watched_folders = [f for f in s.watched_folders if f != folder]
                        save_settings(s)
                        draw_folders()
                        ui.notify("Restart to stop watching that folder")

                    async def add() -> None:
                        picked = await components.choose_folder("Watch which folder?")
                        if not picked:
                            return
                        path = Path(picked)
                        if path in s.watched_folders:
                            ui.notify("Already watching that folder")
                            return
                        s.watched_folders = [*s.watched_folders, path]
                        save_settings(s)
                        draw_folders()
                        ui.notify("Restart to start watching it")

                    draw_folders()
                    ui.button("Add a folder", on_click=add).props("flat dense").classes("dim")

                with ui.card().classes("w-full p-5 gap-3"):
                    ui.label("Filing").classes("text-sm font-medium")
                    _kv("Library", str(s.library_root))
                    _kv("Action", s.file_action.value + "  (originals are kept on copy)")
                    _kv("Auto-file above", f"{s.academic_threshold:.2f} confidence")

                with ui.card().classes("w-full p-5 gap-3"):
                    ui.label("Models").classes("text-sm font-medium")
                    with ui.row().classes("items-center gap-2"):
                        llm_dot = ui.icon("circle").classes("text-xs text-yellow-600")
                        llm_line = ui.label(f"{s.llm_model} · checking…").classes("text-xs")
                    _kv("Embeddings", s.embedding_model)
                    _kv("Image search", s.clip_model)
                    ui.button("Re-run setup", on_click=lambda: ui.navigate.to("/setup")).props(
                        "flat dense"
                    ).classes("dim")

                    async def check_llm() -> None:
                        # Off the page path on purpose: a down Ollama must not stall the
                        # whole Settings page while the connection times out.
                        available = await asyncio.to_thread(container.llm.available)
                        llm_dot.classes(
                            replace="text-xs "
                            + ("text-green-500" if available else "text-red-500")
                        )
                        llm_line.text = (
                            f"{s.llm_model} · {'running' if available else 'not running'}"
                        )

                    ui.timer(0.1, check_llm, once=True)

                with ui.card().classes("w-full p-5 gap-3"):
                    ui.label("Background").classes("text-sm font-medium")
                    ui.switch(
                        "Keep running in the tray when I close the window",
                        value=s.close_to_tray,
                        on_change=lambda e: _persist(s, "close_to_tray", e.value),
                    ).props("dense")
                    ui.label(
                        "Off means closing the window quits, and downloads stop being filed."
                    ).classes("dim text-xs")

                with ui.card().classes("w-full p-5 gap-3 danger-zone"):
                    ui.label("Danger zone").classes("text-sm font-medium text-red-400")
                    ui.label(
                        "Reset the database: forget every file record, the document and "
                        "photo search indexes, and the undo history. Your actual files — "
                        "originals and the organized library — are not touched, and your "
                        "settings are kept."
                    ).classes("dim text-xs")

                    reset_status = ui.label().classes("dim text-xs")
                    reset_status.visible = False

                    async def do_reset() -> None:
                        with ui.dialog() as dialog, ui.card().classes("w-96 p-4 gap-3"):
                            ui.label("Reset the database?").classes("text-sm font-medium")
                            ui.label(
                                "Everything the app has read will be forgotten and undo "
                                "history will be lost. Files on disk stay exactly where "
                                "they are. This cannot be undone."
                            ).classes("dim text-xs")
                            with ui.row().classes("justify-end gap-2 w-full"):
                                ui.button(
                                    "Cancel", on_click=lambda: dialog.submit(False)
                                ).props("flat dense")
                                ui.button(
                                    "Reset everything", on_click=lambda: dialog.submit(True)
                                ).props("unelevated dense color=negative")
                        if not await dialog:
                            return

                        reset_btn.disable()
                        reset_status.visible = True
                        reset_status.text = "Resetting…"
                        try:
                            await container.reset_data()
                        except Exception as exc:
                            reset_status.text = str(exc)
                            reset_btn.enable()
                            return
                        # The chat transcript and image results now point at records
                        # that no longer exist; a fresh start means a fresh start.
                        STATE.reset()
                        reset_status.text = "Done. The app has forgotten everything it read."
                        reset_btn.enable()
                        ui.notify("Database reset — the library is empty", type="positive")

                    reset_btn = (
                        ui.button("Reset database", on_click=do_reset)
                        .props("flat dense color=negative")
                        .mark("reset-db")
                    )


def _persist(settings: object, field: str, value: object) -> None:
    setattr(settings, field, value)
    save_settings(settings)  # type: ignore[arg-type]


def _stat(label: str, value: int) -> None:
    with ui.card().classes("px-4 py-3 min-w-28"):
        ui.label(str(value)).classes("text-xl font-semibold leading-none")
        ui.label(label).classes("dim text-xs mt-1")


def _kv(key: str, value: str) -> None:
    with ui.row().classes("w-full items-center"):
        ui.label(key).classes("dim text-xs w-40")
        ui.label(value).classes("text-xs mono")
