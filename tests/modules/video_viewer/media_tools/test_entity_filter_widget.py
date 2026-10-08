from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.filtering import FilterConfig, FilterOption
from ax_devil.modules.filtering.session_filter import SessionFilter
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
    model = SessionFilter(sample_filter_config)
    widget = EntityFilterWidget(filter_model=model)
    qtbot.addWidget(widget)

    assert set(widget._checkboxes.keys()) == {"keep_all", "keep_none"}
    assert model.is_enabled("keep_all")
    assert not model.is_enabled("keep_none")

    widget._checkboxes["keep_all"].setChecked(False)
    assert not model.is_enabled("keep_all")


def test_shared_model_updates_controls_and_toggle_all_notifies_once(
    qtbot: QtBot, sample_filter_config: FilterConfig
) -> None:
    model = SessionFilter(sample_filter_config)
    widget = EntityFilterWidget(filter_model=model)
    qtbot.addWidget(widget)
    notifications: list[tuple[bool, bool]] = []
    model.changed.connect(lambda: notifications.append((model.is_enabled("keep_all"), model.is_enabled("keep_none"))))

    widget._toggle_all_button.click()
    assert notifications == [(True, True)]
    assert all(checkbox.isChecked() for checkbox in widget._checkboxes.values())

    widget._toggle_all_button.click()
    assert notifications == [(True, True), (False, False)]
    assert not any(checkbox.isChecked() for checkbox in widget._checkboxes.values())

    model.set_enabled("keep_none", True)
    assert widget._checkboxes["keep_none"].isChecked()
    model.set_enabled("keep_none", True)
    assert len(notifications) == 3
