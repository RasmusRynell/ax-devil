"""Getting content in: welcome actions, recent videos, desktop file drops, and overlay handler matching."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.workspace.core import (
    OverlayFile,
    VideoFileSelection,
    VideoItem,
    video_file_selections,
)
from ax_devil.modules.workspace.core.intake import WorkspaceDecoderOption, WorkspaceIntake
from ax_devil.modules.workspace.ui.application_window import ApplicationWindow
from ax_devil.modules.workspace.ui.content_browser import ContentBrowserWidget
from ax_devil.modules.workspace.ui.recent_videos import RecentVideos
from ax_devil.modules.workspace.ui.split_view import SplitView
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


def _video_item(video: Path, overlay: Path | None = None, label: str = "") -> VideoItem:
    return VideoFileSelection(video, overlay, "TXT" if overlay else None).to_item(label)


def _recipes(items: tuple[VideoItem, ...]) -> list[tuple[str, Path, tuple[OverlayFile, ...]]]:
    """Return what recent entries open, leaving out item ids, which every reading creates afresh."""
    return [(item.label, item.video, item.overlays) for item in items]


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
    assert (item.label, item.video, item.overlays) == (
        "cam.mp4",
        Path("/clips/cam.mp4"),
        (OverlayFile(Path("/clips/gt.txt"), "TXT"),),
    )
    assert selection.to_item("Gate").label == "Gate"


def test_recent_videos_keep_newest_first_without_duplicates_or_missing_files(tmp_path: Path) -> None:
    """The list survives reopening, replaces an earlier entry for the same video, and hides deleted videos."""
    videos = [_touch(tmp_path / f"clip{index}.mp4") for index in range(4)]
    overlay = tmp_path / "gt.txt"
    store_path = tmp_path / "state" / "recent-videos.json"
    recent = RecentVideos(store_path, limit=3)
    for video in videos:
        recent.record(_video_item(video))
    recent.record(_video_item(videos[2], overlay, label="Gate"))
    videos[1].unlink()

    assert _recipes(RecentVideos(store_path, limit=3).entries()) == _recipes(
        (_video_item(videos[2], overlay, label="Gate"), _video_item(videos[3]))
    )


def test_deleted_recent_videos_do_not_take_slots_from_existing_ones(tmp_path: Path) -> None:
    """Recording a video drops deleted entries before the limit, so older existing videos stay listed."""
    kept = _touch(tmp_path / "kept.mp4")
    gone = [_touch(tmp_path / f"gone{index}.mp4") for index in range(2)]
    recent = RecentVideos(tmp_path / "recent-videos.json", limit=3)
    for video in (kept, *gone):
        recent.record(_video_item(video))
    for video in gone:
        video.unlink()
    new = _touch(tmp_path / "new.mp4")
    recent.record(_video_item(new))

    assert _recipes(recent.entries()) == _recipes((_video_item(new), _video_item(kept)))


def test_recent_videos_reopen_relative_selections_after_the_working_directory_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recent selections keep their original files and deduplicate relative and absolute paths."""
    video = _touch(tmp_path / "clip.mp4")
    overlay = _touch(tmp_path / "gt.txt")
    recent = RecentVideos(tmp_path / "recent-videos.json")
    monkeypatch.chdir(tmp_path)
    recent.record(_video_item(video))
    recent.record(_video_item(Path("clip.mp4"), Path("gt.txt")))
    other_directory = tmp_path / "other"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)

    assert _recipes(recent.entries()) == _recipes((_video_item(video, overlay),))


@pytest.mark.parametrize("content", ["{not json", "[null]", '["bad"]', '{"video": "x"}', "[{}]", "BAD_FIELD"])
def test_unreadable_recent_videos_file_is_ignored(tmp_path: Path, content: str) -> None:
    """Broken or wrongly shaped recent-video files never stop the workspace from starting."""
    store_path = tmp_path / "recent-videos.json"
    video = _touch(tmp_path / "clip.mp4")
    bad_field = f'[{{"video": "{video}", "label": [], "overlays": []}}]'
    store_path.write_text(bad_field if content == "BAD_FIELD" else content)

    assert RecentVideos(store_path).entries() == ()


