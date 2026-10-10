"""Tests for the export workflow and its options/progress dialog."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import av
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QProgressBar
from pytestqt.qtbot import QtBot

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.engine.quick.image_renderer import FrameImageRenderer
from ax_devil.modules.video_viewer.export.export_dialog import ExportDialog
from ax_devil.modules.video_viewer.export.export_job import ExportJob, ExportLane
from ax_devil.modules.video_viewer.scene_frame_presenter import SceneFramePresenter
from tests.helpers.widgets import button


class _VideoSource:
    """Seekable source test double recording export reads."""

    total_frames = 3
    fps = 25.0
    file_path = "/unused/source.mp4"
    timestamps = (0.0, 40_000.0, 80_000.0)
    size = 2
    colors = (0, 0, 0)

    def __init__(self) -> None:
        self.requested_frames: list[int] = []

    def read_decoded_frame(self, frame_number: int) -> DecodedFrame:
        """Return one deterministic decoded frame."""
        self.requested_frames.append(frame_number)
        return DecodedFrame(
            frame_index=frame_number,
            pixels=np.full((self.size, self.size, 3), self.colors[frame_number], dtype=np.uint8),
            timestamp_us=self.timestamps[frame_number],
        )


def _lane(source: _VideoSource, name: str = "lane") -> ExportLane:
    return ExportLane(name=name, video_source=cast(FileFrameSource, source), presenter=SceneFramePresenter())


def _export(dialog: ExportDialog, output: Path | None) -> None:
    """Press Export and choose *output* in the save dialog, or dismiss it when None."""
    with (
        patch("PySide6.QtWidgets.QFileDialog.exec", return_value=output is not None),
        patch("PySide6.QtWidgets.QFileDialog.selectedFiles", return_value=[str(output)]),
    ):
        button(dialog, "Export").click()


def _progress_bar(dialog: ExportDialog) -> QProgressBar:
    progress = dialog.findChild(QProgressBar)
    assert progress is not None
    return progress


def _status(dialog: ExportDialog) -> str:
    """Return the status line shown under the progress bar."""
    parent = _progress_bar(dialog).parentWidget()
    label = parent.findChild(QLabel) if parent is not None else None
    assert label is not None
    return label.text()


def _shows_options(dialog: ExportDialog) -> bool:
    quality = dialog.findChild(QComboBox)
    assert quality is not None
    return quality.isVisibleTo(dialog)


@pytest.fixture
def export_setup(tmp_path: Path, qtbot: QtBot) -> tuple[_VideoSource, ExportDialog, Path]:
    """Provide a real export dialog and encoder with a deterministic decoded source."""
    source = _VideoSource()
    source.file_path = str(tmp_path / "source.mp4")
    Path(source.file_path).write_bytes(b"source")
    output = tmp_path / "output.mp4"
    output.write_bytes(b"previous export")
    dialog = ExportDialog([_lane(source)])
    qtbot.addWidget(dialog)
    return source, dialog, output


@pytest.fixture
def job_setup(tmp_path: Path, qtbot: QtBot) -> tuple[_VideoSource, ExportJob, Path]:
    """Provide a job with real Quick rendering and encoding on the Qt GUI thread."""
    source = _VideoSource()
    source.file_path = str(tmp_path / "source.mp4")
    Path(source.file_path).write_bytes(b"source")
    output = tmp_path / "output.mp4"
    output.write_bytes(b"previous export")
    return source, ExportJob([_lane(source)], output), output


@pytest.mark.parametrize("failure_frame", [0, 1])
def test_failed_frame_preserves_destination_and_reports_error(
    tmp_path: Path,
    export_setup: tuple[_VideoSource, ExportDialog, Path],
    failure_frame: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed read is visible in the UI/log and never destroys an existing destination."""
    source, dialog, output = export_setup
    original_read = source.read_decoded_frame
    with patch.object(
        source, "read_decoded_frame", side_effect=lambda index: None if index == failure_frame else original_read(index)
    ):
        _export(dialog, output)
    assert output.read_bytes() == b"previous export"
    assert "Error:" in _status(dialog)
    assert "Export failed" in caplog.text
    assert not list(tmp_path.glob(".output-*.mp4"))


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_export_rejects_source_aliases(tmp_path: Path, qtbot: QtBot, alias: str) -> None:
    """The source recording survives direct, symbolic, and hard-link output aliases."""
    source = _VideoSource()
    original = tmp_path / "source.mp4"
    original.write_bytes(b"source")
    source.file_path = str(original)
    output = original if alias == "same" else tmp_path / "output.mp4"
    if alias == "symlink":
        output.symlink_to(original)
    elif alias == "hardlink":
        output.hardlink_to(original)
    job = ExportJob([_lane(source)], output)
    with pytest.raises(ValueError, match="must differ"):
        job.run()
    assert original.read_bytes() == b"source"
    assert source.requested_frames == []


