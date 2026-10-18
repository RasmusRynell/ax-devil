"""Block staged files that are too large for an agent to read comfortably."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from fnmatch import fnmatch
from pathlib import Path
from typing import NamedTuple

MAX_TOKENS = 5000
BYTES_PER_TOKEN = 4

# Lockfiles, generated code and binary/media files are not authored by hand or by agents.
IGNORED_PATTERNS = (
    "uv.lock",
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "*.min.js",
    "*.min.css",
    "*.svg",
    "*_pb2.py",
    "*_pb2.pyi",
    "*_pb2_grpc.py",
    "*.generated.*",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.webp",
    "*.mp4",
    "*.mkv",
    "*.avi",
    "*.mov",
    "*.h264",
    "*.pdf",
    "*.ttf",
    "*.otf",
    "*.woff",
    "*.woff2",
    "*.zip",
    "*.gz",
    "*.tar",
    "*.whl",
    "*.so",
    "*.npy",
    "*.npz",
    "*.onnx",
    "*.pt",
    "*.bin",
    "*.sqlite",
    "*.db",
    # Data files loaded whole as render catalogs, not split into modules.
    "src/ax_devil/modules/scene/rendering/catalog_definitions/*.json",
)

SPLIT_INSTRUCTIONS = f"""\
Split each file above into smaller files, each well under {MAX_TOKENS} tokens (~{MAX_TOKENS * BYTES_PER_TOKEN} bytes):
  1. Find the seams: group the file's top-level classes/functions/sections by responsibility
     (read its headings or top-level definitions).
  2. Create new files next to it (same directory), named for each responsibility. Move each group
     wholesale; do not rewrite it.
  3. Python: keep the original module as the public entry point and re-export moved names only if other
     code imports them; otherwise update the imports at the call sites. Prefer a package (`foo/` with
     `__init__.py`) when splitting into 3+ files.
  4. Markdown docs: split by top-level heading into separate files and link them from the original, which
     becomes an index. Per AGENTS.md "Where A Fact Belongs", prefer deleting redundant text over moving it.
  5. Tests: split by the unit under test; move fixtures shared by several files into `conftest.py`.
  6. Aim for under ~{MAX_TOKENS * 4 // 5} tokens per resulting file so it has headroom.
  7. Re-run `git add` on all new and changed files, then commit again. Do not bypass with --no-verify, and
     do not add the file to IGNORED_PATTERNS in scripts/check_file_tokens.py unless it is generated or a lockfile.
"""


class OversizedFile(NamedTuple):
    """A file whose estimated token count exceeds the limit."""

    path: str
    size: int

    @property
    def tokens(self) -> int:
        """Estimated token count."""
        return self.size // BYTES_PER_TOKEN


def is_ignored(path: str) -> bool:
    """Return True when the path matches a lockfile, generated-file, or binary pattern."""
    posix_path = Path(path).as_posix()
    name = Path(path).name
    return any(fnmatch(posix_path, pattern) or fnmatch(name, pattern) for pattern in IGNORED_PATTERNS)


def find_oversized(paths: Sequence[str]) -> list[OversizedFile]:
    """Return existing, non-ignored regular files whose estimated tokens exceed MAX_TOKENS."""
    oversized: list[OversizedFile] = []
    for path in paths:
        file_path = Path(path)
        if not file_path.is_file() or is_ignored(path):
            continue
        size = file_path.stat().st_size
        if size / BYTES_PER_TOKEN > MAX_TOKENS:
            oversized.append(OversizedFile(path, size))
    return oversized


def main(argv: Sequence[str]) -> int:
    """Fail when any given file exceeds the token limit."""
    oversized = find_oversized(argv)
    if not oversized:
        return 0

    sys.stderr.write(f"Files over the {MAX_TOKENS}-token limit (tokens = bytes / {BYTES_PER_TOKEN}):\n")
    for item in oversized:
        sys.stderr.write(f"  {item.path}: {item.size} bytes, ~{item.tokens} tokens, limit {MAX_TOKENS}\n")
    sys.stderr.write(f"\n{SPLIT_INSTRUCTIONS}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
