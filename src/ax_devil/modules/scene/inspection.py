"""World-model hover presentation helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from html import escape
from typing import Any

from ax_devil.modules.scene.model import Attribute, Classification, ColorClassification, Entity, Geometry, Score

# Inherit the text color and size from the QLabel or document showing the HTML. The same HTML is shown in side
# panels and hover cards, so it follows theme and text-size changes there.
_BOLD = "font-weight:600;"
_VALUE = "font-weight:400;"
_MONO = "font-family:monospace;font-weight:400;"
_INDENT = "&nbsp;&nbsp;&nbsp;&nbsp;"
_MAX_PER_ROW = 4


def build_entity_hover_html(entity: Entity, *, include_debug: bool = True) -> str:
    """Build object inspection HTML, optionally leaving debug sections to the display."""
    if entity.latest_observation is None:
        return ""

    lines = [f'<span style="{_MONO}">{escape(str(entity.id))}</span>']
    lines.extend(
        f'<span style="{_BOLD}">{escape(name)}: </span>{html}'
        for name, html in entity_detail_items(entity, continuation_indent=1, include_debug=False)
    )
    if include_debug:
        lines.extend(debug_section_html(entity.latest_observation.debug))
    return "<br/>".join(lines)


def debug_section_html(values: Mapping[Any, Any]) -> tuple[str, ...]:
    """Render every debug field as path-titled key/value sections, without interpreting values."""
    return tuple(
        f'<table width="100%" cellspacing="0" cellpadding="1">'
        f'<tr><td colspan="2"><span style="{_BOLD}">{escape(" / ".join(path) if path else "debug")}</span>'
        f"</td></tr>{''.join(_debug_row(name, value) for name, value in rows)}</table>"
        for path, rows in _debug_sections(values)
    )


def _debug_sections(
    values: Mapping[Any, Any] | Sequence[Any], path: tuple[str, ...] = ()
) -> Iterable[tuple[tuple[str, ...], list[tuple[str, Any]]]]:
    items = values.items() if isinstance(values, Mapping) else enumerate(values)
    rows: list[tuple[str, Any]] = []
    children: list[tuple[str, Mapping[Any, Any] | Sequence[Any]]] = []
    for name, value in items:
        if (isinstance(value, Mapping) and value) or (
            isinstance(value, list | tuple) and any(isinstance(item, Mapping | list | tuple) for item in value)
        ):
            children.append((str(name), value))
        else:
            rows.append((str(name), value))
    if rows:
        yield path, rows
    for name, child in children:
        yield from _debug_sections(child, (*path, name))


def _debug_row(name: str, value: Any) -> str:
    return (
        f'<tr><td width="62%" valign="top">{escape(name)}</td>'
        f'<td valign="top"><span style="{_MONO}">{escape(_debug_value(value))}</span></td></tr>'
    )


def _debug_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return value or '""'
    if isinstance(value, bytes):
        return f"{len(value)} bytes"
    if isinstance(value, list | tuple):
        return f"[{', '.join(_debug_value(item) for item in value)}]"
    if isinstance(value, Enum):
        return _debug_value(value.value)
    return str(value)


def entity_detail_items(
    entity: Entity, *, continuation_indent: int = 0, include_debug: bool = True
) -> list[tuple[str, str]]:
    """Return object-specific fields; debug sections retain all values and source precision."""
    obs = entity.latest_observation
    items = [
        *_entity_hover_items(entity),
        *(_dataclass_items(obs, skip={"debug", "timestamp"}) if obs is not None else ()),
    ]
    rendered = ((name, _render_value(value, continuation_indent=continuation_indent)) for name, value in items)
    result = [(name, html) for name, html in rendered if html]
    if include_debug and obs is not None and obs.debug:
        result.append(("debug", "".join(debug_section_html(obs.debug))))
    return result


def _entity_hover_items(entity: Entity) -> Iterable[tuple[str, Any]]:
    yield from _dataclass_items(entity, skip={"id", "observations", "images"})
    if entity.images:
        yield "images", entity.images


def _dataclass_items(value: object, *, skip: set[str] | None = None) -> Iterable[tuple[str, Any]]:
    if not is_dataclass(value):
        return

    skipped = skip or set()
    for field in fields(value):
        if field.name in skipped:
            continue
        item = getattr(value, field.name)
        if _is_empty(item):
            continue
        yield field.name, item


def _render_value(value: Any, *, continuation_indent: int = 0) -> str:
    if isinstance(value, Enum):
        return _render_value(value.value, continuation_indent=continuation_indent)
    if isinstance(value, Attribute):
        return _render_attribute(value, continuation_indent=continuation_indent)
    if isinstance(value, Classification):
        return _render_classification(value, continuation_indent=continuation_indent)
    if isinstance(value, ColorClassification):
        return _render_color(value)
    if isinstance(value, Score):
        return _render_plain(_format_float(value.value, 2))
    if isinstance(value, Geometry):
        return _render_plain(_format_geometry(value))
    if isinstance(value, datetime):
        return _render_plain(value.isoformat())
    if isinstance(value, bool):
        return _render_plain(str(value).lower())
    if isinstance(value, float):
        return _render_plain(f"{value:.4g}")
    if isinstance(value, bytes):
        return _render_plain(f"{len(value)} bytes")
    if isinstance(value, Mapping):
        if not value:
            return f'<span style="{_VALUE}">{{}}</span>'
        # Nested mappings start below their label so every key lines up with its siblings.
        prefix = "<br/>" if continuation_indent else ""
        return f"{prefix}{_render_mapping(value, continuation_indent=continuation_indent)}"
    if isinstance(value, list | tuple):
        return _render_sequence(value, continuation_indent=continuation_indent)
    if is_dataclass(value):
        return _render_inline_fields(_dataclass_items(value), continuation_indent=continuation_indent)
    return _render_plain(str(value))


def _render_plain(value: str) -> str:
    return f'<span style="{_VALUE}">{escape(value)}</span>'


def _render_classification(classification: Classification, *, continuation_indent: int = 0) -> str:
    chunks = [_render_classification_header(classification)]
    if classification.attributes:
        chunks.append("<br/>")
        chunks.append(
            _render_indented_named_values(_attribute_items(classification.attributes), continuation_indent + 1)
        )
    return "".join(chunks)


def _render_classification_header(classification: Classification) -> str:
    return (
        f'<span style="{_BOLD}">{escape(classification.type)}</span>'
        f'<span style="{_VALUE}"> ({classification.score.value:.2f})</span>'
    )


def _render_classification_candidate_sequence(values: Sequence[Any], *, indent_level: int) -> str:
    classifications = _sort_classifications_by_score(values)
    chunks = [
        _render_classification_candidate(classification, indent_level=indent_level)
        for classification in classifications
    ]
    return "<br/>".join(chunks)


def _render_classification_candidate(classification: Classification, *, indent_level: int) -> str:
    chunks = [
        f'{_indent(indent_level)}<span style="{_VALUE}">- </span>',
        _render_classification_header(classification),
    ]
    if classification.attributes:
        chunks.append("<br/>")
        chunks.append(_render_indented_named_values(_attribute_items(classification.attributes), indent_level + 1))
    return "".join(chunks)


def _render_attribute(attribute: Attribute, *, continuation_indent: int = 0) -> str:
    return (
        f'<span style="{_BOLD}">{escape(attribute.name)}=</span>'
        f"{_render_attribute_value(attribute.value, continuation_indent=continuation_indent)}"
    )


def _render_attribute_value(value: Any, *, continuation_indent: int) -> str:
    if isinstance(value, list | tuple) and all(isinstance(item, Classification) for item in value):
        return f"<br/>{_render_classification_candidate_sequence(value, indent_level=continuation_indent)}"
    return _render_value(value, continuation_indent=continuation_indent)


def _render_sequence(values: Sequence[Any], *, continuation_indent: int = 0) -> str:
    if all(isinstance(item, Classification) for item in values):
        return _render_classification_sequence(values, continuation_indent=continuation_indent)
    if all(isinstance(item, Attribute) for item in values):
        return _render_indented_named_values(_attribute_items(values), continuation_indent)

    ordered_values = _ordered_sequence(values)
    chunks: list[str] = []
    for index, item in enumerate(ordered_values):
        if index > 0 and index % _MAX_PER_ROW == 0:
            chunks.append(_line_break(continuation_indent))
        elif index > 0:
            chunks.append("&nbsp;&nbsp;")
        chunks.append(_render_value(item, continuation_indent=continuation_indent))
    return "".join(chunks)


def _render_mapping(values: Mapping[Any, Any], *, continuation_indent: int = 0) -> str:
    return _render_indented_named_values(
        ((str(name), value) for name, value in values.items()),
        continuation_indent,
    )


def _ordered_sequence(values: Sequence[Any]) -> list[Any]:
    if all(isinstance(item, Classification) for item in values):
        return _sort_classifications_by_score(values)
    if all(isinstance(item, ColorClassification) for item in values):
        return sorted(values, key=lambda item: item.score.value, reverse=True)
    return list(values)


def _render_classification_sequence(values: Sequence[Any], *, continuation_indent: int = 0) -> str:
    classifications = _sort_classifications_by_score(values)
    return _line_break(continuation_indent).join(
        _render_classification(classification, continuation_indent=continuation_indent)
        for classification in classifications
    )


def _sort_classifications_by_score(values: Sequence[Any]) -> list[Classification]:
    return sorted(
        (item for item in values if isinstance(item, Classification)),
        key=lambda item: item.score.value,
        reverse=True,
    )


def _attribute_items(values: Sequence[Any]) -> Iterable[tuple[str, Any]]:
    for value in values:
        if isinstance(value, Attribute) and not _is_empty(value.value):
            yield value.name, value.value


def _render_indented_named_values(items: Iterable[tuple[str, Any]], indent_level: int) -> str:
    chunks: list[str] = []
    for name, value in items:
        if chunks:
            chunks.append("<br/>")
        chunks.append(f'{_indent(indent_level)}<span style="{_BOLD}">{escape(name)}: </span>')
        chunks.append(_render_attribute_value(value, continuation_indent=indent_level + 1))
    return "".join(chunks)


def _render_inline_fields(items: Iterable[tuple[str, Any]], *, continuation_indent: int = 0) -> str:
    chunks: list[str] = []
    for name, value in items:
        if chunks:
            chunks.append("&nbsp;")
        chunks.append(
            f'<span style="{_BOLD}">{escape(name)}=</span>'
            f"{_render_value(value, continuation_indent=continuation_indent)}"
        )
    return "".join(chunks)


def _line_break(indent_level: int) -> str:
    return f"<br/>{_indent(indent_level)}"


def _indent(level: int) -> str:
    if level <= 0:
        return ""
    return f'<span style="{_VALUE}">{_INDENT * level}</span>'


def _render_color(color: ColorClassification) -> str:
    red, green, blue = color.rgb.r, color.rgb.g, color.rgb.b
    return (
        f'<span style="display:inline-block;width:9px;height:9px;border-radius:2px;'
        f'background-color:rgb({red},{green},{blue});"></span>'
        f'<span style="{_VALUE}"> {escape(color.name)}</span>'
        f'<span style="{_VALUE}"> ({color.score.value:.2f})</span>'
    )


def _format_geometry(geometry: Geometry) -> str:
    x, y, width, height = geometry.as_xywh()
    return f"({_format_float(x)}, {_format_float(y)}, {_format_float(width)}, {_format_float(height)})"


def _is_empty(value: Any) -> bool:
    return value is None or value == [] or value == {} or value == "" or value == b""


def _format_float(value: float, decimals: int = 3) -> str:
    return f"{value:.{decimals}f}"