@pytest.mark.parametrize(("accepted", "typed", "expected"), [(False, "", None), (True, "clip.v2", "clip.v2.mp4")])
def test_export_button_asks_for_mp4_destination(
    tmp_path: Path,
    export_setup: tuple[_VideoSource, ExportDialog, Path],
    accepted: bool,
    typed: str,
    expected: str | None,
) -> None:
    """The destination is chosen after pressing Export and always ends in .mp4."""
    _source, dialog, _output = export_setup
    _export(dialog, tmp_path / typed if accepted else None)
    if expected is None:
        assert _shows_options(dialog)
        assert sorted(path.name for path in tmp_path.iterdir()) == ["output.mp4", "source.mp4"]
    else:
        assert _status(dialog) == f"Done: {tmp_path / expected}"
        assert (tmp_path / expected).stat().st_size > 0


def test_export_dialog_switches_from_options_to_progress(
    export_setup: tuple[_VideoSource, ExportDialog, Path],
) -> None:
    """Progress controls replace setup options only after export starts."""
    _source, dialog, output = export_setup
    assert _shows_options(dialog)
    assert not _progress_bar(dialog).isVisibleTo(dialog)

    def run_export(_job: ExportJob, progress: object = None) -> bool:
        assert not _shows_options(dialog)
        assert _progress_bar(dialog).isVisibleTo(dialog)
        return True

    with patch.object(ExportJob, "run", autospec=True, side_effect=run_export):
        _export(dialog, output)

    assert not _shows_options(dialog)
    assert _progress_bar(dialog).isVisibleTo(dialog)
    assert _status(dialog).startswith("Done:")


def test_export_dialog_size_grows_for_more_and_longer_lanes(qtbot: QtBot) -> None:
    """The dialog allocates more room for lane rows and long source names."""
    source = _VideoSource()
    short_dialog = ExportDialog([_lane(source, "lane")])
    # Exceed the minimum dialog height even with small platform fonts.
    many_lanes_dialog = ExportDialog([_lane(source, f"lane {index}") for index in range(24)])
    long_name = "/".join(["simulated"] * 24)
    long_name_dialog = ExportDialog([_lane(source, long_name), _lane(source, "second lane")])
    qtbot.addWidget(short_dialog)
    qtbot.addWidget(many_lanes_dialog)
    qtbot.addWidget(long_name_dialog)

    assert many_lanes_dialog.sizeHint().height() > short_dialog.sizeHint().height()
    assert long_name_dialog.sizeHint().width() > short_dialog.sizeHint().width()


@pytest.mark.parametrize("cancel_at", [-1, 0, 2])
def test_cancel_preserves_destination(
    tmp_path: Path, job_setup: tuple[_VideoSource, ExportJob, Path], cancel_at: int
) -> None:
    """Cancellation before reading or after encoding removes only the temporary export."""
    source, job, output = job_setup
    if cancel_at == -1:
        job.cancel()
    assert not job.run(lambda index, _total: job.cancel() if index == cancel_at else None)
    assert source.requested_frames == ([] if cancel_at == -1 else list(range(cancel_at + 1)))
    assert output.read_bytes() == b"previous export"
    assert not list(tmp_path.glob(".output-*.mp4"))


def test_successful_export_replaces_destination_with_source_timing(
    tmp_path: Path, job_setup: tuple[_VideoSource, ExportJob, Path]
) -> None:
    """The job passes decoded timing to the real encoder and replaces only on success."""
    source, job, output = job_setup
    source.timestamps = (0, 40_000, 240_000)
    progress: list[tuple[int, int]] = []
    assert job.run(lambda current, total: progress.append((current, total)))
    assert source.requested_frames == [0, 1, 2]
    assert progress[-1] == (2, 3)
    with av.open(str(output)) as container:
        timestamps = [round(frame.time * 1_000_000) for frame in container.decode(video=0)]
    assert timestamps == [0, 40_000, 240_000]
    assert not list(tmp_path.glob(".output-*.mp4"))


def test_image_sequence_can_replace_existing_destination(job_setup: tuple[_VideoSource, ExportJob, Path]) -> None:
    """Image-sequence patterns are valid source identities even though no such file exists."""
    source, job, output = job_setup
    source.file_path = str(output.parent / "%06d.jpg")
    assert job.run()
    with av.open(str(output)) as container:
        assert len(list(container.decode(video=0))) == source.total_frames


@pytest.mark.parametrize("action", ["escape", "close", "cancel"])
def test_closing_during_export_cancels_before_replacing_destination(
    tmp_path: Path, export_setup: tuple[_VideoSource, ExportDialog, Path], action: str
) -> None:
    """Window dismissal must not hide an export that continues overwriting in the background."""
    source, dialog, output = export_setup
    dialog.show()
    original_read = source.read_decoded_frame

    def dismiss_then_read(index: int) -> DecodedFrame:
        if action == "escape":
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        elif action == "close":
            dialog.close()
        else:
            QTest.mouseClick(button(dialog, "Cancel"), Qt.MouseButton.LeftButton)
        assert dialog.isVisible()
        return original_read(index)

    with patch.object(source, "read_decoded_frame", side_effect=dismiss_then_read):
        _export(dialog, output)
    assert output.read_bytes() == b"previous export"
    assert _status(dialog) == "Cancelled"
    assert not list(tmp_path.glob(".output-*.mp4"))
    dialog.reject()
    assert not dialog.isVisible()


