from __future__ import annotations

import pytest
from PySide6.QtWidgets import QCheckBox, QPushButton
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


def _checkboxes(widget: EntityFilterWidget) -> dict[str, QCheckBox]:
    return {checkbox.text(): checkbox for checkbox in widget.findChildren(QCheckBox)}


def test_checkboxes_and_filter_model_stay_in_sync(qtbot: QtBot, sample_filter_config: FilterConfig) -> None:
    model = SessionFilter(sample_filter_config)
    widget = EntityFilterWidget(filter_model=model)
    qtbot.addWidget(widget)
    checkboxes = _checkboxes(widget)

    assert {label: box.isChecked() for label, box in checkboxes.items()} == {"Keep All": True, "Keep None": False}
    checkboxes["Keep All"].setChecked(False)
    assert not model.is_enabled("keep_all")
    model.set_enabled("keep_none", True)
    assert checkboxes["Keep None"].isChecked()


def test_toggle_all_flips_every_option_with_one_notification(qtbot: QtBot, sample_filter_config: FilterConfig) -> None:
    model = SessionFilter(sample_filter_config)
    widget = EntityFilterWidget(filter_model=model)
    qtbot.addWidget(widget)
    toggle_all = widget.findChild(QPushButton, "toggleAllButton")
    assert toggle_all is not None
    notifications: list[tuple[bool, bool]] = []
    model.changed.connect(lambda: notifications.append((model.is_enabled("keep_all"), model.is_enabled("keep_none"))))

    toggle_all.click()
    assert notifications == [(True, True)]
    assert all(checkbox.isChecked() for checkbox in _checkboxes(widget).values())

    toggle_all.click()
    assert notifications == [(True, True), (False, False)]
    assert not any(checkbox.isChecked() for checkbox in _checkboxes(widget).values())

    model.set_enabled("keep_none", True)
    model.set_enabled("keep_none", True)
    assert len(notifications) == 3
