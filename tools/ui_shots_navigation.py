"""Finding and driving the application's windows, dialogs and sidebar views while taking UI screenshots."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialog,
    QLineEdit,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QWidget,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, SignIn
from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    RuntimePluginRegistry,
)
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.workspace.ui.browser_rows import RowAction, WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.section_tree import ROW_ROLE
from tools.ui_samples import SHOWN_FRAME
from tools.ui_shots_config import OUT, WINDOW_SIZES


def trigger(window: QWidget, title: str) -> None:
    [action] = [action for action in window.actions() if action.text() == title]
    assert action.isEnabled(), title
    action.trigger()


def select_tab(dialog: QWidget, title: str) -> None:
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    [index] = [index for index in range(tabs.count()) if tabs.tabText(index) == title]
    QTest.mouseClick(tabs.tabBar(), Qt.MouseButton.LeftButton, pos=tabs.tabBar().tabRect(index).center())


def select_combo(dialog: QWidget, label: str) -> None:
    [combo] = [combo for combo in dialog.findChildren(QComboBox) if combo.findText(label) >= 0]
    combo.setCurrentText(label)


def tree_row(window: QWidget, label: str) -> tuple[QTreeWidget, QTreeWidgetItem] | None:
    for tree in window.findChildren(QTreeWidget):
        if not tree.isVisible():
            continue
        iterator = QTreeWidgetItemIterator(tree)
        while (item := iterator.value()) is not None:
            text = item.data(0, Qt.ItemDataRole.AccessibleTextRole) or ""
            if text.split(", ", 1)[0] == label:
                return tree, item
            iterator += 1
    return None


def open_video(qtbot: QtBot, window: QWidget, label: str) -> OfflineVideoViewerWidget:
    show_sidebar(window, "Offline")
    previous = set(window.findChildren(OfflineVideoViewerWidget))

    def can_open() -> bool:
        row = tree_row(window, label)
        if row is None:
            return False
        model = cast(WorkspaceBrowserRow, row[1].data(0, ROW_ROLE))
        offered = model.offered(RowAction.OPEN)
        return offered is not None and not offered.disabled_reason

    qtbot.waitUntil(can_open, timeout=10000)
    row = tree_row(window, label)
    assert row is not None
    tree, item = row
    tree.scrollToItem(item)
    tree.setCurrentItem(item)
    tree.setFocus()
    QTest.keyClick(tree, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: bool(set(window.findChildren(OfflineVideoViewerWidget)) - previous), timeout=10000)
    [viewer] = set(window.findChildren(OfflineVideoViewerWidget)) - previous
    qtbot.waitUntil(lambda: viewer.findChild(FrameViewport) is not None, timeout=10000)
    viewer.pause_playback()
    [frame] = [spin for spin in viewer.findChildren(QSpinBox) if spin.toolTip() == "Frame"]
    frame.selectAll()
    QTest.keyClicks(frame, str(SHOWN_FRAME))
    QTest.keyClick(frame, Qt.Key.Key_Return)
    viewport = viewer.findChild(FrameViewport)
    assert viewport is not None
    # Observe decoded frame identity; readiness must mean the requested picture, not only the spinbox value.
    qtbot.waitUntil(
        lambda: viewport._video_frame is not None and viewport._video_frame.frame.frame_id == SHOWN_FRAME,
        timeout=10000,
    )
    return viewer


def grab_next_dialog(name: str, before: Callable[[QDialog], None] | None = None) -> None:
    """Save and close the dialog that the next call opens modally, after running *before* on it."""

    def grab() -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                try:
                    if before is not None:
                        before(widget)
                        QApplication.processEvents()
                    widget.grab().save(str(OUT / f"{name}.png"))
                    natural = widget.size()
                    for size_name, (width, height) in WINDOW_SIZES.items():
                        clamped = QSize(min(natural.width(), width), min(natural.height(), height))
                        if clamped == natural:
                            continue
                        widget.resize(clamped)
                        QApplication.processEvents()
                        widget.grab().save(str(OUT / f"{name}-{size_name}.png"))
                finally:
                    widget.reject()

    QTimer.singleShot(300, grab)


def fill_sidebar_views(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, window: QWidget) -> None:
    """Supply external scan results and a failed plugin; let the real views handle them."""
    cameras = [
        DiscoveredCamera(
            "192.0.2.34",
            "AXIS P3265-LVE",
            "ACCC8E123456",
            "12.6.85",
            "Dome Camera",
            93_600.0,
            "aarch64",
            "Ambarella CV25",
            SignIn.SIGNED_IN,
        ),
        DiscoveredCamera("192.0.2.51", "AXIS M3086-V", "", "11.11.73", "Dome Camera", sign_in=SignIn.REFUSED),
        DiscoveredCamera("192.0.2.77", "AXIS C1310-E", "", "12.2.40", "Network Speaker", sign_in=SignIn.REFUSED),
    ]
    monkeypatch.setattr("ax_devil.modules.workspace.ui.live_view_controller.scan_network", lambda **_kwargs: cameras)
    show_sidebar(window, "Live")
    [ip_range] = [field for field in window.findChildren(QLineEdit) if field.placeholderText().startswith("IP range")]
    ip_range.setText("192.0.2.0/24")
    [scan] = [button for button in window.findChildren(QAbstractButton) if button.text() == "Rescan"]
    QTest.mouseClick(scan, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: tree_row(window, "192.0.2.34") is not None, timeout=10000)

    # The registry is restored when the test ends.
    monkeypatch.setattr(
        RuntimePluginRegistry,
        "_plugins_by_type",
        {k: dict(v) for k, v in RuntimePluginRegistry._plugins_by_type.items()},
    )
    RuntimePluginRegistry.mark_failed(
        DECODER_PLUGIN_TYPE,
        "old-model-decoder",
        "old-model-decoder",
        "Plugin requires API 1, but this app has API 2",
    )


def show_sidebar(window: QWidget, title: str) -> None:
    bar = window.findChild(QWidget, "AxDevilActivityBar")
    assert bar is not None
    [button] = [button for button in bar.findChildren(QAbstractButton) if button.toolTip().split(":", 1)[0] == title]
    if not button.isChecked():
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
