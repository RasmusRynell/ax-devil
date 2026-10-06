"""Thin per-view controls for semantic overlay visibility."""

from functools import partial

from PySide6.QtWidgets import QCheckBox, QPushButton, QVBoxLayout, QWidget

from ax_devil.modules.scene.rendering import OverlayFeature, SceneRenderCatalogSelection


class OverlayVisibilityWidget(QWidget):
    """Show the selection's feature choices and available catalog components."""

    def __init__(self, selection: SceneRenderCatalogSelection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selection = selection
        self._cleaned_up = False
        self._controls: dict[OverlayFeature, QCheckBox] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for feature in OverlayFeature:
            control = QCheckBox(feature.label, self)
            control.setObjectName(f"overlayFeature_{feature.value}")
            control.toggled.connect(partial(selection.set_feature_enabled, feature))
            self._controls[feature] = control
            layout.addWidget(control)
        self._reset = QPushButton("Reset", self)
        self._reset.setObjectName("overlayVisibilityReset")
        self._reset.clicked.connect(selection.reset_visibility)
        layout.addWidget(self._reset)
        selection.selectionChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        available = self._selection.available_features()
        disabled = self._selection.visibility.disabled
        for feature, control in self._controls.items():
            control.blockSignals(True)
            control.setChecked(feature not in disabled)
            control.blockSignals(False)
            control.setEnabled(feature in available)
            control.setToolTip(
                feature.description if feature in available else "This catalog does not draw this detail."
            )
        self._reset.setEnabled(bool(disabled))

    def cleanup(self) -> None:
        """Disconnect selection observers and control actions during view teardown."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._selection.selectionChanged.disconnect(self._refresh)
        for control in self._controls.values():
            control.toggled.disconnect()
        self._reset.clicked.disconnect()
