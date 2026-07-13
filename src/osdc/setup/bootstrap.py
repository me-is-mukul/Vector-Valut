"""First-run bootstrap: get Ollama installed and the chosen model pulled.

The MSI is deliberately slim (roadmap: a fully-bundled installer would be ~7 GB), so this
is what turns a fresh install into a working app. It runs once, shows real progress, and
everything after it is offline.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

OLLAMA_WINGET_ID = "Ollama.Ollama"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"

#: Ollama installs here on Windows but does not always land on PATH until the shell is
#: restarted — which, on a fresh install, it never is. So we look for it directly.
_WINDOWS_HINTS = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "Ollama" / "ollama.exe",
)


@dataclass(frozen=True)
class Progress:
    stage: str
    detail: str = ""
    fraction: float | None = None  # None = indeterminate


ProgressFn = Callable[[Progress], None]


def find_ollama() -> Path | None:
    found = shutil.which("ollama")
    if found:
        return Path(found)
    for hint in _WINDOWS_HINTS:
        if hint.is_file():
            return hint
    return None


def ollama_installed() -> bool:
    return find_ollama() is not None


def server_running(host: str = "http://127.0.0.1:11434") -> bool:
    try:
        from ollama import Client

        Client(host=host, timeout=3.0).list()
    except Exception:
        return False
    return True


def start_server() -> bool:
    """Ollama normally runs as a background service; nudge it if it is not up."""
    exe = find_ollama()
    if exe is None:
        return False
    try:
        creation = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        subprocess.Popen(
            [str(exe), "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation,
        )
    except OSError as exc:
        logger.warning("Could not start the Ollama server: %s", exc)
        return False
    return True


def install_ollama(on_progress: ProgressFn) -> bool:
    """Install via winget. Returns False if the user must do it by hand."""
    if ollama_installed():
        return True

    winget = shutil.which("winget")
    if not winget:
        on_progress(
            Progress(
                "error",
                f"winget is not available. Install Ollama manually from {OLLAMA_DOWNLOAD_URL}",
            )
        )
        return False

    on_progress(Progress("installing", "Installing Ollama…"))
    try:
        result = subprocess.run(
            [
                winget,
                "install",
                "--id",
                OLLAMA_WINGET_ID,
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "--disable-interactivity",
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        on_progress(Progress("error", f"Ollama install failed: {exc}"))
        return False

    if result.returncode != 0 and not ollama_installed():
        on_progress(Progress("error", f"Ollama install failed: {result.stdout[-300:]}"))
        return False

    on_progress(Progress("installed", "Ollama installed."))
    return True


def installed_models(host: str = "http://127.0.0.1:11434") -> set[str]:
    try:
        from ollama import Client

        response = Client(host=host, timeout=10.0).list()
    except Exception:
        return set()

    names: set[str] = set()
    for model in getattr(response, "models", []) or []:
        name = getattr(model, "model", None) or (
            model.get("model") if isinstance(model, dict) else None
        )
        if name:
            names.add(str(name))
    return names


def pull_model(model: str, host: str = "http://127.0.0.1:11434") -> Iterator[Progress]:
    """Stream real download progress. This is a multi-gigabyte download; a spinner with no
    percentage looks indistinguishable from a hang."""
    from ollama import Client

    if model in installed_models(host):
        yield Progress("ready", f"{model} is already downloaded.", 1.0)
        return

    client = Client(host=host, timeout=None)
    yield Progress("pulling", f"Downloading {model}…", 0.0)

    try:
        for event in client.pull(model, stream=True):
            status = str(getattr(event, "status", "") or "")
            total = getattr(event, "total", None) or 0
            completed = getattr(event, "completed", None) or 0

            fraction = (completed / total) if total else None
            detail = status
            if total:
                detail = f"{status} — {completed / 1e9:.1f} / {total / 1e9:.1f} GB"

            yield Progress("pulling", detail, fraction)
    except Exception as exc:
        yield Progress("error", f"Download failed: {exc}")
        return

    yield Progress("ready", f"{model} is ready.", 1.0)
