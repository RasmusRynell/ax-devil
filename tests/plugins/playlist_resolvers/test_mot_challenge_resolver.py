"""Tests for the MOT Challenge playlist resolver plugin."""

from __future__ import annotations

import textwrap
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import NEVER, Cancelled, CancelToken
from ax_devil.modules.background_jobs import background_jobs
from ax_devil.modules.workspace.core import FileOverlaySourceSpec, PlaylistItem, SeekableVideoContent
from ax_devil.plugins.playlist_resolvers.mot_challenge import settings_widget
from ax_devil.plugins.playlist_resolvers.mot_challenge.plugin import MOTChallengeResolverPlugin
from ax_devil.plugins.playlist_resolvers.mot_challenge.resolver import (
    SequenceInfo,
    build_playlist_contents,
    discover_sequences,
)
from ax_devil.plugins.playlist_resolvers.mot_challenge.settings_widget import MOTChallengeSettingsWidget
from tests.helpers.widgets import button

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEQINFO_TEMPLATE = textwrap.dedent("""\
    [Sequence]
    name={name}
    imDir=img1
    frameRate={fps}
    seqLength={length}
    imWidth={width}
    imHeight={height}
    imExt=.jpg
""")


def _make_sequence(
    root: Path,
    name: str,
    *,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    length: int = 100,
    create_det: bool = True,
    create_gt: bool = False,
) -> Path:
    """Create a minimal MOT sequence directory structure."""
    seq_dir = root / name
    seq_dir.mkdir(parents=True)
    (seq_dir / "img1").mkdir()
    ini = SEQINFO_TEMPLATE.format(name=name, fps=fps, width=width, height=height, length=length)
    (seq_dir / "seqinfo.ini").write_text(ini)

    if create_det:
        (seq_dir / "det").mkdir()
        (seq_dir / "det" / "det.txt").write_text("1,-1,100,100,50,120,0.9,-1,-1,-1\n")

    if create_gt:
        (seq_dir / "gt").mkdir()
        (seq_dir / "gt" / "gt.txt").write_text("1,1,100,100,50,120,1,1,0.8\n")

    return seq_dir


# ---------------------------------------------------------------------------
# Settings widget
# ---------------------------------------------------------------------------


def test_settings_widget_loads_only_checked_sequences(tmp_path: Path, qtbot: QtBot) -> None:
    _make_sequence(tmp_path, "SEQ-01")
    _make_sequence(tmp_path, "SEQ-02")
    widget = MOTChallengeResolverPlugin().create_settings_widget()
    qtbot.addWidget(widget)
    load = button(widget, "Load Playlist")
    assert not load.isEnabled()

    root = widget.findChild(QLineEdit)
    assert root is not None
    root.setText(str(tmp_path))
    button(widget, "Scan").click()
    sequences = widget.findChild(QListWidget)
    assert sequences is not None
    qtbot.waitUntil(load.isEnabled)
    assert sequences.count() == 2
    first = sequences.item(0)
    assert first is not None
    first.setCheckState(Qt.CheckState.Unchecked)
    with qtbot.waitSignal(widget.settings_submitted) as submitted:
        load.click()

    assert submitted.args[0] == {"root": str(tmp_path), "sequences": ["SEQ-02"]}


@pytest.mark.parametrize("replacement", ["", "second"])
def test_editing_a_completed_scan_clears_old_sequences_until_the_new_root_is_scanned(
    tmp_path: Path, qtbot: QtBot, replacement: str
) -> None:
    """After editing the root, Load cannot submit the previous folder or its checked sequences."""
    _make_sequence(tmp_path / "first", "OLD-01")
    _make_sequence(tmp_path / "second", "NEW-01")
    widget = MOTChallengeSettingsWidget()
    qtbot.addWidget(widget)
    root = widget.findChild(QLineEdit)
    assert root is not None
    load = button(widget, "Load Playlist")
    root.setText(str(tmp_path / "first"))
    button(widget, "Scan").click()
    qtbot.waitUntil(load.isEnabled)
    assert _listed(widget) == ["OLD-01"]
    submitted: list[object] = []
    widget.settings_submitted.connect(submitted.append)

    root.setText(str(tmp_path / replacement) if replacement else "")

    assert _listed(widget) == []
    assert not load.isEnabled()
    assert not _status_says(widget, "Found")
    load.click()
    assert submitted == []
    root.setText(str(tmp_path / "second"))
    button(widget, "Scan").click()
    qtbot.waitUntil(load.isEnabled)
    load.click()
    assert submitted == [{"root": str(tmp_path / "second"), "sequences": ["NEW-01"]}]


