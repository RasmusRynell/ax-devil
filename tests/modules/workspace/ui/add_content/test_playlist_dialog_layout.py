"""Layout regression checks for dynamically selected playlist resolver forms."""

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.plugin_system import PLAYLIST_RESOLVER_PLUGIN_TYPE, PlaylistResolverWidget, RuntimePluginRegistry
from ax_devil.modules.workspace.ui.add_content.add_playlist_dialog import AddPlaylistDialog
from tests.helpers.workspace import inline_resolver


def test_every_resolver_form_fits_the_opening_size(qtbot: QtBot) -> None:
    """The dialog opens large enough for every resolver form, so switching never resizes or scrolls."""
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.resize(800, 600)
    parent.show()
    dialog = AddPlaylistDialog(inline_resolver(), parent)
    qtbot.addWidget(dialog)
    dialog.show()
    QApplication.processEvents()
    opening_size = dialog.size()
    assert opening_size.width() < parent.width()
    assert opening_size.height() < parent.height()
    combo = dialog._settings._resolver_combo
    assert combo.count() >= 3
    for index in [1, 2, 1, 0]:
        combo.setCurrentIndex(index)
        QApplication.processEvents()
        assert dialog.size() == opening_size
        assert dialog._scroll_area.horizontalScrollBar().maximum() == 0
        assert dialog._scroll_area.verticalScrollBar().maximum() == 0


def test_broken_resolver_leaves_other_resolvers_available(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolver plugin that cannot build its form is left out instead of breaking the dialog."""
    records = RuntimePluginRegistry.get_plugins(PLAYLIST_RESOLVER_PLUGIN_TYPE)
    broken, working = records[0], records[1]

    def fail(self: object) -> PlaylistResolverWidget:
        raise RuntimeError("broken resolver")

    monkeypatch.setattr(broken.plugin_class, "create_settings_widget", fail)
    dialog = AddPlaylistDialog(inline_resolver())
    qtbot.addWidget(dialog)
    combo = dialog._settings._resolver_combo
    names = [combo.itemText(index) for index in range(combo.count())]
    assert broken.definition.display_name not in names
    combo.setCurrentIndex(names.index(working.definition.display_name))
    dialog.show()
    QApplication.processEvents()
    visible_forms = [form for form in dialog.findChildren(PlaylistResolverWidget) if form.isVisibleTo(dialog)]
    assert len(visible_forms) == 1
    assert visible_forms[0] is combo.currentData()


def _page_labels(dialog: AddPlaylistDialog) -> list[QLabel]:
    """Return the labels on the selected resolver page."""
    page = dialog._settings._resolver_pages.currentWidget()
    assert page is not None
    return page.findChildren(QLabel)


def test_opening_page_explains_every_source(qtbot: QtBot) -> None:
    """Before a source is chosen, the dialog explains itself and describes each available source."""
    dialog = AddPlaylistDialog(inline_resolver())
    qtbot.addWidget(dialog)
    settings = dialog._settings
    opening_text = " ".join(label.text() for label in _page_labels(dialog))
    assert "Choose a source" in opening_text
    for record in RuntimePluginRegistry.get_plugins(PLAYLIST_RESOLVER_PLUGIN_TYPE):
        index = settings._resolver_combo.findText(record.definition.display_name)
        assert index > 0
        assert record.definition.display_name in opening_text
        description = record.definition.description
        assert description is not None and description in opening_text
        settings._resolver_combo.setCurrentIndex(index)
        page_text = [label.text() for label in _page_labels(dialog)]
        assert description in page_text
