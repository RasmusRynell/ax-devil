"""Filter configuration primitives shared between decoders and UI.

Decoders should expose a ``get_filter_config()`` function returning a *fresh*
``FilterConfig`` instance describing the filterable entities they emit. The UI
layer treats configs as immutable, so callers are expected to construct new
``FilterOption`` objects on each invocation rather than sharing a singleton.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, Iterator, Mapping, Tuple

from ax_devil.modules.scene.model import Entity, KnownClassificationType

if TYPE_CHECKING:
    from .predicate_utils import ClassificationFilter

FilterPredicate = Callable[[Entity, "FilterState"], bool]


@dataclass(slots=True, frozen=True)
class FilterOption:
    """A toggle with an Entity predicate and optional shared classification policy.

    Entity-only predicates remain supported for Scene filtering. Whole-file history filtering requires a
    ``classification_filter`` that also supplies the Entity predicate; use its ``build_option()`` method.
    """

    id: str
    label: str
    predicate: FilterPredicate
    default_enabled: bool = True
    sort_key: int | None = None
    classification_filter: ClassificationFilter | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("FilterOption.id cannot be empty")
        if not self.label:
            raise ValueError("FilterOption.label cannot be empty")
        if self.classification_filter is not None and self.predicate is not self.classification_filter:
            raise ValueError("A classification filter must also be the option's Entity predicate")


@dataclass(slots=True, frozen=True)
class FilterConfig:
    """Immutable bundle of filter options provided by a decoder."""

    options: Tuple[FilterOption, ...]
    name: str | None = None

    def __post_init__(self) -> None:
        if not self.options:
            raise ValueError("FilterConfig requires at least one option")

        ids = [option.id for option in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError(f"FilterConfig contains duplicate option ids: {ids}")

    @property
    def supports_history_filtering(self) -> bool:
        """Return whether every option declares a policy for recorded classification types."""
        return all(option.classification_filter is not None for option in self.options)

    def sorted_options(self) -> Tuple[FilterOption, ...]:
        """Return options sorted by provided sort key or definition order."""
        enumerated = list(enumerate(self.options))
        enumerated.sort(key=lambda item: ((item[1].sort_key if item[1].sort_key is not None else item[0]), item[0]))
        return tuple(option for _, option in enumerated)

    def option_ids(self) -> Tuple[str, ...]:
        """Return option identifiers preserving definition order."""
        return tuple(option.id for option in self.options)


class FilterState:
    """Mutable toggle state built from a FilterConfig, plus a free-text entity id query."""

    __slots__ = ("_options", "_flags", "id_query")

    def __init__(self, config: FilterConfig, initial: Mapping[str, bool] | None = None, *, id_query: str = "") -> None:
        self._options: Dict[str, FilterOption] = {option.id: option for option in config.options}
        flags = {option.id: option.default_enabled for option in config.options}

        if initial:
            for option_id, enabled in initial.items():
                if option_id not in self._options:
                    raise KeyError(f"Unknown filter option '{option_id}'")
                flags[option_id] = bool(enabled)

        self._flags: Dict[str, bool] = flags
        self.id_query = id_query

    def is_enabled(self, option_id: str) -> bool:
        """Return whether the given option id is currently enabled."""
        return self._flags.get(option_id, False)

    def set_enabled(self, option_id: str, enabled: bool) -> None:
        """Toggle an option by identifier."""
        if option_id not in self._options:
            raise KeyError(f"Unknown filter option '{option_id}'")
        self._flags[option_id] = bool(enabled)

    def items(self) -> Iterator[Tuple[str, bool]]:
        """Yield (option_id, enabled) pairs in definition order."""
        for option_id in self._options:
            yield option_id, self._flags[option_id]


def iter_enabled_options(config: FilterConfig, state: FilterState) -> Iterator[FilterOption]:
    """Yield the configured options that are enabled in the supplied state."""
    for option in config.options:
        if state.is_enabled(option.id):
            yield option


def build_default_filter_config() -> FilterConfig:
    """Return a FilterConfig mirroring today's static DrawFilter setup."""
    from .predicate_utils import make_classification_predicate

    options = (
        make_classification_predicate(
            (KnownClassificationType.Unknown.value,), include_empty=True, include_unknown_prefix=False
        ).build_option(id="show_unknown", label="Unknown", sort_key=0),
        make_classification_predicate((KnownClassificationType.Human.value,)).build_option(
            id="show_humans", label="Humans", sort_key=1
        ),
        make_classification_predicate((KnownClassificationType.Head.value,)).build_option(
            id="show_heads", label="Heads", sort_key=2
        ),
        make_classification_predicate((KnownClassificationType.Vehicle.value,)).build_option(
            id="show_vehicles", label="Vehicles", sort_key=3
        ),
        make_classification_predicate((KnownClassificationType.VehicleOther.value,)).build_option(
            id="show_vehicles_other", label="Vehicles (Other)", sort_key=4
        ),
        make_classification_predicate((KnownClassificationType.Car.value,)).build_option(
            id="show_cars", label="Cars", sort_key=5
        ),
        make_classification_predicate((KnownClassificationType.Bus.value,)).build_option(
            id="show_buses", label="Buses", sort_key=6
        ),
        make_classification_predicate((KnownClassificationType.Truck.value,)).build_option(
            id="show_trucks", label="Trucks", sort_key=7
        ),
        make_classification_predicate((KnownClassificationType.Bike.value,)).build_option(
            id="show_bikes", label="Bikes", sort_key=8
        ),
        make_classification_predicate((KnownClassificationType.Bicycle.value,)).build_option(
            id="show_bicycles", label="Bicycles", sort_key=9
        ),
        make_classification_predicate((KnownClassificationType.Animal.value,)).build_option(
            id="show_animals", label="Animals", sort_key=10
        ),
        make_classification_predicate((KnownClassificationType.LicensePlate.value,)).build_option(
            id="show_license_plates", label="License Plates", sort_key=11
        ),
    )
    return FilterConfig(options=options, name="default_draw_filter")
