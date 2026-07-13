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


def pick_folder(title: str = "Choose a folder") -> str | None:
    """A real OS folder picker, shown IN FRONT of the window. Blocking; call off the loop.

    Why not ``window.create_file_dialog``: pywebview invokes the dialog on whatever thread
    called it, while the owning window lives on the WinForms UI thread. Windows treats a
    dialog whose owner belongs to another thread as unrelated for z-ordering, so it opens
    *behind* the app — the classic "where did my dialog go" bug. The fix is to marshal the
    call onto the window's own UI thread with ``Control.Invoke``, which makes the ownership
    real and puts the dialog on top, modal, where it belongs.
    """
    if current is None:
        return None

    try:
        return _owned_folder_dialog(title)
    except Exception:
        # Non-Windows backend, or pywebview internals moved. The fallback works
        # everywhere; it just loses the z-order guarantee.
        logger.exception("Owned folder dialog failed; falling back to pywebview's")
        import webview

        picked = current.create_file_dialog(webview.FOLDER_DIALOG)
        return str(picked[0]) if picked else None


def _owned_folder_dialog(title: str) -> str | None:
    from pathlib import Path

    from System import Action  # type: ignore[import-not-found]  # pythonnet, via pywebview
    from webview.platforms.winforms import BrowserView, OpenFolderDialog

    form = BrowserView.instances[current.uid]
    result: dict[str, tuple[str, ...] | None] = {"paths": None}

    def show_on_ui_thread() -> None:
        result["paths"] = OpenFolderDialog.show(form, str(Path.home()), False, title)

    # Synchronous: returns only after the user closes the dialog.
    form.Invoke(Action(show_on_ui_thread))

    paths = result["paths"]
    return str(paths[0]) if paths else None
