"""System tray icon — what makes this a background app rather than a window.

The point of the whole product is that it files your downloads *while you are not looking*.
An app you have to keep open to get that is not the app the user asked for. So closing the
window hides it, the watcher keeps running, and the tray icon is how you get back.

Quitting is deliberately only possible from the tray menu. Closing a window and having a
process silently keep running is a dark pattern; closing a window that you *know* lives in
the tray is a feature. The setup wizard says so explicitly, and the toggle is in Settings.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


def _icon_image():  # type: ignore[no-untyped-def]
    """Draw the icon in code — a bundled .ico is one more thing for PyInstaller to lose."""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([4, 4, size - 4, size - 4], radius=14, fill=(99, 102, 241, 255))
    draw.rounded_rectangle([16, 20, 48, 30], radius=3, fill=(255, 255, 255, 235))
    draw.rounded_rectangle([16, 34, 40, 44], radius=3, fill=(255, 255, 255, 160))
    return image


class Tray:
    def __init__(
        self,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        tooltip: str = "Vector Vault — watching for new documents",
    ) -> None:
        self._on_open = on_open
        self._on_quit = on_quit
        self._tooltip = tooltip
        self._icon = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        try:
            import pystray
        except ImportError:
            logger.warning("pystray not installed — no tray icon, close will quit")
            return False

        menu = pystray.Menu(
            pystray.MenuItem("Open Vector Vault", self._open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )
        icon = pystray.Icon("vector-vault", _icon_image(), self._tooltip, menu)
        self._icon = icon

        # pystray's run() blocks forever, and the main thread belongs to the webview.
        self._thread = threading.Thread(target=icon.run, daemon=True, name="tray")
        self._thread.start()
        logger.info("Tray icon running — closing the window will hide it, not quit")
        return True

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
            self._icon = None

    def notify(self, title: str, message: str) -> None:
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
            except Exception:  # not every platform supports balloons
                logger.debug("Tray notification unsupported")

    # pystray hands the icon and item to callbacks; we want neither.
    def _open(self, *_args: object) -> None:
        self._on_open()

    def _quit(self, *_args: object) -> None:
        self.stop()
        self._on_quit()
