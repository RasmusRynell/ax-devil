"""The first-frame disk cache decodes a file once and notices when it changes."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import av
import pytest

from ax_devil.core.cancellation import NEVER
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


def test_keep_newest_deletes_the_oldest_files(tmp_path: Path) -> None:
    for age in range(5):
        file = tmp_path / f"{age}.jpg"
        file.write_bytes(b"x")
        os.utime(file, (1_000_000 - age, 1_000_000 - age))

    keep_newest(tmp_path, 2)

    assert sorted(file.name for file in tmp_path.iterdir()) == ["0.jpg", "1.jpg"]
