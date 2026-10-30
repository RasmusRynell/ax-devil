"""The row that makes one setting of a view the default for new views or applies it to every open view."""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedLayout, QWidget

from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_viewer.view_settings import SettingKind, view_settings

logger = get_logger(__name__)

T = TypeVar("T")

_MUTED = "color: palette(placeholder-text);"


class ScopeRow(QWidget, Generic[T]):
    """Say whether a view's value is the default for new views, and offer Make default and Apply to all.

    The owner reports the view's value with `set_value` and supplies *adopt*, which makes the view show a value that
    Apply to all chose in another view. Call `cleanup` when the owning view closes. With *hide_when_idle* the whole
    row is hidden, and takes no room, while it has nothing to offer: the value is the saved default and no other view
    differs.
    """

    def __init__(
        self,
        kind: SettingKind[T],
        value: T,
        adopt: Callable[[T], None],
        parent: QWidget | None = None,
        *,
        hide_when_idle: bool = False,
    ) -> None:
        """Create the row for a view currently showing *value*."""
        super().__init__(parent)
        self.setObjectName(f"{kind.key}Scope")
        self._kind = kind
        self._value = value
        self._adopt = adopt
        self._hide_when_idle = hide_when_idle
        self._closed = False
        self._hub = view_settings(kind)

        self._default_label = QLabel("Default for new views", self)
        self._default_label.setObjectName("defaultLabel")
        self._default_label.setStyleSheet(_MUTED)
        TextRole.SMALL.apply(self._default_label)
        self._make_default_button = SmallButton("Make default", parent=self)
        self._make_default_button.setObjectName("useAsDefaultButton")
        self._make_default_button.setToolTip("Start new viewers and lanes with these settings.")
        self._apply_to_all_button = SmallButton("Apply to all", parent=self)
        self._apply_to_all_button.setObjectName("applyToAllButton")
        self._apply_to_all_button.setToolTip("Use these settings in every open viewer and lane.")

        # The label and Make default share one cell, and a hidden Apply to all keeps its room, so the row's size hint
        # never changes and a menu or window holding it never needs to resize.
        self._status = QStackedLayout()
        self._status.addWidget(self._default_label)
        button_cell = QWidget(self)  # A stacked widget fills its cell; the cell keeps the button its natural width
        button_layout = QHBoxLayout(button_cell)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addWidget(self._make_default_button)
        button_layout.addStretch(1)
        self._status.addWidget(button_cell)
        policy = self._apply_to_all_button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self._apply_to_all_button.setSizePolicy(policy)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.S)
        layout.addLayout(self._status)
        layout.addStretch(1)
        layout.addWidget(self._apply_to_all_button)

        self._key = self._hub.add(value)
        self._make_default_button.clicked.connect(self._make_default)
        self._apply_to_all_button.clicked.connect(lambda: self._hub.apply_to_all(self._value))
        self._hub.changed.connect(self._render)
        self._hub.appliedToAll.connect(self._on_applied_to_all)
        self._render()

    def set_value(self, value: T) -> None:
        """Record the value the view shows now."""
        self._value = value
        self._hub.set_value(self._key, value)
        self._render()

    def cleanup(self) -> None:
        """Leave the shared view list and stop listening to it when the owning view closes; repeat calls do nothing."""
        if self._closed:
            return
        self._closed = True
        self._hub.discard(self._key)
        self._hub.changed.disconnect(self._render)
        self._hub.appliedToAll.disconnect(self._on_applied_to_all)

    def _make_default(self) -> None:
        try:
            self._hub.use_as_default(self._value)
        except OSError as exc:
            logger.error(f"Could not save the default {self._kind.key}: {exc}")

    def _on_applied_to_all(self, value: T) -> None:
        if value != self._value:
            self._adopt(value)
            self.set_value(value)

    def _render(self) -> None:
        in_use = self._hub.values_in_use()
        is_default = self._value == self._hub.default()
        differs = any(value != self._value for value in in_use)
        self._status.setCurrentIndex(0 if is_default else 1)
        self._apply_to_all_button.setText(f"Apply to all {len(in_use)}")
        self._apply_to_all_button.setVisible(differs)
        if self._hide_when_idle:
            self.setVisible(not is_default or differs)
