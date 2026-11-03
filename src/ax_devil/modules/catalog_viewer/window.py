"""The catalog viewer: a render catalog file drawn on example sheets, redrawn whenever the file changes.

Catalogs are written by hand or by an AI agent following ``.agents/skills/render-catalog/SKILL.md``; this window only
shows the result. It follows the catalog manager's file watching, so a saved file is redrawn at once, and an invalid
edit keeps that file's last version that loaded on screen with the error above it.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.background_jobs import LatestRequest
from ax_devil.modules.catalog_viewer.catalog_file_actions import CatalogFileActions
from ax_devil.modules.catalog_viewer.inspector import ObjectInspector
from ax_devil.modules.catalog_viewer.sheet_drawing import Sheet, drawing_errors, sheet_frame
from ax_devil.modules.catalog_viewer.sheets import sheets
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.chrome_window import ChromeWindow
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Radius, Space
from ax_devil.modules.scene.model import EntityId
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalog,
    SceneRenderCatalogListing,
    SceneRenderCatalogManager,
    SceneRenderCatalogRevision,
)
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_player.engine.renderer import VideoFrameRenderer

logger = get_logger(__name__)


class CatalogViewerWindow(ChromeWindow):
    """Show one render catalog file on example sheets: an overview, one sheet per object type and relation, a crowd.

    It opens on the catalog chosen as default and switches when another is chosen, here or by another program. A chosen
    default that stops loading stays on screen with its error; views meanwhile fall back to the default built-in
    catalog.
    """

    def __init__(
        self,
        manager: SceneRenderCatalogManager,
        parent: QWidget | None = None,
        *,
        use_custom_frame: bool = False,
    ) -> None:
        super().__init__(
            parent=parent, use_custom_frame=use_custom_frame, show_custom_frame_border=True, remember_size=True
        )
        self.setObjectName("catalogViewerWindow")
        self.setWindowTitle("Render Catalogs")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._manager = manager
        self._path = manager.listing().chosen_default_path
        self._default = self._path
        self._catalog: SceneRenderCatalog | None = None
        self._sheets: tuple[Sheet, ...] = ()
        self._error: str | None = None
        self._load_request = LatestRequest(self)
        self._action_request = LatestRequest(self)
        self._loaded_at: datetime | None = None
        self._shown_key: object = None
        self._description_status: StatusColor | None = None
        self._changes = 0

        self._catalogs = QComboBox(self)
        self._catalogs.setObjectName("catalogViewerCatalogs")
        self._catalogs.setMinimumContentsLength(16)
        self._catalogs.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._new = QPushButton("New copy", self)
        self._new.setObjectName("catalogViewerNew")
        self._new.setToolTip("Create a new catalog that starts as a copy of this one")
        self._delete = QPushButton("Delete", self)
        self._delete.setObjectName("catalogViewerDelete")
        self._apply_to_all = QPushButton("Apply to all", self)
        self._apply_to_all.setObjectName("catalogViewerApplyToAll")
        self._apply_to_all.setToolTip("Use this catalog in every open viewer and lane")
        self._use_as_default = QPushButton("Use as default", self)
        self._use_as_default.setObjectName("catalogViewerUseAsDefault")
        self._use_as_default.setToolTip("Start new viewers and lanes with this catalog")
        self._file = ElidedLabel("", Qt.TextElideMode.ElideMiddle, self)
        self._file.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._file.setObjectName("catalogViewerFile")
        self._state = QLabel(self)
        self._state.setObjectName("catalogViewerState")
        self._banner = QLabel(self)
        self._banner.setObjectName("catalogViewerError")
        self._banner.setWordWrap(True)
        self._banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._banner.hide()
        self._tabs = QTabBar(self)
        self._tabs.setObjectName("catalogViewerTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.setExpanding(False)
        self._tabs.setUsesScrollButtons(True)
        self._renderer = VideoFrameRenderer(self)
        self._renderer.setObjectName("catalogViewerRenderer")
        self._inspector = ObjectInspector(self)
        self._selected: str | None = None
        self._description = QLabel(self)
        self._description.setWordWrap(True)
        self._build()

        self._catalogs.activated.connect(lambda index: self.show_catalog(self._catalogs.itemData(index)))
        self._tabs.currentChanged.connect(lambda _index: self._redraw())
        self._renderer.selectionChanged.connect(self._on_selected)
        # Kept alive by the buttons' connections; an attribute would make a reference cycle with this window.
        actions = CatalogFileActions(
            self,
            manager,
            self._action_request,
            path=self.catalog_path,
            show_catalog=self.show_catalog,
            reload=self._load,
            load_failed=self._on_load_failed,
        )
        self._new.clicked.connect(lambda: actions.create_copy())
        self._delete.clicked.connect(lambda: actions.delete_catalog())
        self._apply_to_all.clicked.connect(lambda: actions.apply_to_all())
        self._use_as_default.clicked.connect(lambda: actions.use_as_default())
        manager.catalogFileChanged.connect(self._on_file_changed)
        manager.catalogListingChanged.connect(self._on_listing_changed)
        manager.catalogUsabilityChanged.connect(self._show_state)
        self.show_catalog(self._path)
        follow_appearance(self, self._apply_appearance)

    def _apply_appearance(self) -> None:
        """Recolor the error banner, state and any drawing-error description from the current palette."""
        self._apply_error_style()
        self._show_state()
        self._style_description()

    def _style_description(self) -> None:
        status = self._description_status
        self._description.setStyleSheet(status.css(self.palette()) if status is not None else "")

    def _apply_error_style(self) -> None:
        color = StatusColor.ERROR.color(self.palette()).name()
        self._banner.setStyleSheet(
            f"color: {color}; border: 1px solid {color}; border-radius: {Radius.CONTROL}px;"
            f"padding: {Space.S}px {Space.M}px; background: palette(alternate-base);"
        )

    def show_catalog(self, path: Path) -> None:
        """Show the catalog file at *path*."""
        if path != self._path:
            self._action_request.cancel()
            self._catalog, self._sheets, self._changes, self._error = None, (), 0, None
        self._path = path
        self._load()

    def catalog_path(self) -> Path:
        """Return the path of the catalog file shown."""
        return self._path

    def sheet_titles(self) -> list[str]:
        """Return the titles of the example sheets, in tab order."""
        return [self._tabs.tabText(index) for index in range(self._tabs.count())]

    def error(self) -> str | None:
        """Return why the file does not load, or None when the version shown is the file as it is."""
        return self._error

    def cleanup(self) -> None:
        """Stop following the catalog manager and release the renderer."""
        self._load_request.close()
        self._action_request.close()
        self._manager.catalogFileChanged.disconnect(self._on_file_changed)
        self._manager.catalogListingChanged.disconnect(self._on_listing_changed)
        self._manager.catalogUsabilityChanged.disconnect(self._show_state)
        self._renderer.cleanup()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Release resources when the window closes."""
        self.cleanup()
        super().closeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Draw anything that changed while the window was hidden."""
        super().showEvent(event)
        self._redraw()

    # Loading ---------------------------------------------------------------------------------------------------

    def _load(self) -> None:
        self._load_request.start(
            lambda result, error: self._manager.request_revision(self._path, on_loaded=result, on_failed=error),
            on_result=self._on_loaded,
            on_error=self._on_load_failed,
        )
        if self._load_request.pending:
            self._show_all()

    def _on_loaded(self, revision: SceneRenderCatalogRevision) -> None:
        self._error = None
        self._loaded_at = datetime.now()
        if revision.catalog is not self._catalog:
            self._catalog, self._sheets = revision.catalog, sheets(revision.document)
        self._show_all()

    def _on_load_failed(self, error: Exception) -> None:
        self._error = str(error)
        logger.info(f"Render catalog {self._path.name} does not load: {error}")
        self._show_all()

    def _show_all(self) -> None:
        self._show_listing(self._manager.listing())
        self._show_state()
        self._show_tabs()
        self._redraw()

    def _on_listing_changed(self, listing: SceneRenderCatalogListing) -> None:
        """Follow a newly chosen default catalog, such as one an agent made the default; otherwise refresh the choices.

        Only a new choice counts: a chosen default that stops loading makes ``default_path`` the default built-in
        catalog, and the viewer keeps showing the broken file with its error instead.
        """
        if listing.chosen_default_path != self._default:
            self._default = listing.chosen_default_path
            self.show_catalog(self._default)
            return
        self._show_listing(listing)
        self._show_state()

    def _on_file_changed(self, path: Path) -> None:
        if path == self._path:
            self._changes += 1
            self._load()

    # Showing ---------------------------------------------------------------------------------------------------

    def _show_listing(self, listing: SceneRenderCatalogListing) -> None:
        paths = [catalog.path for catalog in listing.catalogs]
        if self._path not in paths:
            paths.append(self._path)
        self._catalogs.blockSignals(True)
        self._catalogs.clear()
        for path in paths:
            self._catalogs.addItem(listing.label(path), path)
        self._catalogs.setCurrentIndex(paths.index(self._path))
        self._catalogs.blockSignals(False)
        self._catalogs.setToolTip(listing.label(self._path))  # The closed box elides a long name.
        self._file.set_full_text(str(self._path))

    def _show_state(self) -> None:
        loads = self._error is None and not self._load_request.pending
        self._new.setEnabled(loads)
        self._new.setToolTip(
            "Create a new catalog that starts as a copy of this one"
            if loads
            else "Only a catalog that has loaded can be copied"
        )
        deletable = self._manager.catalog_store.is_user_catalog(self._path)
        self._delete.setEnabled(deletable)
        self._delete.setToolTip(
            "Delete this catalog file" if deletable else "Built-in catalogs come with the app and cannot be deleted"
        )
        self._apply_to_all.setEnabled(self._manager.can_apply_to_all(self._path))
        self._use_as_default.setEnabled(self._manager.can_use_as_default(self._path))
        if self._load_request.pending:
            self._state.setText("● Loading")
            self._state.setStyleSheet("")
            self._banner.hide()
            return
        if self._error is not None:
            self._state.setText("● Does not load")
            self._state.setStyleSheet(StatusColor.ERROR.css(self.palette()))
            shown = "Showing the last version that loaded." if self._catalog is not None else "Nothing to show yet."
            self._banner.setText(f"{self._error}\n{shown}")
            self._banner.show()
            return
        updated = f" · updated {self._loaded_at:%H:%M:%S}" if self._changes and self._loaded_at is not None else ""
        listed = self._manager.listing().catalog_for(self._path)
        origin = f" · {listed.origin.note}" if listed is not None and listed.origin is not None else ""
        self._state.setText(f"● Live{updated}{origin}")
        self._state.setToolTip("Redrawn every time the catalog file is saved")
        self._state.setStyleSheet(StatusColor.SUCCESS.css(self.palette()))
        self._banner.hide()

    def _show_tabs(self) -> None:
        titles = [sheet.title for sheet in self._sheets]
        if titles == self.sheet_titles():
            return
        current = self._current_sheet()
        keys = [sheet.key for sheet in self._sheets]
        self._tabs.blockSignals(True)
        while self._tabs.count():
            self._tabs.removeTab(0)
        for sheet in self._sheets:
            index = self._tabs.addTab(sheet.title)
            self._tabs.setTabToolTip(index, sheet.description)
        if current is not None and current.key in keys:
            self._tabs.setCurrentIndex(keys.index(current.key))
        self._tabs.blockSignals(False)

    def _current_sheet(self) -> Sheet | None:
        index = self._tabs.currentIndex()
        return self._sheets[index] if 0 <= index < len(self._sheets) else None

    def _redraw(self) -> None:
        sheet = self._current_sheet()
        if sheet is None or self._catalog is None:
            self._renderer.clear()
            self._description.clear()
            self._description_status = None
            self._style_description()
            self._shown_key = None
            return
        if not self.isVisible():
            return
        key = (id(self._catalog), sheet.key)
        if key == self._shown_key:
            return
        self._shown_key = key
        self._renderer.display_frame(sheet_frame(sheet, self._catalog))
        diagnostics = drawing_errors(sheet, self._catalog)
        if diagnostics:
            first, count = diagnostics[0], len(diagnostics)
            plural = "s" if count > 1 else ""
            self._description.setText(f"{count} drawing error{plural}: {first.location}: {first.message}")
            self._description_status = StatusColor.ERROR
        else:
            self._description.setText(sheet.description)
            self._description_status = None
        self._style_description()
        self._show_selection()

    def _on_selected(self, target_id: str | None) -> None:
        self._selected = target_id
        self._show_selection()

    def _show_selection(self) -> None:
        """Show the clicked object's attributes from the sheet shown; an object the sheet lacks is deselected."""
        sheet = self._current_sheet()
        entity = None
        if sheet is not None and self._selected is not None:
            entity = sheet.scene.entities.get(EntityId(self._selected))
            if entity is None:
                self._selected = None
                self._renderer.select_target(None)
        self._inspector.show_entity(entity)

    def _build(self) -> None:
        header = QHBoxLayout()
        header.setSpacing(Space.M)
        header.addWidget(QLabel("Catalog", self))
        header.addWidget(self._catalogs)
        header.addWidget(self._new)
        header.addWidget(self._delete)
        header.addWidget(self._apply_to_all)
        header.addWidget(self._use_as_default)
        header.addSpacing(Space.S)
        header.addWidget(self._file, 1)
        header.addSpacing(Space.L)
        header.addWidget(self._state)
        central = QFrame(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(Space.L, Space.M, Space.L, Space.M)
        layout.setSpacing(Space.M)
        layout.addLayout(header)
        layout.addWidget(self._banner)
        layout.addWidget(self._tabs)
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._renderer, 1)
        body.addWidget(self._inspector)
        layout.addLayout(body, 1)
        layout.addWidget(self._description)
        self.setCentralWidget(central)
