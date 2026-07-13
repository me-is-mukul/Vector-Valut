"""App shell: a thin side rail and nothing else.

Chat is the app. The rail exists because "where did that file actually go?" and "stop
watching my Downloads" are questions the chat is a clumsy way to answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from nicegui import ui

from osdc.ui import theme

# Icon names must exist in the Material set: outlined variants use the `o_` prefix
# (Quasar convention), not an `_outlined` suffix — an unknown name renders as nothing,
# which is how Library and Images silently vanished from the rail in 0.1.0.
NAV = (
    ("/", "chat_bubble_outline", "Chat"),
    ("/library", "o_folder", "Library"),
    ("/images", "o_photo_library", "Images"),
    ("/settings", "tune", "Settings"),
)


@contextmanager
def shell(active: str) -> Iterator[None]:
    theme.apply()
    ui.query(".nicegui-content").classes("p-0 gap-0")

    with ui.row().classes("w-screen h-screen gap-0 flex-nowrap"):
        with ui.column().classes("rail h-full py-4 px-2 gap-1 items-center flex-shrink-0"):
            ui.icon("auto_awesome").classes("text-xl mb-4").style("color: var(--accent)").tooltip(
                "Vector Vault"
            )
            for path, icon, label in NAV:
                classes = "rail-item p-2" + (" active" if path == active else "")
                with (
                    ui.link(target=path).classes("no-underline"),
                    ui.element("div").classes(classes).tooltip(label),
                ):
                    ui.icon(icon).classes("text-lg")

        with ui.column().classes("flex-grow h-full min-w-0 gap-0"):
            yield
