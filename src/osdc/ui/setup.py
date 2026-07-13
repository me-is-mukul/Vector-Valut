"""First-run setup.

The MSI is slim by design, so this screen is what turns a fresh install into a working app:
detect the machine, recommend a model that will actually fit in its memory, install Ollama,
download the weights, and pick the folders to watch.

The recommendation is the part that matters. Suggest a model too big for the GPU and Ollama
silently spills layers to the CPU — the app still "works", but every answer takes forty
seconds and the user concludes the product is slow rather than that the model was wrong.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from nicegui import ui

from osdc.config.settings import save_settings
from osdc.container import Container
from osdc.setup import bootstrap, hardware
from osdc.setup.hardware import Hardware, ModelChoice
from osdc.ui import components, theme

logger = logging.getLogger(__name__)


class SetupWizard:
    def __init__(self, container: Container) -> None:
        self.c = container
        self.hw: Hardware = hardware.detect()
        self.choice: ModelChoice = hardware.recommend(self.hw)
        self.folders: list[Path] = [p for p in (Path.home() / "Downloads",) if p.is_dir()]

    async def build(self) -> None:
        theme.apply()
        ui.query(".nicegui-content").classes("p-0 gap-0")

        with (
            ui.column().classes("w-screen h-screen items-center justify-center p-8"),
            ui.column().classes("w-full max-w-xl gap-8"),
        ):
            with ui.column().classes("gap-2"):
                ui.icon("auto_awesome").classes("text-3xl").style("color: var(--accent)")
                ui.label("Let's get you set up").classes("text-3xl font-semibold")
                ui.label(
                    "Everything runs on your machine. Nothing you own is uploaded anywhere."
                ).classes("dim text-sm")

            self._hardware_card()
            self._model_card()
            self._folders_card()

            self.status = ui.label().classes("dim text-xs")
            self.progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
            self.progress.visible = False

            self.go = (
                ui.button("Install and start", on_click=self._run)
                .props("unelevated")
                .classes("w-full")
            )

    # ------------------------------------------------------------------
    def _hardware_card(self) -> None:
        with ui.card().classes("w-full p-5 gap-2"):
            ui.label("Your machine").classes("dim text-xs uppercase tracking-wider")
            with ui.row().classes("items-center gap-2"):
                ui.icon("memory" if self.hw.has_gpu else "developer_board").classes(
                    "text-base"
                ).style("color: var(--accent)")
                ui.label(hardware.summary(self.hw)).classes("text-sm")
            if not self.hw.has_gpu:
                ui.label(
                    "No usable GPU found, so the model will run on the CPU. It will work, "
                    "but expect answers to take a few seconds."
                ).classes("dim text-xs")

    def _model_card(self) -> None:
        with ui.card().classes("w-full p-5 gap-3"):
            ui.label("Language model").classes("dim text-xs uppercase tracking-wider")

            options = {m.name: m for m in hardware.CATALOG if hardware.fits(m, self.hw)} or {
                self.choice.name: self.choice
            }

            self.model_note = ui.label().classes("dim text-xs")

            def pick(name: str) -> None:
                self.choice = options[name]
                self.model_note.text = (
                    f"{self.choice.size_gb:.1f} GB download · {self.choice.rationale}"
                )

            ui.select(
                {name: f"{name}  —  {m.label}" for name, m in options.items()},
                value=self.choice.name,
                on_change=lambda e: pick(e.value),
            ).props("outlined dense").classes("w-full")
            pick(self.choice.name)

            skipped = [m for m in hardware.CATALOG if not hardware.fits(m, self.hw)]
            if skipped:
                ui.label(
                    "Hidden because they won't fit in memory on this machine: "
                    + ", ".join(m.name for m in skipped)
                ).classes("dim text-xs")

    def _folders_card(self) -> None:
        with ui.card().classes("w-full p-5 gap-3"):
            ui.label("Watch for downloads").classes("dim text-xs uppercase tracking-wider")
            ui.label(
                "New files here get read and filed automatically — even when the window is closed."
            ).classes("dim text-xs")

            self.folder_list = ui.column().classes("w-full gap-1")
            self._draw_folders()

            ui.button("Add a folder", on_click=self._add_folder).props("flat dense").classes("dim")

    def _draw_folders(self) -> None:
        self.folder_list.clear()
        with self.folder_list:
            if not self.folders:
                ui.label("None yet — you can add these later.").classes("dim text-xs")
            for folder in list(self.folders):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.icon("folder_outlined").classes("dim text-sm")
                    ui.label(str(folder)).classes("mono text-xs flex-grow")
                    ui.button(icon="close", on_click=lambda f=folder: self._remove_folder(f)).props(
                        "flat dense round size=xs"
                    ).classes("dim")

    def _remove_folder(self, folder: Path) -> None:
        self.folders = [f for f in self.folders if f != folder]
        self._draw_folders()

    async def _add_folder(self) -> None:
        picked = await components.choose_folder("Watch which folder?")
        if picked:
            path = Path(picked)
            if path not in self.folders:
                self.folders.append(path)
                self._draw_folders()

    # ------------------------------------------------------------------
    async def _run(self) -> None:
        self.go.disable()
        self.progress.visible = True

        def report(p: bootstrap.Progress) -> None:
            self.status.text = p.detail or p.stage

        # 1. Ollama itself.
        if not bootstrap.ollama_installed():
            self.status.text = "Installing Ollama…"
            self.progress.props("indeterminate")
            ok = await asyncio.to_thread(bootstrap.install_ollama, report)
            if not ok:
                self._fail(
                    "Couldn't install Ollama automatically. Install it from ollama.com, "
                    "then click again."
                )
                return

        # 2. The server.
        if not await asyncio.to_thread(bootstrap.server_running, self.c.settings.llm_host):
            self.status.text = "Starting Ollama…"
            await asyncio.to_thread(bootstrap.start_server)
            for _ in range(30):
                if await asyncio.to_thread(bootstrap.server_running, self.c.settings.llm_host):
                    break
                await asyncio.sleep(1)
            else:
                self._fail("Ollama is installed but won't start. Try launching it manually.")
                return

        # 3. The weights. This is the multi-gigabyte part, so show real progress — an
        #    indeterminate spinner for ten minutes is indistinguishable from a hang.
        self.progress.props(remove="indeterminate")
        failed = False
        for update in await asyncio.to_thread(
            lambda: list(bootstrap.pull_model(self.choice.name, self.c.settings.llm_host))
        ):
            report(update)
            if update.fraction is not None:
                self.progress.value = update.fraction
            if update.stage == "error":
                failed = True
        if failed:
            self._fail(self.status.text)
            return

        # 4. Remember all of it.
        settings = self.c.settings
        settings.llm_model = self.choice.name
        settings.watched_folders = self.folders
        settings.onboarded = True
        save_settings(settings)

        self.status.text = "Ready."
        self.progress.value = 1.0
        ui.notify("Setup complete — restart to start watching your folders", type="positive")
        await asyncio.sleep(0.6)
        ui.navigate.to("/")

    def _fail(self, message: str) -> None:
        self.progress.visible = False
        self.status.text = message
        self.go.enable()
