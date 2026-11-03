"""Replace a file the way an editor saves, for tests of code that follows files on disk."""

from __future__ import annotations

import os
import time
from pathlib import Path


def replace_like_an_editor(source: Path, destination: Path) -> None:
    """Rename *source* over *destination*, retrying briefly as editors do when Windows holds it for a virus scan."""
    deadline = time.monotonic() + 2
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
