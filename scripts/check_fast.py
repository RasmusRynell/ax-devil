"""Run file-local checks on changed Python files; Make keeps global checks full."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def base_revision() -> str:
    """Return the merge-base with main, or HEAD when it cannot be found."""
    result = subprocess.run(
        ("git", "merge-base", "HEAD", "main"), cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "HEAD"


def main() -> int:
    """Check changes since the merge-base with main and untracked files, with config fallback."""
    base = base_revision()
    changed = {
        Path(name.decode())
        for args in (("diff", "--name-only", "-z", base), ("ls-files", "--others", "--exclude-standard", "-z"))
        for name in subprocess.check_output(("git", *args), cwd=PROJECT_ROOT).split(b"\0")
        if name
    }
    full = any(path in {Path("pyproject.toml"), Path("Makefile")} or path.parts[0] == "scripts" for path in changed)
    files = sorted(str(path) for path in changed if path.suffix == ".py" and (PROJECT_ROOT / path).is_file())
    if not full and not files:
        return 0
    targets = ["."] if full else files
    for args in (
        ("-m", "ruff", "format", "--check", *targets),
        ("-m", "ruff", "check", *targets),
        ("scripts/check_logging.py", *([] if full else files)),
    ):
        result = subprocess.run((sys.executable, *args), cwd=PROJECT_ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
