"""Fixtures that open offline viewer entries at once over stubbed sources."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PySide6.QtWidgets import (
    QApplication,
)

from ax_devil.modules.video_viewer import (
    offline_entry_media,
    offline_video_viewer,
    offline_viewer_runtime,
)
from ax_devil.modules.video_viewer.offline_entry_media import EntryMedia
from ax_devil.modules.workspace.core import (
    SeekableVideoContent,
)
from tests.helpers.offline_sources import (
    TEST_FRAME_SOURCE_OPENERS,
    OverlayDecoderRequest,
    SpecFileOverlaySource,
    TrackedOverlaySource,
    stub_frame_source,
)
from tests.helpers.offline_viewer import (
    DeferredBackground,
)


@pytest.fixture(autouse=True)
def _background(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> DeferredBackground:
    """Open entries as soon as they are requested, and run releases immediately."""
    background = DeferredBackground()

    def release_now(media: EntryMedia) -> None:
        media.close()
        assert all(source.wait(0) for source in media.video_sources)
        media.delete_later()

    monkeypatch.setattr(offline_entry_media, "background_jobs", lambda *_args, **_kwargs: background)
    monkeypatch.setattr(offline_entry_media, "release_media", release_now)
    monkeypatch.setattr(offline_viewer_runtime, "release_media", release_now)
    monkeypatch.setattr(offline_video_viewer, "release_media", release_now)
    return background


@pytest.fixture
def deferred_background(_background: DeferredBackground) -> DeferredBackground:
    """Make entry opening wait for the test to finish it."""
    _background.immediate = False
    return _background


@pytest.fixture(autouse=True)
def _stub_spec_source_opening(monkeypatch: pytest.MonkeyPatch) -> None:
    def _create_frame_source(video: SeekableVideoContent) -> object:
        factory = TEST_FRAME_SOURCE_OPENERS.get(video.source_spec.path)
        if factory is not None:
            return factory()
        return stub_frame_source()

    def _get_file_decoder_factory(handler_type: str) -> Callable[..., OverlayDecoderRequest]:
        assert handler_type == "TEST_FILE"

        def _decoder_factory(
            *,
            name: str,
            registry: list[TrackedOverlaySource] | None = None,
        ) -> OverlayDecoderRequest:
            return OverlayDecoderRequest(name, registry)

        return _decoder_factory

    monkeypatch.setattr(offline_entry_media, "create_frame_source", _create_frame_source)
    monkeypatch.setattr(offline_entry_media, "FileOverlaySource", SpecFileOverlaySource)
    monkeypatch.setattr(offline_entry_media, "get_file_decoder_factory", _get_file_decoder_factory)
    yield
    TEST_FRAME_SOURCE_OPENERS.clear()
