"""Mutable workspace content and consideration state with mutation signals."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.content import ConsiderationItemRef, Content

logger = get_logger(__name__)


class WorkspaceManager(QObject):
    """Own workspace content, consideration state, and mutation signals."""

    contents_added = Signal(list)
    content_removed = Signal(object)
    item_consideration_changed = Signal(object, bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._contents: list[Content] = []
        self._not_considered: set[ConsiderationItemRef] = set()

    def add_content(self, content: Content) -> None:
        """Append *content* and emit one addition event."""
        self.add_contents([content])

    def add_contents(self, contents: Sequence[Content]) -> None:
        """Append multiple content items and emit one batch addition event."""
        if not contents:
            return
        added_contents = list(contents)
        for content in added_contents:
            self._contents.append(content)
            self._seed_consideration_defaults(content)
            logger.debug(f"Content added: {content.content_id}")
        self.contents_added.emit(added_contents)

    def remove_content(self, content: Content) -> None:
        """Remove *content* and emit ``content_removed`` when it existed."""
        try:
            self._contents.remove(content)
        except ValueError:
            logger.warning(f"Attempted to remove content not in workspace: {content.content_id}")
            return
        self._discard_refs_for_content(content.content_id)
        logger.debug(f"Content removed: {content.content_id}")
        self.content_removed.emit(content)

    def get_contents(self) -> list[Content]:
        """Return a shallow copy of the workspace contents."""
        return list(self._contents)

    def is_item_considered(self, item_ref: ConsiderationItemRef) -> bool:
        """Return whether the referenced item participates in navigation/layout."""
        return item_ref not in self._not_considered

    def set_item_considered(self, item_ref: ConsiderationItemRef, considered: bool) -> None:
        """Update consideration state and emit a change signal when it changes."""
        if not self._contains_consideration_ref(item_ref):
            logger.warning(f"Ignoring consideration change for unknown Workspace item: {item_ref}")
            return
        if self.is_item_considered(item_ref) == considered:
            return
        if considered:
            self._not_considered.discard(item_ref)
        else:
            self._not_considered.add(item_ref)
        logger.debug(f"Item consideration changed: {item_ref} considered={considered}")
        self.item_consideration_changed.emit(item_ref, considered)

    def _seed_consideration_defaults(self, content: Content) -> None:
        """Populate initial not-considered refs from content default flags."""
        self._discard_refs_for_content(content.content_id)
        self._not_considered.update(item.ref for item in content.consideration_items() if not item.default_considered)

    def _contains_consideration_ref(self, item_ref: ConsiderationItemRef) -> bool:
        """Return whether *item_ref* identifies a current Workspace item."""
        return any(
            any(item.ref == item_ref for item in content.consideration_items())
            for content in self._contents
            if content.content_id == item_ref.content_id
        )

    def _discard_refs_for_content(self, content_id: str) -> None:
        """Remove all consideration refs belonging to the given content."""
        self._not_considered = {ref for ref in self._not_considered if ref.content_id != content_id}
