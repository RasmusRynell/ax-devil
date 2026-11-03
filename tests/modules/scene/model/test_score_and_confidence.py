"""Score and Confidence are separate values; the primary classification ranks by them."""

from __future__ import annotations

import pickle

import pytest

from ax_devil.modules.scene.model import BoundingBox, Classification, Confidence, Observation, Score


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_score_and_confidence_reject_non_finite_values(bad: float) -> None:
    for kind in (Score, Confidence):
        with pytest.raises(ValueError):
            kind(bad)


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_score_and_confidence_accept_values_outside_the_unit_range_without_a_fraction(value: float) -> None:
    assert (Score(value).fraction, Confidence(value).fraction) == (None, None)


def test_score_and_confidence_show_plain_numbers() -> None:
    assert (Score(0.874).text, Score(0.874).fraction) == ("0.87", 0.874)
    assert (Score(2.5).text, Score(2.5).fraction) == ("2.50", None)
    assert (Confidence(0.874).text, Confidence(0.874).fraction) == ("0.87", 0.874)


def test_score_and_confidence_survive_pickling() -> None:
    for value in (Score(2.5), Confidence(0.25)):
        assert pickle.loads(pickle.dumps(value)) == value


def _observation(*classifications: Classification) -> Observation:
    return Observation(BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2), list(classifications), frame_number=0)


def test_primary_classification_prefers_a_score_then_ties_keep_decoder_order() -> None:
    unscored = Classification("a", confidence=Confidence(0.99))
    low = Classification("b", Score(0.1))
    high = Classification("c", Score(0.8))
    tie = Classification("d", Score(0.8))

    observation = _observation(unscored, low, high, tie)

    assert [c.type for c in observation.ranked_classifications] == ["c", "d", "b", "a"]
    assert observation.primary_classification is high
    assert _observation().primary_classification is None


def test_confidence_decides_when_no_classification_has_a_score() -> None:
    plain = Classification("a")
    sure = Classification("b", confidence=Confidence(0.9))
    unsure = Classification("c", confidence=Confidence(0.2))

    assert [c.type for c in _observation(plain, unsure, sure).ranked_classifications] == ["b", "c", "a"]