@pytest.mark.parametrize("stage", ["encode", "finish", "replace"])
def test_export_write_failure_preserves_destination(
    tmp_path: Path,
    job_setup: tuple[_VideoSource, ExportJob, Path],
    stage: str,
) -> None:
    """Failures writing, flushing, or committing output leave the destination intact."""
    _source, job, output = job_setup
    targets = {
        "encode": "ax_devil.modules.video_viewer.export.encoder.VideoEncoder.write_frame",
        "finish": "ax_devil.modules.video_viewer.export.encoder.VideoEncoder.finish",
        "replace": "pathlib.Path.replace",
    }
    with patch(targets[stage], side_effect=OSError("output unavailable")):
        with pytest.raises(OSError, match="output unavailable"):
            job.run()
    assert output.read_bytes() == b"previous export"
    assert not list(tmp_path.glob(".output-*.mp4"))


def test_export_selects_retained_sample_without_playback_history(
    job_setup: tuple[_VideoSource, ExportJob, Path],
) -> None:
    """A direct export frame uses source retention and the original sample's expiry."""
    from ax_devil.core.data_types import FrameIdentifier, OverlayData
    from ax_devil.modules.scene.model import Scene, TimeSlice
    from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy, OverlayPersistenceSettings

    class SparseSource:
        """Provide a retained sample only when the caller requests past lookup."""

        def get_overlay_at_frame(
            self, frame_id: FrameIdentifier, *, allow_previous: bool = False
        ) -> OverlayData | None:
            """Return the original sample with retention metadata."""
            assert allow_previous
            return OverlayData(
                content=Scene(time_slice=TimeSlice(0, 0)),
                frame_id=FrameIdentifier(0, 0),
                metadata={"timestamp_match_type": "retained"},
            )

    source, _job, output = job_setup
    lane = ExportLane(
        name="lane",
        video_source=cast(FileFrameSource, source),
        presenter=SceneFramePresenter(),
        overlay_source=SparseSource(),
        overlay_policy=OverlayPersistencePolicy(OverlayPersistenceSettings(enabled=True, timeout_ms=50, opacity=0.4)),
    )
    frames: list[VideoFrameWithOverlays] = []
    original_render = FrameImageRenderer.render_frame

    def render(renderer: FrameImageRenderer, frame: VideoFrameWithOverlays) -> QImage:
        frames.append(frame)
        image: QImage = original_render(renderer, frame)
        return image

    with patch.object(FrameImageRenderer, "render_frame", autospec=True, side_effect=render):
        assert ExportJob([lane], output).run()
    displayed = frames[1]
    assert displayed.overlays is not None
    assert displayed.overlays.timestamp_monotime_us == 0
    assert displayed.overlays.metadata is not None
    assert displayed.overlays.metadata["overlay_reused"] is True
    assert displayed.overlays.metadata["overlay_opacity"] == 0.4
    expired = frames[2]
    assert expired.overlays is None


def test_selected_lanes_are_tiled_and_follow_the_first_lane(tmp_path: Path, qtbot: QtBot) -> None:
    """Selected lanes share one output; pooled sources decode once and a shorter lane holds its last frame."""
    first, shorter, unselected = _VideoSource(), _VideoSource(), _VideoSource()
    for source in (first, shorter, unselected):
        source.size = 32
    shorter.total_frames = 2
    shorter.colors = (100, 200, 0)
    output = tmp_path / "output.mp4"
    lanes = [_lane(first, "a"), _lane(first, "a2"), _lane(shorter, "b"), _lane(unselected, "c")]
    dialog = ExportDialog(lanes)
    qtbot.addWidget(dialog)
    next(check for check in dialog.findChildren(QCheckBox) if check.text() == "c").click()
    _export(dialog, output)
    assert _status(dialog).startswith("Done:")
    assert first.requested_frames == [0, 1, 2]
    assert shorter.requested_frames == [0, 1]
    assert unselected.requested_frames == []
    with av.open(str(output)) as container:
        frames = list(container.decode(video=0))
    # Three lanes use the viewer's 2x2 grid.
    assert (frames[0].width, frames[0].height) == (64, 64)
    assert [round(frame.time * 1_000_000) for frame in frames] == [0, 40_000, 80_000]
    # A pixel below the label in the shorter lane changes once, then holds at EOF.
    colors = [int(frame.to_ndarray(format="rgb24")[60, 28, 0]) for frame in frames]
    assert colors == pytest.approx([100, 200, 200], abs=2)
