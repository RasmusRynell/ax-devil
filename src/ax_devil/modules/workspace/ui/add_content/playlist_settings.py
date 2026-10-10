"""Settings panel for choosing a playlist resolver plugin.

Presents a combo box of registered resolver plugins and shows the selected
resolver's own settings widget.  When the selected resolver submits settings,
the panel emits a Playlist Item for that resolver and those settings.
"""

from __future__ import annotations

from contextlib import suppress
from typing import cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QStackedWidget, QVBoxLayout, QWidget

from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.plugin_system import (
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    PlaylistResolverPlugin,
    PlaylistResolverWidget,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import PlaylistItem, PlaylistSettings

logger = get_logger(__name__)

_INTRODUCTION = (
    "Build a playlist from video and overlay files on disk. Choose a source above, fill in its settings, "
    "and load the playlist to add it to the workspace. Available sources:"
)


def _wrapped_label(text: str) -> QLabel:
    """Return a plain-text label that wraps to the panel width."""
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _page(layout: QVBoxLayout) -> QWidget:
    """Return a stacked page holding the finished *layout*'s widgets, kept at the top by a trailing stretch."""
    page = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(Space.L)
    layout.addStretch(1)
    page.setLayout(layout)
    return page


class PlaylistSettingsUI(QWidget):
    """Settings panel that lets the user pick a resolver and configure it.

    Every resolver form is built up front as a stacked page under its plugin description, so the panel's preferred
    size already covers the largest form and selecting a resolver never changes the window size. Before a resolver
    is chosen, the first page explains the dialog and lists every available resolver.
    """

    item_ready = Signal(object)  # PlaylistItem

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Each resolver form, with its resolver's plugin id and display name.
        self._resolver_by_widget: dict[PlaylistResolverWidget, tuple[str, str]] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.L)

        layout.addWidget(QLabel("Playlist source"))

        self._resolver_combo = QComboBox()
        layout.addWidget(self._resolver_combo)

        self._resolver_pages = QStackedWidget()
        layout.addWidget(self._resolver_pages)
        layout.addStretch(1)
        self._resolver_combo.currentIndexChanged.connect(self._resolver_pages.setCurrentIndex)

        self._populate_resolvers()

    def _populate_resolvers(self) -> None:
        overview = QVBoxLayout()
        overview.addWidget(_wrapped_label(_INTRODUCTION))
        self._resolver_combo.addItem("Select a source...", None)

        for record in RuntimePluginRegistry.get_plugins(PLAYLIST_RESOLVER_PLUGIN_TYPE):
            if record.status != PluginStatus.LOADED:
                continue
            plugin_class = cast(type[PlaylistResolverPlugin] | None, record.plugin_class)
            if plugin_class is None:
                logger.warning(f"Could not find plugin class for {record.definition.plugin_id}")
                continue
            try:
                widget = plugin_class().create_settings_widget()
            except Exception as exc:
                plugin_id = record.definition.plugin_id
                logger.error(f"Resolver {plugin_id} failed to create its settings: {exc}", exc_info=True)
                continue
            widget.settings_submitted.connect(self._on_settings_submitted)
            name = record.definition.display_name
            self._resolver_by_widget[widget] = (record.definition.plugin_id, name)
            description = record.definition.description or ""
            overview.addWidget(_wrapped_label(f"{name}: {description}" if description else name))
            page = QVBoxLayout()
            if description:
                page.addWidget(_wrapped_label(description))
            page.addWidget(widget)
            self._resolver_combo.addItem(name, widget)
            self._resolver_pages.addWidget(_page(page))
        # The overview lists every resolver, so it is built last and shown first, matching the combo's first item.
        self._resolver_pages.insertWidget(0, _page(overview))
        self._resolver_pages.setCurrentIndex(0)

    def _on_settings_submitted(self, settings: PlaylistSettings) -> None:
        # A resolver the user switched away from may still submit; only the selected one supplies the playlist.
        widget = self._resolver_combo.currentData()
        if widget is None or self.sender() is not widget:
            return
        resolver_id, name = self._resolver_by_widget[cast(PlaylistResolverWidget, widget)]
        self.item_ready.emit(PlaylistItem(label=name, resolver=resolver_id, settings=settings))

    def cleanup(self) -> None:
        """Disconnect and release every resolver form."""
        for widget in self._resolver_by_widget:
            with suppress(RuntimeError, TypeError):
                widget.settings_submitted.disconnect(self._on_settings_submitted)
            widget.setParent(None)
            widget.deleteLater()
        self._resolver_by_widget.clear()
