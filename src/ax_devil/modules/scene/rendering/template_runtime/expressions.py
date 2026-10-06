"""Typed compilation metadata for immutable Python executable instructions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ax_devil.modules.video_player.engine.render_context import RenderContext

from . import executable
from .definitions import ValueType

UNSET = object()
TemplateInputs = Mapping[str, object]


def instruction(
    kind: str, operand: object = None, children: tuple[executable.Node, ...] = (), location: str = "$"
) -> executable.Node:
    """Build Python code from validated compiler output, never from executable user text."""
    return executable.Node(kind, operand, children, location)


@dataclass(frozen=True, slots=True)
class CompiledExpression:
    """A typed Python instruction with optional immutable precomputed output."""

    value_type: ValueType
    plan: executable.Node
    location: str
    constant: object = UNSET
    uses_context: bool = False
    uses_inputs: bool = False
    members: tuple[tuple[str, CompiledExpression], ...] | None = None
    code: executable.Node = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        code = instruction("constant", self.constant) if self.constant is not UNSET else self.plan
        # The located wrapper carries the value type, which selects the row-column representation.
        object.__setattr__(self, "code", instruction("located", self.value_type, (code,), self.location))

    def evaluate(self, context: RenderContext) -> object:
        """Evaluate constants during compilation using the same engine as display."""
        return executable.evaluate(self.code, context)
