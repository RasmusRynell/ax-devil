"""Shared filtering contracts used by decoders, sources, and UI."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

from .filter_config import (
    FilterConfig,
    FilterOption,
    FilterPredicate,
    FilterState,
    build_default_filter_config,
    iter_enabled_options,
)
from .predicate_utils import (
    ClassFilterSpec,
    make_classification_predicate,
    make_other_classification_predicate,
)

FilterFactory: TypeAlias = Callable[[], FilterConfig]

__all__ = [
    "ClassFilterSpec",
    "FilterConfig",
    "FilterFactory",
    "FilterOption",
    "FilterPredicate",
    "FilterState",
    "build_default_filter_config",
    "iter_enabled_options",
    "make_classification_predicate",
    "make_other_classification_predicate",
]
