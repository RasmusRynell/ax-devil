"""Recent workspaces shared by ax-devil instances."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ax_devil.modules.workspace.core import RecentWorkspaces


def test_instances_recording_at_the_same_time_all_keep_their_entries(tmp_path: Path) -> None:
    storage = tmp_path / "recent-workspaces.json"
    files = [tmp_path / f"{number}.ax-devil.workspace" for number in range(24)]

    with ThreadPoolExecutor(max_workers=len(files)) as pool:
        for file in files:
            pool.submit(RecentWorkspaces(lambda: storage, limit=len(files)).record, file)

    assert {entry.path for entry in RecentWorkspaces(lambda: storage).entries()} == set(files)
