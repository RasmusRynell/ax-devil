"""Exercise deferred source disposal using real Qt workers and ownership."""

import gc
import weakref
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from pytestqt.qtbot import QtBot

from ax_devil.modules.data_sources.base import DataSource, Worker


class _Worker(Worker):
    def __init__(self, release: Event) -> None:
        super().__init__()
        self.release = release
        self.entered = Event()

    def run_loop(self) -> bool:
        """Hold the worker in production until the test releases it."""
        self.entered.set()
        self.release.wait(timeout=5)
        return False


class _Source(DataSource):
    def __init__(self, release: Event) -> None:
        super().__init__()
        self.worker = _Worker(release)
        self._own_worker(self.worker)

    def play(self) -> bool:
        """Start production."""
        return self.worker.play()

    def pause(self) -> None:
        """Pause production."""
        self.worker.pause()

    def stop(self) -> None:
        """Request shutdown without waiting for the blocked operation."""
        # Model a bounded stop returning before the worker exits.
        self.worker.stop()

    def wait(self, timeout: int = 2000) -> bool:
        """Wait for production to finish."""
        return self.worker.wait(timeout)


@pytest.mark.parametrize("running", [False, True])
def test_source_disposal_releases_source_and_worker(qtbot: QtBot, running: bool) -> None:
    """Deletion survives late completion and retains neither Python nor Qt objects."""
    release = Event()
    source = _Source(release)
    destroyed: list[str] = []
    source.destroyed.connect(lambda: destroyed.append("source"))
    source.worker.destroyed.connect(lambda: destroyed.append("worker"))
    source_ref = weakref.ref(source)
    worker_ref = weakref.ref(source.worker)
    try:
        if running:
            assert source.play()
            qtbot.waitUntil(source.worker.entered.is_set)
        source.stop()
        source.deleteLater()
        source.deleteLater()
        if running:
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert not destroyed
        del source
        gc.collect()
        release.set()
        qtbot.waitUntil(lambda: len(destroyed) == 2)
        gc.collect()
        assert source_ref() is None
        assert worker_ref() is None
    finally:
        release.set()
        worker = worker_ref()
        if worker is not None:
            worker.wait(6000)