class _SlowScan:
    """Hold the scan of a root named *slow* until released; scans of other roots run normally."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.interrupted = False
        real = discover_sequences

        def discover(root: Path, token: CancelToken) -> list[SequenceInfo]:
            if root.name != "slow":
                return real(root, token)
            self.started.set()
            assert self.release.wait(5)
            if fail:
                raise ValueError("stale failure")
            try:
                return real(root, token)
            except Cancelled:
                self.interrupted = True
                raise

        monkeypatch.setattr(settings_widget, "discover_sequences", discover)

    def finish(self, qtbot: QtBot) -> None:
        """Let the held scan end and wait until its outcome has been delivered or dropped."""
        with qtbot.waitSignal(background_jobs("plugins").finished):
            self.release.set()


def _status_says(widget: MOTChallengeSettingsWidget, text: str) -> bool:
    return any(label.text().startswith(text) for label in widget.findChildren(QLabel))


def _listed(widget: MOTChallengeSettingsWidget) -> list[str]:
    sequences = widget.findChild(QListWidget)
    assert sequences is not None
    return [item.text().split()[0] for item in sequences.findItems("*", Qt.MatchFlag.MatchWildcard)]


@pytest.mark.parametrize("stale_fails", [False, True])
def test_a_scan_replaced_by_a_newer_one_cannot_change_the_list(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, stale_fails: bool
) -> None:
    _make_sequence(tmp_path / "slow", "STALE-01")
    _make_sequence(tmp_path / "fast", "CURRENT-01")
    slow = _SlowScan(monkeypatch, fail=stale_fails)
    widget = MOTChallengeSettingsWidget()
    qtbot.addWidget(widget)
    root = widget.findChild(QLineEdit)
    assert root is not None
    load = button(widget, "Load Playlist")
    root.setText(str(tmp_path / "slow"))
    button(widget, "Scan").click()
    qtbot.waitUntil(slow.started.is_set)

    root.setText(str(tmp_path / "fast"))
    button(widget, "Scan").click()
    qtbot.waitUntil(load.isEnabled)
    slow.finish(qtbot)

    assert _listed(widget) == ["CURRENT-01"]
    assert load.isEnabled()
    with qtbot.waitSignal(widget.settings_submitted) as submitted:
        load.click()
    assert submitted.args[0] == {"root": str(tmp_path / "fast"), "sequences": ["CURRENT-01"]}


@pytest.mark.parametrize("stale_fails", [False, True])
def test_editing_the_root_while_scanning_leaves_load_disabled(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, stale_fails: bool
) -> None:
    _make_sequence(tmp_path / "slow", "STALE-01")
    slow = _SlowScan(monkeypatch, fail=stale_fails)
    widget = MOTChallengeSettingsWidget()
    qtbot.addWidget(widget)
    root = widget.findChild(QLineEdit)
    assert root is not None
    root.setText(str(tmp_path / "slow"))
    button(widget, "Scan").click()
    qtbot.waitUntil(slow.started.is_set)

    root.setText(str(tmp_path / "elsewhere"))
    slow.finish(qtbot)

    assert _listed(widget) == []
    assert not button(widget, "Load Playlist").isEnabled()
    assert not _status_says(widget, "Found")
    assert not _status_says(widget, "Scan failed")


def test_a_cancelled_scan_does_not_touch_its_root_again_to_resolve_it(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_sequence(tmp_path / "slow", "STALE-01")
    slow = _SlowScan(monkeypatch)
    widget = MOTChallengeSettingsWidget()
    qtbot.addWidget(widget)
    root = widget.findChild(QLineEdit)
    assert root is not None
    root.setText(str(tmp_path / "slow"))
    resolved: list[Path] = []
    real_resolve = Path.resolve

    def resolve(self: Path, strict: bool = False) -> Path:
        resolved.append(self)
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    button(widget, "Scan").click()
    qtbot.waitUntil(slow.started.is_set)

    root.setText(str(tmp_path / "elsewhere"))
    slow.finish(qtbot)

    assert tmp_path / "slow" not in resolved


def test_a_missing_root_reports_the_failure_and_keeps_load_disabled(tmp_path: Path, qtbot: QtBot) -> None:
    widget = MOTChallengeSettingsWidget()
    qtbot.addWidget(widget)
    root = widget.findChild(QLineEdit)
    assert root is not None
    root.setText(str(tmp_path / "gone"))

    button(widget, "Scan").click()

    qtbot.waitUntil(lambda: _status_says(widget, "Scan failed"))
    assert not button(widget, "Load Playlist").isEnabled()


def test_a_scan_still_running_when_the_form_is_closed_is_told_to_stop(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_sequence(tmp_path / "slow", "SEQ-01")
    slow = _SlowScan(monkeypatch)
    widget = MOTChallengeSettingsWidget()
    root = widget.findChild(QLineEdit)
    assert root is not None
    root.setText(str(tmp_path / "slow"))
    button(widget, "Scan").click()
    qtbot.waitUntil(slow.started.is_set)

    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    slow.finish(qtbot)

    assert slow.interrupted


# ---------------------------------------------------------------------------
# Resolving from settings and the CLI
# ---------------------------------------------------------------------------


def _entry_names(settings: dict[str, object]) -> list[str]:
    [playlist] = MOTChallengeResolverPlugin().resolve(settings, NEVER)
    return [entry.lanes[0].video.display_name for entry in playlist.entries]


class TestResolveFromSettings:
    def test_resolves_every_sequence_without_a_sequence_list(self, tmp_path: Path) -> None:
        _make_sequence(tmp_path, "SEQ-01")
        _make_sequence(tmp_path, "SEQ-02")

        assert _entry_names({"root": str(tmp_path)}) == ["SEQ-01", "SEQ-02"]

    def test_resolves_only_the_listed_sequences(self, tmp_path: Path) -> None:
        _make_sequence(tmp_path, "SEQ-01")
        _make_sequence(tmp_path, "SEQ-02")

        assert _entry_names({"root": str(tmp_path), "sequences": ["SEQ-02", "GONE"]}) == ["SEQ-02"]

    @pytest.mark.parametrize(
        ("settings", "message"),
        [
            ({}, "'root'"),
            ({"root": ""}, "'root'"),
            ({"root": "ROOT", "sequences": "SEQ-01"}, "'sequences'"),
            ({"root": "ROOT", "sequences": ["GONE"]}, "No MOT sequences"),
        ],
    )
    def test_invalid_settings_raise_a_clear_error(
        self, tmp_path: Path, settings: dict[str, object], message: str
    ) -> None:
        _make_sequence(tmp_path, "SEQ-01")
        settings = {key: str(tmp_path) if value == "ROOT" else value for key, value in settings.items()}

        with pytest.raises(ValueError, match=message):
            MOTChallengeResolverPlugin().resolve(settings, NEVER)


class TestCliCommand:
    def test_cli_opens_a_playlist_item_for_every_sequence(self, tmp_path: Path) -> None:
        _make_sequence(tmp_path, "SEQ-01")
        launched: list[list[PlaylistItem]] = []
        command = MOTChallengeResolverPlugin.create_cli_command()
        assert command is not None

        result = CliRunner().invoke(command, [str(tmp_path)], obj={"run_with_items": launched.append})

        assert result.exit_code == 0, result.output
        [[item]] = launched
        assert (item.label, item.resolver, item.settings) == ("", "mot_challenge", {"root": str(tmp_path)})


# ---------------------------------------------------------------------------
# Sequence discovery
# ---------------------------------------------------------------------------


def test_discovers_sorted_sequences_with_metadata_and_skips_other_folders(tmp_path: Path) -> None:
    """Discovery accepts an empty root and yields only readable sequence folders in name order."""
    assert discover_sequences(tmp_path, NEVER) == []
    (tmp_path / "random_folder").mkdir()
    broken = tmp_path / "MOT16-02"
    broken.mkdir()
    (broken / "seqinfo.ini").write_text("[Other]\nkey=val\n")
    _make_sequence(tmp_path, "MOT16-06", fps=14, width=640, height=480, length=1194)
    _make_sequence(tmp_path, "MOT16-01", length=450, fps=30)

    seqs = discover_sequences(tmp_path, NEVER)

    assert [seq.name for seq in seqs] == ["MOT16-01", "MOT16-06"]
    assert seqs[0].frame_count == 450
    seq = seqs[1]
    assert (seq.fps, seq.width, seq.height, seq.frame_count) == (14.0, 640, 480, 1194)
    assert (seq.im_dir, seq.im_ext) == ("img1", ".jpg")


def test_discovers_train_and_test_split_sequences_from_dataset_root(tmp_path: Path) -> None:
    train = _make_sequence(tmp_path / "train", "MOT17-02-DPM", create_gt=True)
    test = _make_sequence(tmp_path / "test", "MOT17-01-DPM", create_gt=False)

    assert [seq.name for seq in discover_sequences(tmp_path, NEVER)] == ["MOT17-01-DPM", "MOT17-02-DPM"]
    sources = MOTChallengeResolverPlugin().source_paths({"root": str(tmp_path)}, NEVER)
    assert sources.folders == {tmp_path} and sources.files == {train / "seqinfo.ini", test / "seqinfo.ini"}, (
        "a correction to a sequence's seqinfo.ini resolves the playlist again"
    )
    assert MOTChallengeResolverPlugin().source_paths({"root": str(tmp_path / "gone")}, NEVER).folders == {
        tmp_path / "gone"
    }


def test_discovery_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover_sequences(tmp_path / "nonexistent", NEVER)


def test_a_cancelled_token_stops_the_scan_before_it_probes_a_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_sequence(tmp_path, "SEQ-01")
    _make_sequence(tmp_path, "SEQ-02")
    token = CancelToken()
    token.cancel()
    probed: list[Path] = []
    real_is_file = Path.is_file

    def is_file(self: Path) -> bool:
        probed.append(self)
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)

    with pytest.raises(Cancelled):
        discover_sequences(tmp_path, token)

    assert probed == []


def test_cancelling_while_listing_the_root_stops_before_checking_its_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("SEQ-01", "SEQ-02", "SEQ-03"):
        _make_sequence(tmp_path, name)
    token = CancelToken()
    checked: list[Path] = []
    real_iterdir, real_is_dir = Path.iterdir, Path.is_dir

    def iterdir(self: Path) -> Iterator[Path]:
        for child in real_iterdir(self):
            token.cancel()
            yield child

    def is_dir(self: Path) -> bool:
        checked.append(self)
        return real_is_dir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    monkeypatch.setattr(Path, "is_dir", is_dir)

    with pytest.raises(Cancelled):
        discover_sequences(tmp_path, token)

    assert checked == [tmp_path]


def test_cancelling_while_probing_a_sequence_stops_before_the_next_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_sequence(tmp_path, "SEQ-01")
    _make_sequence(tmp_path, "SEQ-02")
    token = CancelToken()
    probed: list[Path] = []
    real_is_file = Path.is_file

    def is_file(self: Path) -> bool:
        probed.append(self)
        result = real_is_file(self)
        token.cancel()
        return result

    monkeypatch.setattr(Path, "is_file", is_file)

    with pytest.raises(Cancelled):
        discover_sequences(tmp_path, token)

    assert probed == [tmp_path / "SEQ-01" / "seqinfo.ini"]


# ---------------------------------------------------------------------------
# Playlist building
# ---------------------------------------------------------------------------


def test_sequence_becomes_image_video_with_detection_overlay_at_frame_size(tmp_path: Path) -> None:
    """MOT boxes are in pixels, so the overlay decoder needs the sequence's own frame size."""
    _make_sequence(tmp_path, "SEQ-01", width=640, height=480)

    [playlist] = build_playlist_contents(discover_sequences(tmp_path, NEVER), NEVER)

    [entry] = playlist.entries
    [lane] = entry.lanes
    assert lane.video.display_name == "SEQ-01"
    assert isinstance(lane.video, SeekableVideoContent)
    assert lane.video.source_spec.path == tmp_path / "SEQ-01" / "img1" / "%06d.jpg"
    assert lane.overlay is not None
    assert lane.overlay.display_name == "DET"
    spec = lane.overlay.source_spec
    assert isinstance(spec, FileOverlaySourceSpec)
    assert (spec.path, spec.handler_type) == (tmp_path / "SEQ-01" / "det" / "det.txt", "MOT_FILE")
    assert spec.decoder_kwargs == {"width": 640, "height": 480}


