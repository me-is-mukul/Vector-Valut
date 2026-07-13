"""The native window — ours, not NiceGUI's.

NiceGUI's ``native=True`` mode runs pywebview in a **separate process** and, by its own
comment in ``native_mode.py``, does not bridge the ``closing`` event:

    # 'closing' is not bridged yet - it requires a synchronous round-trip to support
    # vetoing the close

Vetoing the close is exactly what close-to-tray *is*. And NiceGUI shuts the whole app down
when its window closes, which is exactly what we must not do — the entire product promise is
that it keeps filing your downloads after you close the window.

So we run NiceGUI as a plain local server on a thread and create the window ourselves on the
main thread. We then own the close event, the folder dialogs, and the process lifetime.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

#: The live window, if we are in desktop mode. The UI layer reaches for this to open native
#: folder pickers; in browser mode it stays None and the UI falls back to a typed path.
current: Any = None

_hidden = threading.Event()


def create(url: str, title: str = "Vector Vault", width: int = 1180, height: int = 800) -> Any:
    global current
    import webview

    current = webview.create_window(title, url, width=width, height=height, min_size=(900, 620))
    return current


def hide() -> None:
    if current is not None:
        current.hide()
        _hidden.set()


def show() -> None:
    if current is None:
        return
    try:
        current.show()
        current.restore()
    except Exception:
        logger.exception("Could not restore the window")
    _hidden.clear()


def is_hidden() -> bool:
    return _hidden.is_set()


async def choose_folder() -> str | None:
    """A real OS folder picker. Returns None in browser mode."""
    if current is None:
        return None

    import webview

    picked = current.create_file_dialog(webview.FOLDER_DIALOG)
    if not picked:
        return None
    return str(picked[0])
