"""Content-driven scroll areas shared by dialogs and their pages."""

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget


class ContentScrollArea(QScrollArea):
    """Prefer the whole content while allowing a smaller, scrollable viewport."""

    def __init__(self, content: QWidget) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidget(content)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def sizeHint(self) -> QSize:
        """Include the content and styled frame without Qt's default viewport cap."""
        content = self.widget()
        if content is None:
            return super().sizeHint()
        frame = 2 * self.frameWidth()
        return content.sizeHint() + QSize(frame, frame)

    def heightForWidth(self, width: int) -> int:
        """Include extra lines when content wraps at the bounded opening width."""
        content = self.widget()
        if content is None:
            return self.sizeHint().height()
        frame = 2 * self.frameWidth()
        height = content.heightForWidth(max(0, width - frame))
        return (height if height >= 0 else content.sizeHint().height()) + frame
