"""The first-frame disk cache decodes a file once and notices when it changes."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import av
import pytest
from PySide6.QtGui import QColor, QImage

from ax_devil.core.cancellation import NEVER
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import ImageSequenceConfig
from ax_devil.modules.thumbnails.first_frame import cached_first_frame, keep_newest


@pytest.fixture
def opens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every file PyAV is asked to decode."""
    opened: list[str] = []
    real_open = av.open

    def counting_open(file: str, *args: Any, **kwargs: Any) -> Any:
        opened.append(file)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(av, "open", counting_open)
    return opened


def test_first_frame_is_decoded_once_until_the_file_changes(
    isolated_cache: Path, video_file_factory: Callable[[float, int], Path], tmp_path: Path, opens: list[str]
) -> None:
    video = tmp_path / "clip.mp4"
    shutil.copyfile(video_file_factory(1.0, 30), video)

    first = cached_first_frame(video, None, NEVER)
    second = cached_first_frame(video, None, NEVER)

    assert not first.isNull()
    assert second.size() == first.size()
    assert len(opens) == 1

    shutil.copyfile(video_file_factory(2.0, 25), video)
    assert not cached_first_frame(video, None, NEVER).isNull()
    assert len(opens) == 2


def test_an_image_sequence_is_decoded_again_when_its_first_image_is_overwritten(
    isolated_cache: Path, tmp_path: Path, opens: list[str]
) -> None:
    folder = tmp_path / "img1"
    folder.mkdir()
    pattern = folder / "%06d.png"
    sequence = ImageSequenceConfig(fps=10, width=8, height=6, total_frames=1)
    first = QImage(8, 6, QImage.Format.Format_RGB32)
    first.fill(QColor("red"))
    first.save(str(folder / "000001.png"))

    cached_first_frame(pattern, sequence, NEVER)
    cached_first_frame(pattern, sequence, NEVER)
    assert len(opens) == 1

    first.fill(QColor("blue"))
    first.save(str(folder / "000001.png"))
    cached_first_frame(pattern, sequence, NEVER)

    assert len(opens) == 2
    with pytest.raises(FileNotFoundError):
        cached_first_frame(folder / "%06d.jpg", sequence, NEVER)


def test_keep_newest_deletes_the_oldest_files(tmp_path: Path) -> None:
    for age in range(5):
        file = tmp_path / f"{age}.jpg"
        file.write_bytes(b"x")
        os.utime(file, (1_000_000 - age, 1_000_000 - age))

    keep_newest(tmp_path, 2)

    assert sorted(file.name for file in tmp_path.iterdir()) == ["0.jpg", "1.jpg"]
