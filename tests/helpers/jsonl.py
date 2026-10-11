"""JSON-lines files written by data-provider tests."""

import json
from pathlib import Path
from typing import Any


def write_jsonl(path: Path, lines: list[dict[str, Any] | str]) -> Path:
    """Write records as JSON lines; strings are written verbatim."""
    path.write_text(
        "".join(f"{line if isinstance(line, str) else json.dumps(line)}\n" for line in lines), encoding="utf-8"
    )
    return path
