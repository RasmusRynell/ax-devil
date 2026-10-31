"""Names for the Workspace items that viewers show and the user can consider or ignore."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class ConsiderationItemRef:
    """Names one Workspace item that can be considered or ignored, by the key bookmarks use, so it survives a re-run."""

    item_id: str
    entry: str
    """The ``bookmark_entries`` key of the entry: "" for a Video Item, "<playlist name>/<entry title>" otherwise."""
    lane_index: int | None = None
    """The lane within the entry, or None for the entry itself."""


@dataclass(frozen=True, slots=True)
class ConsiderationItem:
    """One supported consideration item and its initial state."""

    ref: ConsiderationItemRef
    default_considered: bool


@dataclass(frozen=True, slots=True)
class OnScreenWorkspaceItem:
    """One item currently shown by a workspace viewer."""

    kind: Literal["video", "playlist_entry"]
    content_id: str
    entry: str = ""


class ConsiderationQuery(Protocol):
    """Read-only consideration state consumed by Workspace viewers."""

    def is_item_considered(self, item_ref: ConsiderationItemRef) -> bool:
        """Return whether the referenced item participates in navigation or layout."""
        ...
