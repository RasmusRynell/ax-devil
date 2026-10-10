"""Workspace doubles shared by workspace, viewer, and session tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, TypeVar, cast

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
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget
from ax_devil.modules.workspace.ui.workspace_prompts import SaveChoice
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

ContentT = TypeVar("ContentT", SeekableVideoContent, LiveVideoContent, PlaylistContent)


@dataclass(frozen=True, kw_only=True)
class ContentItem(WorkspaceItem):
    """An item kind that resolves to fixed Content, for tests that start from Content."""

    kind: ClassVar[str] = "test_content"
    contents: tuple[Content, ...]

    @property
    def default_name(self) -> str:
        return self.contents[0].display_name

    def _build_contents(self, context: ResolutionContext) -> Sequence[Content]:
        return self.contents

    def _fields_to_json(self, base_dir: Path | None) -> dict[str, Any]:
        raise NotImplementedError("Test Content is never saved.")

    @classmethod
    def _fields_from_json(cls, data: Mapping[str, Any], base_dir: Path | None) -> dict[str, Any]:
        raise NotImplementedError("Test Content is never saved.")


def content_item(*contents: Content, label: str = "") -> ContentItem:
    """Return an item resolving to *contents*, named after the first unless *label* is given."""
    return ContentItem(label=label, contents=contents)


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


class FakePrompts:
    """Workspace prompts answered by the test instead of dialogs; records what was asked."""

    def __init__(
        self,
        save_choice: SaveChoice = SaveChoice.CANCEL,
        open_path: Path | None = None,
        save_path: Path | None = None,
        replace: bool = False,
    ) -> None:
        self.save_choice = save_choice
        self.replace = replace
        self.asked_to_replace: list[Path] = []
        self.open_path = open_path
        self.save_path = save_path
        self.asked_to_save: list[str] = []
        self.suggested_save_paths: list[Path] = []
        self.errors: list[str] = []

    def ask_save_changes(self, workspace_name: str) -> SaveChoice:
        self.asked_to_save.append(workspace_name)
        return self.save_choice

    def choose_workspace_to_open(self) -> Path | None:
        return self.open_path

    def choose_save_path(self, suggested: Path) -> Path | None:
        self.suggested_save_paths.append(suggested)
        return self.save_path

    def confirm_replace(self, path: Path) -> bool:
        self.asked_to_replace.append(path)
        return self.replace

    def show_error(self, message: str) -> None:
        self.errors.append(message)


def inline_resolver(context: ResolutionContext | None = None) -> ItemResolver:
    """Return a resolver that resolves at once on the calling thread, with *context* or a fake one."""
    return ItemResolver(context or FakeResolutionContext(), in_background=False)


def store_with(content: ContentT) -> tuple[WorkspaceStore, ContentT]:
    """Return a store holding an item for *content*, and the content as resolved, with its identity."""
    store = WorkspaceStore(inline_resolver())
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
