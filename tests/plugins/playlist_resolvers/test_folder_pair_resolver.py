"""Tests for the folder-pair playlist resolver plugin."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from pytestqt.qtbot import QtBot

from ax_devil.modules.plugin_system import PLAYLIST_RESOLVER_PLUGIN_TYPE, PlaylistResolverWidget
from ax_devil.modules.workspace import (
    FileOverlaySourceSpec,
    OverlaySourceKind,
    ResolvedPlaylistStartup,
    SeekableVideoContent,
)
from ax_devil.plugins.playlist_resolvers.folder_pair.plugin import FolderPairResolverPlugin
from ax_devil.plugins.playlist_resolvers.folder_pair.resolver import build_playlist_contents, discover_folder_pairs
from ax_devil.plugins.playlist_resolvers.folder_pair.settings_widget import FolderPairSettingsWidget


def _write_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("data")


class TestFolderPairResolverPluginMetadata:
    """Tests for FolderPairResolverPlugin identity and definition."""

    def test_definition_and_settings_widget(self, qtbot: QtBot) -> None:
        assert FolderPairResolverPlugin.plugin_id() == "folder_pair"
        assert FolderPairResolverPlugin.plugin_type() == PLAYLIST_RESOLVER_PLUGIN_TYPE
        assert FolderPairResolverPlugin.display_name() == "Folder Pair"
        assert FolderPairResolverPlugin.description() is not None

        defn = FolderPairResolverPlugin.definition()
        assert defn.plugin_type == PLAYLIST_RESOLVER_PLUGIN_TYPE
        assert defn.plugin_id == "folder_pair"
        assert defn.display_name == "Folder Pair"

        widget = FolderPairResolverPlugin().create_settings_widget()
        qtbot.addWidget(widget)
        assert isinstance(widget, PlaylistResolverWidget)

    def test_cli_resolves_folder_pair(self, tmp_path: Path) -> None:
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
        lane = playlist.entries[0].lanes[0]
        assert isinstance(lane.video, SeekableVideoContent)
        assert lane.video.source_spec.path == videos_dir / "sample.mp4"
        assert lane.overlay is not None
        assert lane.overlay.source_spec == FileOverlaySourceSpec(
            path=overlays_dir / "sample.json", handler_type="ADF_BETA_FRAME"
        )


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
    def test_builds_entries_with_file_overlay_decoder(self, tmp_path: Path) -> None:
        videos_dir = tmp_path / "videos"
        overlays_dir = tmp_path / "annotations"
        _write_file(videos_dir / "sample.mp4")
        _write_file(overlays_dir / "sample.json")

        playlists = build_playlist_contents(discover_folder_pairs(videos_dir, overlays_dir), "ADF_BETA_FRAME")

        assert len(playlists) == 1
        playlist = playlists[0]
        assert playlist.metadata == {"resolver": "Folder Pair", "matches": 1}
        assert len(playlist.entries) == 1
        lane = playlist.entries[0].lanes[0]
        assert lane.video.display_name == "sample"
        assert isinstance(lane.video, SeekableVideoContent)
        assert lane.video.source_spec.path == videos_dir / "sample.mp4"
        assert lane.overlay is not None
        assert lane.overlay.display_name == "sample"
        assert lane.display_name == "sample"
        assert lane.overlay.source_spec == FileOverlaySourceSpec(
            path=overlays_dir / "sample.json",
            handler_type="ADF_BETA_FRAME",
        )
        assert lane.source_kind == OverlaySourceKind.FILE_SOURCE
        assert lane.metadata == {}
        assert lane.overlay.metadata == {}

    def test_empty_matches_return_no_playlist(self) -> None:
        assert build_playlist_contents([], "ADF_BETA_FRAME") == []


class TestFolderPairSettingsWidget:
    def test_scan_requires_both_paths(self, qtbot: QtBot) -> None:
        widget = FolderPairSettingsWidget()
        qtbot.addWidget(widget)

        widget._scan_pairs()

        assert widget._matches == []
        assert not widget._load_btn.isEnabled()
        assert widget._status_label.text() == "Select video and overlay folders."

    def test_path_change_clears_scanned_matches(self, tmp_path: Path, qtbot: QtBot) -> None:
        videos_dir = tmp_path / "videos"
        overlays_dir = tmp_path / "annotations"
        _write_file(videos_dir / "sample.mp4")
        _write_file(overlays_dir / "sample.json")

        widget = FolderPairSettingsWidget()
        qtbot.addWidget(widget)
        widget._videos_input.setText(str(videos_dir))
        widget._overlays_input.setText(str(overlays_dir))
        widget._scan_pairs()

        assert len(widget._matches) == 1
        assert widget._load_btn.isEnabled()

        widget._videos_input.setText(str(tmp_path / "other-videos"))

        assert widget._matches == []
        assert not widget._load_btn.isEnabled()
        assert widget._status_label.text() == ""
