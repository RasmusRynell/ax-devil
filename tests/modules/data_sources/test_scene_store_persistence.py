"""Tests that the scene file provider's persisted index and decoded artifacts stay valid for the file they describe."""

import json
import os
import shutil
import sys
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ax_devil.modules.cache import CacheManager
from ax_devil.modules.data_sources.file_data_provider import scene_catalog, stores
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
@pytest.mark.parametrize("ctime_is_change_time", [True, False], ids=["ctime-shortcut", "digest-only"])
def test_source_identity_is_its_content_not_its_timestamps(
    tmp_path: Path, storage_mode: StorageMode, ctime_is_change_time: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A touch reuses the artifact; a same-size edit that restores mtime is still noticed.

    Without a usable ctime (Windows) the stat key is made blind to the edit, so only the digest can notice it.
    """
    if not ctime_is_change_time:
        monkeypatch.setattr(scene_catalog, "STAT_SHORTCUT_IS_SOUND", False)
        for module in (scene_catalog, stores):
            original = module.build_scene_artifact_identity
            monkeypatch.setattr(
                module,
                "build_scene_artifact_identity",
                lambda *, original=original, **kwargs: replace(original(**kwargs), source_ctime_ns=0),
            )
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


class _RegeneratesSource(list[str]):
    """Decoded-payload log that regenerates the source, same size, once, and can restore it after the parse."""

    def __init__(self, path: Path, *, restore: bool) -> None:
        super().__init__()
        self._path = path
        self._restore = restore
        self._original = (path.read_bytes(), path.stat().st_mtime_ns)
        self._regenerated = False

    def regenerate(self) -> None:
        """Replace the contents with another generation (once), moving the mtime on."""
        if self._regenerated:
            return
        self._regenerated = True
        self._path.write_bytes(self._original[0].replace(b'"a"', b'"z"'))
        stat_result = self._path.stat()
        os.utime(self._path, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000_000))

    def append(self, payload: str) -> None:
        self.regenerate()
        super().append(payload)
        if self._restore and len(self) == 2:
            self._path.write_bytes(self._original[0])
            os.utime(self._path, ns=(self._original[1],) * 2)


@pytest.mark.parametrize("storage_mode", list(StorageMode))
@pytest.mark.parametrize("restored", [False, True], ids=["regenerated", "regenerated-then-restored"])
def test_source_changed_while_indexing_is_rebuilt_on_next_open(
    tmp_path: Path, storage_mode: StorageMode, restored: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regeneration that lands during indexing must not be cached as if the index had already seen it."""
    if restored and not scene_catalog.STAT_SHORTCUT_IS_SOUND:
        pytest.skip("Windows has no change time, so a restore during indexing cannot be told apart")
    path = write_jsonl(tmp_path / "scenes.jsonl", [_record(0, "a"), _record(1, "b")])
    log = _RegeneratesSource(path, restore=restored)
    real_hash_file = scene_catalog.hash_file

    def regenerate_after_hashing(file_path: Path) -> str:
        digest = real_hash_file(file_path)
        log.regenerate()  # Lands between hashing and parsing, so the parse reads the other generation.
        return digest

    with monkeypatch.context() as patch:
        patch.setattr(stores, "hash_file", regenerate_after_hashing)
        _build_provider(path, storage_mode=storage_mode, decoded=log).close()

    reopened_decodes: list[str] = []
    provider = _build_provider(path, storage_mode=storage_mode, decoded=reopened_decodes)
    try:
        assert len(reopened_decodes) == 2
        scene = provider.load_by_frame_id(_frame(min(provider.get_available_frames())))
        assert scene is not None and set(scene.entities) == {EntityId("a" if restored else "z")}
    finally:
        provider.close()


_ORIGINAL_LINES = [
    json.dumps(_record(second, *ids)).encode() for second, ids in enumerate(("a", "ab", "b", "c", "c", "a"))
]
_NEW_LINE = json.dumps(_record(6, "d")).encode()
_EDITS: dict[str, Callable[[list[bytes]], list[bytes]]] = {
    "edit in the middle, same size": lambda lines: [
        *lines[:2],
        lines[2].replace(b'"id": "b"', b'"id": "x"'),
        *lines[3:],
    ],
    "edit in the middle, one byte longer": lambda lines: [
        *lines[:2],
        lines[2].replace(b'"id": "b"', b'"id": "bb"'),
        *lines[3:],
    ],
    "insert in the middle": lambda lines: [*lines[:3], _NEW_LINE, *lines[3:]],
    "delete a line": lambda lines: [*lines[:2], *lines[3:]],
    "append after a last line without newline": lambda lines: [*lines, _NEW_LINE],
    "append a duplicate timestamp": lambda lines: [*lines, json.dumps(_record(1, "z")).encode()],
    "swap two lines": lambda lines: [lines[0], lines[3], lines[2], lines[1], *lines[4:]],
    "add blank, corrupt and non-UTF-8 lines": lambda lines: [
        lines[0],
        b"",
        b"{broken",
        lines[1],
        b"\xff\xfe",
        *lines[2:4],
        b"   ",
        *lines[4:],
    ],
    "one CRLF line": lambda lines: [*lines[:2], lines[2] + b"\r", *lines[3:]],
}


def _decoder_inputs(lines: Iterable[bytes]) -> set[str]:
    """Return the distinct texts the decoder receives for *lines*; blank and non-UTF-8 lines never reach it."""
    texts = set()
    for line in lines:
        try:
            texts.add(line.decode("utf-8").strip())
        except UnicodeDecodeError:
            pass
    return texts - {""}


@pytest.mark.parametrize("edit", _EDITS.values(), ids=list(_EDITS))
def test_edited_source_decodes_only_changed_records_and_matches_a_cold_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    edit: Callable[[list[bytes]], list[bytes]],
) -> None:
    """Records can be reused after an edit, but never at the price of a different index than a cold open builds."""
    path = tmp_path / "scenes.jsonl"
    edited_lines = edit(_ORIGINAL_LINES)
    path.write_bytes(b"\n".join(_ORIGINAL_LINES))
    options: dict[str, Any] = {"supports_sequence_lookup": True, "independent_records": True}
    _build_provider(path, **options).close()

    path.write_bytes(b"\n".join(edited_lines))
    decoded: list[str] = []
    reopened = _build_provider(path, decoded=decoded, **options)
    new_inputs = _decoder_inputs(set(edited_lines) - set(_ORIGINAL_LINES))
    assert sorted(decoded) == sorted(new_inputs)

    other_version: list[str] = []
    _build_provider(path, decoded=other_version, artifact_version=2, **options).close()
    assert sorted(other_version) == sorted(_decoder_inputs(edited_lines))

    cold_cache = tmp_path / "cold_cache"
    cold_cache.mkdir()
    monkeypatch.setattr(CacheManager, "get_cache_subdir", lambda *_args, **_kwargs: cold_cache)
    cold = _build_provider(path, supports_sequence_lookup=True)
    try:
        assert reopened.samples() == cold.samples()
        assert reopened.get_metadata().total_lines == cold.get_metadata().total_lines
        for timestamp in sorted(cold.get_available_frames()):
            assert reopened.load_by_frame_id(_frame(timestamp)) == cold.load_by_frame_id(_frame(timestamp))
    finally:
        reopened.close()
        cold.close()


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
