"""Getting content in: welcome and start panel actions, recent workspaces, file drops, and overlay handler matching."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.workspace.core import (
    OverlayFile,
    VideoFileSelection,
    video_file_selections,
)
from ax_devil.modules.workspace.core.intake import WorkspaceDecoderOption, WorkspaceIntake
from ax_devil.modules.workspace.ui.application_window import ApplicationWindow
from ax_devil.modules.workspace.ui.content_browser import ContentBrowserWidget
from ax_devil.modules.workspace.ui.sidebar_panel import SidebarPanel
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.start_panel import StartPanel, recent_date
from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget
from ax_devil.modules.workspace.ui.welcome_widget import WelcomeWidget


class _OptionProvider:
    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (
            WorkspaceDecoderOption(handler_type="TXT", display_name="Text", file_extensions=(".txt",)),
            WorkspaceDecoderOption(handler_type="FRAME", display_name="Frame", file_extensions=(".jsonl",)),
            WorkspaceDecoderOption(handler_type="TRACKS", display_name="Tracks", file_extensions=(".jsonl",)),
        )

    def live_overlay_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return ()


class _PaneWidget(ViewerWidget):
    def _setup_widget_ui(self) -> None:
        pass

    def get_display_name(self) -> str:
        return "Pane"


def _touch(path: Path) -> Path:
    path.write_bytes(b"")
    return path


def test_overlay_decoder_matching_uses_declared_extensions_and_keeps_undeclared_decoders() -> None:
    """Only decoders declaring the suffix match, while decoders declaring none stay possible for any file."""
    intake = WorkspaceIntake(_OptionProvider())

    assert [option.handler_type for option in intake.file_decoder_options_for(Path("gt.TXT"))] == ["TXT"]
    assert [option.handler_type for option in intake.file_decoder_options_for(Path("a.jsonl"))] == ["FRAME", "TRACKS"]
    assert intake.file_decoder_options_for(Path("notes.pdf")) == ()
    assert WorkspaceDecoderOption(handler_type="ANY", display_name="Any").may_read(Path("notes.pdf"))


def test_dropped_files_pair_one_video_with_one_overlay_and_open_other_videos_alone() -> None:
    """One video plus one overlay pairs up; ambiguous overlays wait for a decoder; other drops open each video."""
    intake = WorkspaceIntake(_OptionProvider())
    video = Path("/clips/cam.MP4")

    assert video_file_selections([video, Path("/clips/gt.txt")], intake) == (
        VideoFileSelection(video, Path("/clips/gt.txt"), "TXT"),
    )
    [ambiguous] = video_file_selections([Path("/clips/scene.jsonl"), video], intake)
    assert ambiguous == VideoFileSelection(video, Path("/clips/scene.jsonl"))
    assert ambiguous.needs_decoder
    assert video_file_selections([video, Path("/clips/notes.pdf")], intake) == (VideoFileSelection(video),)
    assert video_file_selections([video, Path("/clips/b.mkv"), Path("/clips/gt.txt")], intake) == (
        VideoFileSelection(video),
        VideoFileSelection(Path("/clips/b.mkv")),
    )
    assert video_file_selections([Path("/clips/gt.txt")], intake) == ()


def test_video_selection_becomes_an_item_named_after_the_video_unless_named() -> None:
    selection = VideoFileSelection(Path("/clips/cam.mp4"), Path("/clips/gt.txt"), "TXT")

    item = selection.to_item()
    assert (item.display_name, item.video, item.overlays) == (
        "cam.mp4",
        Path("/clips/cam.mp4"),
        (OverlayFile(Path("/clips/gt.txt"), "TXT"),),
    )
    assert selection.to_item("Gate").label == "Gate"


def _row_center(welcome: WelcomeWidget, label: str) -> QPointF:
    """Locate a row through the widget's hit-testing interface without depending on its layout implementation."""
    for y in range(welcome.height()):
        point = QPointF(welcome.width() / 2, y)
        item = welcome.item_at(point)
        if item is not None and item.label == label:
            return point
    raise AssertionError(f"Welcome row not found: {label}")


def test_clicking_welcome_rows_triggers_shortcut_actions(qtbot: QtBot) -> None:
    """Welcome rows are buttons: a shortcut row triggers its action, and empty space does nothing."""
    host = QWidget()
    qtbot.addWidget(host)
    manager = ShortcutManager()
    manager.register_defaults()
    manager.install(host)
    welcome = WelcomeWidget(host)
    welcome.resize(900, 700)
    welcome.set_shortcut_manager(manager)
    host.show()

    triggered: list[bool] = []
    manager.get_action("app.add_video").triggered.connect(lambda: triggered.append(True))

    QTest.mouseClick(welcome, Qt.MouseButton.LeftButton, pos=_row_center(welcome, "Add Video").toPoint())
    QTest.mouseClick(welcome, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))

    assert triggered == [True]


