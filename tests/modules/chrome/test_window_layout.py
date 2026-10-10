"""Behavior checks for shared window sizing and responsive dialog content."""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QSize
from PySide6.QtGui import QScreen, QShowEvent
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.chrome_window import ChromeWindow
from ax_devil.modules.chrome.form_layout import FormLayout
from ax_devil.modules.chrome.window_geometry import fit_window
from ax_devil.modules.workspace.add_content.add_video_dialog import AddVideoDialog
from tests.helpers.dialogs import content_scroll


class _OpeningDialog(BaseDialog):
    """Record the geometry delivered to the first show event."""

    opening_size: QSize | None = None

    def showEvent(self, event: QShowEvent) -> None:
        """Capture geometry before any show-event handling can resize it."""
        self.opening_size = self.size()
        super().showEvent(event)


def test_dialog_geometry_is_ready_before_show_event(qtbot: QtBot) -> None:
    """The native window opens at its prepared size rather than resizing after showing."""
    dialog = _OpeningDialog()
    qtbot.addWidget(dialog)
    dialog.add_content_widget(QLabel("Compact content"))
    dialog.add_button("Close")
    dialog.resize(700, 550)
    dialog.show()
    QApplication.processEvents()
    assert dialog.opening_size == dialog.size()
    assert dialog.width() < 700
    assert dialog.height() < 550


def test_dialog_opens_without_scrolling_when_content_fits_the_screen(qtbot: QtBot) -> None:
    """A form taller than Qt's default scroll-area preference still opens fully visible."""
    dialog = BaseDialog()
    qtbot.addWidget(dialog)
    form = QWidget()
    layout = FormLayout(form)
    for index in range(18):
        layout.addRow(f"Field {index}", QLineEdit())
    dialog.add_content_widget(form)
    screen = dialog.screen()
    assert screen is not None
    # Taller than a plain QScrollArea would ask for, yet well within the screen.
    assert 24 * dialog.fontMetrics().height() < form.sizeHint().height()
    assert dialog.sizeHint().height() < screen.availableGeometry().height() - 2 * dialog.fontMetrics().height()
    dialog.show()
    QApplication.processEvents()
    assert content_scroll(dialog).verticalScrollBar().maximum() == 0
    assert screen.availableGeometry().contains(dialog.frameGeometry())


def test_stretching_content_fills_the_opening_dialog(qtbot: QtBot) -> None:
    """A stretch-1 list gets the dialog's height when it opens, instead of staying at its minimum size."""
    dialog = BaseDialog()
    qtbot.addWidget(dialog)
    dialog.add_content_widget(QLineEdit())
    list_area = QScrollArea()
    list_area.setWidgetResizable(True)
    rows = QWidget()
    rows_layout = QVBoxLayout(rows)
    for index in range(30):
        rows_layout.addWidget(QLabel(f"Row {index}"))
    list_area.setWidget(rows)
    dialog.add_content_widget(list_area, stretch=1)
    note = QLabel("")
    note.setWordWrap(True)
    dialog.add_content_widget(note)
    dialog.show()
    QApplication.processEvents()
    assert list_area.height() > content_scroll(dialog).viewport().height() * 2 // 3


@pytest.mark.parametrize("custom_frame", [False, True])
def test_long_dialog_scrolls_with_actions_visible(qtbot: QtBot, custom_frame: bool) -> None:
    """A large form stays usable on a small screen with either window frame."""
    parent = ChromeWindow(use_custom_frame=custom_frame)
    qtbot.addWidget(parent)
    parent.show()
    form = QWidget()
    layout = FormLayout(form)
    for index in range(40):
        layout.addRow(f"Field {index}", QLineEdit())
    dialog = BaseDialog(parent)
    qtbot.addWidget(dialog)
    dialog.add_content_widget(form)
    button = dialog.add_button("Close", dialog.accept)
    dialog.show()
    QApplication.processEvents()
    screen = dialog.screen()
    assert screen is not None
    assert screen.availableGeometry().contains(dialog.frameGeometry())
    dialog.resize(420, 300)
    QApplication.processEvents()
    assert content_scroll(dialog).verticalScrollBar().maximum() > 0
    assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    content_scroll(dialog).ensureWidgetVisible(form.findChildren(QLineEdit)[-1])
    assert content_scroll(dialog).verticalScrollBar().value() > 0


