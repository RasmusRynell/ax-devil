"""Shared presentation for discovered live analytics choices."""

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.theme import StatusColor

from .analytics_discovery import AnalyticsChoiceLoader


class AnalyticsChoice(QWidget):
    """Display discovery status and preserve the preferred or selected choice."""

    def __init__(self, loader: AnalyticsChoiceLoader, preferred: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.loader = loader
        self._selection = preferred
        self._preserve_saved = False
        self.combo = QComboBox()
        self.refresh = QPushButton("Refresh")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo, 1)
        layout.addWidget(self.refresh)
        self.refresh.clicked.connect(loader.load)
        loader.connection_changed.connect(self._forget_saved_selection)
        loader.changed.connect(self._render)
        follow_appearance(self, self._render)  # The failure icon takes its color from the palette.

    def restore_selection(self, selection: str) -> None:
        """Retain a saved choice even while discovery has not found it."""
        self._preserve_saved = True
        self._selection = selection
        self.combo.clear()
        self._render()

    def _forget_saved_selection(self) -> None:
        if self._preserve_saved:
            self._preserve_saved = False
            self._selection = ""
            self.combo.clear()

    def _render(self) -> None:
        selected = self.combo.currentData()
        if isinstance(selected, str):
            self._selection = selected
        self.combo.clear()
        if self.loader.failed:
            self.combo.addItem(Icon.WARNING.icon(StatusColor.ERROR.color(self.palette())), self.loader.status, None)
        else:
            self.combo.addItem(self.loader.status, None)
        for choice in self.loader.choices:
            self.combo.addItem(choice, choice)
        if self._preserve_saved and self._selection and self._selection not in self.loader.choices:
            self.combo.addItem(self._selection, self._selection)
        index = self.combo.findData(self._selection)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        self.combo.setEnabled(not self.loader.loading)
        self.refresh.setEnabled(not self.loader.loading)

    def cleanup(self) -> None:
        """Release pending discovery callbacks."""
        self.loader.cleanup()
