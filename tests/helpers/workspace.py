"""Workspace doubles shared by workspace, viewer, and session tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, TypeVar, cast

from PySide6.QtWidgets import QWidget

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace.core import (
    ConsiderationItemRef,
    ConsiderationQuery,
    Content,
    ItemResolutionError,
    LiveVideoContent,
    PlaylistContent,
    PlaylistResolver,
    ResolutionContext,
    SeekableVideoContent,
    WorkspaceDecoderOption,
    WorkspaceIntake,
    WorkspaceItem,
)
from ax_devil.modules.workspace.core.content import OnScreenWorkspaceItem
from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

ContentT = TypeVar("ContentT", SeekableVideoContent, LiveVideoContent, PlaylistContent)


@dataclass(frozen=True, kw_only=True)
class ContentItem(WorkspaceItem):
    """An item kind that resolves to fixed Content, for tests that start from Content."""

    kind: ClassVar[str] = "test_content"
    contents: tuple[Content, ...]

    def _build_contents(self, context: ResolutionContext) -> Sequence[Content]:
        return self.contents


def content_item(*contents: Content, label: str = "") -> ContentItem:
    """Return an item resolving to *contents*, labeled after the first unless *label* is given."""
    return ContentItem(label=label or contents[0].display_name, contents=contents)


class StaticDecoderOptions:
    """Decoder options fixed by a test."""

    def __init__(
        self,
        file_options: Sequence[WorkspaceDecoderOption] = (),
        live_options: Sequence[WorkspaceDecoderOption] = (),
    ) -> None:
        self._file_options = tuple(file_options)
        self._live_options = tuple(live_options)

    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return self._file_options

    def live_overlay_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return self._live_options


class FakeResolutionContext:
    """Resolution context with a test intake and playlist resolvers keyed by id."""

    def __init__(
        self,
        intake: WorkspaceIntake | None = None,
        resolvers: Mapping[str, PlaylistResolver] | None = None,
    ) -> None:
        self._intake = intake or WorkspaceIntake(StaticDecoderOptions())
        self._resolvers = dict(resolvers or {})

    @property
    def intake(self) -> WorkspaceIntake:
        return self._intake

    def playlist_resolver(self, resolver_id: str) -> PlaylistResolver:
        if resolver_id not in self._resolvers:
            raise ItemResolutionError(f"Playlist resolver '{resolver_id}' is not installed.")
        return self._resolvers[resolver_id]


def store_with(content: ContentT) -> tuple[WorkspaceStore, ContentT]:
    """Return a store holding an item for *content*, and the content as resolved, with its identity."""
    store = WorkspaceStore(FakeResolutionContext())
    store.add_items([content_item(content)])
    return store, cast(ContentT, store.contents()[0])


class DummyViewer(ViewerWidget):
    """Record workspace routing and lifecycle without opening media sources."""

    def __init__(
        self,
        payload: object,
        render_catalog_manager: SceneRenderCatalogManager,
        start_index: int = 0,
        parent: QWidget | None = None,
        consideration_query: ConsiderationQuery | None = None,
    ) -> None:
        self.payload = payload
        self.start_index = start_index
        self.consideration_query = consideration_query
        self.render_catalog_manager = render_catalog_manager
        self.cleaned_up = False
        self.refresh_calls: list[tuple[object, bool]] = []
        super().__init__(parent)

    def _setup_widget_ui(self) -> None:
        pass

    def get_display_name(self) -> str:
        """Use the content label for workspace titles."""
        return getattr(self.payload, "display_name", "dummy")

    def current_on_screen_item(self) -> OnScreenWorkspaceItem | None:
        """Project the selected entry into the workspace browser."""
        if isinstance(self.payload, (SeekableVideoContent, LiveVideoContent)):
            return OnScreenWorkspaceItem(kind="video", content_id=self.payload.content_id)
        if isinstance(self.payload, PlaylistContent):
            return OnScreenWorkspaceItem(
                kind="playlist_entry",
                content_id=self.payload.content_id,
                entry_index=self.start_index,
            )
        return None

    def cleanup(self) -> None:
        """Record that the workspace disposed this viewer."""
        self.cleaned_up = True

    def refresh_item_consideration(self, item_ref: ConsiderationItemRef, considered: bool) -> None:
        """Record consideration updates forwarded by the workspace."""
        self.refresh_calls.append((item_ref, considered))
