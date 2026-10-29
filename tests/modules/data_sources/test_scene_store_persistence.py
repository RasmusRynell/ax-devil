"""Tests that the scene file provider's persisted index and decoded artifacts stay valid for the file they describe."""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.scene.model import EntityId
from tests.helpers.jsonl import build_provider as _build_provider
from tests.helpers.jsonl import frame_at as _frame
from tests.helpers.jsonl import jsonl_record as _record
from tests.helpers.jsonl import write_jsonl

pytestmark = pytest.mark.usefixtures("isolated_cache")


@pytest.mark.parametrize("storage_mode", list(StorageMode))
def test_persisted_scenes_are_reused_only_for_an_unchanged_source_and_identity(
    tmp_path: Path, storage_mode: StorageMode
) -> None:
    """Reopening reuses the persisted artifact; changed options, version, or source contents rebuild it."""
    path = write_jsonl(tmp_path / "scenes.jsonl", [_record(0, "a"), _record(1, "b")])
    options = {"profile": "initial"}

    def open_provider(decoded: list[str], **kwargs: Any) -> SceneDecoderFileProvider:
        provider = _build_provider(path, storage_mode=storage_mode, decoded=decoded, **kwargs)
        provider.close()
        return provider

    cold: list[str] = []
    cold_frames = open_provider(cold, decode_options=options).get_available_frames()
    assert len(cold) == 2

    warm: list[str] = []
    warm_provider = _build_provider(path, storage_mode=storage_mode, decoded=warm, decode_options=options)
    try:
        assert warm == []
        assert warm_provider.get_available_frames() == cold_frames
        assert all(warm_provider.load_by_frame_id(_frame(timestamp)) is not None for timestamp in cold_frames)
    finally:
        warm_provider.close()

    changed_options: list[str] = []
    open_provider(changed_options, decode_options={"profile": "changed"})
    assert len(changed_options) == 2

    new_version: list[str] = []
    open_provider(new_version, decode_options={"profile": "changed"}, artifact_version=2)
    assert len(new_version) == 2

    with path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(f"{json.dumps(_record(2))}\n")
    changed_source: list[str] = []
    rebuilt = open_provider(changed_source, decode_options={"profile": "changed"}, artifact_version=2)
    assert len(changed_source) == 3
    assert rebuilt.get_total_frames() == 3

    # A same-size, same-mtime replacement with other contents (cp -p, rsync) is a different file.
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text(path.read_text(encoding="utf-8").replace('"a"', '"z"'), encoding="utf-8")
    shutil.copystat(path, replacement)
    os.replace(replacement, path)
    replaced: list[str] = []
    open_provider(replaced, decode_options={"profile": "changed"}, artifact_version=2)
    assert len(replaced) == 3


@pytest.mark.parametrize("storage_mode", list(StorageMode))
def test_source_identity_is_its_content_not_its_timestamps(tmp_path: Path, storage_mode: StorageMode) -> None:
    """A touch reuses the artifact; a same-size edit that restores mtime is still noticed."""
    path = write_jsonl(tmp_path / "scenes.jsonl", [_record(0, "a"), _record(1, "b")])
    _build_provider(path, storage_mode=storage_mode).close()
    original_mtime_ns = path.stat().st_mtime_ns

    os.utime(path, ns=(original_mtime_ns + 5_000_000_000,) * 2)
    touched: list[str] = []
    _build_provider(path, storage_mode=storage_mode, decoded=touched).close()
    assert touched == []
    recorded_mtime_ns = path.stat().st_mtime_ns  # the mtime the republished identity now holds

    path.write_bytes(path.read_bytes().replace(b'"id": "b"', b'"id": "c"'))
    os.utime(path, ns=(recorded_mtime_ns,) * 2)
    edited: list[str] = []
    provider = _build_provider(path, storage_mode=storage_mode, decoded=edited)
    try:
        assert len(edited) == 2
        last = max(provider.get_available_frames())
        scene = provider.load_by_frame_id(_frame(last))
        assert scene is not None and EntityId("c") in scene.entities
    finally:
        provider.close()


class _RegeneratesSourceWhenFirstDecoded(list[str]):
    """Decoded-payload log that rewrites the source, same size, as the first payload is decoded."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def append(self, payload: str) -> None:
        if not self:
            self._path.write_text(self._path.read_text(encoding="utf-8").replace('"a"', '"z"'), encoding="utf-8")
            stat_result = self._path.stat()
            os.utime(self._path, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000_000))
        super().append(payload)


@pytest.mark.parametrize("storage_mode", list(StorageMode))
def test_source_changed_while_indexing_is_rebuilt_on_next_open(tmp_path: Path, storage_mode: StorageMode) -> None:
    """A regeneration that lands during indexing must not be cached as if the index had already seen it."""
    path = write_jsonl(tmp_path / "scenes.jsonl", [_record(0, "a"), _record(1, "b")])
    _build_provider(path, storage_mode=storage_mode, decoded=_RegeneratesSourceWhenFirstDecoded(path)).close()

    reopened_decodes: list[str] = []
    _build_provider(path, storage_mode=storage_mode, decoded=reopened_decodes).close()
    assert len(reopened_decodes) == 2


@pytest.mark.parametrize("storage_mode", list(StorageMode))
@pytest.mark.parametrize("in_place", [True, False], ids=["rewritten-in-place", "replaced-by-rename"])
def test_open_provider_stops_serving_a_source_rewritten_in_place(
    tmp_path: Path, storage_mode: StorageMode, in_place: bool
) -> None:
    """An in-place rewrite must not show another file's overlays; a renamed-over file leaves the open one valid."""
    if sys.platform == "win32" and storage_mode is StorageMode.SOURCE_INDEX and not in_place:
        pytest.skip("Windows cannot rename over a file that is open, even one opened to share deletion")
    path = write_jsonl(tmp_path / "scenes.jsonl", [_record(0, "a"), _record(1, "b")])
    provider = _build_provider(path, storage_mode=storage_mode)
    try:
        first, _ = sorted(provider.get_available_frames())
        regenerated = path.read_text(encoding="utf-8").replace('"a"', '"z"')
        if in_place:
            before = path.stat()
            path.write_text(regenerated, encoding="utf-8")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        else:
            replacement = tmp_path / "replacement.jsonl"
            replacement.write_text(regenerated, encoding="utf-8")
            os.replace(replacement, path)

        scene = provider.load_by_frame_id(_frame(first))
        if in_place and storage_mode is StorageMode.SOURCE_INDEX:
            assert scene is None
        else:
            assert scene is not None and set(scene.entities) == {EntityId("a")}
    finally:
        provider.close()
