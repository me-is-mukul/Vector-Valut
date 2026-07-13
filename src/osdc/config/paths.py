"""Per-OS application paths.

Databases and indexes never live next to the source tree — they go where the OS
says user data belongs (``%LOCALAPPDATA%\\osdc`` on Windows, ``~/.local/share/osdc``
on Linux, ``~/Library/Application Support/osdc`` on macOS).

``OSDC_DATA_DIR`` overrides the root, which is what the test suite uses to get a
throwaway install per test.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "osdc"
_dirs = PlatformDirs(appname=APP_NAME, appauthor=False, roaming=False)

_DATA_DIR_ENV = "OSDC_DATA_DIR"


def data_dir() -> Path:
    """Root for everything the app writes."""
    override = os.environ.get(_DATA_DIR_ENV)
    return Path(override) if override else Path(_dirs.user_data_dir)


def config_file() -> Path:
    return data_dir() / "settings.toml"


def db_path() -> Path:
    return data_dir() / "osdc.sqlite3"


def db_url() -> str:
    return f"sqlite:///{db_path()}"


def vector_dir() -> Path:
    return data_dir() / "vectors"


def log_dir() -> Path:
    return data_dir() / "logs"


def default_library_root() -> Path:
    return Path.home() / "AI Library"


def ensure_dirs() -> None:
    for d in (data_dir(), vector_dir(), log_dir()):
        d.mkdir(parents=True, exist_ok=True)
