"""Tests for the folder-pair playlist resolver plugin."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import NEVER, Cancelled, CancelToken
from ax_devil.modules.background_jobs import background_jobs
from ax_devil.modules.workspace.core import FileOverlaySourceSpec, PlaylistItem, SeekableVideoContent
from ax_devil.plugins.playlist_resolvers.folder_pair import settings_widget
from ax_devil.plugins.playlist_resolvers.folder_pair.plugin import FolderPairResolverPlugin
from ax_devil.plugins.playlist_resolvers.folder_pair.resolver import (
    FolderPairMatch,
    build_playlist_contents,
    discover_folder_pairs,
)
from ax_devil.plugins.playlist_resolvers.folder_pair.settings_widget import FolderPairSettingsWidget
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

        [playlist] = FolderPairResolverPlugin().resolve(settings, NEVER)

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
            FolderPairResolverPlugin().resolve(settings, NEVER)

    def test_missing_folder_raises(self, tmp_path: Path) -> None:
        settings = {
            "videos_dir": str(tmp_path / "gone"),
            "overlays_dir": str(tmp_path),
            "handler_type": "ADF_BETA_FRAME",
        }

        with pytest.raises(FileNotFoundError):
            FolderPairResolverPlugin().resolve(settings, NEVER)


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
    assert not load.isEnabled()
    decoder.setCurrentIndex(decoder.findData("ADF_BETA_FRAME"))
    assert load.isEnabled()
    scan.click()
    assert not load.isEnabled()
    qtbot.waitUntil(load.isEnabled)
    with qtbot.waitSignal(widget.settings_submitted) as submitted:
        load.click()
    assert submitted.args[0] == {
        "videos_dir": str(videos_dir),
        "overlays_dir": str(overlays_dir),
        "handler_type": "ADF_BETA_FRAME",
    }

    videos_input.clear()
    assert not load.isEnabled()
    changed_videos, changed_overlays = _pair_folders(tmp_path, "changed", "another")
    videos_input.setText(str(changed_videos))
    overlays_input.setText(str(changed_overlays))
    with qtbot.waitSignal(widget.settings_submitted) as submitted:
        load.click()
        assert not load.isEnabled()
    assert submitted.args[0] == {
        "videos_dir": str(changed_videos),
        "overlays_dir": str(changed_overlays),
        "handler_type": "ADF_BETA_FRAME",
    }


class _SlowScan:
    """Hold the scan of the *slow* video folder until released; scans of other folders run normally."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.interrupted = False
        real = discover_folder_pairs

        def discover(videos_dir: Path, overlays_dir: Path, token: CancelToken) -> list[FolderPairMatch]:
            if videos_dir.name != "slow":
                return real(videos_dir, overlays_dir, token)
            self.started.set()
            assert self.release.wait(5)
            if fail:
                raise ValueError("stale failure")
            try:
                return real(videos_dir, overlays_dir, token)
            except Cancelled:
                self.interrupted = True
                raise

        monkeypatch.setattr(settings_widget, "discover_folder_pairs", discover)

    def finish(self, qtbot: QtBot) -> None:
        """Let the held scan end and wait until its outcome has been delivered or dropped."""
        with qtbot.waitSignal(background_jobs("plugins").finished):
            self.release.set()


def _pair_folders(root: Path, name: str, pair: str) -> tuple[Path, Path]:
    _write_file(root / name / "videos" / f"{pair}.mp4")
    _write_file(root / name / "overlays" / f"{pair}.json")
    return root / name / "videos", root / name / "overlays"


def _says(widget: FolderPairSettingsWidget, text: str) -> bool:
    return any(label.text() == text for label in widget.findChildren(QLabel))


@pytest.mark.parametrize("stale_fails", [False, True])
def test_a_scan_replaced_by_a_newer_one_cannot_change_the_form(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, stale_fails: bool
) -> None:
    slow_videos = tmp_path / "slow"
    _write_file(slow_videos / "stale.mp4")
    _write_file(tmp_path / "slow-overlays" / "stale.json")
    fast_videos, fast_overlays = _pair_folders(tmp_path, "fast", "current")
    slow = _SlowScan(monkeypatch, fail=stale_fails)
    widget = FolderPairSettingsWidget()
    qtbot.addWidget(widget)
    videos_input = form_field(widget, "Videos:", QLineEdit)
    overlays_input = form_field(widget, "Overlays:", QLineEdit)
    form_field(widget, "Decoder", QComboBox).setCurrentIndex(1)
    load = button(widget, "Load Playlist")
    videos_input.setText(str(slow_videos))
    overlays_input.setText(str(tmp_path / "slow-overlays"))
    button(widget, "Scan").click()
    qtbot.waitUntil(slow.started.is_set)

    videos_input.setText(str(fast_videos))
    overlays_input.setText(str(fast_overlays))
    button(widget, "Scan").click()
    qtbot.waitUntil(load.isEnabled)
    slow.finish(qtbot)

    assert load.isEnabled()
    assert _says(widget, "Found 1 matched pair(s).")
    with qtbot.waitSignal(widget.settings_submitted) as submitted:
        load.click()
    assert submitted.args[0]["videos_dir"] == str(fast_videos)


