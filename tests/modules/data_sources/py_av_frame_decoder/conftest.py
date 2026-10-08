"""Immutable long-GOP media shared by decoder seek regressions."""

from pathlib import Path

import pytest

from tests.helpers.video import create_test_video


@pytest.fixture(scope="session")
def asf_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Provide nonzero-start-PTS media spanning several keyframe intervals."""
    path = tmp_path_factory.mktemp("asf-seek") / "test.asf"
    create_test_video(path, duration=10.0, fps=25, gop=60, container="asf")
    return path
