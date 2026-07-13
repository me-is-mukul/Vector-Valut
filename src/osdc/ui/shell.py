"""App shell: a thin side rail and nothing else.

Chat is the app. The rail exists because "where did that file actually go?" and "stop
watching my Downloads" are questions the chat is a clumsy way to answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from nicegui import ui

from osdc.ui import theme

if TYPE_CHECKING:
    from osdc.container import Container

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
def shell(active: str, container: Container | None = None) -> Iterator[None]:
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
            if container is not None:
                _status_banner(container, show_chat_link=active != "/")
            yield


def _status_banner(container: Container, show_chat_link: bool) -> None:
    with ui.row().classes("status-banner w-full items-center justify-between gap-3 px-6 py-3"):
        message = ui.label().classes("text-xs")
        action = ui.button("Go to Chat", on_click=lambda: ui.navigate.to("/")).props(
            "flat dense"
        ).classes("dim")
        action.visible = False  # nothing to act on until the first refresh says so

        async def refresh() -> None:
            try:
                stats = await container.library.stats()
            except Exception:
                message.text = "Working status unavailable right now."
                action.visible = False
                return

            active_items = stats.queued + stats.running + container.queue.depth
            if active_items:
                message.text = (
                    f"Processing {active_items} item{'s' if active_items != 1 else ''} "
                    f"with {stats.review_count} waiting in review."
                )
            elif stats.review_count:
                message.text = (
                    f"{stats.review_count} file{'s' if stats.review_count != 1 else ''} "
                    "waiting in review."
                )
            else:
                message.text = "All caught up."
            # "Go to Chat" is noise when you are already looking at the chat.
            action.visible = show_chat_link and bool(active_items or stats.review_count)

        # One immediate paint, then a steady 2s cadence. (Two *repeating* timers here
        # used to poll the database ten times a second, forever, on every page.)
        ui.timer(0.1, refresh, once=True)
        ui.timer(2.0, refresh)
