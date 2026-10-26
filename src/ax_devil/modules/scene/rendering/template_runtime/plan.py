"""Compile-time instruction plan handed from the template compiler to the lowering layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True, eq=False)
class Node:
    """Temporary compiler instruction; no instruction dispatch occurs during rendering."""

    kind: str
    operand: Any = None
    children: tuple[Node, ...] = ()
    location: str = "$"


@dataclass(frozen=True, slots=True)
class Step:
    """Resolved guarded template call or primitive emission."""

    enabled: Node
    fields: Node
    style: Node | None
    target: Program | None
    primitive: str
    location: str


@dataclass(frozen=True, slots=True)
class Program:
    """Compile-time template structure, inlined before executable code is generated."""

    enabled: Node
    values: tuple[Node, ...]
    steps: tuple[Step, ...]
