"""Filtering policy for the classification types recorded in offline object histories."""

from __future__ import annotations

from collections.abc import Sequence

from .filter_config import FilterConfig, FilterState, iter_enabled_options


def history_type_filter_keeps(types: Sequence[str], config: FilterConfig, state: FilterState) -> bool:
    """Keep recorded types using enabled classification policies and uncovered-type defaults.

    Empty types mean an unclassified object. Entity-only predicates cannot be evaluated against history;
    unsupported configurations raise ``ValueError`` instead of inventing scores, geometry or observations.
    """
    if not config.supports_history_filtering:
        raise ValueError("Whole-file filtering requires classification policies for every option")
    enabled = tuple(iter_enabled_options(config, state))
    if not enabled:
        return False
    for option in enabled:
        assert option.classification_filter is not None
        if option.classification_filter.matches_types(types):
            return True
    for option in config.options:
        assert option.classification_filter is not None
        if option.classification_filter.matches_types(types):
            return False
    return True
