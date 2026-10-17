"""Write a file so that readers see the old one or the complete new one, never a partial file."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import BinaryIO, Literal, TextIO, overload


@overload
def write_atomically(
    path: Path, mode: Literal["w"] = "w", *, newline: str | None = None
) -> AbstractContextManager[TextIO]: ...


@overload
def write_atomically(path: Path, mode: Literal["wb"], *, newline: None = None) -> AbstractContextManager[BinaryIO]: ...


@contextmanager
def write_atomically(
    path: Path, mode: Literal["w", "wb"] = "w", *, newline: str | None = None
) -> Iterator[TextIO | BinaryIO]:
    """Yield a file to write the new contents of *path* to; the old file is replaced when the block exits cleanly.

    The contents go to a temporary file beside *path*, which replaces it in one step, keeping the permissions of the
    file it replaces. The parent folder is created. When the block raises, the old file stays and no temporary file is
    left. Text is UTF-8; *newline* is passed to ``open`` (use ``""`` for ``csv``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        if mode == "wb":
            with os.fdopen(descriptor, "wb") as binary:
                yield binary
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline=newline) as text:
                yield text
        try:
            os.chmod(temporary_name, stat.S_IMODE(path.stat().st_mode))
        except FileNotFoundError:
            pass
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
