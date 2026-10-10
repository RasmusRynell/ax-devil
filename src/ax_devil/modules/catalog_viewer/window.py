"""The catalog viewer: a render catalog file drawn on example sheets, redrawn whenever the file changes.

Catalogs are written by hand or by an AI agent following ``.agents/skills/render-catalog/SKILL.md``; this window only
shows the result. It follows the catalog manager's file watching, so a saved file is redrawn at once, and an invalid
edit keeps that file's last version that loaded on screen with the error above it.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from weakref import ref

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.catalog_viewer.sheets import Sheet, drawing_errors, sheet_frame, sheets
from ax_devil.modules.chrome import BaseDialog
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.chrome_window import ChromeWindow, window_uses_custom_frame
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.form_layout import FormLayout
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Radius, Space
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalog,
    SceneRenderCatalogListing,
    SceneRenderCatalogManager,
)
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_player.engine.renderer import VideoFrameRenderer

logger = get_logger(__name__)


class NewCatalogDialog(BaseDialog):
    """Ask for the name of a new catalog that starts as a copy of another."""

    def __init__(self, source: str, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent, title="New Catalog")
        content = QWidget(self)
        form = FormLayout(content)
        self.name_edit = QLineEdit(f"{source} copy", content)
        self.name_edit.setObjectName("newCatalogName")
        self.name_edit.selectAll()
        form.addRow("Name", self.name_edit)
        note = QLabel(f"Starts as a copy of {source}.", content)
        note.setWordWrap(True)
        form.addRow("", note)
        self.add_content_widget(content)
        ok, _cancel = self.add_standard_buttons()
        ok.setText("Create")
        self.name_edit.textChanged.connect(lambda text: ok.setEnabled(bool(text.strip())))

    def name(self) -> str:
        """Return the entered name."""
        return self.name_edit.text().strip()


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
        self._description = QLabel(self)
        self._description.setWordWrap(True)
        self._build()

        self._catalogs.activated.connect(lambda index: self.show_catalog(self._catalogs.itemData(index)))
        self._tabs.currentChanged.connect(lambda _index: self._redraw())
        self._new.clicked.connect(self._create_copy)
        self._delete.clicked.connect(self._delete_catalog)
        self._apply_to_all.clicked.connect(self._apply_catalog_to_all)
        self._use_as_default.clicked.connect(self._use_catalog_as_default)
        manager.catalogFileChanged.connect(self._on_file_changed)
        manager.catalogListingChanged.connect(self._on_listing_changed)
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
            self._catalog, self._sheets, self._changes = None, (), 0
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
        self._manager.catalogFileChanged.disconnect(self._on_file_changed)
        self._manager.catalogListingChanged.disconnect(self._on_listing_changed)
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
        try:
            revision = self._manager.load_revision(self._path)
        except (OSError, ValueError) as error:
            self._error = str(error)
            logger.info(f"Render catalog {self._path.name} does not load: {error}")
        else:
            self._error = None
            self._loaded_at = datetime.now()
            if revision.catalog is not self._catalog:
                self._catalog, self._sheets = revision.catalog, sheets(revision.document)
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
        self._file.set_full_text(str(self._path))

    def _show_state(self) -> None:
        loads = self._error is None
        self._new.setEnabled(loads)
        self._new.setToolTip(
            "Create a new catalog that starts as a copy of this one"
            if loads
            else "Only a catalog that loads can be copied"
        )
        deletable = self._manager.catalog_store.is_user_catalog(self._path)
        self._delete.setEnabled(deletable)
        self._delete.setToolTip(
            "Delete this catalog file" if deletable else "Built-in catalogs come with the app and cannot be deleted"
        )
        self._apply_to_all.setEnabled(self._manager.can_apply_to_all(self._path))
        self._use_as_default.setEnabled(self._manager.can_use_as_default(self._path))
        if self._error is not None:
            self._state.setText("● Does not load")
            self._state.setStyleSheet(StatusColor.ERROR.css(self.palette()))
            shown = "Showing the last version that loaded." if self._catalog is not None else "Nothing to show yet."
            self._banner.setText(f"{self._error}\n{shown}")
            self._banner.show()
            return
        updated = f" · updated {self._loaded_at:%H:%M:%S}" if self._changes and self._loaded_at is not None else ""
        self._state.setText(f"● Live{updated}")
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

    # Catalog files ---------------------------------------------------------------------------------------------

    def _create_copy(self) -> None:
        source = self._manager.listing().catalog_for(self._path)
        with NewCatalogDialog(source.name if source is not None else self._path.stem, self) as dialog:
            if dialog.exec() != BaseDialog.DialogCode.Accepted or not dialog.name():
                return
            name = dialog.name()
        try:
            created = self._manager.create_catalog(name, base_catalog_path=self._path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "New Catalog", f"Could not create the catalog: {error}")
            return
        self.show_catalog(created.path)

    def _apply_catalog_to_all(self) -> None:
        try:
            self._manager.apply_to_all(self._path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Apply to All", f"Could not use the catalog: {error}")

    def _use_catalog_as_default(self) -> None:
        try:
            self._manager.set_default_catalog(self._path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Use as Default", f"Could not make the catalog the default: {error}")

    def _delete_catalog(self) -> None:
        listing = self._manager.listing()
        default_note = (
            "\n\nIt is the default catalog, so new views will start from the default built-in catalog."
            if self._path == listing.default_path
            else ""
        )
        answer = QMessageBox.question(
            self,
            "Delete Catalog",
            f"Delete {listing.label(self._path)}?\n\n{self._path}\n\nThe file is deleted permanently.{default_note}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._manager.delete_catalog(self._path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Delete Catalog", f"Could not delete the catalog: {error}")
            return
        self.show_catalog(self._manager.listing().chosen_default_path)

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
        layout.addWidget(self._renderer, 1)
        layout.addWidget(self._description)
        self.setCentralWidget(central)


_OPEN_WINDOWS: dict[int, CatalogViewerWindow] = {}


def close_catalog_viewer(manager: SceneRenderCatalogManager) -> None:
    """Close the catalog viewer of *manager*, if open, so it cleans up before the window that owns it goes away."""
    window = _OPEN_WINDOWS.pop(id(manager), None)
    if window is not None:
        window.close()


def show_catalog_viewer(
    manager: SceneRenderCatalogManager,
    parent: QWidget | None = None,
    *,
    catalog_path: Path | None = None,
) -> CatalogViewerWindow:
    """Show the application's catalog viewer on *catalog_path*, or on the default catalog when it opens.

    There is one viewer per catalog manager; showing it again raises the open window.
    """
    window = _OPEN_WINDOWS.get(id(manager))
    if window is None:
        owner = parent.window() if parent is not None else None
        window = CatalogViewerWindow(
            manager, owner, use_custom_frame=owner is not None and window_uses_custom_frame(owner)
        )
        _OPEN_WINDOWS[id(manager)] = window
        key, window_ref = id(manager), ref(window)

        def forget_window() -> None:
            if _OPEN_WINDOWS.get(key) is window_ref():
                _OPEN_WINDOWS.pop(key, None)

        window.destroyed.connect(forget_window)
    if catalog_path is not None:
        window.show_catalog(catalog_path)
    window.show()
    window.raise_()
    window.activateWindow()
    return window
