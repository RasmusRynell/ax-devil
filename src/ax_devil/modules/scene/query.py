"""Search-box query language for filtering scene records.

Syntax: bare words search free text; ``path:value`` and ``path=value`` compare equal; ``path>n``, ``<``, ``>=`` and
``<=`` compare numbers; a leading ``-`` negates a term; ``a OR b`` joins adjacent terms; everything else is ANDed.
Values may be quoted (``class:"traffic light"``). Compiling never fails: incomplete terms are ignored and
malformed comparisons search as plain text.
"""

from __future__ import annotations

import math
import operator
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, auto

FieldValue = str | int | float | bool

_TERM = re.compile(r"([A-Za-z_][\w./-]*)(>=|<=|:|=|>|<)")
_COMPARATORS: Mapping[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
}


@dataclass(frozen=True, slots=True)
class QueryRecord:
    """One searchable thing: dotted field paths with their values, plus the strings free text searches."""

    fields: Mapping[str, tuple[FieldValue, ...]]
    text: tuple[str, ...]


class TokenKind(Enum):
    """How a span of query text is used, for syntax highlighting."""

    TEXT = auto()
    FIELD = auto()
    OPERATOR = auto()
    VALUE = auto()
    OR = auto()
    NOT = auto()
    INVALID = auto()


@dataclass(frozen=True, slots=True)
class QueryToken:
    """A classified span ``text[start:end]`` of the query."""

    start: int
    end: int
    kind: TokenKind


def _number(value: FieldValue) -> float | None:
    """Return ``value`` as a finite number, or None for strings, booleans and non-finite numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    return float(value) if math.isfinite(value) else None


@dataclass(frozen=True, slots=True)
class _Term:
    """One condition: free text when ``path`` is None, otherwise ``path op value``."""

    path: str | None
    op: str
    value: str
    negated: bool

    def matches(self, record: QueryRecord) -> bool:
        needle = self.value.lower()
        if self.path is None:
            hit = any(needle in text.lower() for text in record.text)
        else:
            number = _number(self.value)
            hit = any(
                _compare(self.op, value, needle, number)
                for key, values in record.fields.items()
                if _path_matches(self.path, key)
                for value in values
            )
        return hit != self.negated


def _path_matches(path: str, key: str) -> bool:
    """A path names a key when equal to it or to a trailing run of its dotted parts, ignoring case."""
    path, key = path.lower(), key.lower()
    return key == path or key.endswith(f".{path}")


def _compare(op: str, value: FieldValue, needle: str, number: float | None) -> bool:
    """Compare a record value with the query value; ``:``/``=`` fall back to case-insensitive text."""
    value_number = None if isinstance(value, str) else _number(value)
    if op in _COMPARATORS:
        return number is not None and value_number is not None and _COMPARATORS[op](value_number, number)
    if number is not None and value_number is not None:
        return value_number == number
    return str(value).lower() == needle


@dataclass(frozen=True, slots=True)
class Query:
    """A compiled query: AND of groups, each group an OR of terms."""

    groups: tuple[tuple[_Term, ...], ...]

    @property
    def is_empty(self) -> bool:
        """True when the query has no usable terms and so matches every record."""
        return not self.groups

    def matches(self, record: QueryRecord) -> bool:
        """Return True when every group has at least one term matching ``record``."""
        return all(any(term.matches(record) for term in group) for group in self.groups)

    def missing_paths(self, records: Iterable[QueryRecord]) -> tuple[str, ...]:
        """Return the field paths used by the query that no record has, in query order without repeats."""
        keys = [key for record in records for key in record.fields]
        if not keys:
            return ()
        paths = (term.path for group in self.groups for term in group if term.path is not None)
        missing = (path for path in paths if not any(_path_matches(path, key) for key in keys))
        return tuple(dict.fromkeys(missing))


def _words(text: str) -> Iterable[tuple[int, int]]:
    """Yield the (start, end) of each whitespace-separated word; whitespace inside double quotes is kept."""
    start: int | None = None
    quoted = False
    for index, char in enumerate(text):
        if char.isspace() and not quoted:
            if start is not None:
                yield start, index
                start = None
            continue
        if start is None:
            start = index
        if char == '"':
            quoted = not quoted
    if start is not None:
        yield start, len(text)


def _parse_word(text: str, start: int, end: int) -> tuple[list[QueryToken], _Term | None]:
    """Classify one word; the term is None when the word is incomplete and should be ignored."""
    tokens: list[QueryToken] = []
    negated = end - start > 1 and text[start] == "-"
    if negated:
        tokens.append(QueryToken(start, start + 1, TokenKind.NOT))
        start += 1
    rest = text[start:end]
    found = _TERM.match(rest)
    if found is None:
        value = rest.replace('"', "")
        if not value or value == "-":
            return [*tokens, QueryToken(start, end, TokenKind.INVALID)], None
        return [*tokens, QueryToken(start, end, TokenKind.TEXT)], _Term(None, ":", value, negated)
    path, op = found.group(1), found.group(2)
    value = rest[found.end() :].replace('"', "")
    if not value:
        return [*tokens, QueryToken(start, end, TokenKind.INVALID)], None
    if op in _COMPARATORS and _number(value) is None:
        return [*tokens, QueryToken(start, end, TokenKind.INVALID)], _Term(None, ":", rest.replace('"', ""), negated)
    field_end, op_end = start + len(path), start + found.end()
    tokens += [
        QueryToken(start, field_end, TokenKind.FIELD),
        QueryToken(field_end, op_end, TokenKind.OPERATOR),
        QueryToken(op_end, end, TokenKind.VALUE),
    ]
    return tokens, _Term(path, op, value, negated)


def _parse(text: str) -> tuple[tuple[QueryToken, ...], Query]:
    """Parse ``text`` once into highlighting tokens and the compiled query."""
    items: list[tuple[list[QueryToken], _Term | None, bool]] = []
    for start, end in _words(text):
        is_or = text[start:end] == "OR"
        tokens, term = ([QueryToken(start, end, TokenKind.OR)], None) if is_or else _parse_word(text, start, end)
        items.append((tokens, term, is_or))

    tokens_out: list[QueryToken] = []
    groups: list[list[_Term]] = []
    join = False
    for index, (tokens, term, is_or) in enumerate(items):
        if is_or:
            # OR only joins when a term stands on both sides.
            joins = 0 < index < len(items) - 1 and items[index - 1][1] is not None and items[index + 1][1] is not None
            if joins:
                join = True
            else:
                tokens = [QueryToken(tokens[0].start, tokens[0].end, TokenKind.INVALID)]
        elif term is not None:
            if join and groups:
                groups[-1].append(term)
            else:
                groups.append([term])
            join = False
        tokens_out.extend(tokens)
    return tuple(tokens_out), Query(tuple(tuple(group) for group in groups))


def tokenize(text: str) -> tuple[QueryToken, ...]:
    """Classify the spans of ``text`` for syntax highlighting; whitespace is not covered by any token."""
    return _parse(text)[0]


def compile_query(text: str) -> Query:
    """Compile ``text`` into a Query. Never raises: unusable pieces are ignored or searched as plain text."""
    return _parse(text)[1]
