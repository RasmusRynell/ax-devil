"""Write a file so that readers see the old one or the complete new one, never a partial file."""

from __future__ import annotations

import io
import os
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import BinaryIO, Literal, TextIO, overload

TEMPORARY_TAG_ENV = "AX_DEVIL_TEMPORARY_TAG"
"""When set, temporary files are named ``<name>.<random>.<tag>.tmp``, so whoever set it can delete the ones left by a
writer that was killed."""


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
    left; a process killed mid-write leaves its temporary file, named with ``TEMPORARY_TAG_ENV`` when that is set.
    Text is UTF-8; *newline* is passed to ``open`` (use ``""`` for ``csv``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tag = os.environ.get(TEMPORARY_TAG_ENV)
    suffix = f".{tag}.tmp" if tag else ".tmp"
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=suffix)
    # Written through the descriptor mkstemp created, never by reopening the name, which another writer of the
    # folder could have swapped for a symlink. ``opener`` hands that descriptor to ``open`` so ``name`` stays the
    # path, which a caller needs to open its own handle on the temporary file.
    try:
        if mode == "wb":
            stream: TextIO | BinaryIO = open(temporary_name, "w+b", opener=lambda _path, _flags: descriptor)
        else:
            stream = open(
                temporary_name, "w", encoding="utf-8", newline=newline, opener=lambda _path, _flags: descriptor
            )
    except BaseException:
        os.close(descriptor)  # ``open`` failed before it took the descriptor.
        Path(temporary_name).unlink(missing_ok=True)
        raise
    try:
        with stream:
            yield stream
        try:
            os.chmod(temporary_name, stat.S_IMODE(path.stat().st_mode))
        except FileNotFoundError:
            pass
        replace_file(Path(temporary_name), path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def open_replaceable(path: Path) -> BinaryIO:
    """Open *path* for reading so that it can still be replaced or deleted while the handle is open.

    Windows refuses to rename or delete a file Python holds open, because Python opens files without
    ``FILE_SHARE_DELETE``; this opens with it. Elsewhere it is a plain ``open``. A memory map still blocks
    replacement on Windows, so callers that must stay replaceable do not map the file.
    """
    if sys.platform != "win32":
        return path.open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.restype = wintypes.HANDLE
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    generic_read, share_all, open_existing = 0x80000000, 0x1 | 0x2 | 0x4, 3
    handle = create_file(str(path), generic_read, share_all, None, open_existing, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb")


def open_replaceable_text(path: Path) -> TextIO:
    """Open *path* for reading UTF-8 text with the guarantees of ``open_replaceable``."""
    return io.TextIOWrapper(open_replaceable(path), encoding="utf-8")


def replace_file(source: Path, target: Path) -> None:
    """Rename *source* over *target*, which readers may have open.

    Windows' ordinary rename cannot replace a file that anything has open. Where it is available, the rename with
    POSIX semantics can, provided the readers opened with ``FILE_SHARE_DELETE`` (see ``open_replaceable``); it keeps
    their handles on the old contents. Elsewhere, and where Windows lacks it, this is ``os.replace``.
    """
    if sys.platform == "win32" and _posix_rename(source, target):
        return
    os.replace(source, target)


def _posix_rename(source: Path, target: Path) -> bool:
    """Rename *source* over *target* with POSIX semantics; return whether Windows did it."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class RenameInfo(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", ctypes.c_wchar * 32768),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = str(target.absolute())
    if len(name) >= 32768:
        return False
    delete, share_all, open_existing = 0x10000, 0x1 | 0x2 | 0x4, 3
    handle = kernel.CreateFileW(str(source), delete, share_all, None, open_existing, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        return False
    try:
        info = RenameInfo()
        info.Flags = 0x1 | 0x2  # FILE_RENAME_FLAG_REPLACE_IF_EXISTS | FILE_RENAME_FLAG_POSIX_SEMANTICS
        info.FileName = name
        info.FileNameLength = len(name) * 2
        file_rename_info_ex = 22
        size = RenameInfo.FileName.offset + info.FileNameLength + 2
        return bool(kernel.SetFileInformationByHandle(handle, file_rename_info_ex, ctypes.byref(info), size))
    finally:
        kernel.CloseHandle(handle)
