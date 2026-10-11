"""Tests for the folder-pair playlist resolver plugin."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from PySide6.QtWidgets import QComboBox, QLineEdit
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import FileOverlaySourceSpec, PlaylistItem, SeekableVideoContent
from ax_devil.plugins.playlist_resolvers.folder_pair.plugin import FolderPairResolverPlugin
from ax_devil.plugins.playlist_resolvers.folder_pair.resolver import build_playlist_contents, discover_folder_pairs
from tests.helpers.forms import form_field
from tests.helpers.widgets import button


def _write_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("data")


def test_cli_opens_a_playlist_item_with_absolute_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The command hands the runner a Playlist Item whose settings do not depend on the working directory."""
    videos_dir = tmp_path / "videos"
    overlays_dir = tmp_path / "annotations"
    _write_file(videos_dir / "sample.mp4")
    _write_file(overlays_dir / "sample.json")
    launched: list[list[PlaylistItem]] = []
    command = FolderPairResolverPlugin.create_cli_command()
    assert command is not None
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        command,
        ["videos", "annotations", "--handler-type", "ADF_BETA_FRAME"],
        obj={"run_with_items": launched.append},
    )

    assert result.exit_code == 0, result.output
    [[item]] = launched
    assert (item.label, item.resolver) == ("", "folder_pair")
    assert item.settings == {
        "videos_dir": str(videos_dir),
        "overlays_dir": str(overlays_dir),
        "handler_type": "ADF_BETA_FRAME",
    }


class TestResolveFromSettings:
    def test_resolves_matched_pairs_without_a_widget(self, tmp_path: Path) -> None:
        _write_file(tmp_path / "videos" / "sample.mp4")
        _write_file(tmp_path / "annotations" / "sample.json")
        settings = {
            "videos_dir": str(tmp_path / "videos"),
            "overlays_dir": str(tmp_path / "annotations"),
            "handler_type": "ADF_BETA_FRAME",
        }

        [playlist] = FolderPairResolverPlugin().resolve(settings)

        [entry] = playlist.entries
        [lane] = entry.lanes
        assert lane.display_name == "sample"
        assert isinstance(lane.video, SeekableVideoContent)
        assert lane.video.source_spec.path == tmp_path / "videos" / "sample.mp4"
        assert lane.overlay is not None
        assert lane.overlay.source_spec == FileOverlaySourceSpec(
            path=tmp_path / "annotations" / "sample.json", handler_type="ADF_BETA_FRAME"
        )

    @pytest.mark.parametrize("missing", ["videos_dir", "overlays_dir", "handler_type"])
    def test_missing_settings_name_the_setting(self, tmp_path: Path, missing: str) -> None:
        settings = {"videos_dir": str(tmp_path), "overlays_dir": str(tmp_path), "handler_type": "ADF_BETA_FRAME"}
        del settings[missing]

        with pytest.raises(ValueError, match=missing):
            FolderPairResolverPlugin().resolve(settings)

    def test_missing_folder_raises(self, tmp_path: Path) -> None:
        settings = {
            "videos_dir": str(tmp_path / "gone"),
            "overlays_dir": str(tmp_path),
            "handler_type": "ADF_BETA_FRAME",
        }

        with pytest.raises(FileNotFoundError):
            FolderPairResolverPlugin().resolve(settings)


def test_settings_widget_submits_scanned_folders_and_needs_a_new_scan_after_path_change(
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
    with qtbot.waitSignal(widget.settings_submitted) as submitted:
        load.click()
    assert submitted.args[0] == {
        "videos_dir": str(videos_dir),
        "overlays_dir": str(overlays_dir),
        "handler_type": "ADF_BETA_FRAME",
    }

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
