"""About dialog for ax-devil.

This module provides the AboutDialog that displays application information,
version details, and other relevant information when accessed from Help ->
About.
"""

from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ax_devil.modules.chrome import BaseDialog
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.version import APP_VERSION


class AboutDialog(BaseDialog):
    """About dialog showing application information.

    Displays application name, version, description, and other relevant information in a clean, professional layout.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent, title="About ax-devil", modal=True)

        self._logger = get_logger(__name__)
        self._apply_styles()
        self._setup_content()
        self._setup_buttons()

        self._logger.debug("AboutDialog initialized")

    def _apply_styles(self) -> None:
        """Apply palette-aware styling for the About dialog."""
        self.setStyleSheet(
            """
            QFrame#AboutRule {
                border: 1px solid palette(mid);
            }
            QLabel#AboutTitle {
                font-size: 30px;
                font-weight: 700;
            }
            QLabel#AboutVersion {
                color: palette(link);
                font-size: 14px;
                font-weight: 600;
            }
            QLabel#AboutBody {
                font-size: 14px;
            }
            QLabel#AboutLink {
                color: palette(link);
                font-size: 14px;
                font-weight: 600;
            }
            QLabel#AboutMeta {
                color: palette(placeholder-text);
                font-size: 13px;
                font-weight: 500;
            }
            """
        )

    def _setup_content(self) -> None:
        """Set up the dialog content."""
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(28, 20, 28, 12)
        content_layout.setSpacing(14)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title_label = QLabel("ax-devil")
        title_label.setObjectName("AboutTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        content_layout.addWidget(title_label)

        version_label = QLabel(f"Version {APP_VERSION}")
        version_label.setObjectName("AboutVersion")
        version_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        content_layout.addWidget(version_label)

        rule = QFrame()
        rule.setObjectName("AboutRule")
        rule.setFrameShape(QFrame.Shape.HLine)
        content_layout.addWidget(rule)

        description_label = QLabel("Inspect Axis camera streams, offline video, and analytics overlays.")
        description_label.setObjectName("AboutBody")
        description_label.setWordWrap(True)
        description_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        content_layout.addWidget(description_label)

        details_label = QLabel(
            '<a href="https://github.com/rasmusrynell/ax-devil">github.com/rasmusrynell/ax-devil</a>'
        )
        details_label.setObjectName("AboutLink")
        details_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        details_label.setTextFormat(Qt.TextFormat.RichText)
        details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        details_label.setOpenExternalLinks(False)
        details_label.linkActivated.connect(self._open_external_link)
        content_layout.addWidget(details_label)

        self.add_content_widget(content_widget)

    def _setup_buttons(self) -> None:
        """Set up dialog buttons."""
        # Add just an OK button since this is an informational dialog
        self.add_standard_buttons(include_cancel=False)

    def _open_external_link(self, url: str) -> None:
        """Open an external link from the About dialog."""
        QDesktopServices.openUrl(QUrl(url))
