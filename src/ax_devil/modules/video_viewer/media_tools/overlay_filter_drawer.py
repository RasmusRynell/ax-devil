"""The overlay filter of the Objects page: a button with a count, and the drawer it opens beneath the search."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.toggle_chip import ToggleChips
from ax_devil.modules.chrome.tokens import Space, TextRole, keep_minimum_chars
from ax_devil.modules.filtering.filter_choice import FilterChoice
from ax_devil.modules.filtering.measure_range import Measure, MeasureRange
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.video_viewer.media_tools.media_tools_section import MediaToolsSection
from ax_devil.modules.video_viewer.media_tools.scope_row import ScopeRow
from ax_devil.modules.video_viewer.view_settings import OBJECT_FILTER

_MUTED = "color: palette(placeholder-text);"
_RANGE_EDIT_CHARS = 5


def _number_text(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def _number_of(edit: QLineEdit) -> float | None:
    """Return the number typed in *edit*, or None when it is empty or unfinished (an open end)."""
    try:
        return float(edit.text().replace(",", "."))
    except ValueError:
        return None


class OverlayFilterDrawer(QWidget):
    """Class chips and a score and confidence range of the session filter, hidden until ``button`` is clicked.

    The button reads "Filter N" for the N choices that differ from the decoder's defaults and is highlighted while
    any do. The drawer only edits the ``SessionFilter`` and follows its ``changed`` signal; its last row makes the
    filter the default for new views or applies it to every open view.
    """

    def __init__(self, scene_filter: SessionFilter, parent: QWidget | None = None) -> None:
        """Create the closed drawer and its button for *scene_filter*; the owner places both."""
        super().__init__(parent)
        self.setObjectName("objectsFilterDrawer")
        self._filter = scene_filter
        self._range_edits: dict[Measure, tuple[QLineEdit, QLineEdit]] = {}

        self.button = QToolButton(parent)
        self.button.setObjectName("objectsFilterButton")
        self.button.setIcon(Icon.FILTER.icon())
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setAutoRaise(True)
        self.button.setToolTip(MediaToolsSection.FILTERS.help_html)
        self.button.setStyleSheet('QToolButton[active="true"] { color: palette(highlight); }')
        self.button.clicked.connect(lambda: self.setVisible(not self.isVisible()))

        self._chips = ToggleChips([(option.id, option.label) for option in scene_filter.options], self)
        for option in scene_filter.options:
            self._chips.chip(option.id).setObjectName(f"filterOption_{option.id}")
            self._chips.set_checked(option.id, scene_filter.is_enabled(option.id))
        self._chips.toggled.connect(scene_filter.set_enabled)

        self._summary = QLabel(self)
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(_MUTED)
        TextRole.SMALL.apply(self._summary)
        self._summary.hide()

        everything = SmallButton("All", parent=self)
        everything.clicked.connect(lambda: scene_filter.set_all_enabled(True))
        nothing = SmallButton("None", parent=self)
        nothing.clicked.connect(lambda: scene_filter.set_all_enabled(False))
        self._clear = SmallButton("Clear", parent=self)
        self._clear.setObjectName("filterClear")
        self._clear.clicked.connect(scene_filter.reset)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(Space.S)
        footer.addWidget(everything)
        footer.addWidget(nothing)
        footer.addStretch(1)
        footer.addWidget(self._clear)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(Space.S)
        column.addWidget(self._chips)
        column.addLayout(self._range_rows())
        column.addWidget(self._summary)
        column.addLayout(footer)
        self._scope = ScopeRow(
            OBJECT_FILTER, scene_filter.choice(), adopt=scene_filter.apply_choice, parent=self, hide_when_idle=True
        )
        column.addWidget(self._scope)

        self.hide()
        scene_filter.changed.connect(self._refresh)
        self._refresh()

    def set_frame_counts(self, kept: int | None, total: int | None) -> None:
        """Say how many of the *total* objects in the displayed frame the filter keeps; None hides the line."""
        shown = kept is not None and total is not None
        self._summary.setVisible(shown)
        if shown:
            self._summary.setText(f"Showing {kept} of {total} in this frame")

    def _range_rows(self) -> QGridLayout:
        """Return a row per measure: its name, then the smallest and largest value kept (empty keeps all)."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(Space.S)
        grid.setVerticalSpacing(Space.S)
        for row, measure in enumerate(Measure):
            grid.addWidget(QLabel(measure.value, self), row, 0)
            edits = (self._range_edit("min"), self._range_edit("max"))
            for column, edit in enumerate(edits, start=1):
                grid.addWidget(edit, row, column)
                edit.textChanged.connect(lambda _text, measure=measure: self._on_range_edited(measure))
            self._range_edits[measure] = edits
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        return grid

    def _range_edit(self, placeholder: str) -> QLineEdit:
        edit = QLineEdit(self)
        edit.setPlaceholderText(placeholder)
        edit.setValidator(QDoubleValidator(edit))
        edit.setClearButtonEnabled(True)
        keep_minimum_chars(edit, _RANGE_EDIT_CHARS)
        return edit

    def _on_range_edited(self, measure: Measure) -> None:
        low, high = self._range_edits[measure]
        self._filter.set_range(measure, MeasureRange(_number_of(low), _number_of(high)))

    def _refresh(self) -> None:
        """Show the filter's state in every control, without editing the filter back."""
        for option in self._filter.options:
            self._chips.set_checked(option.id, self._filter.is_enabled(option.id))
        for measure, edits in self._range_edits.items():
            kept = self._filter.range(measure)
            for edit, value in zip(edits, (kept.minimum, kept.maximum), strict=True):
                if _number_of(edit) != value:
                    blocked = edit.blockSignals(True)
                    edit.setText(_number_text(value))
                    edit.blockSignals(blocked)
        changed = self._filter.changed_count
        self.button.setText("Filter" if changed == 0 else f"Filter {changed}")
        self.button.setProperty("active", changed > 0)
        self._clear.setEnabled(self._filter.choice() != FilterChoice())  # Also for a choice with no visible effect
        self._scope.set_value(self._filter.choice())
        if (style := self.button.style()) is not None:
            style.unpolish(self.button)
            style.polish(self.button)