def test_video_form_stays_compact_when_dialog_grows(qtbot: QtBot) -> None:
    """Additional height does not stretch input controls; width reaches the fields."""
    dialog = AddVideoDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    QApplication.processEvents()
    height = dialog._name_edit.height()
    width = dialog._name_edit.width()
    dialog.resize(dialog.width() + 150, dialog.height() + 200)
    QApplication.processEvents()
    assert dialog._name_edit.height() == height
    assert dialog._name_edit.width() > width
    resized = dialog.size()
    dialog.hide()
    dialog.show()
    assert dialog.size() == resized


def test_themed_frame_does_not_force_scrollbars(qtbot: QtBot) -> None:
    """Style-provided scroll borders are included in the natural opening size."""
    dialog = AddVideoDialog()
    qtbot.addWidget(dialog)
    dialog.setStyleSheet("QScrollArea { border: 2px solid gray; }")
    dialog.show()
    QApplication.processEvents()
    assert content_scroll(dialog).horizontalScrollBar().maximum() == 0
    assert content_scroll(dialog).verticalScrollBar().maximum() == 0


def test_window_geometry_round_trip(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reopening restores the size the user chose, without touching real settings."""
    settings = QSettings(str(tmp_path / "windows.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("ax_devil.modules.chrome.chrome_window.window_state_settings", lambda: settings)
    window = ChromeWindow(remember_size=True)
    window.setObjectName("testWindow")
    window.setCentralWidget(QLabel("Content"))
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    window.showNormal()
    QApplication.processEvents()
    window.resize(500, 350)
    window.close()
    reopened = ChromeWindow(remember_size=True)
    reopened.setObjectName("testWindow")
    reopened.setCentralWidget(QLabel("Content"))
    qtbot.addWidget(reopened)
    reopened.show()
    assert reopened.size() == QSize(500, 350)


def test_geometry_fits_offscreen_parent(qtbot: QtBot) -> None:
    """A dialog anchored near a screen edge remains fully reachable."""
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.setGeometry(-1000, -1000, 300, 200)
    dialog = BaseDialog(parent)
    qtbot.addWidget(dialog)
    dialog.add_button("Close")
    dialog.show()
    fit_window(dialog, QSize(5000, 5000), center=True)
    screen = dialog.screen()
    assert screen is not None
    assert screen.availableGeometry().contains(dialog.frameGeometry())


def test_form_wraps_labels_at_narrow_width(qtbot: QtBot) -> None:
    """Labels move above fields instead of forcing a wide form."""
    form = QWidget()
    qtbot.addWidget(form)
    layout = FormLayout(form)
    field = QLineEdit()
    layout.addRow("Custom stream address:", field)
    label = layout.labelForField(field)
    assert label is not None
    form.resize(500, 200)
    form.show()
    QApplication.processEvents()
    assert field.y() < label.geometry().bottom()
    form.resize(150, 200)
    QApplication.processEvents()
    assert field.y() > label.geometry().bottom()


def test_added_content_becomes_scrollable(qtbot: QtBot) -> None:
    """Revealing fields after opening does not push dialog buttons out of reach."""
    dialog = BaseDialog()
    qtbot.addWidget(dialog)
    form = QWidget()
    layout = FormLayout(form)
    layout.addRow("Name", QLineEdit())
    dialog.add_content_widget(form)
    button = dialog.add_button("Close")
    dialog.show()
    opening_size = dialog.size()
    for index in range(30):
        layout.addRow(f"Extra field {index}", QLineEdit())
    qtbot.waitUntil(lambda: content_scroll(dialog).verticalScrollBar().maximum() > 0)
    assert dialog.size() == opening_size
    assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))


def test_maximized_geometry_round_trip(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A maximized main window reopens maximized and retains its normal size."""
    settings = QSettings(str(tmp_path / "windows.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("ax_devil.modules.chrome.chrome_window.window_state_settings", lambda: settings)
    window = ChromeWindow(remember_size=True)
    window.setObjectName("testWindow")
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    window.showNormal()
    QApplication.processEvents()
    window.resize(500, 350)
    window.showMaximized()
    QApplication.processEvents()
    window.close()
    reopened = ChromeWindow(remember_size=True)
    reopened.setObjectName("testWindow")
    qtbot.addWidget(reopened)
    reopened.show()
    assert reopened.isMaximized()
    reopened.showNormal()
    assert reopened.size() == QSize(500, 350)


def test_main_window_never_overrides_desktop_placement(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Old saved coordinates cannot force a screen or move the main window."""
    settings = QSettings(str(tmp_path / "windows.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("ax_devil.modules.chrome.chrome_window.window_state_settings", lambda: settings)
    legacy = QWidget()
    qtbot.addWidget(legacy)
    legacy.setGeometry(1500, 300, 640, 480)
    settings.setValue("testWindow", legacy.saveGeometry())
    window = _DesktopPlacementWindow(remember_size=True)
    window.setObjectName("testWindow")
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    QApplication.processEvents()
    assert not window.isMaximized()
    qtbot.waitUntil(window.isActiveWindow)
    screen = window.screen()
    assert screen is not None
    assert screen.availableGeometry().size().expandedTo(window.minimumSize()).width() >= window.width()
    assert screen.availableGeometry().size().expandedTo(window.minimumSize()).height() >= window.height()


class _DesktopPlacementWindow(ChromeWindow):
    """Reject application-level placement overrides without patching Qt objects."""

    def setScreen(self, screen: QScreen) -> None:
        """Reject explicit monitor selection."""
        raise AssertionError("The desktop must select the monitor")

    def move(self, *args: object) -> None:
        """Reject application-controlled window placement."""
        raise AssertionError("The desktop must position the window")

    def restoreGeometry(self, geometry: object) -> bool:
        """Reject restoring stored coordinates and monitor identity."""
        raise AssertionError("Only size and maximized state should be restored")


@pytest.mark.parametrize(
    "window_type",
    [ChromeWindow, BaseDialog],
)
def test_shared_minimum_prevents_unusable_windows(qtbot: QtBot, window_type: type[QWidget]) -> None:
    """Windows and dialogs cannot be shrunk below their shared usable minimum."""
    window = window_type()
    qtbot.addWidget(window)
    window.resize(1, 1)
    assert window.minimumWidth() > 1 and window.minimumHeight() > 1
    assert window.size() == window.minimumSize()


def test_secondary_windows_fit_the_available_screen(qtbot: QtBot) -> None:
    """Secondary windows open with usable dimensions within the available screen."""
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.resize(800, 600)
    parent.show()
    window = ChromeWindow(parent)
    qtbot.addWidget(window)
    window.show()
    screen = window.screen()
    assert screen is not None
    assert screen.availableGeometry().contains(window.frameGeometry())


def test_dialog_uses_content_size_instead_of_parent_proportions(qtbot: QtBot) -> None:
    """Dialogs stay compact while still respecting their shared minimum size."""
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.resize(800, 600)
    parent.show()
    dialog = BaseDialog(parent)
    qtbot.addWidget(dialog)
    dialog.add_content_widget(QLabel("Compact content"))
    dialog.add_button("Close")
    dialog.show()
    QApplication.processEvents()
    assert dialog.size().width() < parent.width()
    assert dialog.size().height() < parent.height()


def test_close_before_activation_does_not_save_temporary_size(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An early close leaves the next launch at the same default size as a first launch."""
    first_launch = QSettings(str(tmp_path / "first.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("ax_devil.modules.chrome.chrome_window.window_state_settings", lambda: first_launch)
    fresh = ChromeWindow(remember_size=True)
    fresh.setObjectName("testWindow")
    qtbot.addWidget(fresh)
    fresh.show()
    fresh.activateWindow()
    qtbot.waitUntil(fresh.isActiveWindow)
    default_size = fresh.size()
    assert default_size != QSize(500, 350)

    settings = QSettings(str(tmp_path / "windows.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("ax_devil.modules.chrome.chrome_window.window_state_settings", lambda: settings)
    window = ChromeWindow(remember_size=True)
    window.setObjectName("testWindow")
    qtbot.addWidget(window)
    window.show()
    assert not window.isActiveWindow()
    window.resize(500, 350)
    window.close()

    reopened = ChromeWindow(remember_size=True)
    reopened.setObjectName("testWindow")
    qtbot.addWidget(reopened)
    reopened.show()
    reopened.activateWindow()
    qtbot.waitUntil(reopened.isActiveWindow)
    assert reopened.size() == default_size


@pytest.mark.parametrize("maximized", [False, True])
def test_oversized_saved_window_fits_without_overriding_placement(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, maximized: bool
) -> None:
    """A size saved on a larger monitor remains reachable on the current one."""
    settings = QSettings(str(tmp_path / "windows.ini"), QSettings.Format.IniFormat)
    settings.setValue("testWindow/size", QSize(5000, 5000))
    settings.setValue("testWindow/maximized", maximized)
    monkeypatch.setattr("ax_devil.modules.chrome.chrome_window.window_state_settings", lambda: settings)
    window = _DesktopPlacementWindow(remember_size=True)
    window.setObjectName("testWindow")
    qtbot.addWidget(window)
    window.show()
    assert window.isMaximized() is maximized
    window.showNormal()
    QApplication.processEvents()
    screen = window.screen()
    assert screen is not None
    assert screen.availableGeometry().contains(window.frameGeometry())


def test_large_dialog_opens_bounded_but_user_can_enlarge_it(qtbot: QtBot) -> None:
    """The opening limit leaves large forms scrollable without restricting later resizing."""
    dialog = BaseDialog()
    qtbot.addWidget(dialog)
    form = QWidget()
    layout = FormLayout(form)
    for index in range(80):
        layout.addRow(f"Field {index}", QLineEdit())
    dialog.add_content_widget(form)
    button = dialog.add_button("Close", dialog.accept)
    dialog.show()
    QApplication.processEvents()
    screen = dialog.screen()
    assert screen is not None
    assert screen.availableGeometry().contains(dialog.frameGeometry())
    assert dialog.height() < dialog.sizeHint().height()
    assert content_scroll(dialog).verticalScrollBar().maximum() > 0
    assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    height = dialog.height()
    dialog.resize(dialog.width(), height + 100)
    assert dialog.height() == height + 100


@pytest.mark.parametrize("saved_size", [None, QSize(500, 350)], ids=["first-open", "saved-size"])
def test_remembered_child_window_opens_centered_over_its_parent(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, saved_size: QSize | None
) -> None:
    """A window that remembers its size still opens over its parent, like other secondary windows."""
    settings = QSettings(str(tmp_path / "windows.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("ax_devil.modules.chrome.chrome_window.window_state_settings", lambda: settings)
    if saved_size is not None:
        settings.setValue("childWindow/size", saved_size)
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.setGeometry(100, 100, 600, 450)
    parent.show()
    window = ChromeWindow(parent, remember_size=True)
    window.setObjectName("childWindow")
    qtbot.addWidget(window)

    window.show()
    QApplication.processEvents()

    assert saved_size is None or window.size() == saved_size
    # Frame margins are only known once the window maps, so allow a few pixels.
    center, expected = window.frameGeometry().center(), parent.frameGeometry().center()
    assert abs(center.x() - expected.x()) <= 4 and abs(center.y() - expected.y()) <= 4
