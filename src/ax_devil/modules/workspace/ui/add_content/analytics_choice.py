"""Shared presentation for discovered live analytics choices."""

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QWidget

from .analytics_discovery import AnalyticsChoiceLoader


class AnalyticsChoice(QWidget):
    """Display discovery status and preserve the preferred or selected choice."""

    def __init__(self, loader: AnalyticsChoiceLoader, preferred: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.loader = loader
        self._selection = preferred
        self.combo = QComboBox()
        self.refresh = QPushButton("Refresh")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo, 1)
        layout.addWidget(self.refresh)
        self.refresh.clicked.connect(loader.load)
        loader.changed.connect(self._render)
        self._render()

    def _render(self) -> None:
        selected = self.combo.currentData()
        if isinstance(selected, str):
            self._selection = selected
        self.combo.clear()
        self.combo.addItem(self.loader.status, None)
        for choice in self.loader.choices:
            self.combo.addItem(choice, choice)
        index = self.combo.findData(self._selection)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        self.combo.setEnabled(not self.loader.loading)
        self.refresh.setEnabled(not self.loader.loading)

    def cleanup(self) -> None:
        """Release pending discovery callbacks."""
        self.loader.cleanup()
