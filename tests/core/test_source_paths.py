"""A snapshot says what the source paths looked like, and the earlier one wins when snapshots are combined."""

from __future__ import annotations

from pathlib import Path

from ax_devil.core.cancellation import NEVER
from ax_devil.core.source_paths import SourcePaths


def test_combined_snapshots_keep_the_left_fingerprint_of_a_shared_path(tmp_path: Path) -> None:
    shared = tmp_path / "shared.txt"
    shared.write_text("before")
    left = SourcePaths(files=frozenset({shared})).snapshot(NEVER)
    shared.write_text("after, and longer")
    right = SourcePaths(files=frozenset({shared, tmp_path / "missing.txt"})).snapshot(NEVER)

    combined = left | right

    assert combined.fingerprints == {shared: left.fingerprints[shared]}
    assert combined.paths.files == right.paths.files