def test_start_panel_buttons_trigger_actions_and_recent_rows_request_their_workspace(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """The start panel's open buttons trigger and are named after the menu actions; a recent row requests its file."""
    host = QWidget()
    qtbot.addWidget(host)
    manager = ShortcutManager()
    manager.register_defaults()
    manager.install(host)
    panel = StartPanel(host)
    panel.set_shortcut_manager(manager)
    recent = [_touch(tmp_path / f"{name}.ax-devil.workspace") for name in ("Parking lot", "Exp 3")]
    panel.set_recent_workspaces(recent)
    panel.resize(320, 600)
    host.show()
    triggered: list[str] = []
    for action_id in ("app.open_workspace", "app.add_live_stream"):
        manager.get_action(action_id).triggered.connect(
            lambda _=False, action_id=action_id: triggered.append(action_id)
        )
    requested: list[object] = []
    panel.recent_workspace_requested.connect(requested.append)

    panel.open_button("app.open_workspace").click()
    panel.open_button("app.add_live_stream").click()
    rows = panel.recent_rows()
    QTest.mouseClick(rows[1], Qt.MouseButton.LeftButton, pos=rows[1].rect().center())

    assert triggered == ["app.open_workspace", "app.add_live_stream"]
    assert panel.open_button("app.add_live_stream").text() == manager.get_action("app.add_live_stream").text()
    assert [row.name_label.full_text() for row in rows] == ["Parking lot", "Exp 3"]
    assert requested == [recent[1]]


def test_start_panel_never_overflows_a_narrow_sidebar(qtbot: QtBot, tmp_path: Path) -> None:
    """Open buttons shrink and long recent names and folders elide instead of pushing past a narrow sidebar."""
    panel = StartPanel()
    qtbot.addWidget(panel)
    folder = tmp_path / ("very-long-folder-name-" * 6)
    folder.mkdir()
    panel.set_recent_workspaces([_touch(folder / f"{'Long workspace name ' * 6}.ax-devil.workspace")])
    panel.resize(160, 400)
    panel.show()
    QApplication.processEvents()

    row = panel.recent_rows()[0]
    assert panel.minimumSizeHint().width() <= 160
    assert row.geometry().right() < panel.width()
    assert row.date_label.geometry().right() <= row.width()
    assert all(button.geometry().right() < panel.width() for button in panel._open_buttons.values())


def test_recent_date_shows_today_a_weekday_then_a_date() -> None:
    """Saves within the week show as today or a weekday; older ones as a date, with the year once it differs."""
    today = date(2026, 10, 10)  # A Saturday.

    assert recent_date(today, today) == "today"
    assert recent_date(date(2026, 10, 6), today) == "Tue"
    assert recent_date(date(2026, 9, 28), today) == "Sep 28"
    assert recent_date(date(2025, 9, 28), today) == "Sep 28 2025"


def test_welcome_actions_remain_clickable_in_a_small_workspace(qtbot: QtBot) -> None:
    """Every welcome action stays reachable by scrolling in a supported small window."""
    center = SplitView()
    qtbot.addWidget(center)
    manager = ShortcutManager()
    manager.register_defaults()
    manager.install(center)
    center.set_welcome_shortcut_manager(manager)
    welcome = center.welcome_widget()
    center.resize(480, 280)
    center.show()
    QApplication.processEvents()
    scroll = center.findChild(QScrollArea)
    assert scroll is not None
    triggered: list[bool] = []
    manager.get_action("app.add_video").triggered.connect(lambda: triggered.append(True))

    for label in ("Add Video", "Settings"):
        point = _row_center(welcome, label).toPoint()
        scroll.ensureVisible(point.x(), point.y(), 10, 10)
        QApplication.processEvents()
        visible_point = welcome.mapTo(scroll.viewport(), point)
        assert scroll.viewport().rect().contains(visible_point)
        QTest.mouseClick(welcome, Qt.MouseButton.LeftButton, pos=point)

    assert triggered == [True]


def _send_drop(target: QWidget, paths: list[Path]) -> bool:
    """Deliver a desktop file drag-and-drop to *target* and return whether the drag was accepted."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    enter = QDragEnterEvent(
        QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    )
    QApplication.sendEvent(target, enter)
    if not enter.isAccepted():
        return False
    drop = QDropEvent(
        QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    )
    QApplication.sendEvent(target, drop)
    return True


def test_desktop_file_drops_reach_the_workspace_over_welcome_and_open_panes(qtbot: QtBot, tmp_path: Path) -> None:
    """Video drops land on the workspace from the welcome screen or a pane; drops without a video are refused."""
    center = SplitView()
    window = ApplicationWindow(sidebar=SidebarPanel(ContentBrowserWidget(), StartPanel()), center_area=center)
    qtbot.addWidget(window)
    window.resize(800, 600)
    window.show()
    dropped: list[list[Path]] = []
    window.files_dropped.connect(dropped.append)
    video = tmp_path / "cam.mp4"
    overlay = tmp_path / "gt.txt"

    assert _send_drop(center.welcome_widget(), [video, overlay])
    assert not _send_drop(center.welcome_widget(), [overlay])
    pane = _PaneWidget()
    center.add_viewer_widget(pane)
    assert _send_drop(pane, [video])

    assert dropped == [[video, overlay], [video]]
