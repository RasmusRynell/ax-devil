"""Compile one template program: calculated values, guards, references and calls, into resolved expressions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from math import prod
from typing import TYPE_CHECKING, cast

from ax_devil.modules.video_player.engine.render_context import RenderContext

from .coercion import coerce, literal_type, record_evaluator, union
from .definitions import (
    ANY,
    LENGTH,
    MAX_DEPTH,
    NUMBER,
    OPERATIONS,
    STRING,
    ValueType,
)
from .expressions import UNSET, CompiledExpression, instruction
from .plan import Node
from .structure import Guards, TemplateDefinition, guard, require_keys, require_list, require_object
from .validation import validate_value
from .values import TemplateRuntimeError, freeze, record

if TYPE_CHECKING:
    from .compiler import RenderProgramCompiler


_DUMMY_CONTEXT = RenderContext.create(1, 1)


def _arguments(children: tuple[CompiledExpression, ...]) -> Node:
    return instruction("tuple", children=tuple(child.code for child in children))


class ProgramCompiler:
    """Compiles the raw JSON expressions of one template program into typed ``CompiledExpression`` trees.

    One instance serves one program (``RenderProgramCompiler.compile_program`` creates it) and is not reused.
    Before compiling, the caller sets ``static_parameters`` (call-site constants that fold parameter references)
    and ``base_guards`` (references the program's ``enabled`` condition proves present). It then compiles
    ``enabled`` and each step input with ``expression`` and every calculated value with ``value``, in sorted name
    order (``names``).

    Calculated values are memoized on first use, so ``base_guards`` must be set before the first ``value`` call.
    Scene references collect in ``reference_values`` at slots ``len(names) + i``, after the calculated values; read
    it only once every expression has been compiled.
    Failures raise ``TemplateRuntimeError`` located at the offending raw expression.
    """

    def __init__(
        self,
        owner: RenderProgramCompiler,
        definition: TemplateDefinition,
        roots: Mapping[str, ValueType],
        location: str,
    ) -> None:
        """Bind *owner* (the creating compiler), the program *location* and *roots*.

        *roots* maps each referenceable scope other than the program's own ``values`` (such as ``context``,
        ``parameters`` or ``scene``) to its record type.
        """
        self.owner, self.roots, self.location = owner, roots, location
        self.raw_values = require_object(definition.get("values", {}), f"{location}.values")
        self.names = tuple(sorted(self.raw_values))
        self.indices = {name: index for index, name in enumerate(self.names)}
        self.compiled: dict[str, CompiledExpression] = {}
        self.active: set[str] = set()
        self.base_guards: Guards = frozenset()
        self.guard_depth = 0
        self.static_parameters: Mapping[str, object] = {}
        self.reference_indices: dict[tuple[tuple[str, ...], ValueType], int] = {}
        self.reference_values: list[CompiledExpression] = []

    def value(self, name: str) -> CompiledExpression:
        """Return the compiled calculated value *name*, compiling and memoizing it on first use.

        Raises ``TemplateRuntimeError`` for an unknown name, a reference cycle or nesting beyond ``MAX_DEPTH``.
        """
        if len(self.active) >= MAX_DEPTH:
            raise TemplateRuntimeError(
                "Calculated-value depth limit exceeded.", code="execution_limit", location=self.location
            )
        if name in self.active:
            raise TemplateRuntimeError(
                f"Calculated-value cycle at {name}.", code="value_cycle", location=f"{self.location}.values.{name}"
            )
        if name not in self.raw_values:
            raise TemplateRuntimeError(
                f"Unknown calculated value: {name}.", code="unknown_reference", location=self.location
            )
        if name not in self.compiled:
            self.active.add(name)
            try:
                self.compiled[name] = self.expression(
                    self.raw_values[name], f"{self.location}.values.{name}", self.base_guards, guarded=True
                )
            finally:
                self.active.remove(name)
        return self.compiled[name]

    def expression(
        self,
        raw: object,
        location: str,
        guards: Guards,
        expected: ValueType | None = None,
        depth: int = 0,
        *,
        guarded: bool = False,
    ) -> CompiledExpression:
        """Compile the raw expression *raw* (a literal, ``ref``, ``call``, record or list) at *location*.

        *guards* lists the reference paths known to be present here; *expected* coerces the result to that type;
        *depth* counts nesting. ``guarded=True`` marks conditional execution, which stops operations inside from
        being folded at compile time. An error without a location is re-raised at *location*.
        """
        if depth > MAX_DEPTH:
            raise TemplateRuntimeError("Expression depth limit exceeded.", code="execution_limit", location=location)
        self.guard_depth += int(guarded)
        try:
            expression = self._expression(raw, location, guards, depth)
            return coerce(expression, expected) if expected is not None else expression
        except TemplateRuntimeError as exc:
            if exc.diagnostic.location != "$":
                raise
            raise TemplateRuntimeError(exc.diagnostic.message, code=exc.diagnostic.code, location=location) from exc
        finally:
            self.guard_depth -= int(guarded)

    def _expression(self, raw: object, location: str, guards: Guards, depth: int) -> CompiledExpression:
        if isinstance(raw, Mapping):
            if "literal" in raw:
                require_keys(raw, {"literal"}, {"literal"}, location)
                value = freeze(raw["literal"])
                kind = literal_type(value)
                return CompiledExpression(kind, instruction("constant", value), location, constant=value)
            if "ref" in raw:
                require_keys(raw, {"ref"}, {"ref"}, location)
                path = tuple(require_list(raw["ref"], location))
                if len(path) < 2 or not all(isinstance(part, str) and part for part in path):
                    raise TemplateRuntimeError(
                        "Reference must contain a scope and exact field names.", location=location
                    )
                return self.reference(cast(tuple[str, ...], path), location, guards)
            if "call" in raw:
                require_keys(raw, {"call", "args"}, {"call", "args"}, location)
                return self.call(
                    cast(str, raw["call"]), require_object(raw["args"], f"{location}.args"), location, guards, depth
                )
            fields = tuple(
                (key, self.expression(value, f"{location}.{key}", guards, depth=depth + 1))
                for key, value in sorted(raw.items())
            )
            kind = ValueType("record", tuple((key, value.value_type) for key, value in fields))
            expression = self.build(
                kind,
                record_evaluator(fields),
                tuple(value for _, value in fields),
                location,
            )
            expression = replace(expression, members=fields)
            return coerce(expression, LENGTH) if {key for key, _ in fields} == {"value", "unit"} else expression
        if isinstance(raw, (tuple, list)):
            items = tuple(
                self.expression(value, f"{location}[{index}]", guards, depth=depth + 1)
                for index, value in enumerate(raw)
            )
            item_type = items[0].value_type if items else ValueType("empty")
            for item in items[1:]:
                # Items widen to a common type, such as text and optional text to optional text.
                try:
                    item_type = union(item_type, item.value_type, location)
                except TemplateRuntimeError:
                    item_type = ANY
                    break
            return self.build(ValueType("list", item=item_type), _arguments(items), items, location)
        value = freeze(raw)
        return CompiledExpression(literal_type(value), instruction("constant", value), location, constant=value)

    def reference(self, path: tuple[str, ...], location: str, guards: Guards) -> CompiledExpression:
        """Compile the reference ``(scope, name, *fields)`` to a calculated value, root input or scene input.

        The type is narrowed to present for each prefix of *path* in *guards*. A ``parameters`` reference in
        ``static_parameters`` becomes a constant. A ``scene`` reference reads a slot shared per path and type.
        """
        scope, name, *tail = path
        if scope == "values":
            expression = self.value(name)
            kind = expression.value_type
            getter = instruction("slot", self.indices[name]) if expression.uses_inputs else expression.code
        else:
            root = self.roots.get(scope)
            if root is None:
                raise TemplateRuntimeError(f"Unknown scope '{scope}'.", code="unknown_reference", location=location)
            kind = root.field(name)
            getter = instruction("input", (scope, name))

        if path[:2] in guards:
            kind = kind.present()
        for index, part in enumerate(tail, start=2):
            nullable = kind.nullable
            kind = kind.field(part)
            if nullable:
                kind = kind.optional()
            if path[: index + 1] in guards:
                kind = kind.present()

        if scope == "parameters" and name in self.static_parameters:
            value = self.static_parameters[name]
            for part in tail:
                if value is None:
                    break
                value = record(value)[part]
            if value is not None or kind.nullable:
                value = validate_value(value, kind)
                return CompiledExpression(kind, instruction("constant", value), location, constant=value)

        evaluate = instruction("access", tuple(tail), (getter,)) if tail else getter

        if scope == "scene":
            key = (path, kind)
            if key not in self.reference_indices:
                self.reference_indices[key] = len(self.names) + len(self.reference_values)
                # Keep failures unlocated until the demanding reference attaches its own location.
                self.reference_values.append(
                    CompiledExpression(
                        kind,
                        instruction("validate", kind, (evaluate,)),
                        "$",
                        uses_inputs=True,
                    )
                )
            index = self.reference_indices[key]
            return CompiledExpression(kind, instruction("slot", index), location, uses_inputs=True)
        if scope == "values":
            return self.build(kind, evaluate, (expression,), location)
        return CompiledExpression(
            kind, evaluate, location, uses_context=scope == "context", uses_inputs=scope != "context"
        )

    def call(
        self, name: str, args: Mapping[str, object], location: str, guards: Guards, depth: int
    ) -> CompiledExpression:
        """Compile a call of the operation *name* from ``OPERATIONS``, checking its arguments and result type.

        ``if`` and ``coalesce`` compile their branches as guarded; ``if`` also treats its condition as a guard for
        ``then``. Raises ``TemplateRuntimeError`` for an unknown operation, bad arguments or incompatible types.
        """
        definition = OPERATIONS.get(name)
        if definition is None:
            raise TemplateRuntimeError(f"Unknown operation '{name}'.", code="unknown_operation", location=location)
        allowed = {key for key, _ in definition.arguments}
        optional = {key for key, _ in definition.defaults}
        require_keys(args, allowed, allowed - optional, location)
        if name == "inset_box" and len(set(args) & {"margin", "fraction"}) != 1:
            raise TemplateRuntimeError("inset_box requires exactly one of margin or fraction.", location=location)
        children: list[CompiledExpression] = []
        for key, kind in definition.arguments:
            branch_guards = guards | guard(args.get("condition")) if name == "if" and key == "then" else guards
            children.append(
                self.expression(
                    args.get(key),
                    f"{location}.args.{key}",
                    branch_guards,
                    kind,
                    depth + 1,
                    guarded=name in ("if", "coalesce"),
                )
            )
        result = definition.result
        if name == "if":
            result = union(children[1].value_type, children[2].value_type, location)
            return self.build(
                result,
                instruction("if", children=tuple(child.code for child in children)),
                tuple(children),
                location,
                operation=True,
            )
        if name == "coalesce":
            items = tuple(
                self.expression(raw, f"{location}.args.values[{index}]", guards, depth=depth + 1, guarded=True)
                for index, raw in enumerate(require_list(args["values"], location))
            )
            if not items:
                raise TemplateRuntimeError("coalesce requires at least one value.", location=location)
            result = items[0].value_type
            for child in items[1:]:
                result = union(result, child.value_type, location)
            if any(not item.value_type.nullable for item in items):
                result = result.present()

            return self.build(
                result,
                instruction("coalesce", children=tuple(item.code for item in items)),
                items,
                location,
                operation=True,
            )
        if name in ("add", "sub", "mul", "div", "min", "max", "clamp"):
            operands = (
                tuple(
                    self.expression(raw, f"{location}.args.values[{index}]", guards, depth=depth + 1, guarded=True)
                    for index, raw in enumerate(require_list(args["values"], location))
                )
                if "values" in args
                else tuple(children)
            )
            if not operands or any(
                item.value_type.nullable or item.value_type.name not in ("number", "integer", "length")
                for item in operands
            ):
                raise TemplateRuntimeError(
                    "Arithmetic requires non-null numbers or lengths.", code="type_mismatch", location=location
                )
            dimensions = tuple(item.value_type.name == "length" for item in operands)
            length_count = sum(dimensions)
            if name == "mul":
                valid = length_count <= 1
                length_result = length_count == 1
            elif name == "div":
                valid = dimensions != (False, True)
                length_result = dimensions == (True, False)
            else:
                valid = length_count in (0, len(dimensions))
                length_result = length_count > 0
            if not valid:
                raise TemplateRuntimeError(
                    "Incompatible arithmetic dimensions.", code="type_mismatch", location=location
                )
            result = LENGTH if length_result else NUMBER
        else:
            if name == "is_not_empty":
                kind = children[0].value_type
                if kind.name not in ("null", "string") and kind.item is None:
                    raise TemplateRuntimeError("is_not_empty requires string, list or null.", location=location)
            if name in ("trim_text", "lookup_text") and not children[0].value_type.nullable:
                result = STRING
        metadata: tuple[object, ...] = (name,)
        if name in ("add", "sub", "mul", "div", "min", "max", "clamp"):
            metadata = (
                *metadata,
                dimensions,
                length_result,
                {"add": sum, "mul": prod, "min": min, "max": max}.get(name),
            )
        context_dependent = (
            name in ("box_min_size", "offset_point", "inset_box", "resize_box", "place_box", "vector_arrow")
            or result.name == "length"
            or any(child.value_type.name == "length" for child in children)
        )
        return self.build(
            result,
            instruction("kernel", metadata, tuple(child.code for child in children)),
            tuple(children),
            location,
            context_dependent,
            operation=True,
        )

    def build(
        self,
        kind: ValueType,
        evaluator: Node,
        children: tuple[CompiledExpression, ...],
        location: str,
        context: bool = False,
        *,
        operation: bool = False,
    ) -> CompiledExpression:
        """Wrap *evaluator* as an expression of type *kind* that depends on whatever its *children* depend on.

        *context* marks a dependence on the render context beyond the children's. An expression depending on
        neither context nor inputs is folded to a constant, unless evaluation fails or, under a guard, it is an
        *operation* or has a non-constant child.
        """
        uses_context = context or any(child.uses_context for child in children)
        uses_inputs = any(child.uses_inputs for child in children)
        expression = CompiledExpression(kind, evaluator, location, uses_context=uses_context, uses_inputs=uses_inputs)
        if not uses_context and not uses_inputs:
            # A guarded operation stays executable: folding it could fail on a branch that never runs.
            if self.guard_depth and operation:
                return expression
            if self.guard_depth and any(child.constant is UNSET for child in children):
                return expression
            try:
                value = expression.evaluate(_DUMMY_CONTEXT)
            except TemplateRuntimeError:
                # A guarded arithmetic error is an executable expression, never a compile-time side effect.
                return expression
            return replace(expression, constant=freeze(value))
        return expression
