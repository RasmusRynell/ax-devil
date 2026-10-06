from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.filtering import FilterConfig, FilterOption
from ax_devil.modules.video_viewer.media_tools import EntityFilterWidget


@pytest.fixture
def sample_filter_config() -> FilterConfig:
    return FilterConfig(
        options=(
            FilterOption(
                id="keep_all",
                label="Keep All",
                predicate=lambda entity, state: True,
                default_enabled=True,
                sort_key=0,
            ),
            FilterOption(
                id="keep_none",
                label="Keep None",
                predicate=lambda entity, state: False,
                default_enabled=False,
                sort_key=1,
            ),
        )
    )


def test_entity_filter_widget_uses_supplied_config(qtbot: QtBot, sample_filter_config: FilterConfig) -> None:
    widget = EntityFilterWidget(filter_config=sample_filter_config)
    qtbot.addWidget(widget)

    assert widget.filter_config is sample_filter_config
    assert set(widget._checkboxes.keys()) == {"keep_all", "keep_none"}
    assert widget.filter_state.is_enabled("keep_all")
    assert not widget.filter_state.is_enabled("keep_none")

    widget._checkboxes["keep_all"].setChecked(False)
    assert not widget.filter_state.is_enabled("keep_all")
