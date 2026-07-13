"""Entrypoint.

One process holds everything: the FastAPI app (``nicegui.app`` *is* a FastAPI instance, so
the routers mount straight onto it), the NiceGUI UI, the folder watcher, the pipeline
workers, and the tray icon.

**Desktop mode does not use NiceGUI's ``native=True``.** That mode runs pywebview in a
separate process and does not bridge the window's ``closing`` event — see
``desktop/window.py`` — so it cannot be vetoed, and NiceGUI kills the app when the window
closes. Since "keeps running after you close the window" is the whole point of this product,
we serve the UI on localhost and own the window ourselves.

    VectorVault.exe            open the window
    VectorVault.exe --tray     start hidden in the tray (this is what runs at login)
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import urllib.request

from nicegui import app, ui

from osdc.api import routes
from osdc.config import paths
from osdc.config.settings import load_settings
from osdc.container import Container
from osdc.desktop import window
from osdc.desktop.tray import Tray
from osdc.ui.pages import register_pages

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    paths.ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(paths.log_dir() / "osdc.log", encoding="utf-8"),
        ],
    )
    for noisy in ("watchdog", "httpx", "sentence_transformers", "PIL", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build() -> Container:
    _configure_logging()
    container = Container(settings=load_settings())

    routes.bind(container)
    app.include_router(routes.router)
    register_pages(container)

    app.on_startup(container.start)
    app.on_shutdown(container.stop)

    logger.info("Library:  %s", container.settings.library_root)
    logger.info("Watching: %s", container.settings.watched_folders or "(nothing yet)")
    logger.info("Data:     %s", paths.data_dir())
    return container


def _wait_for_server(url: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:
            time.sleep(0.25)
    return False


def _run_desktop(container: Container) -> None:
    """Server on a thread, window on the main thread — because only the main thread may own
    a native window, and only we can veto its close."""
    import webview

    settings = container.settings
    port = settings.ui_port
    url = f"http://127.0.0.1:{port}"

    server = threading.Thread(
        target=lambda: ui.run(
            native=False,
            show=False,
            port=port,
            dark=True,
            reload=False,
            title="Vector Vault",
            favicon="🗂️",
        ),
        daemon=True,
        name="ui-server",
    )
    server.start()

    if not _wait_for_server(f"{url}/api/health"):
        logger.error("The UI server never came up")
        return

    start_hidden = "--tray" in sys.argv
    win = window.create(url)

    tray = Tray(on_open=window.show, on_quit=lambda: _quit(win))
    has_tray = tray.start()

    def on_closing() -> bool:
        """False cancels the close. We hide instead, and keep filing downloads."""
        if not settings.close_to_tray or not has_tray:
            return True  # the user opted out — closing really does quit
        window.hide()
        tray.notify(
            "Still running",
            "Vector Vault is filing new downloads in the background. Quit from this icon.",
        )
        return False

    win.events.closing += on_closing

    if start_hidden:
        # Autostart at login. Nobody wants a window in their face every boot.
        win.events.shown += lambda: window.hide()

    try:
        webview.start()  # blocks until the window is destroyed
    finally:
        tray.stop()
        app.shutdown()


def _quit(win: object) -> None:
    """Quit for real. Only reachable from the tray menu."""
    logger.info("Quitting")
    try:
        win.destroy()  # type: ignore[attr-defined]
    except Exception:
        logger.exception("Could not destroy the window")


def main() -> None:
    container = build()
    settings = container.settings

    if settings.ui_native:
        _run_desktop(container)
    else:
        ui.run(
            title="Vector Vault",
            native=False,
            port=settings.ui_port,
            dark=True,
            reload=False,
            show=True,
            favicon="🗂️",
        )


# NiceGUI re-executes this module under uvicorn's reloader and multiprocessing spawn;
# the guard is what stops the container being built twice.
if __name__ in {"__main__", "__mp_main__"}:
    main()
