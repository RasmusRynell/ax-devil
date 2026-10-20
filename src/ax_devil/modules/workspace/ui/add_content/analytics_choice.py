"""Shared presentation for discovered live analytics choices."""

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QWidget

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
        loader.changed.connect(self._render)
        self._render()

    def restore_selection(self, selection: str) -> None:
        """Retain a saved choice even while discovery has not found it."""
        self._preserve_saved = True
        self._selection = selection
        self.combo.clear()
        self._render()

    def _render(self) -> None:
        selected = self.combo.currentData()
        if isinstance(selected, str):
            self._selection = selected
        self.combo.clear()
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