@pytest.mark.parametrize("stale_fails", [False, True])
def test_editing_a_folder_while_scanning_drops_the_stale_outcome(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, stale_fails: bool
) -> None:
    slow_videos = tmp_path / "slow"
    _write_file(slow_videos / "stale.mp4")
    _write_file(tmp_path / "overlays" / "stale.json")
    slow = _SlowScan(monkeypatch, fail=stale_fails)
    widget = FolderPairSettingsWidget()
    qtbot.addWidget(widget)
    videos_input = form_field(widget, "Videos:", QLineEdit)
    form_field(widget, "Overlays:", QLineEdit).setText(str(tmp_path / "overlays"))
    form_field(widget, "Decoder", QComboBox).setCurrentIndex(1)
    videos_input.setText(str(slow_videos))
    button(widget, "Scan").click()
    qtbot.waitUntil(slow.started.is_set)

    videos_input.setText(str(tmp_path / "elsewhere"))
    slow.finish(qtbot)

    assert not any(label.text().startswith(("Found", "Scan failed")) for label in widget.findChildren(QLabel))


def test_a_cancelled_scan_does_not_touch_its_folders_again_to_resolve_them(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_videos = tmp_path / "slow"
    _write_file(slow_videos / "stale.mp4")
    _write_file(tmp_path / "overlays" / "stale.json")
    slow = _SlowScan(monkeypatch)
    widget = FolderPairSettingsWidget()
    qtbot.addWidget(widget)
    videos_input = form_field(widget, "Videos:", QLineEdit)
    form_field(widget, "Overlays:", QLineEdit).setText(str(tmp_path / "overlays"))
    videos_input.setText(str(slow_videos))
    resolved: list[Path] = []
    real_resolve = Path.resolve

    def resolve(self: Path, strict: bool = False) -> Path:
        resolved.append(self)
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    button(widget, "Scan").click()
    qtbot.waitUntil(slow.started.is_set)

    videos_input.setText(str(tmp_path / "elsewhere"))
    slow.finish(qtbot)

    assert slow_videos not in resolved
    assert tmp_path / "overlays" not in resolved


def test_a_failed_scan_reports_the_reason_and_keeps_load_disabled(tmp_path: Path, qtbot: QtBot) -> None:
    widget = FolderPairSettingsWidget()
    qtbot.addWidget(widget)
    form_field(widget, "Videos:", QLineEdit).setText(str(tmp_path / "gone"))
    form_field(widget, "Overlays:", QLineEdit).setText(str(tmp_path))
    form_field(widget, "Decoder", QComboBox).setCurrentIndex(1)

    button(widget, "Scan").click()

    qtbot.waitUntil(lambda: any(label.text().startswith("Scan failed") for label in widget.findChildren(QLabel)))
    assert not button(widget, "Load Playlist").isEnabled()


def test_loading_before_a_scan_scans_and_then_submits_the_chosen_decoder(tmp_path: Path, qtbot: QtBot) -> None:
    videos_dir, overlays_dir = _pair_folders(tmp_path, "set", "sample")
    widget = FolderPairSettingsWidget()
    qtbot.addWidget(widget)
    load = button(widget, "Load Playlist")
    form_field(widget, "Videos:", QLineEdit).setText(str(videos_dir))
    form_field(widget, "Overlays:", QLineEdit).setText(str(overlays_dir))
    decoder = form_field(widget, "Decoder", QComboBox)
    decoder.setCurrentIndex(decoder.findData("ADF_BETA_FRAME"))
    assert load.isEnabled()

    with qtbot.waitSignal(widget.settings_submitted) as submitted:
        load.click()
        assert not load.isEnabled()

    assert submitted.args[0] == {
        "videos_dir": str(videos_dir),
        "overlays_dir": str(overlays_dir),
        "handler_type": "ADF_BETA_FRAME",
    }


def test_loading_folders_without_pairs_reports_it_and_waits_for_a_folder_change(tmp_path: Path, qtbot: QtBot) -> None:
    _write_file(tmp_path / "videos" / "lonely.mp4")
    _write_file(tmp_path / "overlays" / "other.json")
    paired_videos, paired_overlays = _pair_folders(tmp_path, "set", "sample")
    widget = FolderPairSettingsWidget()
    qtbot.addWidget(widget)
    load = button(widget, "Load Playlist")
    videos_input = form_field(widget, "Videos:", QLineEdit)
    overlays_input = form_field(widget, "Overlays:", QLineEdit)
    videos_input.setText(str(tmp_path / "videos"))
    overlays_input.setText(str(tmp_path / "overlays"))
    form_field(widget, "Decoder", QComboBox).setCurrentIndex(1)
    submitted: list[object] = []
    widget.settings_submitted.connect(submitted.append)

    load.click()
    qtbot.waitUntil(lambda: _says(widget, "No matched video/overlay pairs found."))

    assert submitted == []
    assert not load.isEnabled()
    videos_input.setText(str(paired_videos))
    overlays_input.setText(str(paired_overlays))
    assert load.isEnabled()


def test_changing_the_decoder_while_loading_scans_keeps_the_pairs_but_submits_nothing(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_file(tmp_path / "slow" / "sample.mp4")
    _write_file(tmp_path / "overlays" / "sample.json")
    slow = _SlowScan(monkeypatch)
    widget = FolderPairSettingsWidget()
    qtbot.addWidget(widget)
    load = button(widget, "Load Playlist")
    form_field(widget, "Videos:", QLineEdit).setText(str(tmp_path / "slow"))
    form_field(widget, "Overlays:", QLineEdit).setText(str(tmp_path / "overlays"))
    decoder = form_field(widget, "Decoder", QComboBox)
    assert decoder.count() > 2
    decoder.setCurrentIndex(1)
    submitted: list[object] = []
    widget.settings_submitted.connect(submitted.append)
    load.click()
    qtbot.waitUntil(slow.started.is_set)

    decoder.setCurrentIndex(2)
    slow.finish(qtbot)

    assert submitted == []
    assert load.isEnabled()


def test_a_scan_still_running_when_the_form_is_closed_submits_nothing(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_file(tmp_path / "slow" / "sample.mp4")
    _write_file(tmp_path / "overlays" / "sample.json")
    slow = _SlowScan(monkeypatch)
    widget = FolderPairSettingsWidget()
    submitted: list[object] = []
    widget.settings_submitted.connect(submitted.append)
    form_field(widget, "Videos:", QLineEdit).setText(str(tmp_path / "slow"))
    form_field(widget, "Overlays:", QLineEdit).setText(str(tmp_path / "overlays"))
    form_field(widget, "Decoder", QComboBox).setCurrentIndex(1)
    button(widget, "Load Playlist").click()
    qtbot.waitUntil(slow.started.is_set)

    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    slow.finish(qtbot)

    assert slow.interrupted
    assert submitted == []


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

        matches = discover_folder_pairs(videos_dir, overlays_dir, NEVER)

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

        matches = discover_folder_pairs(videos_dir, overlays_dir, NEVER)

        assert [match.name for match in matches] == ["camera.1", "camera.2"]

    def test_raises_on_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            discover_folder_pairs(tmp_path / "missing", tmp_path / "annotations", NEVER)

    def test_a_cancelled_token_stops_the_scan_before_it_inspects_a_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        videos_dir = tmp_path / "videos"
        overlays_dir = tmp_path / "annotations"
        for name in ("a", "b", "c"):
            _write_file(videos_dir / f"{name}.mp4")
            _write_file(overlays_dir / f"{name}.json")
        token = CancelToken()
        inspected: list[Path] = []
        real_iterdir, real_is_file = Path.iterdir, Path.is_file

        def iterdir(self: Path) -> Iterator[Path]:
            for child in real_iterdir(self):
                token.cancel()
                yield child

        def is_file(self: Path) -> bool:
            inspected.append(self)
            return real_is_file(self)

        monkeypatch.setattr(Path, "iterdir", iterdir)
        monkeypatch.setattr(Path, "is_file", is_file)

        with pytest.raises(Cancelled):
            discover_folder_pairs(videos_dir, overlays_dir, token)

        assert inspected == []

    def test_raises_on_duplicate_stripped_names(self, tmp_path: Path) -> None:
        videos_dir = tmp_path / "videos"
        overlays_dir = tmp_path / "annotations"
        _write_file(videos_dir / "sample.mp4")
        _write_file(videos_dir / "sample.mov")
        _write_file(overlays_dir / "sample.json")

        with pytest.raises(ValueError, match="Duplicate video"):
            discover_folder_pairs(videos_dir, overlays_dir, NEVER)


class TestBuildPlaylistContents:
    def test_empty_matches_return_no_playlist(self) -> None:
        assert build_playlist_contents([], "ADF_BETA_FRAME", NEVER) == []
