"""Navigation policy for offline video viewers."""

from __future__ import annotations

from ax_devil.modules.workspace import (
    ConsiderationItemRef,
    ConsiderationQuery,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)


class OfflineViewerNavigation:
    """Encapsulate entry and lane consideration rules for offline viewers."""

    def __init__(
        self,
        content: SeekableVideoContent | PlaylistContent,
        consideration_query: ConsiderationQuery | None = None,
    ) -> None:
        self._content = content
        self._consideration_query = consideration_query

    def get_entries(self) -> tuple[PlaylistEntry, ...]:
        """Return the effective playlist entries for the configured content."""
        if isinstance(self._content, PlaylistContent):
            return self._content.entries
        if isinstance(self._content, SeekableVideoContent):
            return (PlaylistEntry(lanes=self._content.standalone_lanes(), default_considered=True),)
        return ()

    def is_entry_considered(self, entry_index: int) -> bool:
        """Return whether a playlist entry participates in stepping."""
        if not isinstance(self._content, PlaylistContent) or self._consideration_query is None:
            return True
        item_ref = ConsiderationItemRef.playlist_entry(self._content.content_id, entry_index)
        return self._consideration_query.is_item_considered(item_ref)

    def is_lane_considered(self, entry_index: int, lane_index: int) -> bool:
        """Return whether a lane should participate in active entry layout."""
        if self._consideration_query is None:
            return True
        if isinstance(self._content, PlaylistContent):
            item_ref = ConsiderationItemRef.playlist_lane(self._content.content_id, entry_index, lane_index)
            return self._consideration_query.is_item_considered(item_ref)
        if isinstance(self._content, SeekableVideoContent):
            item_ref = ConsiderationItemRef.video_lane(self._content.content_id, lane_index)
            return self._consideration_query.is_item_considered(item_ref)
        return True

    def considered_entry_indices(self) -> list[int]:
        """Return playlist entry indices that should participate in stepping."""
        entries = self.get_entries()
        if not isinstance(self._content, PlaylistContent):
            return list(range(len(entries)))
        return [index for index in range(len(entries)) if self.is_entry_considered(index)]

    def normalize_entry_index(self, index: int) -> int:
        """Choose a valid starting index, preferring considered playlist entries."""
        entries = self.get_entries()
        if not entries:
            return 0
        bounded_index = min(max(index, 0), len(entries) - 1)
        considered_indices = self.considered_entry_indices()
        if not considered_indices:
            return bounded_index
        if bounded_index in considered_indices:
            return bounded_index
        for considered_index in considered_indices:
            if considered_index > bounded_index:
                return considered_index
        return considered_indices[0]

    def find_prev_considered_index(self, current_index: int) -> int | None:
        """Return the previous considered playlist entry index, if one exists."""
        for index in reversed(self.considered_entry_indices()):
            if index < current_index:
                return index
        return None

    def find_next_considered_index(self, current_index: int) -> int | None:
        """Return the next considered playlist entry index, if one exists."""
        for index in self.considered_entry_indices():
            if index > current_index:
                return index
        return None

    def replacement_index_for_current_entry(self, current_index: int) -> int | None:
        """Return the best replacement when the active entry is no longer considered."""
        next_index = self.find_next_considered_index(current_index)
        if next_index is not None:
            return next_index
        return self.find_prev_considered_index(current_index)
