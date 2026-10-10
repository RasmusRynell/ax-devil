"""Tests for the MOT Challenge playlist resolver plugin."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QListWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace import FileOverlaySourceSpec, SeekableVideoContent
from ax_devil.plugins.playlist_resolvers.mot_challenge.plugin import MOTChallengeResolverPlugin
from ax_devil.plugins.playlist_resolvers.mot_challenge.resolver import build_playlist_contents, discover_sequences
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
    assert sequences.count() == 2
    assert load.isEnabled()
    first = sequences.item(0)
    assert first is not None
    first.setCheckState(Qt.CheckState.Unchecked)
    with qtbot.waitSignal(widget.playlist_resolved) as resolved:
        load.click()

    [playlist] = resolved.args[0]
    assert [entry.lanes[0].video.display_name for entry in playlist.entries] == ["SEQ-02"]


# ---------------------------------------------------------------------------
# Sequence discovery
# ---------------------------------------------------------------------------


def test_discovers_sorted_sequences_with_metadata_and_skips_other_folders(tmp_path: Path) -> None:
    """Discovery accepts an empty root and yields only readable sequence folders in name order."""
    assert discover_sequences(tmp_path) == []
    (tmp_path / "random_folder").mkdir()
    broken = tmp_path / "MOT16-02"
    broken.mkdir()
    (broken / "seqinfo.ini").write_text("[Other]\nkey=val\n")
    _make_sequence(tmp_path, "MOT16-06", fps=14, width=640, height=480, length=1194)
    _make_sequence(tmp_path, "MOT16-01", length=450, fps=30)

    seqs = discover_sequences(tmp_path)

    assert [seq.name for seq in seqs] == ["MOT16-01", "MOT16-06"]
    assert seqs[0].frame_count == 450
    seq = seqs[1]
    assert (seq.fps, seq.width, seq.height, seq.frame_count) == (14.0, 640, 480, 1194)
    assert (seq.im_dir, seq.im_ext) == ("img1", ".jpg")


def test_discovers_train_and_test_split_sequences_from_dataset_root(tmp_path: Path) -> None:
    _make_sequence(tmp_path / "train", "MOT17-02-DPM", create_gt=True)
    _make_sequence(tmp_path / "test", "MOT17-01-DPM", create_gt=False)

    assert [seq.name for seq in discover_sequences(tmp_path)] == ["MOT17-01-DPM", "MOT17-02-DPM"]


def test_discovery_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover_sequences(tmp_path / "nonexistent")


# ---------------------------------------------------------------------------
# Playlist building
# ---------------------------------------------------------------------------


def test_sequence_becomes_image_video_with_detection_overlay_at_frame_size(tmp_path: Path) -> None:
    """MOT boxes are in pixels, so the overlay decoder needs the sequence's own frame size."""
    _make_sequence(tmp_path, "SEQ-01", width=640, height=480)

    [playlist] = build_playlist_contents(discover_sequences(tmp_path))

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

    [playlist] = build_playlist_contents(discover_sequences(tmp_path))

    assert [lane.display_name for lane in playlist.entries[0].lanes] == ["DET", "GT"]


def test_sequence_without_detections_has_video_only(tmp_path: Path) -> None:
    _make_sequence(tmp_path, "SEQ-01", create_det=False)

    [playlist] = build_playlist_contents(discover_sequences(tmp_path))

    [lane] = playlist.entries[0].lanes
    assert lane.overlay is None


def test_detector_variants_share_one_entry_with_a_single_gt(tmp_path: Path) -> None:
    """MOT17 ships one video per sequence under three detector folders; other sequences stay separate."""
    for detector in ("DPM", "FRCNN", "SDP"):
        _make_sequence(tmp_path, f"MOT17-01-{detector}", create_gt=True)
    _make_sequence(tmp_path, "MOT17-02-DPM")

    [playlist] = build_playlist_contents(discover_sequences(tmp_path))

    assert len(playlist.entries) == 2
    lanes = playlist.entries[0].lanes
    assert {lane.video.display_name for lane in lanes} == {"MOT17-01"}
    assert [lane.display_name for lane in lanes] == ["DPM", "GT", "FRCNN", "SDP"]
