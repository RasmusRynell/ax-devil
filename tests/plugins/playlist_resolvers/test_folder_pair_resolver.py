"""Tests for the folder-pair playlist resolver plugin."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from PySide6.QtWidgets import QComboBox, QLineEdit
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace import FileOverlaySourceSpec, ResolvedPlaylistStartup, SeekableVideoContent
from ax_devil.plugins.playlist_resolvers.folder_pair.plugin import FolderPairResolverPlugin
from ax_devil.plugins.playlist_resolvers.folder_pair.resolver import build_playlist_contents, discover_folder_pairs
from tests.helpers.forms import form_field
from tests.helpers.widgets import button


def _write_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("data")


def test_cli_resolves_folder_pair(tmp_path: Path) -> None:
    """The contributed command produces startup content through its public runtime callback."""
    videos_dir = tmp_path / "videos"
    overlays_dir = tmp_path / "annotations"
    _write_file(videos_dir / "sample.mp4")
    _write_file(overlays_dir / "sample.json")
    startups: list[ResolvedPlaylistStartup] = []
    command = FolderPairResolverPlugin.create_cli_command()
    assert command is not None

    result = CliRunner().invoke(
        command,
        [str(videos_dir), str(overlays_dir), "--handler-type", "ADF_BETA_FRAME"],
        obj={"run_with_startup_content": startups.append},
    )

    assert result.exit_code == 0, result.output
    [startup] = startups
    [playlist] = startup.playlists
    assert playlist.display_name == "Folder Pair"
    [entry] = playlist.entries
    [lane] = entry.lanes
    assert lane.display_name == "sample"
    assert isinstance(lane.video, SeekableVideoContent)
    assert lane.video.source_spec.path == videos_dir / "sample.mp4"
    assert lane.overlay is not None
    assert lane.overlay.source_spec == FileOverlaySourceSpec(
        path=overlays_dir / "sample.json", handler_type="ADF_BETA_FRAME"
    )


def test_settings_widget_loads_scanned_pairs_and_needs_a_new_scan_after_path_change(
    tmp_path: Path, qtbot: QtBot
) -> None:
    videos_dir = tmp_path / "videos"
    overlays_dir = tmp_path / "annotations"
    _write_file(videos_dir / "sample.mp4")
    _write_file(overlays_dir / "sample.json")
    widget = FolderPairResolverPlugin().create_settings_widget()
    qtbot.addWidget(widget)
    videos_input = form_field(widget, "Videos:", QLineEdit)
    overlays_input = form_field(widget, "Overlays:", QLineEdit)
    decoder = form_field(widget, "Decoder", QComboBox)
    scan, load = button(widget, "Scan"), button(widget, "Load Playlist")

    scan.click()
    assert not load.isEnabled()

    videos_input.setText(str(videos_dir))
    overlays_input.setText(str(overlays_dir))
    scan.click()
    assert load.isEnabled()
    decoder.setCurrentIndex(decoder.findData("ADF_BETA_FRAME"))
    with qtbot.waitSignal(widget.playlist_resolved) as resolved:
        load.click()
    [playlist] = resolved.args[0]
    [entry] = playlist.entries
    [lane] = entry.lanes
    assert lane.overlay is not None
    assert lane.overlay.source_spec == FileOverlaySourceSpec(
        path=overlays_dir / "sample.json", handler_type="ADF_BETA_FRAME"
    )

    videos_input.setText(str(tmp_path / "other-videos"))
    assert not load.isEnabled()


class TestDiscoverFolderPairs:
    def test_discovers_pairs_by_suffix_stripped_name(self, tmp_path: Path) -> None:
        videos_dir = tmp_path / "videos"
        overlays_dir = tmp_path / "annotations"
        _write_file(videos_dir / "alpha.mp4")
        _write_file(videos_dir / "beta.mov")
        _write_file(videos_dir / "unmatched.mp4")
        _write_file(overlays_dir / "alpha.json")
        _write_file(overlays_dir / "beta.jsonl.gz")
        _write_file(overlays_dir / "orphan.json")

        matches = discover_folder_pairs(videos_dir, overlays_dir)

        assert [match.name for match in matches] == ["alpha", "beta"]
        assert matches[0].video_path == videos_dir / "alpha.mp4"
        assert matches[0].overlay_path == overlays_dir / "alpha.json"
        assert matches[1].overlay_path == overlays_dir / "beta.jsonl.gz"

    def test_preserves_dotted_stems_when_matching(self, tmp_path: Path) -> None:
        videos_dir = tmp_path / "videos"
        overlays_dir = tmp_path / "annotations"
        _write_file(videos_dir / "camera.1.mp4")
        _write_file(videos_dir / "camera.2.mp4")
        _write_file(overlays_dir / "camera.1.jsonl.gz")
        _write_file(overlays_dir / "camera.2.jsonl.gz")

        matches = discover_folder_pairs(videos_dir, overlays_dir)

        assert [match.name for match in matches] == ["camera.1", "camera.2"]

    def test_raises_on_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            discover_folder_pairs(tmp_path / "missing", tmp_path / "annotations")

    def test_raises_on_duplicate_stripped_names(self, tmp_path: Path) -> None:
        videos_dir = tmp_path / "videos"
        overlays_dir = tmp_path / "annotations"
        _write_file(videos_dir / "sample.mp4")
        _write_file(videos_dir / "sample.mov")
        _write_file(overlays_dir / "sample.json")

        with pytest.raises(ValueError, match="Duplicate video"):
            discover_folder_pairs(videos_dir, overlays_dir)


class TestBuildPlaylistContents:
    def test_empty_matches_return_no_playlist(self) -> None:
        assert build_playlist_contents([], "ADF_BETA_FRAME") == []
