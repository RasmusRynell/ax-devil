"""Compiled ordered draw steps, independent scopes and generic primitive emission."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import InitVar, dataclass, field
from types import MappingProxyType

import numpy as np

from ax_devil.modules.scene.rendering.visibility import OverlayFeature
from ax_devil.modules.video_player.engine.drawing import DrawingTarget
from ax_devil.modules.video_player.engine.render_context import RenderContext

from . import executable
from .definitions import PRIMITIVES, ValueType, has_constraints, validate_value
from .expressions import CompiledExpression, TemplateInputs, instruction
from .kernels import RowState, emit
from .values import TemplateRuntimeError


@dataclass(frozen=True, slots=True)
class RenderStep:
    """A resolved primitive or template target with a guard evaluated first."""

    location: str
    enabled: CompiledExpression
    fields: CompiledExpression
    style: CompiledExpression | None = None
    primitive: str = ""
    target: RenderProgram | None = None

    def lower(self) -> executable.Step:
        """Discard expression metadata once the guarded step has been resolved."""
        return executable.Step(
            self.enabled.code,
            self.fields.code if self.target is not None else _primitive_fields(self.fields, self.primitive),
            self.style.code if self.style is not None else None,
            self.target.code if self.target is not None else None,
            self.primitive,
            self.location,
        )


def _primitive_fields(expression: CompiledExpression, kind: str) -> executable.Node:
    names = tuple(name for name, _ in PRIMITIVES[kind].fields)
    if expression.members is not None and not has_constraints(expression.value_type):
        members = dict(expression.members)
        return instruction("tuple", children=tuple(members[name].code for name in names))
    return instruction("fields", names, (expression.code,))


@dataclass(frozen=True, slots=True)
class RenderProgram:
    """A sealed program; generated functions evaluate it for one row or a table of rows."""

    values: InitVar[tuple[CompiledExpression, ...]]
    steps: InitVar[tuple[RenderStep, ...]]
    enabled: InitVar[CompiledExpression]
    parameter_defaults: Mapping[str, object]
    parameter_type: ValueType
    expanded_steps: int
    depth: int
    roots: Mapping[str, ValueType] = field(default_factory=dict)
    features: frozenset[OverlayFeature] = frozenset()
    """Feature tags on the output this program draws, including output of the templates it calls."""
    draws: bool = True
    """Whether any output is reachable: false when its own condition or every step is constantly off."""

    code: executable.Program = field(init=False, repr=False, compare=False)
    _run: Callable[..., object] | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(
        self, values: tuple[CompiledExpression, ...], steps: tuple[RenderStep, ...], enabled: CompiledExpression
    ) -> None:
        # Keep lowering plans for template specialization; execute only the compiled function.
        object.__setattr__(
            self,
            "code",
            executable.Program(
                enabled.code,
                tuple(value.code for value in values),
                tuple(step.lower() for step in steps if step.enabled.constant is not False),
            ),
        )

    def demanded_parameters(self) -> frozenset[str]:
        """Return parameters demanded by the retained steps and calculated values."""
        names: set[str] = set()
        visited: set[int] = set()

        def visit(node: executable.Node) -> None:
            if node.kind == "input" and node.operand[0] == "parameters":
                names.add(node.operand[1])
            if node.kind == "slot" and node.operand not in visited:
                visited.add(node.operand)
                visit(self.code.values[node.operand])
            for child in node.children:
                visit(child)

        visit(self.code.enabled)
        for step in self.code.steps:
            visit(step.enabled)
            visit(step.fields)
            if step.style is not None:
                visit(step.style)
        return frozenset(names)

    def emit(self, inputs: TemplateInputs, context: RenderContext, output: DrawingTarget) -> None:
        """Execute once for shared mapping inputs; a failed check raises and emits nothing."""
        run = self._run
        if run is None:
            run = executable.compile_rows(self.code, self.roots, table=False)
            object.__setattr__(self, "_run", run)
        state = RowState(1)
        with np.errstate(all="ignore"):
            run(inputs, context, state)
        if state.hits:
            raise state.first_error()
        emit(output, state)

    def compile_table(self) -> Callable[..., object]:
        """Compile for tables of rows: ``update(table, context, state)``."""
        return executable.compile_rows(self.code, self.roots, table=True)


@dataclass(frozen=True, slots=True)
class RenderCatalog:
    """Reusable compiled template definitions."""

    programs: Mapping[str, RenderProgram]

    def __post_init__(self) -> None:
        object.__setattr__(self, "programs", MappingProxyType(dict(self.programs)))

    def render(self, name: str, inputs: TemplateInputs, output: DrawingTarget, *, context: RenderContext) -> None:
        """Render a standalone template through its validated parameter boundary."""
        program = self.programs.get(name)
        if program is None:
            raise TemplateRuntimeError(f"Unknown template: {name}.")
        missing = {key for key, _ in program.parameter_type.fields} - set(program.parameter_defaults) - set(inputs)
        if missing:
            raise TemplateRuntimeError(f"Missing required template inputs: {sorted(missing)}.")
        parameters = validate_value({**program.parameter_defaults, **inputs}, program.parameter_type)
        program.emit({"parameters": parameters}, context, output)
