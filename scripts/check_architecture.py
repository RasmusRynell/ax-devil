"""Enforce the import boundaries described by the module map and domain invariants."""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path
from typing import NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULES = "ax_devil.modules"

# These are direct import boundaries, including type-only and deferred imports.
FORBIDDEN = {
    f"{MODULES}.video_player": (
        f"{MODULES}.scene",
        f"{MODULES}.video_viewer",
        f"{MODULES}.workspace",
        f"{MODULES}.data_sources",
    ),
    f"{MODULES}.object_inspection": (f"{MODULES}.video_viewer", f"{MODULES}.catalog_viewer"),
    f"{MODULES}.catalog_viewer": (f"{MODULES}.video_viewer",),
    # Plugin settings forms may use shared chrome and plugin_system widgets.
    "ax_devil.plugins": (
        "ax_devil.app",
        "ax_devil.cli",
        f"{MODULES}.application_shell",
        f"{MODULES}.workspace.ui",
        f"{MODULES}.video_viewer",
        f"{MODULES}.video_player",
        f"{MODULES}.catalog_viewer",
        f"{MODULES}.object_inspection",
        f"{MODULES}.plugin_manager",
    ),
}


class Import(NamedTuple):
    """An import, whether it executes while loading its module, and whether only type checkers see it."""

    target: str
    line: int
    runtime: bool
    type_only: bool


def within(name: str, package: str) -> bool:
    """Return whether a dotted name belongs to a package."""
    return name == package or name.startswith(f"{package}.")


class Imports(ast.NodeVisitor):
    """Collect resolved imports without confusing deferred imports with loading edges."""

    def __init__(self, package: str, modules: dict[str, Path]) -> None:
        self.package = package
        self.modules = modules
        self.edges: list[Import] = []
        self.deferred = False
        self.type_only = False

    def edge(self, target: str, line: int) -> Import:
        """Describe an import at the visitor's current position."""
        return Import(target, line, not (self.deferred or self.type_only), self.type_only)

    def visit_Import(self, node: ast.Import) -> None:
        """Collect absolute imports."""
        self.edges.extend(self.edge(alias.name, node.lineno) for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Resolve relative imports and distinguish submodules from imported attributes."""
        parts = self.package.split(".")
        prefix = ".".join(parts[: len(parts) - node.level + 1]) if node.level else ""
        base = ".".join(part for part in (prefix, node.module) if part)
        targets = {f"{base}.{alias.name}" if f"{base}.{alias.name}" in self.modules else base for alias in node.names}
        self.edges.extend(self.edge(target, node.lineno) for target in sorted(targets))

    def visit_If(self, node: ast.If) -> None:
        """Exclude type-checking branches from import-time reachability."""
        type_only = (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING") or (
            isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"
        )
        outer = self.type_only
        self.type_only = outer or type_only
        for child in node.body:
            self.visit(child)
        self.type_only = outer
        for child in node.orelse:
            self.visit(child)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Retain function imports for direct rules, but not loading reachability."""
        outer = self.deferred
        self.deferred = True
        self.generic_visit(node)
        self.deferred = outer

    visit_AsyncFunctionDef = visit_FunctionDef


def loaded_modules(target: str, modules: dict[str, Path]) -> list[str]:
    """Include package initializers executed by an import."""
    parts = target.split(".")
    return [name for end in range(1, len(parts) + 1) if (name := ".".join(parts[:end])) in modules]


def qt_path(start: str, graph: dict[str, list[Import]], modules: dict[str, Path]) -> list[tuple[str, Import]]:
    """Find one shortest import-time path from a module to PySide6."""
    pending: deque[tuple[str, list[tuple[str, Import]]]] = deque([(start, [])])
    seen = {start}
    while pending:
        name, path = pending.popleft()
        for edge in graph[name]:
            if not edge.runtime:
                continue
            step = [*path, (name, edge)]
            if within(edge.target, "PySide6"):
                return step
            for target in loaded_modules(edge.target, modules):
                if target not in seen:
                    seen.add(target)
                    pending.append((target, step))
    return []


def check(root: Path) -> list[str]:
    """Return violations for direct ownership boundaries and Qt-free Workspace core.

    Core may not import PySide6 anywhere outside type-checking blocks; reachability through other modules counts
    only import-time edges.
    """
    modules: dict[str, Path] = {}
    for path in sorted((root / "src/ax_devil").rglob("*.py")):
        parts = path.relative_to(root / "src").with_suffix("").parts
        modules[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = path.relative_to(root)
    graph: dict[str, list[Import]] = {}
    for name, path in modules.items():
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        visitor = Imports(package, modules)
        visitor.visit(ast.parse((root / path).read_text(encoding="utf-8"), filename=str(path)))
        graph[name] = visitor.edges

    violations: list[str] = []
    for name, edges in graph.items():
        in_core = within(name, f"{MODULES}.workspace.core")
        direct_qt = False
        for edge in edges:
            settings_fanout = (
                within(name, f"{MODULES}.settings")
                and within(edge.target, MODULES)
                and edge.target != MODULES
                and not within(edge.target, f"{MODULES}.settings")
            )
            forbidden = any(
                within(name, owner) and any(within(edge.target, target) for target in targets)
                for owner, targets in FORBIDDEN.items()
            )
            if settings_fanout or forbidden:
                violations.append(f"{modules[name]}:{edge.line}: forbidden dependency: {name} -> {edge.target}")
            if in_core and within(edge.target, "PySide6") and not edge.type_only:
                violations.append(f"{modules[name]}:{edge.line}: Workspace core must not import PySide6: {name}")
                direct_qt = True
        if in_core and not direct_qt:
            # Importing a core submodule also executes each parent package's __init__.
            for entry in loaded_modules(name, modules):
                chain = qt_path(entry, graph, modules)
                if chain:
                    source, first = chain[0]
                    steps = " -> ".join(f"{modules[owner]}:{edge.line} ({edge.target})" for owner, edge in chain)
                    violations.append(
                        f"{modules[source]}:{first.line}: Workspace core must be Qt-free: {name} -> {steps}"
                    )
                    break
    return sorted(set(violations))


def main() -> int:
    """Run the architecture check without importing application code."""
    violations = check(PROJECT_ROOT)
    for violation in violations:
        print(f"{violation}")
    return int(bool(violations))


if __name__ == "__main__":
    raise SystemExit(main())
