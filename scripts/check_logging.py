"""Validate ax-devil logging conventions."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGGING_CONFIG = Path("src/ax_devil/modules/settings/logging_config.py")
CHECKED_ROOTS = (Path("src"), Path("tests"), Path("tools"))
LOGGER_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
LOGGER_METHODS_WITH_LOG = LOGGER_METHODS | {"log"}
ALLOWED_LOGGING_IMPORTS = {"CRITICAL", "DEBUG", "ERROR", "INFO", "Logger", "WARNING"}


class Violation(NamedTuple):
    """A project logging convention violation."""

    path: Path
    line: int
    message: str


def iter_python_files() -> Iterable[Path]:
    """Yield Python files that are covered by the logging contract."""
    for root in CHECKED_ROOTS:
        for path in (PROJECT_ROOT / root).rglob("*.py"):
            yield path.relative_to(PROJECT_ROOT)


def is_logger_receiver(node: ast.AST) -> bool:
    """Return True when a call receiver looks like a module or instance logger."""
    if isinstance(node, ast.Name):
        return node.id in {"logger", "_logger"}
    return isinstance(node, ast.Attribute) and node.attr in {"logger", "_logger"}


def is_dunder_name(node: ast.AST) -> bool:
    """Return True when a node is the __name__ module identifier."""
    return isinstance(node, ast.Name) and node.id == "__name__"


def check_import(node: ast.Import | ast.ImportFrom, path: Path) -> list[Violation]:
    """Validate logging imports."""
    if path == LOGGING_CONFIG:
        return []

    violations: list[Violation] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name == "logging":
                violations.append(Violation(path, node.lineno, "use get_logger; do not import logging"))
        return violations

    if node.module != "logging":
        return []

    imported_names = {alias.name for alias in node.names}
    unsupported_names = imported_names - ALLOWED_LOGGING_IMPORTS
    if unsupported_names:
        names = ", ".join(sorted(unsupported_names))
        violations.append(Violation(path, node.lineno, f"unsupported logging import(s): {names}"))
    return violations


def check_stdlib_logging_call(node: ast.Call, path: Path) -> Violation | None:
    """Reject direct stdlib logging calls outside logging_config.py."""
    if path == LOGGING_CONFIG:
        return None
    if not isinstance(node.func, ast.Attribute):
        return None
    if not isinstance(node.func.value, ast.Name):
        return None
    if node.func.value.id not in {"logging", "_logging"}:
        return None
    if node.func.attr in {"getLogger", "basicConfig"}:
        return Violation(path, node.lineno, f"use project logging instead of logging.{node.func.attr}")
    return None


def check_get_logger_call(node: ast.Call, path: Path) -> Violation | None:
    """Require module-scoped project loggers outside logging_config.py."""
    if path == LOGGING_CONFIG:
        return None
    if not isinstance(node.func, ast.Name) or node.func.id != "get_logger":
        return None
    if not node.args or not is_dunder_name(node.args[0]):
        return Violation(path, node.lineno, "use get_logger(__name__) for project loggers")
    return None


def check_logger_call(node: ast.Call, path: Path) -> Violation | None:
    """Reject lazy %-style logging and string concatenation."""
    if not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in LOGGER_METHODS_WITH_LOG or not is_logger_receiver(node.func.value):
        return None

    if node.func.attr == "log":
        if len(node.args) > 2:
            return Violation(path, node.lineno, "use f-strings, not logger.log(..., msg, *args)")
        if len(node.args) < 2:
            return None
        message_arg = node.args[1]
    else:
        if len(node.args) > 1:
            return Violation(path, node.lineno, "use f-strings, not logger lazy formatting args")
        if not node.args:
            return None
        message_arg = node.args[0]

    if isinstance(message_arg, ast.BinOp) and isinstance(message_arg.op, ast.Mod):
        return Violation(path, node.lineno, "use f-strings, not %-formatted logger messages")
    if isinstance(message_arg, ast.BinOp) and isinstance(message_arg.op, ast.Add):
        return Violation(path, node.lineno, "use f-strings, not concatenated logger messages")
    return None


def check_file(path: Path) -> list[Violation]:
    """Return logging convention violations for one file."""
    source_path = PROJECT_ROOT / path
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            violations.extend(check_import(node, path))
            continue

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                violations.append(Violation(path, node.lineno, "use project logging instead of print()"))
                continue

            for check in (check_stdlib_logging_call, check_get_logger_call, check_logger_call):
                violation = check(node, path)
                if violation is not None:
                    violations.append(violation)
                    break

    return violations


def main() -> int:
    """Run the logging convention check."""
    violations = [violation for path in iter_python_files() for violation in check_file(path)]
    if not violations:
        return 0

    for violation in sorted(violations):
        print(f"{violation.path}:{violation.line}: {violation.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
