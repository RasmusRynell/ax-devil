"""Live appearance updates: widgets restyle themselves when the palette or text size changes.

Widgets that only inherit the application font and palette follow changes on their own. A widget that sizes something
from the text, or colors something in code, registers that code with ``follow_appearance`` instead of overriding
``changeEvent``, so one call covers a theme switch, a palette set on the widget, and a text-size change.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, Signal


class _TextSizeNotifier(QObject):
    changed = Signal()


_text_size_notifier = _TextSizeNotifier()


class _Follower(QObject):
    """Reruns one widget's styling code after appearance changes; destroyed, and disconnected, with the widget.

    A text-size change reruns it at once (see ``notify_text_size_changed``). Every application stylesheet change,
    including a theme switch, re-polishes widgets and puts back the fonts they were polished with, and reaches a widget
    as several palette changes; those reruns are merged into one on the next event-loop turn.
    """

    # A queued signal is a posted event, which the next event-loop turn always delivers.
    _rerun = Signal()

    def __init__(self, widget: QObject, apply: Callable[[], None]) -> None:
        super().__init__(widget)
        self._rerun.connect(self._run, Qt.ConnectionType.QueuedConnection)
        self._apply = apply
        self._pending = False
        self._applying = False
        widget.installEventFilter(self)
        _text_size_notifier.changed.connect(self._run)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        # Palette changes caused by the widget's own restyle are not a new appearance.
        if event.type() == QEvent.Type.PaletteChange and not self._applying:
            self._schedule()
        return False

    def _schedule(self) -> None:
        if not self._pending:
            self._pending = True
            self._rerun.emit()

    def _run(self) -> None:
        self._pending = False
        if self._applying:
            return
        self._applying = True
        try:
            self._apply()
        finally:
            self._applying = False


def follow_appearance(widget: QObject, apply: Callable[[], None]) -> None:
    """Run *apply* now and again after palette or text-size changes, for as long as *widget* exists."""
    apply()
    _Follower(widget, apply)


def notify_text_size_changed() -> None:
    """Restyle every following widget for the new application font.

    Called on both sides of the stylesheet restyle: before it, so the restyle hands the new fonts down to child
    widgets such as a spin box's text field; after it, so the fonts it puts back are replaced again.
    """
    _text_size_notifier.changed.emit()
