"""Windows MSI installer.

    uv pip install -e ".[build]"
    uv run python build_msi.py bdist_msi
    # -> dist/VectorVault-0.1.0-win64.msi

**cx_Freeze, not WiX.** WiX v5 needs the .NET SDK and its custom actions are C#. cx_Freeze
is pip-installable, produces a real MSI, and everything we need from an installer (Start
Menu shortcut, autostart-at-login, clean uninstall) it does natively. The hardware
detection and model download live in the app's first-run wizard instead, which is both
easier to build and easier to fix — a bug in an MSI custom action means recutting the
installer; a bug in a Python screen means shipping a patch.

**The MSI is slim on purpose.** It carries the app, Torch, and the pipeline (~1.2 GB
installed). It does NOT carry Ollama or a multi-gigabyte LLM — bundling those makes a ~7 GB
installer *and* makes the hardware-based model recommendation pointless, because the model
would already be baked in. First run detects the machine, recommends a model that actually
fits, and fetches it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cx_Freeze import Executable, setup

# Torch's import graph is deep enough to blow Python's default 1000-frame limit while
# cx_Freeze walks it. This is the single most common reason a Torch app fails to freeze.
sys.setrecursionlimit(10_000)

ROOT = Path(__file__).parent
VERSION = "0.1.0"

# A stable GUID. Change it and Windows treats the next build as a *different product*, so
# users end up with two Vector Vaults installed side by side instead of an upgrade.
UPGRADE_CODE = "{6F3A9C2E-8B14-4D7A-9E5F-2C1B7D4A0E93}"

build_exe_options = {
    "packages": [
        "osdc",
        "nicegui",
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "alembic",
        "pydantic",
        "pydantic_settings",
        "watchdog",
        "sentence_transformers",
        "transformers",
        "torch",
        "PIL",
        "pystray",
        "webview",
        "ollama",
        "fitz",  # PyMuPDF
        "pdfplumber",
        "docx",
        "pptx",
        "yaml",
        "psutil",
    ],
    "includes": ["osdc.main"],
    "include_files": [
        # The Subject Knowledge Base has to ship — without it the academic classifier has
        # nothing to classify against and every document lands in Review.
        (str(ROOT / "src" / "osdc" / "data"), "lib/osdc/data"),
    ],
    "excludes": [
        # Torch drags these in and they are pure weight in a shipped app.
        "tkinter",
        "test",
        "unittest",
        "pytest",
        "matplotlib",
        "notebook",
        "IPython",
    ],
    "zip_include_packages": [],
    "optimize": 1,
    # Torch and transformers both do runtime introspection that a frozen zip breaks.
    "zip_exclude_packages": ["*"],
}

bdist_msi_options = {
    "upgrade_code": UPGRADE_CODE,
    "add_to_path": False,
    "initial_target_dir": r"[LocalAppDataFolder]\Programs\Vector Vault",
    "all_users": False,  # per-user install: no UAC prompt, and the app writes to LOCALAPPDATA
    "summary_data": {
        "author": "Mukul and Dhvani",
        "comments": "Local-first AI document organizer",
        "keywords": "documents ai search organize local",
    },
    # Start it at login. The whole point is that it files downloads while you are not
    # looking; an app you must remember to launch does not do that.
    "data": {
        "Registry": [
            (
                "VectorVaultStartup",
                -1,  # HKEY_CURRENT_USER
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                "Vector Vault",
                r'"[TARGETDIR]VectorVault.exe" --tray',
                "TARGETDIR",
            )
        ]
    },
}

executables = [
    Executable(
        script="src/osdc/main.py",
        # "gui" = no console window behind the app. (cx_Freeze 8 renamed this from Win32GUI.)
        base="gui" if sys.platform == "win32" else None,
        target_name="VectorVault.exe",
        shortcut_name="Vector Vault",
        shortcut_dir="ProgramMenuFolder",
        copyright="Mukul and Dhvani",
    )
]


def _set_properties(msi_path: Path, properties: dict[str, str]) -> None:
    """Fix what Add/Remove Programs shows.

    setuptools reads ``[project] name`` from pyproject.toml and overrides whatever ``setup()``
    was given, so the product would otherwise be listed under the lowercase *package* name
    with an UNKNOWN publisher. Rewriting the Property rows afterwards is the reliable fix;
    arguing with setuptools' pyproject handling is not. (``msilib`` was removed in Python
    3.13, hence the COM API.)
    """
    import win32com.client  # type: ignore[import-not-found]

    installer = win32com.client.Dispatch("WindowsInstaller.Installer")
    database = installer.OpenDatabase(str(msi_path), 1)  # 1 = transact
    for key, value in properties.items():
        escaped = value.replace("'", "''")
        view = database.OpenView(
            f"UPDATE `Property` SET `Value` = '{escaped}' WHERE `Property` = '{key}'"
        )
        view.Execute(None)
        view.Close()
    database.Commit()


setup(
    name="Vector Vault",
    version=VERSION,
    author="Mukul and Dhvani",
    description="Local-first AI document & image organizer",
    options={"build_exe": build_exe_options, "bdist_msi": bdist_msi_options},
    executables=executables,
)

if "bdist_msi" in sys.argv and sys.platform == "win32":
    built = next(iter((ROOT / "dist").glob("*.msi")), None)
    if built is not None:
        try:
            _set_properties(
                built, {"ProductName": "Vector Vault", "Manufacturer": "Mukul and Dhvani"}
            )
        except ImportError:
            print("\npywin32 not installed — display name left as the package name")
        # setuptools names the file after the pyproject package ("osdc"); the shipped
        # artifact should carry the product's actual name.
        final = built.with_name(f"VectorVault-{VERSION}-win64.msi")
        built.replace(final)
        print(f"\nInstaller ready: {final}  ({final.stat().st_size / 1e6:.0f} MB)")
