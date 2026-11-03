"""Run benchmark children: private displays, temporary HOME, the swap shim and reused worktrees of older refs."""

from __future__ import annotations

import contextlib
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from tools import private_display

from . import report

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ax-devil-bench"
RESULT_PREFIX = "AX_BENCH_RESULT "
_CATALOGS = Path("ax_devil/modules/scene/rendering/catalog_definitions")
_CHILD_SECONDS = 600


class Side(NamedTuple):
    """Code under measurement: the working tree ("head") or a ref's worktree ("base")."""

    label: str
    ref: str
    commit: str
    dirty: bool
    python: str
    source: Path


def builtin_catalogs(side: Side) -> set[str]:
    """Return the names of the catalogs that ship with *side*'s code."""
    folder = side.source / _CATALOGS
    return {path.stem for path in folder.glob("*.json") if path.name != "catalog.schema.json"}


def _git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def head() -> Side:
    """Return the current working tree as the code to measure."""
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    return Side("head", "HEAD", _git("rev-parse", "--short", "HEAD"), dirty, sys.executable, ROOT / "src")


def _worktree(commit: str) -> Path:
    path = CACHE / commit
    if not path.exists():
        _git("worktree", "add", "--detach", str(path), commit)
    return path


def base(ref: str) -> Side:
    """Return *ref* as the code to measure, in a detached worktree that is reused between runs.

    The current environment runs it through PYTHONPATH when uv.lock is unchanged; otherwise the worktree gets its own.
    """
    commit = _git("rev-parse", "--verify", "--short", f"{ref}^{{commit}}")
    path = _worktree(commit)
    python = sys.executable
    if not filecmp.cmp(ROOT / "uv.lock", path / "uv.lock", shallow=False):
        subprocess.run(["uv", "sync", "--project", str(path)], check=True)
        python = str(path / ".venv/bin/python")
    return Side("base", ref, commit, False, python, path / "src")


def clean(ref: str) -> None:
    """Remove the reused worktree of *ref*."""
    commit = _git("rev-parse", "--verify", "--short", f"{ref}^{{commit}}")
    if (CACHE / commit).exists():
        _git("worktree", "remove", "--force", str(CACHE / commit))


def _swap_shim() -> Path | None:
    """Compile the glXSwapBuffers-to-glFlush shim on first use; None when there is no C compiler."""
    library = CACHE / "swap_shim.so"
    source = Path(__file__).with_name("swap_shim.c")
    if not library.exists() or library.stat().st_mtime < source.stat().st_mtime:
        compiler = shutil.which("cc")
        if compiler is None:
            return None
        CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run([compiler, "-shared", "-fPIC", "-o", str(library), str(source), "-ldl"], check=True)
    return library


@contextlib.contextmanager
def session(display: str, graphics: bool, real_swap: bool) -> Iterator[dict[str, str]]:
    """Yield the environment for children: a temporary HOME and, for graphics, the chosen display.

    ``private`` starts an Xvfb with the inherited driver, ``mesa`` forces llvmpipe on it, and ``real`` uses the
    inherited display (only for the user). Presentation is a glFlush on private displays unless *real_swap*.
    """
    with tempfile.TemporaryDirectory(prefix="ax-devil-bench-") as home:
        env = dict(os.environ)
        env.update(HOME=home, QT_QPA_PLATFORM="offscreen")
        for variable, folder in (("CONFIG", ".config"), ("DATA", ".data"), ("CACHE", ".cache"), ("STATE", ".state")):
            env[f"XDG_{variable}_HOME"] = f"{home}/{folder}"
        if not graphics:
            yield env
            return
        env["QSG_RHI_BACKEND"] = "opengl"
        if display == "real":
            env.pop("QT_QPA_PLATFORM")
            yield env
            return
        shim = None if real_swap else _swap_shim()
        if shim is not None:
            env["LD_PRELOAD"] = str(shim)
        screen = private_display.start()
        try:
            for name, value in private_display.environment(screen.name, mesa=display == "mesa").items():
                if value is None:
                    env.pop(name, None)
                else:
                    env[name] = value
            yield env
        finally:
            private_display.stop(screen)


def run_child(side: Side, command: str, options: dict[str, object], env: dict[str, str]) -> report.Record:
    """Run one measurement in a fresh process on *side*'s code and return its record."""
    env = {**env, "PYTHONPATH": str(side.source)}
    argv = [side.python, "-m", "tools.bench", "--child", command, json.dumps(options)]
    done = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=_CHILD_SECONDS)
    result = None
    for line in done.stdout.splitlines():
        if line.startswith(RESULT_PREFIX):
            result = json.loads(line.removeprefix(RESULT_PREFIX))
        else:
            report.emit(line)
    if result is None or done.returncode != 0:
        raise RuntimeError(f"{command} failed on {side.ref} (exit {done.returncode}):\n{done.stderr[-3000:]}")
    return {
        "schema": report.SCHEMA,
        "tool": "ax-devil-bench",
        "ref": side.ref,
        "commit": side.commit,
        "dirty": side.dirty,
        "args": options,
        **result,
    }
