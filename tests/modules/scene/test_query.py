"""The search-box query language selects scene records and never rejects what the user types."""

from __future__ import annotations

import pytest

from ax_devil.modules.scene.query import FieldValue, QueryRecord, TokenKind, compile_query, tokenize


def _record(text: tuple[str, ...] = (), **fields: FieldValue | tuple[FieldValue, ...]) -> QueryRecord:
    """Build a record; keyword names use ``__`` for dots."""
    return QueryRecord(
        fields={
            name.replace("__", "."): value if isinstance(value, tuple) else (value,) for name, value in fields.items()
        },
        text=text,
    )


_CAR = _record(("17", "Car", "moving"), classification__type="Car", classification__conf=0.97, motion__speed=1.5)
_PERSON = _record(
    ("4", "Person", "idle"),
    classification__type=("Person", "Pedestrian"),
    classification__conf=0.6,
    debug__latency=12,
    debug__lost=True,
    **{"debug__track-id": 9, "debug__trk/id": 3},
)


def _selected(query: str) -> list[str]:
    compiled = compile_query(query)
    return [text[1] for record in (_CAR, _PERSON) if compiled.matches(record) for text in (record.text,)]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", ["Car", "Person"]),
        ("   ", ["Car", "Person"]),
        ("car", ["Car"]),
        ("IDLE", ["Person"]),
        ("17", ["Car"]),
        ("classification.type:car", ["Car"]),
        ("type:PERSON", ["Person"]),
        ("type:pedestrian", ["Person"]),
        ("type:ped", []),
        ('type:"Car"', ["Car"]),
        ("conf>0.9", ["Car"]),
        ("conf<0.9", ["Person"]),
        ("conf>=0.97", ["Car"]),
        ("conf<=0.6", ["Person"]),
        ("conf=0.6", ["Person"]),
        ("conf:0.97", ["Car"]),
        ("latency>10", ["Person"]),
        ("lost:true", ["Person"]),
        ("lost>0", []),
        ("type>1", []),
        ("-car", ["Person"]),
        ("-type:car", ["Person"]),
        ("-conf>0.9", ["Person"]),
        ("-nothing:here", ["Car", "Person"]),
        ("debug.track-id:9", ["Person"]),
        ("track-id>8", ["Person"]),
        ("-track-id>8", ["Car"]),
        ("trk/id=3", ["Person"]),
        ("car OR person", ["Car", "Person"]),
        ("type:car OR conf<0.7 speed>1", ["Car"]),
        ("car person", []),
        ("speed:1.5 conf>0.5", ["Car"]),
    ],
)
def test_query_selects_records(query: str, expected: list[str]) -> None:
    assert _selected(query) == expected


def test_path_matches_whole_trailing_segments_only() -> None:
    assert compile_query("classification.conf>0.9").matches(_CAR)
    assert compile_query("conf>0.9").matches(_CAR)
    assert not compile_query("onf>0.9").matches(_CAR)
    assert not compile_query("CONF>0.99").matches(_CAR)  # case is ignored, value is too high
    assert compile_query("CONF>0.9").matches(_CAR)


def test_any_value_of_a_multi_valued_field_may_match() -> None:
    query = compile_query("type:pedestrian")
    assert query.matches(_PERSON)
    assert not compile_query("-type:pedestrian").matches(_PERSON)


@pytest.mark.parametrize(
    "text", ["", "conf:", "conf>", "-", '"', "OR", "OR OR", "a OR", "OR a", "conf>abc", "((", ":x", "a:b:c", '"open']
)
def test_malformed_input_never_raises(text: str) -> None:
    query = compile_query(text)
    query.matches(_CAR)
    query.missing_paths([_CAR])
    assert tokenize(text) is not None


def test_incomplete_terms_are_ignored_while_typing() -> None:
    for text in ("conf:", "conf>", "-", "OR"):
        assert compile_query(text).is_empty
    assert not compile_query("car OR").matches(_PERSON)
    assert compile_query("car OR").matches(_CAR)
    assert compile_query("OR car").matches(_CAR)


def test_comparison_with_non_number_searches_as_text() -> None:
    record = _record(("conf>abc",))
    assert compile_query("conf>abc").matches(record)
    assert not compile_query("conf>abc").matches(_CAR)


def test_integers_compare_exactly_beyond_float_precision() -> None:
    record = _record(debug__big=2**53, debug__huge=10**1000)
    assert compile_query("big:9007199254740992").matches(record)
    assert not compile_query("big:9007199254740993").matches(record)
    assert compile_query("big<9007199254740993").matches(record)
    assert not compile_query("big>=9007199254740993").matches(record)
    assert compile_query("huge>5").matches(record)  # too large for a float, and compared without raising
    assert not compile_query("huge:5").matches(record)


def test_escaped_quote_matches_a_literal_quote_in_a_value() -> None:
    record = _record(('car "alpha"',), debug__message='car "alpha"')
    assert compile_query(r'message:"car \"alpha\""').matches(record)
    assert compile_query(r"\"alpha\"").matches(record)
    assert not compile_query('message:"car alpha"').matches(record)
    assert _kinds(r'message:"car \"alpha\"" x')[2:] == [(r'"car \"alpha\""', TokenKind.VALUE), ("x", TokenKind.TEXT)]


def test_missing_paths_names_fields_no_record_has() -> None:
    query = compile_query("speed>1 -zoom:2 conf>0.5 zoom>3 car")
    assert query.missing_paths([_CAR, _PERSON]) == ("zoom",)
    assert query.missing_paths([]) == ()
    assert query.missing_paths([_record(("empty",))]) == ("speed", "zoom", "conf")
    assert compile_query("debug.latency>1").missing_paths([_CAR]) == ("debug.latency",)
    assert compile_query("car").missing_paths([_CAR]) == ()


def test_file_scope_records_without_attributes_yield_a_note() -> None:
    record = _record(("17", "Car"), id="17", event="merged")
    query = compile_query("conf>0.5")
    assert not query.matches(record)
    assert query.missing_paths([record]) == ("conf",)


def _kinds(text: str) -> list[tuple[str, TokenKind]]:
    return [(text[token.start : token.end], token.kind) for token in tokenize(text)]


def test_tokenize_classifies_each_piece_for_highlighting() -> None:
    kinds = TokenKind
    assert _kinds('-car conf>=0.9 OR type:"a b"') == [
        ("-", kinds.NOT),
        ("car", kinds.TEXT),
        ("conf", kinds.FIELD),
        (">=", kinds.OPERATOR),
        ("0.9", kinds.VALUE),
        ("OR", kinds.OR),
        ("type", kinds.FIELD),
        (":", kinds.OPERATOR),
        ('"a b"', kinds.VALUE),
    ]
    assert _kinds("conf>abc conf: OR car") == [
        ("conf>abc", kinds.INVALID),
        ("conf:", kinds.INVALID),
        ("OR", kinds.INVALID),
        ("car", kinds.TEXT),
    ]
