"""Settings resolution, especially through environment variables.

These exist because the original Phase 0 test suite built ``Settings(...)`` with init
kwargs everywhere and so never exercised the env-var source at all — which is precisely
where the app blew up the first time it was launched for real. ``OSDC_WATCHED_FOLDERS``
is a ``list[Path]``, and pydantic-settings tries to JSON-decode env vars for complex
types, so a plain Windows path was a JSON syntax error before any validator saw it.

The README documents these env vars as the primary way to configure the app, so they
get tested like a public interface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from osdc.config.settings import Settings, load_settings, save_settings
from osdc.domain.enums import FileAction


def test_a_single_watched_folder_from_env(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OSDC_WATCHED_FOLDERS", str(tmp_path / "Downloads"))
    assert load_settings().watched_folders == [tmp_path / "Downloads"]


def test_several_watched_folders_are_semicolon_separated(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Semicolon, because it is the Windows path separator and commas appear in filenames."""
    monkeypatch.setenv("OSDC_WATCHED_FOLDERS", f"{tmp_path / 'Downloads'};{tmp_path / 'Desktop'}")
    assert load_settings().watched_folders == [tmp_path / "Downloads", tmp_path / "Desktop"]


def test_a_windows_path_with_a_drive_letter_survives(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact string that crashed the first live run."""
    monkeypatch.setenv("OSDC_WATCHED_FOLDERS", r"C:\Users\someone\Downloads")
    assert load_settings().watched_folders == [Path(r"C:\Users\someone\Downloads")]


def test_no_watched_folders_is_valid(data_dir: Path) -> None:
    assert load_settings().watched_folders == []


def test_scalar_overrides_from_env(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSDC_FILE_ACTION", "move")
    monkeypatch.setenv("OSDC_ACADEMIC_THRESHOLD", "0.9")
    monkeypatch.setenv("OSDC_AUTO_APPROVE", "false")

    settings = load_settings()
    assert settings.file_action is FileAction.MOVE
    assert settings.academic_threshold == 0.9
    assert settings.auto_approve is False


def test_defaults_are_conservative(data_dir: Path) -> None:
    """Copy, not move. If this ever flips by accident, the app starts eating originals."""
    settings = load_settings()
    assert settings.file_action is FileAction.COPY
    assert settings.academic_threshold == 0.85
    assert settings.general_threshold == 0.70


def test_thresholds_must_be_probabilities(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSDC_ACADEMIC_THRESHOLD", "1.5")
    with pytest.raises(ValueError, match="less than or equal to 1"):
        load_settings()


def test_settings_round_trip_through_toml(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """TOML has no Path type, so paths go out as strings and must come back as Paths."""
    original = Settings(
        library_root=tmp_path / "Library",
        watched_folders=[tmp_path / "Downloads"],
        file_action=FileAction.MOVE,
        current_semester=5,
    )
    save_settings(original)

    # Env must not shadow what we are trying to read back from disk.
    for key in ("OSDC_LIBRARY_ROOT", "OSDC_WATCHED_FOLDERS", "OSDC_FILE_ACTION"):
        monkeypatch.delenv(key, raising=False)

    reloaded = load_settings()
    assert reloaded.library_root == tmp_path / "Library"
    assert reloaded.watched_folders == [tmp_path / "Downloads"]
    assert reloaded.file_action is FileAction.MOVE
    assert reloaded.current_semester == 5


def test_env_beats_the_toml_file(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    save_settings(Settings(file_action=FileAction.COPY))
    monkeypatch.setenv("OSDC_FILE_ACTION", "move")
    assert load_settings().file_action is FileAction.MOVE


def test_max_file_size_converts_to_bytes(data_dir: Path) -> None:
    assert Settings(max_file_size_mb=2).max_file_size_bytes == 2 * 1024 * 1024
