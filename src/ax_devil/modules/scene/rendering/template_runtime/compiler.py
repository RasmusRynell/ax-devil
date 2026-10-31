"""Validate catalog programs and lower them to immutable, resolved execution plans."""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import cast

from ax_devil.modules.scene.rendering.visibility import OverlayFeature

from .definitions import (
    BOOL,
    CONTEXT,
    MAX_EXPANDED_STEPS,
    MAX_TEMPLATE_DEPTH,
    PRIMITIVES,
    STYLE,
    STYLES,
    ValueType,
)
from .expressions import UNSET, CompiledExpression, instruction
from .program import RenderCatalog, RenderProgram, RenderStep
from .program_compiler import ProgramCompiler
from .structure import TemplateDefinition, guard, parameter_type, require_keys, require_list, require_object
from .validation import validate_value
from .values import TemplateRuntimeError, freeze


def _constant_key(value: object) -> Hashable:
    if isinstance(value, float):
        # Signed zero is observable through number_text, despite comparing equal numerically.
        return (float, value.hex())
    if isinstance(value, Mapping):
        return tuple((key, _constant_key(item)) for key, item in sorted(value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(_constant_key(item) for item in value)
    return (type(value), value)


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
            definition = require_object(raw, f"$.templates.{name}")
            parameters = require_object(definition.get("parameters", {}), f"$.templates.{name}.parameters")
            fields: list[tuple[str, ValueType]] = []
            defaults: dict[str, object] = {}
            for key, raw_parameter in sorted(parameters.items()):
                location = f"$.templates.{name}.parameters.{key}"
                parameter = require_object(raw_parameter, location)
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
                    require_object(self._definitions[name], name),
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
                require_object(self._definitions[name], name),
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
        require_keys(
            definition,
            {"parameters", "values", "steps", "enabled", "description", "label", "feature"},
            {"steps"},
            location,
        )
        feature = _feature(definition, location)
        compiler = ProgramCompiler(self, definition, roots or {"context": CONTEXT}, location)
        compiler.static_parameters = static_parameters or {}
        enabled = compiler.expression(definition.get("enabled", True), f"{location}.enabled", frozenset(), BOOL)
        guards = guard(definition.get("enabled"))
        compiler.base_guards = guards
        values = tuple(compiler.value(name) for name in compiler.names)
        steps: list[RenderStep] = []
        features: set[OverlayFeature] = set()
        draws = False
        expanded, depth = 0, 1
        raw_steps = [] if feature in self._disabled else require_list(definition["steps"], f"{location}.steps")
        for index, raw in enumerate(raw_steps):
            step_location = f"{location}.steps[{index}]"
            step = require_object(raw, step_location)
            is_template = "template" in step
            require_keys(
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
            step_guards = guards | guard(step.get("enabled"))
            if is_template:
                name = cast(str, step["template"])
                target = self.template(name)
                arguments = dict(require_object(step.get("inputs", {}), f"{step_location}.inputs"))
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
