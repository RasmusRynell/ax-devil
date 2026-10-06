"""Validate catalog programs and lower them to immutable, resolved execution plans."""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import replace
from math import prod
from types import MappingProxyType
from typing import cast

from ax_devil.modules.scene.rendering.visibility import OverlayFeature
from ax_devil.modules.video_player.engine.render_context import RenderContext

from .definitions import (
    ANY,
    BOOL,
    CONTEXT,
    INTEGER,
    LENGTH,
    MAX_DEPTH,
    MAX_EXPANDED_STEPS,
    MAX_TEMPLATE_DEPTH,
    NULL,
    NUMBER,
    OPERATIONS,
    PARAMETER_BOUNDS,
    PRIMITIVES,
    STRING,
    STYLE,
    STYLES,
    TYPES,
    ValueType,
    bounded,
    has_constraints,
    validate_value,
)
from .executable import Node
from .expressions import UNSET, CompiledExpression, instruction
from .program import RenderCatalog, RenderProgram, RenderStep
from .values import TemplateRuntimeError, freeze, record, sequence

TemplateDefinition = Mapping[str, object]
Guards = frozenset[tuple[str, ...]]
_DUMMY_CONTEXT = RenderContext.create(1, 1)


def _constant_key(value: object) -> Hashable:
    if isinstance(value, float):
        # Signed zero is observable through number_text, despite comparing equal numerically.
        return (float, value.hex())
    if isinstance(value, Mapping):
        return tuple((key, _constant_key(item)) for key, item in sorted(value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(_constant_key(item) for item in value)
    return (type(value), value)


def _arguments(children: tuple[CompiledExpression, ...]) -> Node:
    return instruction("tuple", children=tuple(child.code for child in children))


def _record_evaluator(fields: tuple[tuple[str, CompiledExpression], ...]) -> Node:
    return instruction("record", tuple(key for key, _ in fields), tuple(child.code for _, child in fields))


def _object(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise TemplateRuntimeError("Expected an object.", code="invalid_structure", location=location)
    return cast(Mapping[str, object], value)


def _keys(value: Mapping[str, object], allowed: set[str], required: set[str], location: str) -> None:
    unknown, missing = set(value) - allowed, required - set(value)
    if unknown or missing:
        raise TemplateRuntimeError(
            f"Unknown fields {sorted(unknown)}; missing fields {sorted(missing)}.",
            code="invalid_structure",
            location=location,
        )


def _list(value: object, location: str) -> Sequence[object]:
    if not isinstance(value, (tuple, list)):
        raise TemplateRuntimeError("Expected an array.", code="invalid_structure", location=location)
    return value


def compatible(actual: ValueType, expected: ValueType) -> bool:
    """Return whether a value of type *actual* can be used where *expected* is required."""
    if expected.name == "any":
        return True
    if actual.nullable and not expected.nullable:
        return False
    if actual.name == "null":
        return expected.nullable
    if actual.name == expected.name or (actual.name == "integer" and expected.name == "number"):
        return True
    if expected.choices:
        return actual.name == "string" or bool(actual.choices)
    if expected.name == "string" and actual.choices:
        return True
    if expected.fields and actual.fields:
        fields = dict(actual.fields)
        return not set(fields).difference(key for key, _ in expected.fields) and all(
            (key not in fields and kind.nullable) or (key in fields and compatible(fields[key], kind))
            for key, kind in expected.fields
        )
    if expected.item is not None and actual.item is not None:
        return compatible(actual.item, expected.item) or actual.item.name == "empty"
    return False


def _union(left: ValueType, right: ValueType, location: str) -> ValueType:
    if left.name == "null":
        return right.optional()
    if right.name == "null":
        return left.optional()
    nullable = left.nullable or right.nullable
    a, b = left.present(), right.present()
    if a.name == b.name == "record":
        # Record literals join field by field; a field only some of them have is nullable.
        left_fields, right_fields = dict(a.fields), dict(b.fields)
        fields = tuple(
            (key, _union(left_fields.get(key, ValueType("null")), right_fields.get(key, ValueType("null")), location))
            for key in {**left_fields, **right_fields}
        )
        return ValueType("record", fields, nullable=nullable)
    if compatible(a, b):
        return replace(b, nullable=nullable)
    if compatible(b, a):
        return replace(a, nullable=nullable)
    raise TemplateRuntimeError(
        f"Incompatible branch types {left.name} and {right.name}.", code="type_mismatch", location=location
    )


def _guard(value: object) -> Guards:
    if not isinstance(value, Mapping) or value.get("call") not in ("is_present", "is_not_empty"):
        return frozenset()
    args = value.get("args")
    if not isinstance(args, Mapping):
        return frozenset()
    reference = args.get("value")
    if not isinstance(reference, Mapping) or set(reference) != {"ref"}:
        return frozenset()
    path = reference["ref"]
    if not isinstance(path, (tuple, list)) or not all(isinstance(item, str) for item in path):
        return frozenset()
    return frozenset({tuple(path)})


def _coerce(expression: CompiledExpression, expected: ValueType, *, member: bool = False) -> CompiledExpression:
    """Convert *expression* to *expected*, adding the checks its bounds need.

    A bounded number or text is checked by itself, unless it is a *member* of a record or list, whose own check
    covers its scalar parts.
    """
    if expected.name == "any":
        return expression
    if not expression.value_type.nullable:
        expected = expected.present()
    if expression.constant is not UNSET:
        try:
            value = validate_value(expression.constant, expected)
        except TemplateRuntimeError as exc:
            raise TemplateRuntimeError(exc.diagnostic.message, location=expression.location) from exc
        return CompiledExpression(expected, instruction("constant", value), expression.location, constant=value)
    if not compatible(expression.value_type, expected):
        actual_name = f"{expression.value_type.name}{'|null' if expression.value_type.nullable else ''}"
        raise TemplateRuntimeError(
            f"Expected {expected.name}{'|null' if expected.nullable else ''}, got {actual_name}.",
            code="type_mismatch",
            location=expression.location,
        )
    if expression.value_type == expected:
        return expression
    if expression.members is not None and expected.fields:
        supplied = dict(expression.members)
        members = tuple(
            (
                key,
                _coerce(supplied[key], kind, member=True)
                if key in supplied
                else CompiledExpression(kind, instruction("constant", None), expression.location, constant=None),
            )
            for key, kind in expected.fields
        )
        if all(child.constant is not UNSET for _, child in members):
            value = validate_value(MappingProxyType({key: child.constant for key, child in members}), expected)
            return CompiledExpression(expected, instruction("constant", value), expression.location, constant=value)
        callback = _record_evaluator(members)
        if has_constraints(expected):
            callback = instruction("constraint", expected, (callback,))

        return CompiledExpression(
            expected,
            callback,
            expression.location,
            uses_context=expression.uses_context,
            uses_inputs=expression.uses_inputs,
            members=members,
        )
    converter = _conversion(expression.value_type, expected)
    if converter is None:
        if expected.is_scalar and expected.bounds and not member:
            checked = instruction("constraint", expected, (expression.code,))
            return replace(expression, value_type=expected, plan=checked)
        return replace(expression, value_type=expected)
    return CompiledExpression(
        expected,
        instruction("convert", converter, (expression.code,)),
        expression.location,
        uses_context=expression.uses_context,
        uses_inputs=expression.uses_inputs,
    )


def _conversion(actual: ValueType, expected: ValueType) -> tuple[object, ...] | None:
    if actual == expected or expected.name == "any":
        return None
    if expected.fields:
        actual_fields = dict(actual.fields)
        converters = tuple(
            (name, _conversion(actual_fields[name], kind)) for name, kind in expected.fields if name in actual_fields
        )
        transforms = tuple((name, converter) for name, converter in converters if converter is not None)
        missing = tuple(name for name, _ in expected.fields if name not in actual_fields)
        if not transforms and not missing and not has_constraints(expected):
            return None
        return (1, expected, transforms, missing, None, None)
    if expected.item is not None and actual.item is not None:
        converter = _conversion(actual.item, expected.item)
        if converter is None and not has_constraints(expected):
            return None
        return (2, expected, (), (), converter, None)
    if expected.choices and actual.choices != expected.choices:
        return (0, expected, (), (), None, expected)
    return None


def parameter_type(parameter: Mapping[str, object], location: str) -> ValueType:
    """Return the value type a template parameter declaration accepts, checking the declaration's shape.

    A required parameter has no default; every other parameter declares a literal default.
    """
    _keys(
        parameter,
        {"type", "required", "nullable", "default", "description", "label", *PARAMETER_BOUNDS},
        {"type"},
        location,
    )
    kind = TYPES.get(cast(str, parameter["type"]))
    if kind is None:
        raise TemplateRuntimeError(f"Unknown parameter type {parameter['type']}.", location=location)
    kind = bounded(kind, {name: parameter[name] for name in PARAMETER_BOUNDS if name in parameter}, location)
    required, nullable = parameter.get("required", False), parameter.get("nullable", False)
    if not isinstance(required, bool) or not isinstance(nullable, bool):
        raise TemplateRuntimeError("required and nullable must be booleans.", location=location)
    if required and "default" in parameter:
        raise TemplateRuntimeError("Required parameters cannot have defaults.", location=location)
    if not required and "default" not in parameter:
        raise TemplateRuntimeError("Optional parameters require a literal default.", location=location)
    return kind.optional() if nullable else kind


def _feature(definition: Mapping[str, object], location: str) -> OverlayFeature | None:
    raw = definition.get("feature")
    if raw is None:
        return None
    try:
        return OverlayFeature(raw)
    except (TypeError, ValueError) as exc:
        raise TemplateRuntimeError(f"Unknown overlay feature: {raw}.", location=f"{location}.feature") from exc


class RenderProgramCompiler:
    """Own one compilation; share only the resulting immutable plans with consumers.

    Components tagged with a *disabled* feature are left out of the compiled programs, together with inputs only they
    read. Validation of the full document is the caller's job; a compile with features disabled skips their steps.
    """

    def __init__(self, disabled: frozenset[OverlayFeature] = frozenset()) -> None:
        self._disabled = disabled
        self._definitions: Mapping[str, object] = {}
        self._programs: dict[str, RenderProgram] = {}
        self._parameters: dict[str, ValueType] = {}
        self._defaults: dict[str, Mapping[str, object]] = {}
        self._active: set[str] = set()
        self._specialized: dict[tuple[str, Hashable], RenderProgram] = {}

    def compile_catalog(self, templates: Mapping[str, object]) -> RenderCatalog:
        """Validate all template declarations and resolve an acyclic call graph."""
        self._definitions = templates
        self._programs = {}
        self._parameters = {}
        self._defaults = {}
        self._specialized = {}
        for name, raw in templates.items():
            definition = _object(raw, f"$.templates.{name}")
            parameters = _object(definition.get("parameters", {}), f"$.templates.{name}.parameters")
            fields: list[tuple[str, ValueType]] = []
            defaults: dict[str, object] = {}
            for key, raw_parameter in sorted(parameters.items()):
                location = f"$.templates.{name}.parameters.{key}"
                parameter = _object(raw_parameter, location)
                kind = parameter_type(parameter, location)
                if "default" in parameter:
                    try:
                        defaults[key] = validate_value(freeze(parameter["default"]), kind, key)
                    except TemplateRuntimeError as exc:
                        raise TemplateRuntimeError(
                            exc.diagnostic.message, code=exc.diagnostic.code, location=f"{location}.default"
                        ) from exc
                fields.append((key, kind))
            self._parameters[name] = ValueType("parameters", tuple(fields))
            self._defaults[name] = MappingProxyType(defaults)
        for name in templates:
            self.template(name)
        return RenderCatalog(self._programs)

    def template(self, name: str) -> RenderProgram:
        """Resolve a template target once, rejecting recursion before execution."""
        if name in self._active:
            raise TemplateRuntimeError(
                f"Recursive template call: {name}.", code="template_cycle", location=f"$.templates.{name}"
            )
        if name not in self._definitions:
            raise TemplateRuntimeError(f"Unknown template: {name}.", code="unknown_template")
        if name not in self._programs:
            if len(self._active) >= MAX_TEMPLATE_DEPTH:
                raise TemplateRuntimeError("Template nesting limit exceeded.", code="execution_limit")
            self._active.add(name)
            try:
                self._programs[name] = self.compile_program(
                    _object(self._definitions[name], name),
                    roots={"parameters": self._parameters[name], "context": CONTEXT},
                    location=f"$.templates.{name}",
                    defaults=self._defaults[name],
                )
            finally:
                self._active.remove(name)
        return self._programs[name]

    def specialize(self, name: str, parameters: Mapping[str, object]) -> RenderProgram:
        """Fold call-site constants while retaining the existing template execution model."""
        if not parameters:
            return self.template(name)
        key = (name, _constant_key(parameters))
        if key not in self._specialized:
            self._specialized[key] = self.compile_program(
                _object(self._definitions[name], name),
                roots={"parameters": self._parameters[name], "context": CONTEXT},
                location=f"$.templates.{name}",
                defaults=self._defaults[name],
                static_parameters=parameters,
            )
        return self._specialized[key]

    def compile_program(
        self,
        definition: TemplateDefinition,
        *,
        roots: Mapping[str, ValueType] | None = None,
        location: str = "$",
        defaults: Mapping[str, object] | None = None,
        static_parameters: Mapping[str, object] | None = None,
    ) -> RenderProgram:
        """Compile one program with explicit root bindings and ordered steps."""
        _keys(
            definition,
            {"parameters", "values", "steps", "enabled", "description", "label", "feature"},
            {"steps"},
            location,
        )
        feature = _feature(definition, location)
        compiler = _ProgramCompiler(self, definition, roots or {"context": CONTEXT}, location)
        compiler.static_parameters = static_parameters or {}
        enabled = compiler.expression(definition.get("enabled", True), f"{location}.enabled", frozenset(), BOOL)
        guards = _guard(definition.get("enabled"))
        compiler.base_guards = guards
        values = tuple(compiler.value(name) for name in compiler.names)
        steps: list[RenderStep] = []
        features: set[OverlayFeature] = set()
        draws = False
        expanded, depth = 0, 1
        raw_steps = [] if feature in self._disabled else _list(definition["steps"], f"{location}.steps")
        for index, raw in enumerate(raw_steps):
            step_location = f"{location}.steps[{index}]"
            step = _object(raw, step_location)
            is_template = "template" in step
            _keys(
                step,
                {"template", "inputs", "enabled", "visible", "label", "feature"}
                if is_template
                else {"primitive", "fields", "style", "enabled", "visible", "label", "feature"},
                {"template"} if is_template else {"primitive", "fields"},
                step_location,
            )
            step_feature = _feature(step, step_location)
            if step_feature in self._disabled:
                continue
            if self._disabled and is_template and not self.template(cast(str, step["template"])).draws:
                # Drop empty invocations before lowering their inputs or conditions.
                continue
            visible = step.get("visible", True)
            if not isinstance(visible, bool):
                raise TemplateRuntimeError("visible must be true or false.", location=f"{step_location}.visible")
            condition = compiler.expression(step.get("enabled", True), f"{step_location}.enabled", guards, BOOL)
            step_guards = guards | _guard(step.get("enabled"))
            if is_template:
                name = cast(str, step["template"])
                target = self.template(name)
                arguments = dict(_object(step.get("inputs", {}), f"{step_location}.inputs"))
                required = {key for key, _ in self._parameters[name].fields} - set(self._defaults[name])
                missing = required - set(arguments)
                if missing:
                    raise TemplateRuntimeError(
                        f"Missing required template inputs: {sorted(missing)}.", location=step_location
                    )
                for key, value in self._defaults[name].items():
                    arguments.setdefault(key, {"literal": value})
                # Each input is checked on its own first, so an error is located at that input.
                constants = {}
                for key, raw_argument in arguments.items():
                    argument = compiler.expression(
                        raw_argument,
                        f"{step_location}.inputs.{key}",
                        step_guards,
                        self._parameters[name].field(key),
                        guarded=condition.constant is not True or enabled.constant is not True,
                    )
                    if argument.constant is not UNSET:
                        constants[key] = argument.constant
                target = self.specialize(name, constants)
                argument_type = self._parameters[name]
                if self._disabled:
                    if not target.draws:
                        continue
                    demanded = target.demanded_parameters()
                    arguments = {key: value for key, value in arguments.items() if key in demanded}
                    argument_type = replace(
                        argument_type,
                        fields=tuple((key, kind) for key, kind in argument_type.fields if key in demanded),
                    )
                fields = compiler.expression(
                    arguments,
                    f"{step_location}.inputs",
                    step_guards,
                    argument_type,
                    guarded=condition.constant is not True or enabled.constant is not True,
                )
                if visible:
                    steps.append(RenderStep(step_location, condition, fields, target=target))
                    if condition.constant is not False and target.draws:
                        draws = True
                        features.update(target.features)
                        features.update({step_feature} if step_feature else ())
                expanded = min(MAX_EXPANDED_STEPS + 1, expanded + target.expanded_steps + 1)
                depth = max(depth, 1 + target.depth)
            else:
                primitive = cast(str, step["primitive"])
                fields_type = PRIMITIVES.get(primitive)
                if fields_type is None:
                    raise TemplateRuntimeError(f"Unknown primitive: {primitive}.", location=step_location)
                fields = compiler.expression(
                    step["fields"],
                    f"{step_location}.fields",
                    step_guards,
                    fields_type,
                    guarded=condition.constant is not True or enabled.constant is not True,
                )
                raw_style = compiler.expression(
                    step.get("style", {}),
                    f"{step_location}.style",
                    step_guards,
                    STYLES.get(primitive, STYLE),
                    guarded=condition.constant is not True or enabled.constant is not True,
                )
                style = CompiledExpression(
                    ValueType("paint_style"),
                    instruction("style", primitive, (raw_style.code,)),
                    raw_style.location,
                    uses_context=True,
                    uses_inputs=raw_style.uses_inputs,
                )
                if visible:
                    steps.append(RenderStep(step_location, condition, fields, style, primitive))
                    if condition.constant is not False:
                        draws = True
                        features.update({step_feature} if step_feature else ())
                expanded = min(MAX_EXPANDED_STEPS + 1, expanded + 1)
            if expanded > MAX_EXPANDED_STEPS or depth > MAX_TEMPLATE_DEPTH:
                raise TemplateRuntimeError(
                    "Worst-case expanded execution limit exceeded.", code="execution_limit", location=step_location
                )
        if self._disabled and not any(step.enabled.constant is not False for step in steps):
            # No retained drawing depends on a recipe/template guard anymore.
            enabled = CompiledExpression(BOOL, instruction("constant", True), f"{location}.enabled", constant=True)
        draws = draws and enabled.constant is not False
        if draws and feature is not None:
            features.add(feature)
        parameter_type = (roots or {}).get("parameters", ValueType("parameters"))
        return RenderProgram(
            (*values, *compiler.reference_values),
            tuple(steps),
            enabled,
            defaults or MappingProxyType({}),
            parameter_type,
            expanded,
            depth,
            MappingProxyType(dict(roots or {"context": CONTEXT})),
            frozenset(features) if draws else frozenset(),
            draws,
        )


class _ProgramCompiler:
    def __init__(
        self,
        owner: RenderProgramCompiler,
        definition: TemplateDefinition,
        roots: Mapping[str, ValueType],
        location: str,
    ) -> None:
        self.owner, self.roots, self.location = owner, roots, location
        self.raw_values = _object(definition.get("values", {}), f"{location}.values")
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
        if depth > MAX_DEPTH:
            raise TemplateRuntimeError("Expression depth limit exceeded.", code="execution_limit", location=location)
        self.guard_depth += int(guarded)
        try:
            expression = self._expression(raw, location, guards, depth)
            return _coerce(expression, expected) if expected is not None else expression
        except TemplateRuntimeError as exc:
            if exc.diagnostic.location != "$":
                raise
            raise TemplateRuntimeError(exc.diagnostic.message, code=exc.diagnostic.code, location=location) from exc
        finally:
            self.guard_depth -= int(guarded)

    def _expression(self, raw: object, location: str, guards: Guards, depth: int) -> CompiledExpression:
        if isinstance(raw, Mapping):
            if "literal" in raw:
                _keys(raw, {"literal"}, {"literal"}, location)
                value = freeze(raw["literal"])
                kind = self._literal_type(value)
                return CompiledExpression(kind, instruction("constant", value), location, constant=value)
            if "ref" in raw:
                _keys(raw, {"ref"}, {"ref"}, location)
                path = tuple(_list(raw["ref"], location))
                if len(path) < 2 or not all(isinstance(part, str) and part for part in path):
                    raise TemplateRuntimeError(
                        "Reference must contain a scope and exact field names.", location=location
                    )
                return self.reference(cast(tuple[str, ...], path), location, guards)
            if "call" in raw:
                _keys(raw, {"call", "args"}, {"call", "args"}, location)
                return self.call(
                    cast(str, raw["call"]), _object(raw["args"], f"{location}.args"), location, guards, depth
                )
            fields = tuple(
                (key, self.expression(value, f"{location}.{key}", guards, depth=depth + 1))
                for key, value in sorted(raw.items())
            )
            kind = ValueType("record", tuple((key, value.value_type) for key, value in fields))
            expression = self.build(
                kind,
                _record_evaluator(fields),
                tuple(value for _, value in fields),
                location,
            )
            expression = replace(expression, members=fields)
            return _coerce(expression, LENGTH) if {key for key, _ in fields} == {"value", "unit"} else expression
        if isinstance(raw, (tuple, list)):
            items = tuple(
                self.expression(value, f"{location}[{index}]", guards, depth=depth + 1)
                for index, value in enumerate(raw)
            )
            item_type = items[0].value_type if items else ValueType("empty")
            for item in items[1:]:
                # Items widen to a common type, such as text and optional text to optional text.
                try:
                    item_type = _union(item_type, item.value_type, location)
                except TemplateRuntimeError:
                    item_type = ANY
                    break
            return self.build(ValueType("list", item=item_type), _arguments(items), items, location)
        value = freeze(raw)
        return CompiledExpression(self._literal_type(value), instruction("constant", value), location, constant=value)

    @staticmethod
    def _literal_type(value: object) -> ValueType:
        if value is None:
            return NULL
        if isinstance(value, bool):
            return BOOL
        if isinstance(value, int):
            return INTEGER
        if isinstance(value, float):
            return NUMBER
        if isinstance(value, str):
            return STRING
        if isinstance(value, Mapping):
            return ValueType(
                "record", tuple((key, _ProgramCompiler._literal_type(item)) for key, item in sorted(value.items()))
            )
        items = sequence(value)
        kind = _ProgramCompiler._literal_type(items[0]) if items else ValueType("empty")
        for item in items[1:]:
            # Items widen to a common type, such as integers and numbers to numbers; conflicting items are any.
            try:
                kind = _union(kind, _ProgramCompiler._literal_type(item), "$")
            except TemplateRuntimeError:
                kind = ANY
                break
        return ValueType("list", item=kind)

    def reference(self, path: tuple[str, ...], location: str, guards: Guards) -> CompiledExpression:
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
        definition = OPERATIONS.get(name)
        if definition is None:
            raise TemplateRuntimeError(f"Unknown operation '{name}'.", code="unknown_operation", location=location)
        allowed = {key for key, _ in definition.arguments}
        optional = {key for key, _ in definition.defaults}
        _keys(args, allowed, allowed - optional, location)
        if name == "inset_box" and len(set(args) & {"margin", "fraction"}) != 1:
            raise TemplateRuntimeError("inset_box requires exactly one of margin or fraction.", location=location)
        children: list[CompiledExpression] = []
        for key, kind in definition.arguments:
            branch_guards = guards | _guard(args.get("condition")) if name == "if" and key == "then" else guards
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
            result = _union(children[1].value_type, children[2].value_type, location)
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
                for index, raw in enumerate(_list(args["values"], location))
            )
            if not items:
                raise TemplateRuntimeError("coalesce requires at least one value.", location=location)
            result = items[0].value_type
            for child in items[1:]:
                result = _union(result, child.value_type, location)
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
                    for index, raw in enumerate(_list(args["values"], location))
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
