"""Lowering of kernel operations: units, arithmetic, boxes, arrows, text and color tables."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ax_devil.modules.video_player.engine.drawing import ANCHORS

from . import colormaps, kernels
from .definitions import ValueType
from .lowering_expressions import ExpressionBuilder, Scope
from .lowering_values import Value
from .lowering_writer import FALSE, MISSING, TRUE
from .plan import Node


class OperationBuilder(ExpressionBuilder):
    """Lowers kernel calls and the geometry they are built from."""

    def pixels(self, length: Value, active: str, location: str) -> Value:
        assert length.fields is not None
        amount, unit = length.fields["value"], length.fields["unit"]
        varying = amount.varying or unit.varying
        if unit.const == "px":
            return Value(amount.code, varying=amount.varying)
        if unit.const in ("image_width", "image_height", "image_min"):
            scale = {
                "image_width": "context.width",
                "image_height": "context.height",
                "image_min": "context.scale_factor",
            }
            return self.finite(f"{amount.code} * {scale[unit.const]}", varying, active, location)
        return self.finite(f"{amount.code} * unit_scale({unit.code}, context)", varying, active, location)

    def number(self, value: Value, dimension: bool, active: str, location: str) -> Value:
        return self.pixels(value, active, location) if dimension else value

    def nonnegative(self, value: Value, active: str, location: str) -> Value:
        self.require(f"{value.code} >= 0", value.varying, "Value must be nonnegative.", active, location)
        return value

    def point(self, x: Value, y: Value) -> Value:
        return Value(fields={"x": x, "y": y}, varying=x.varying or y.varying)

    def box(self, x: Value, y: Value, w: Value, h: Value) -> Value:
        return Value(fields={"x": x, "y": y, "w": w, "h": h}, varying=any(v.varying for v in (x, y, w, h)))

    def length(self, value: Value) -> Value:
        return Value(fields={"value": value, "unit": self.constant("px")}, varying=value.varying)

    def anchor(self, value: Value) -> tuple[Value, Value]:
        if value.const is not MISSING and value.const in ANCHORS:
            ax, ay = ANCHORS[value.const]
            return Value(repr(ax)), Value(repr(ay))
        factors = self.assign(f"anchor_factors({value.code})")
        return Value(f"{factors}[0]", varying=value.varying), Value(f"{factors}[1]", varying=value.varying)

    def node_kernel(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
        args = [self.expression(child, scope, active, location) for child in node.children]
        name = node.operand[0]
        varying = any(arg.varying for arg in args)
        if name == "is_present":
            return self.boolean(args[0].present, args[0].varying)
        if name == "is_not_empty":
            value = args[0]
            if value.items is not None:
                base = f"({value.size}) > 0" if value.size is not None else repr(bool(value.items))
            else:
                base = f"nonempty({value.code})"
            return self.boolean(
                self.both(value.present, self.assign(base) if base not in (TRUE, FALSE) else base), varying
            )
        if name == "gt":
            return self.boolean(f"({args[0].code}) > ({args[1].code})", varying)
        if name in ("add", "sub", "mul", "div", "min", "max", "clamp"):
            return self.arithmetic(node.operand, args, active, location)
        if name == "has_area":
            box = args[0]
            return self.boolean(f"({self.field(box, 'w').code} > 0) & ({self.field(box, 'h').code} > 0)", varying)
        if name == "offset_point":
            point = args[0]
            dx, dy = self.pixels(args[1], active, location), self.pixels(args[2], active, location)
            return self.point(
                self.finite(f"{self.field(point, 'x').code} + {dx.code} / context.width", varying, active, location),
                self.finite(f"{self.field(point, 'y').code} + {dy.code} / context.height", varying, active, location),
            )
        if name in ("box_anchor", "box_min_size", "resize_box", "scale_box", "place_box", "inset_box"):
            return self.box_operation(name, args, varying, active, location)
        if name == "vector_arrow":
            return self.vector_arrow(args, varying, active, location)
        if name == "trim_text":
            text, count, delimiter, suffix, elide = args
            self.require(
                f"{count.code} >= 0",
                count.varying,
                "Text length must be nonnegative.",
                self.both(active, text.present),
                location,
            )
            code = f"trim_text({text.code}, {count.code}, {delimiter.code}, {suffix.code}, {elide.code})"
            return Value(self.assign(code), present=text.present, varying=varying)
        if name == "lookup_text":
            text, mapping, default = args
            if mapping.const is not MISSING:
                code = f"per_row({self.bind(kernels.text_table(mapping.const).get)}, {text.code}, {default.code})"
            else:
                code = f"lookup_rows({text.code}, {self.rows(mapping)}, {default.code})"
            return Value(self.assign(code), present=text.present, varying=varying)
        if name == "number_text":
            value, precision, hide = args
            self.require(
                f"(0 <= {precision.code}) & ({precision.code} <= 100)",
                precision.varying,
                "Precision must be in [0,100].",
                self.both(active, value.present),
                location,
            )
            result = self.assign(f"number_text({value.code}, {precision.code}, {hide.code})")
            return Value(
                self.assign(f"{result}[1]"),
                present=self.both(value.present, self.assign(f"{result}[0]")),
                varying=varying,
            )
        if name == "percent_text":
            value, precision = args
            self.require(
                f"(0 <= {precision.code}) & ({precision.code} <= 100)",
                precision.varying,
                "Precision must be in [0,100].",
                self.both(active, value.present),
                location,
            )
            code = f"percent_text({value.code}, {precision.code})"
            return Value(self.assign(code), present=value.present, varying=varying)
        if name == "color_ramp":
            value, stops = args
            if stops.const is not MISSING:
                positions, colors = colormaps.ramp_table(stops.const)
                channels = self.assign(f"ramp({value.code}, {self.bind(positions)}, {self.bind(colors)})")
            else:
                channels = self.assign(f"ramp_rows({value.code}, {self.rows(stops)})")
            items = tuple(Value(self.assign(f"{channels}[{index}]"), varying=varying) for index in range(3))
            return Value(items=items, varying=varying)
        if name == "pick_color":
            text, palette = args
            if palette.const is not MISSING:
                channels = self.assign(f"pick_color({text.code}, {self.bind(colormaps.palette_table(palette.const))})")
            else:
                channels = self.assign(f"pick_color_rows({text.code}, {self.rows(palette)})")
            items = tuple(Value(self.assign(f"{channels}[{index}]"), varying=varying) for index in range(3))
            return Value(items=items, varying=varying)
        raise ValueError(f"Unknown operation: {name}")

    def arithmetic(self, operand: tuple[Any, ...], args: list[Value], active: str, location: str) -> Value:
        name, dimensions, result_length = operand[0], operand[1], operand[2]
        if name in ("add", "mul", "min", "max"):
            items = args[0].items
            assert items is not None
            operands = list(items)
        else:
            operands = list(args)
        numbers = [self.number(arg, dimension, active, location) for arg, dimension in zip(operands, dimensions)]
        codes = [number.code for number in numbers]
        varying = any(number.varying for number in numbers)
        if name == "sub":
            code = f"({codes[0]}) - ({codes[1]})"
        elif name == "div":
            self.require(f"({codes[1]}) != 0", numbers[1].varying, "Cannot divide by zero.", active, location)
            code = f"divide({codes[0]}, {codes[1]})"
        elif name == "clamp":
            self.require(f"({codes[1]}) <= ({codes[2]})", varying, "Clamp minimum exceeds maximum.", active, location)
            code = f"maximum({codes[1]}, minimum({codes[2]}, {codes[0]}))"
        elif name == "add":
            code = " + ".join(("0", *(f"({item})" for item in codes)))
        elif name == "mul":
            code = " * ".join(("1", *(f"({item})" for item in codes)))
        else:
            code = f"{'minimum' if name == 'min' else 'maximum'}({', '.join(codes)})"
        result = self.finite(code, varying, active, location)
        return self.length(result) if result_length else result

    def box_operation(self, name: str, args: list[Value], varying: bool, active: str, location: str) -> Value:
        box = args[0]
        x, y, w, h = (self.field(box, key).code for key in ("x", "y", "w", "h"))
        if name == "box_min_size":
            return self.length(
                self.finite(f"minimum({w} * context.width, {h} * context.height)", varying, active, location)
            )
        if name == "inset_box":
            margin, fraction = args[1:]
            self.require(
                f"({margin.present}) != ({fraction.present})",
                margin.varying or fraction.varying,
                "inset_box requires exactly one of margin or fraction.",
                active,
                location,
            )
            by_fraction = self.both(active, fraction.present)
            by_margin = self.both(active, self.negate(fraction.present))
            dx = dy = "0.0"
            if by_fraction != FALSE:
                self.require(
                    f"(0 <= {fraction.code}) & ({fraction.code} <= 0.5)",
                    fraction.varying,
                    "Inset fraction must be in [0,0.5].",
                    by_fraction,
                    location,
                )
                dx, dy = self.assign(f"{w} * {fraction.code}"), self.assign(f"{h} * {fraction.code}")
            if by_margin != FALSE:
                amount = self.nonnegative(self.pixels(margin, by_margin, location), by_margin, location)
                mx = self.assign(f"minimum({w} / 2, {amount.code} / context.width)")
                my = self.assign(f"minimum({h} / 2, {amount.code} / context.height)")
                dx, dy = self.pick(fraction.present, dx, mx), self.pick(fraction.present, dy, my)
            return self.numeric_box(
                (f"{x} + {dx}", f"{y} + {dy}", f"maximum(0, {w} - 2 * {dx})", f"maximum(0, {h} - 2 * {dy})"),
                varying,
                active,
                location,
            )
        ax, ay = self.anchor(args[1 if name == "box_anchor" else 3])
        varying = varying or ax.varying
        if name == "box_anchor":
            return self.point(
                self.finite(f"{x} + {w} * {ax.code}", varying, active, location),
                self.finite(f"{y} + {h} * {ay.code}", varying, active, location),
            )
        if name == "scale_box":
            width = self.assign(f"{w} * {self.nonnegative(args[1], active, location).code}")
            height = self.assign(f"{h} * {self.nonnegative(args[2], active, location).code}")
        else:
            sizes = []
            for argument, original, extent in ((args[1], w, "context.width"), (args[2], h, "context.height")):
                rows = self.both(active, argument.present) if name == "resize_box" else active
                if rows == FALSE:
                    sizes.append(original)
                    continue
                amount = self.nonnegative(self.pixels(argument, rows, location), rows, location)
                scaled = self.assign(f"{amount.code} / {extent}")
                sizes.append(self.pick(argument.present, scaled, original) if name == "resize_box" else scaled)
            width, height = sizes
        left = f"{x} + ({w} - {width}) * {ax.code}"
        top = f"{y} + ({h} - {height}) * {ay.code}"
        if name == "place_box":
            margin = self.nonnegative(self.pixels(args[4], active, location), active, location)
            left = f"{left} + (1 - 2 * {ax.code}) * {margin.code} / context.width"
            top = f"{top} + (1 - 2 * {ay.code}) * {margin.code} / context.height"
        return self.numeric_box((left, top, width, height), varying, active, location)

    def numeric_box(self, codes: tuple[str, str, str, str], varying: bool, active: str, location: str) -> Value:
        x, y, w, h = (self.finite(code, varying, active, location) for code in codes)
        return self.box(x, y, w, h)

    def vector_arrow(self, args: list[Value], varying: bool, active: str, location: str) -> Value:
        origin, vector, gain, maximum, head, half, threshold = args
        gain = self.nonnegative(gain, active, location)
        limits = [
            self.nonnegative(self.pixels(length, active, location), active, location)
            for length in (maximum, head, half)
        ]
        threshold = self.nonnegative(threshold, active, location)
        ox, oy = self.field(origin, "x").code, self.field(origin, "y").code
        vx, vy = self.field(vector, "vx").code, self.field(vector, "vy").code
        result = self.assign(
            f"arrow({ox}, {oy}, {vx}, {vy}, {gain.code}, {limits[0].code}, {limits[1].code}, {limits[2].code}, "
            f"{threshold.code}, context.width, context.height)"
        )
        drawn = self.both(active, vector.present)
        self.require(f"{result}[1]", varying, "Numeric result must be finite.", drawn, location)
        parts = [Value(self.assign(f"{result}[{index}]"), varying=varying) for index in range(2, 8)]
        end = self.point(parts[0], parts[1])
        head_points = Value(
            items=(end, self.point(parts[2], parts[3]), self.point(parts[4], parts[5])),
            varying=varying,
            size=self.assign(f"np.where({result}[8], 3, 0)"),
        )
        present = self.both(vector.present, self.assign(f"{result}[0]"))
        start = replace(origin, present=TRUE)
        return Value(fields={"start": start, "end": end, "head_points": head_points}, present=present, varying=varying)
