"""Cancellable work polls a `CancelToken`; no second cancellation mechanism may creep back into the app.

A second idiom (a `threading.Event` passed around as a flag, a bool-returning callback parameter, a swallowing
`except BaseException`, a private cancellation exception) would be one more thing every author has to know and every
call site has to translate. The files named below are the only code allowed to use these constructs, each for a reason
that is not cancelling a job.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "ax_devil"

_DECODER = "modules/data_sources/file_data_provider/pyav_decoder"
_ALLOWED: dict[str, dict[str, str]] = {
    # threading.Event objects: signals between long-lived threads, not requests to stop finite work.
    "event": {
        "core/cancellation.py": "the token wraps the one event",
        f"{_DECODER}/frame_worker.py": "stop, pause and wake signals of a thread",
        f"{_DECODER}/video_frame_reader.py": "completion handshake of one read",
        "modules/data_sources/file_frame_delivery.py": "completion handshake of one synchronous read",
        "modules/diagnostics/trace_recorder.py": "stop signal of the recorder's own thread",
    },
    # Asking an event whether it is set.
    "is_set": {
        "core/cancellation.py": "the token's own poll",
        f"{_DECODER}/frame_worker.py": "the decoder thread's own loop",
        "modules/diagnostics/trace_recorder.py": "the recorder thread's own loop",
    },
    # `except BaseException` that does not end in `raise` swallows `Cancelled`.
    "swallow": {
        "modules/background_jobs/jobs.py": "the runner turns anything work raised, SystemExit included, into on_error",
    },
    "predicate": {},
    "name": {},
}
_BANNED_NAMES = {"CancelCheck", "AnalysisCancelled", "is_cancelled"}


def _found(tree: ast.AST) -> list[tuple[str, int, str]]:
    """Return the ``(rule, line, message)`` of every second-mechanism construct in *tree*, allowed or not."""
    found = []
    for node in ast.walk(tree):
        line: int = getattr(node, "lineno", 0)
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Attribute) and function.attr == "is_set":
                found.append(("is_set", line, "asks an event whether it is set; poll a CancelToken instead"))
            if (isinstance(function, ast.Attribute) and function.attr == "Event") or (
                isinstance(function, ast.Name) and function.id == "Event"
            ):
                found.append(("event", line, "creates a threading.Event; cancellation is a CancelToken"))
        if isinstance(node, ast.ExceptHandler):
            caught = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            if any(isinstance(kind, ast.Name) and kind.id == "BaseException" for kind in caught):
                if not isinstance(node.body[-1], ast.Raise):
                    found.append(("swallow", line, "catches BaseException without re-raising, so Cancelled is lost"))
        if isinstance(node, ast.Subscript) and _is_bool_callable(node):
            found.append(("predicate", line, "a Callable[[], bool] is a second way to ask for cancellation"))
        if _name_of(node) in _BANNED_NAMES:
            found.append(("name", line, f"{_name_of(node)} is a second cancellation mechanism; use a CancelToken"))
    return found


def _violations(tree: ast.AST, relative: str) -> list[str]:
    return [f"{relative}:{line}: {message}" for rule, line, message in _found(tree) if relative not in _ALLOWED[rule]]


def _is_bool_callable(node: ast.Subscript) -> bool:
    if not ast.unparse(node.value).endswith("Callable"):
        return False
    arguments = node.slice
    if not (isinstance(arguments, ast.Tuple) and len(arguments.elts) == 2):
        return False
    parameters, result = arguments.elts
    return isinstance(parameters, ast.List) and not parameters.elts and ast.unparse(result) == "bool"


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.arg):
        return node.arg
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name
    return ""


def test_no_second_cancellation_mechanism_exists_in_the_app() -> None:
    violations = []
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE).as_posix()
        violations += _violations(ast.parse(path.read_text(encoding="utf-8")), relative)

    assert violations == [], "Poll a CancelToken (see modules/background_jobs/README.md):\n" + "\n".join(violations)


def test_every_allowlisted_file_still_needs_its_entry() -> None:
    """An entry that no longer matters is deleted, so the allowlists never grow stale."""
    for rule, files in _ALLOWED.items():
        for relative in files:
            tree = ast.parse((SOURCE / relative).read_text(encoding="utf-8"))
            assert rule in {found_rule for found_rule, _line, _message in _found(tree)}, f"{relative}: {rule}"


@pytest.mark.parametrize(
    "code",
    [
        "stop = threading.Event()",
        "from threading import Event\nstop = Event()",
        "if stop.is_set():\n    return",
        "def run(check: Callable[[], bool] = lambda: False): ...",
        "def run(check: collections.abc.Callable[[], bool]): ...",
        "try:\n    work()\nexcept BaseException:\n    log()",
        "try:\n    work()\nexcept (OSError, BaseException):\n    log()",
        "class AnalysisCancelled(Exception): ...",
        "def run(cancel_check: CancelCheck): ...",
        "def is_cancelled(): ...",
    ],
)
def test_the_guard_catches_each_second_mechanism(code: str) -> None:
    assert _violations(ast.parse(code), "somewhere/new_module.py")


@pytest.mark.parametrize(
    "code",
    [
        "token.raise_if_cancelled()",
        "for item in token.polled(items): ...",
        "try:\n    work()\nexcept BaseException:\n    cleanup()\n    raise",
        "try:\n    work()\nexcept Exception:\n    log()",
        "def run(progress: Callable[[int], bool]): ...",
    ],
)
def test_the_guard_leaves_the_token_and_unrelated_code_alone(code: str) -> None:
    assert not _violations(ast.parse(code), "somewhere/new_module.py")
