"""UI state that survives switching pages on the rail.

Every rail click is a full NiceGUI page navigation, and pages are rebuilt from scratch —
which used to throw away the chat transcript, the image search results, and whatever was
half-typed in the composer. This module is where that state lives instead.

Process-wide on purpose: this is a local, single-user desktop app, so "the process" IS the
session. No cookies, no client storage, nothing to expire — close the app, lose the
transcript; switch pages, keep it. That is exactly the contract a user expects from a
desktop tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatEntry:
    """One rendered block of the conversation, replayable after navigation.

    ``kind`` selects the renderer in ``ChatPane._render``; ``data`` is its payload and
    doubles as the mutable record of what happened to it ("state" of a plan, "undone" on
    an apply) so buttons never come back from the dead on replay.
    """

    kind: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatState:
    entries: list[ChatEntry] = field(default_factory=list)
    draft: str = ""


@dataclass
class ImagesState:
    query: str = ""
    #: The last results and the query they answered — None means "never searched".
    hits: list[Any] | None = None
    searched_query: str = ""


@dataclass
class UiState:
    chat: ChatState = field(default_factory=ChatState)
    images: ImagesState = field(default_factory=ImagesState)

    def reset(self) -> None:
        """Test seam; also handy if a 'clear conversation' action ever ships."""
        self.chat = ChatState()
        self.images = ImagesState()


STATE = UiState()