def test_unreadable_recent_entries_are_skipped_and_readable_ones_kept(tmp_path: Path) -> None:
    """An entry from an older format is skipped without hiding the entries that can still be opened."""
    store_path = tmp_path / "recent-videos.json"
    video = _touch(tmp_path / "clip.mp4")
    store_path.write_text(
        f'[{{"video_path": "{video}"}}, {{"label": "Clip", "video": "{video}", "overlays": []}}]', encoding="utf-8"
    )

    assert _recipes(RecentVideos(store_path).entries()) == [("Clip", video, ())]


def _row_center(welcome: WelcomeWidget, label: str) -> QPointF:
    """Locate a row through the widget's hit-testing interface without depending on its layout implementation."""
    for y in range(welcome.height()):
        point = QPointF(welcome.width() / 2, y)
        item = welcome.item_at(point)
        if item is not None and item.label == label:
            return point
    raise AssertionError(f"Welcome row not found: {label}")


def test_clicking_welcome_rows_triggers_shortcut_actions_and_recent_videos(qtbot: QtBot, tmp_path: Path) -> None:
    """Welcome rows are buttons: shortcut rows trigger their action, recent rows request their video."""
    host = QWidget()
    qtbot.addWidget(host)
    manager = ShortcutManager()
    manager.register_defaults()
    manager.install(host)
    welcome = WelcomeWidget(host)
    welcome.resize(900, 700)
    welcome.set_shortcut_manager(manager)
    recent = _video_item(tmp_path / "gate.mp4", tmp_path / "gt.txt")
    welcome.set_recent_videos([recent])
    host.show()

    triggered: list[bool] = []
    manager.get_action("app.add_video").triggered.connect(lambda: triggered.append(True))
    requested: list[object] = []
    welcome.recent_video_requested.connect(requested.append)

    QTest.mouseClick(welcome, Qt.MouseButton.LeftButton, pos=_row_center(welcome, "Add Video").toPoint())
    QTest.mouseClick(welcome, Qt.MouseButton.LeftButton, pos=_row_center(welcome, "gate.mp4  +  gt.txt").toPoint())
    QTest.mouseClick(welcome, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))

    assert triggered == [True]
    assert requested == [recent]


def test_welcome_actions_and_recents_remain_clickable_in_a_small_workspace(qtbot: QtBot, tmp_path: Path) -> None:
    """A full recent list never clips actions out of reach in a supported small window."""
    center = SplitView()
    qtbot.addWidget(center)
    manager = ShortcutManager()
    manager.register_defaults()
    manager.install(center)
    center.set_welcome_shortcut_manager(manager)
    recent = [_video_item(tmp_path / f"clip{index}.mp4") for index in range(5)]
    welcome = center.welcome_widget()
    welcome.set_recent_videos(recent)
    center.resize(480, 280)
    center.show()
    QApplication.processEvents()
    scroll = center.findChild(QScrollArea)
    assert scroll is not None
    triggered: list[bool] = []
    manager.get_action("app.add_video").triggered.connect(lambda: triggered.append(True))
    requested: list[object] = []
    welcome.recent_video_requested.connect(requested.append)

    for label in ("Add Video", recent[-1].label):
        point = _row_center(welcome, label).toPoint()
        scroll.ensureVisible(point.x(), point.y(), 10, 10)
        QApplication.processEvents()
        visible_point = welcome.mapTo(scroll.viewport(), point)
        assert scroll.viewport().rect().contains(visible_point)
        QTest.mouseClick(welcome, Qt.MouseButton.LeftButton, pos=point)

    assert triggered == [True]
    assert requested == [recent[-1]]


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
    window = ApplicationWindow(content_browser=ContentBrowserWidget(), center_area=center)
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
