"""Hover card pictures: one read per source, failures remembered, open streams never read."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import ClassVar

import pytest
from PySide6.QtGui import QColor, QImage
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.thumbnails import OpenFrames, Thumbnails


def _picture() -> QImage:
    image = QImage(8, 6, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    return image


@dataclass(frozen=True)
class StubSource:
    """Reads a picture when released, or fails; open_frame answers for an open stream."""

    name: str
    fails: bool = False
    opened: QImage | None = field(default=None, compare=False)
    release: threading.Event = field(default_factory=threading.Event, compare=False)
    reads: list[str] = field(default_factory=list, compare=False)
    fresh_s: ClassVar[float] = 60.0

    def open_frame(self, open_frames: OpenFrames) -> QImage | None:
        return self.opened

    def read(self, token: CancelToken) -> QImage:
        self.reads.append(self.name)
        assert self.release.wait(5)
        if self.fails:
            raise OSError("no picture")
        return _picture()


@pytest.fixture
def thumbnails(qtbot: QtBot) -> Iterator[Thumbnails]:
    jobs = BackgroundJobs(max_workers=1)
    service = Thumbnails(lambda _id: None, jobs)
    yield service
    service.cleanup()


def test_a_slow_read_is_not_restarted_by_asking_again(qtbot: QtBot, thumbnails: Thumbnails) -> None:
    source = StubSource("slow")
    delivered: list[QImage | None] = []

    thumbnails.request(source, lambda image: None)
    thumbnails.request(source, delivered.append)
    source.release.set()

    qtbot.waitUntil(lambda: len(delivered) == 1, timeout=5000)
    assert delivered[0] is not None and not delivered[0].isNull()
    assert source.reads == ["slow"]


def test_a_failure_is_remembered_instead_of_asking_the_camera_again(qtbot: QtBot, thumbnails: Thumbnails) -> None:
    source = StubSource("down", fails=True)
    source.release.set()
    delivered: list[QImage | None] = []

    thumbnails.request(source, delivered.append)
    qtbot.waitUntil(lambda: delivered == [None], timeout=5000)
    thumbnails.request(source, delivered.append)

    assert delivered == [None, None]
    assert source.reads == ["down"]


def test_an_open_stream_shows_its_frame_without_a_read(thumbnails: Thumbnails) -> None:
    source = StubSource("open", opened=_picture())
    delivered: list[QImage | None] = []

    thumbnails.request(source, delivered.append)

    assert len(delivered) == 1 and delivered[0] is not None and not delivered[0].isNull()
    assert source.reads == []


def test_a_stream_still_connecting_shows_nothing_and_is_not_read(thumbnails: Thumbnails) -> None:
    source = StubSource("connecting", opened=QImage())
    delivered: list[QImage | None] = []

    thumbnails.request(source, delivered.append)

    assert delivered == []
    assert source.reads == []