def test_det_and_gt_overlays_attached(tmp_path: Path) -> None:
    _make_sequence(tmp_path, "SEQ-01", create_det=True, create_gt=True)

    [playlist] = build_playlist_contents(discover_sequences(tmp_path, NEVER), NEVER)

    assert [lane.display_name for lane in playlist.entries[0].lanes] == ["DET", "GT"]


def test_sequence_without_detections_has_video_only(tmp_path: Path) -> None:
    _make_sequence(tmp_path, "SEQ-01", create_det=False)

    [playlist] = build_playlist_contents(discover_sequences(tmp_path, NEVER), NEVER)

    [lane] = playlist.entries[0].lanes
    assert lane.overlay is None


def test_detector_variants_share_one_entry_with_a_single_gt(tmp_path: Path) -> None:
    """MOT17 ships one video per sequence under three detector folders; other sequences stay separate."""
    for detector in ("DPM", "FRCNN", "SDP"):
        _make_sequence(tmp_path, f"MOT17-01-{detector}", create_gt=True)
    _make_sequence(tmp_path, "MOT17-02-DPM")

    [playlist] = build_playlist_contents(discover_sequences(tmp_path, NEVER), NEVER)

    assert len(playlist.entries) == 2
    lanes = playlist.entries[0].lanes
    assert {lane.video.display_name for lane in lanes} == {"MOT17-01"}
    assert [lane.display_name for lane in lanes] == ["DPM", "GT", "FRCNN", "SDP"]
