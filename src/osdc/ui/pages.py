"""Pages. Thin: read a service, render. All logic lives below the UI layer."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from nicegui import ui

from osdc.config.settings import save_settings
from osdc.container import Container
from osdc.ui import components
from osdc.ui.chat import ChatPane
from osdc.ui.setup import SetupWizard
from osdc.ui.shell import shell

logger = logging.getLogger(__name__)


def register_pages(container: Container) -> None:
    @ui.page("/")
    async def index() -> None:
        # A fresh install has no model and no folders. Sending someone straight to a chat
        # box that cannot answer anything is a bad first thirty seconds.
        if not container.settings.onboarded:
            await SetupWizard(container).build()
            return
        with shell(active="/"):
            ChatPane(container).build()

    @ui.page("/setup")
    async def setup() -> None:
        await SetupWizard(container).build()

    # --- library ------------------------------------------------------
    @ui.page("/library")
    async def library() -> None:
        with shell(active="/library"):
            with ui.column().classes("w-full h-full p-8 gap-6 overflow-y-auto"):
                ui.label("Library").classes("text-2xl font-semibold")

                stats_row = ui.row().classes("gap-3")
                table = ui.column().classes("w-full")

                async def refresh() -> None:
                    stats = await container.library.stats()
                    files = await container.library.list_files(300)

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
                            row_key="filename",
                        ).classes("w-full").props("dense flat")

                await refresh()
                ui.timer(3.0, refresh)

    # --- images -------------------------------------------------------
    @ui.page("/images")
    async def images() -> None:
        with shell(active="/images"):
            with ui.column().classes("w-full h-full p-8 gap-6 overflow-y-auto"):
                ui.label("Images").classes("text-2xl font-semibold")
                ui.label(
                    "Point me at a folder of photos. I look at each one, then you can find "
                    'them by describing what is in them — "a man holding a baby". Your '
                    "photos are never moved or copied."
                ).classes("dim text-sm max-w-2xl")

                status = ui.label().classes("dim text-xs")
                status.text = f"{container.images.count()} photo(s) indexed"

                results = ui.column().classes("w-full")

                async def do_index() -> None:
                    folder = await components.choose_folder("Choose a folder of photos")
                    if not folder:
                        return
                    status.text = "Looking at every photo…"
                    count = await container.images.index_folder(Path(folder))
                    status.text = f"{container.images.count()} photo(s) indexed (+{count})"

                async def do_search(query: str) -> None:
                    results.clear()
                    with results:
                        spinner = ui.spinner(color="primary")
                    hits = await container.images.search(query)
                    spinner.delete()
                    results.clear()
                    with results:
                        components.image_results(hits, query)

                with ui.row().classes("w-full gap-2 items-center"):
                    box = (
                        ui.input(placeholder="Describe a photo…")
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

    # --- settings -----------------------------------------------------
    @ui.page("/settings")
    async def settings_page() -> None:
        s = container.settings
        with shell(active="/settings"):
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
                        if picked:
                            s.watched_folders = [*s.watched_folders, Path(picked)]
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
                    available = await asyncio.to_thread(container.llm.available)
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("circle").classes(
                            "text-xs " + ("text-green-500" if available else "text-red-500")
                        )
                        ui.label(
                            f"{s.llm_model} · {'running' if available else 'not running'}"
                        ).classes("text-xs")
                    _kv("Embeddings", s.embedding_model)
                    _kv("Image search", s.clip_model)
                    ui.button("Re-run setup", on_click=lambda: ui.navigate.to("/setup")).props(
                        "flat dense"
                    ).classes("dim")

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
