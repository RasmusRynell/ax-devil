"""Export timing through the real file source and its lazy frame cache."""

from pathlib import Path

import av
import pytest
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from ax_devil.modules.cache.cache_manager import CacheManager
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.video_viewer.export.encoder import VideoEncoder
from ax_devil.modules.video_viewer.export.export_job import ExportJob, ExportLane
from ax_devil.modules.video_viewer.scene_frame_presenter import SceneFramePresenter


def test_export_preserves_source_duration_after_cached_reads(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final source duration survives pixel conversion and differs from the preceding gap."""
    monkeypatch.setattr(CacheManager, "_get_base_cache_dir", lambda self: tmp_path / "cache")
    source_path = tmp_path / "source.mp4"
    image = QImage(16, 16, QImage.Format.Format_RGB888)
    image.fill(0)
    encoder = VideoEncoder(source_path, width=16, height=16, fps=25)
    for timestamp, duration in [(0, 0.04), (40_000, 0.2), (240_000, 0.08)]:
        encoder.write_frame(image, timestamp_us=timestamp, duration_s=duration)
    encoder.finish()

    source = FileFrameSource(str(source_path))
    try:
        for _ in range(2):
            frame = source.read_decoded_frame(2)
            assert frame is not None
            assert frame.duration_s == pytest.approx(0.08)
            assert frame.period_after_s == pytest.approx(0.2)

        output = tmp_path / "export.mp4"
        job = ExportJob([ExportLane(name="lane", video_source=source, presenter=SceneFramePresenter())], output)
        assert job.run()
        # Export borrows the runtime source; its owner can keep reading it after completion.
        assert source.read_decoded_frame(0) is not None
        with av.open(str(output)) as container:
            frames = list(container.decode(video=0))
            assert [round(frame.time * 1_000_000) for frame in frames] == [0, 40_000, 240_000]
            last_frame = frames[-1]
            assert last_frame.time_base is not None
            assert float(last_frame.duration * last_frame.time_base) == pytest.approx(0.08)
    finally:
        source.stop()
        assert source.wait()
