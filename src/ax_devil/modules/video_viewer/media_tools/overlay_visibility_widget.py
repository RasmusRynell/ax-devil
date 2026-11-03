"""Thin per-view controls for semantic overlay visibility."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.toggle_chip import ToggleChips
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.scene.rendering import OverlayFeature, SceneRenderCatalogSelection


class OverlayVisibilityWidget(QWidget):
    """Show the selection's feature choices as chips and which of them the catalog can draw."""

    def __init__(self, selection: SceneRenderCatalogSelection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selection = selection
        self._cleaned_up = False
        self._chips = ToggleChips([(feature.value, feature.label) for feature in OverlayFeature], self)
        for feature in OverlayFeature:
            self._chips.chip(feature.value).setObjectName(f"overlayFeature_{feature.value}")
        self._chips.toggled.connect(self._toggle)
        self._reset = SmallButton("Reset", parent=self)
        self._reset.setObjectName("overlayVisibilityReset")
        self._reset.clicked.connect(selection.reset_visibility)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addStretch(1)
        footer.addWidget(self._reset)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.S)
        layout.addWidget(self._chips)
        layout.addLayout(footer)
        selection.selectionChanged.connect(self._refresh)
        self._refresh()

    def _toggle(self, key: str, checked: bool) -> None:
        self._selection.set_feature_enabled(OverlayFeature(key), checked)

    def _refresh(self) -> None:
        available = self._selection.available_features()
        disabled = self._selection.visibility.disabled
        for feature in OverlayFeature:
            chip = self._chips.chip(feature.value)
            self._chips.set_checked(feature.value, feature not in disabled)
            chip.setEnabled(feature in available)
            chip.setToolTip(feature.description if feature in available else "This catalog does not draw this detail.")
        self._reset.setEnabled(bool(disabled))

    def cleanup(self) -> None:
        """Disconnect selection observers and control actions during view teardown."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._selection.selectionChanged.disconnect(self._refresh)
        self._chips.toggled.disconnect()
        self._reset.clicked.disconnect()
