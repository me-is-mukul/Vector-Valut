from __future__ import annotations

from pathlib import Path

from osdc.ui.chat import _IMAGE_INTENT, _ORGANIZE_INTENT, _folder_in, _strip_intent
from osdc.ui.state import STATE, ChatEntry


def test_folder_in_accepts_bare_named_folders(tmp_path: Path, monkeypatch) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert _folder_in("organize downloads") == downloads


def test_folder_in_strips_terminal_punctuation(tmp_path: Path) -> None:
    folder = tmp_path / "Work"
    folder.mkdir()

    assert _folder_in(f"please organize {folder},") == folder


def test_folder_in_trims_trailing_words(tmp_path: Path) -> None:
    """Paths may contain spaces, so the regex eats trailing words — they must be trimmed
    back off until the real directory emerges."""
    folder = tmp_path / "My Documents"
    folder.mkdir()

    assert _folder_in(f"organize {folder} right now please") == folder


def test_folder_in_strips_copy_as_path_quotes(tmp_path: Path) -> None:
    folder = tmp_path / "Work"
    folder.mkdir()

    assert _folder_in(f'organize "{folder}"') == folder


def test_strip_intent_keeps_the_real_query() -> None:
    assert _strip_intent("show me photos of a bridge at sunset") == "a bridge at sunset"
    assert _strip_intent("photos") == "photos"


def test_organize_intent_requires_a_filing_verb_with_an_object() -> None:
    # These are filing requests.
    assert _ORGANIZE_INTENT.search("organize my downloads")
    assert _ORGANIZE_INTENT.search("please tidy this up")
    assert _ORGANIZE_INTENT.search("sort out my desktop")
    assert _ORGANIZE_INTENT.search("file these away for me")

    # These are questions that merely contain the words "file" or "sort".
    assert not _ORGANIZE_INTENT.search("what does that file say about my rent")
    assert not _ORGANIZE_INTENT.search("what sort of insurance do I have")


def test_image_intent_matches_photo_words() -> None:
    assert _IMAGE_INTENT.search("find photos of a dog")
    assert _IMAGE_INTENT.search("show me that screenshot")
    assert not _IMAGE_INTENT.search("summarise my tax return")


def test_chat_state_survives_and_resets() -> None:
    STATE.reset()
    STATE.chat.entries.append(ChatEntry(kind="user", data={"text": "hello"}))
    STATE.chat.draft = "half a thou"

    # Another "page build" sees the same state object.
    assert STATE.chat.entries[0].data["text"] == "hello"
    assert STATE.chat.draft == "half a thou"

    STATE.reset()
    assert STATE.chat.entries == []
    assert STATE.chat.draft == ""
