"""A push/pop stack of drilled pages over a set of root pages, with a breadcrumb header on pushed pages."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QHideEvent, QKeyEvent, QMouseEvent, QPalette, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.data_sources.scene_history import FrameEvent

_TITLE_CHARS = 14  # The characters of the title that stay visible.
_BACK_MIN_CHARS = 10  # The characters of the previous page's title the back button keeps at the narrowest.
_BACK_MAX_CHARS = 18  # ... and the most it asks for, so a long id does not crowd the title.


@dataclass(frozen=True, slots=True)
class ObjectPageKey:
    """The page of one object."""

    entity_id: str

    @property
    def title(self) -> str:
        """The breadcrumb title."""
        return self.entity_id


@dataclass(frozen=True, slots=True)
class EventPageKey:
    """The page of one event; equal events of a list are told apart by their ``occurrence``, counted from 0."""

    event: FrameEvent
    occurrence: int = 0

    @property
    def title(self) -> str:
        """The breadcrumb title."""
        return self.event.label or self.event.kind


PageKey = ObjectPageKey | EventPageKey

_NAVIGATION_KEYS = frozenset({Qt.Key.Key_Escape, Qt.Key.Key_Backspace})


class RowSource(Protocol):
    """The list a page was opened from: it orders the rows ▲▼ move through and shows which row is open."""

    def neighbor(self, key: PageKey, delta: int) -> PageKey | None:
        """Return the key *delta* rows from *key* in the list's current order, or None past either end."""
        ...

    def highlight(self, key: PageKey | None) -> None:
        """Mark the row of *key* as the one the open page came from; None clears the mark."""
        ...


@dataclass(slots=True)
class _Entry:
    key: PageKey
    origin: RowSource | None


class _BackButton(SmallButton):
    """The button back to the previous page, named after it; the name is cut in the middle to the room the button gets.

    Its width never depends on the name: it asks for room for a few characters more than it needs at the narrowest.
    """

    _full_text = "‹"  # Set before the base class builds the button, which already reports a font change.

    def __init__(self, parent: QWidget) -> None:
        super().__init__("‹", parent=parent)
        # A button that cannot shrink is laid out no narrower than its size hint, which follows the name.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_previous(self, title: str) -> None:
        """Name *title* as the page the button goes back to; the tooltip has it whole."""
        self._full_text = f"‹ {title}"
        self.setToolTip(f"Back (Esc) to {title}")
        self._elide()

    def _padding(self) -> int:
        return QPushButton.sizeHint(self).width() - self.fontMetrics().horizontalAdvance(self.text())

    def _width_for(self, chars: int) -> int:
        metrics = self.fontMetrics()
        return self._padding() + metrics.horizontalAdvance("‹ ") + chars * metrics.averageCharWidth()

    def sizeHint(self) -> QSize:  # noqa: N802
        """Return the room for the name, up to a few more characters than the narrowest."""
        natural = self._padding() + self.fontMetrics().horizontalAdvance(self._full_text)
        return QSize(min(natural, self._width_for(_BACK_MAX_CHARS)), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return the room for the arrow and the start of the name."""
        return QSize(self._width_for(_BACK_MIN_CHARS), super().sizeHint().height())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Cut the name again for the new width."""
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """Cut the name again when the font changes."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._elide()

    def _elide(self) -> None:
        room = max(0, self.width() - self._padding())
        text = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, room)
        if text != self.text():
            self.setText(text)


class PageStack(QWidget):
    """Shows one root page, or the drilled page on top of it, under a breadcrumb header.

    The roots are built by the caller and stay alive, so their query, sort and scroll position are as they were left
    when the user comes back. Only the top drilled page exists as a widget: pushing discards the page below it, and
    going back rebuilds that page from its key with *build*. Esc, Backspace and the mouse back button each go back
    one level. These are local navigation keys, not application shortcuts.

    An entry's origin is the list the page was opened from. Only a push from a root keeps its origin: a drilled page
    that pushes is discarded and takes its lists with it.
    """

    pageChanged = Signal(object)
    """The page on top changed; carries its key, or None when a root is showing."""

    def __init__(
        self, roots: Mapping[str, QWidget], build: Callable[[PageKey], QWidget], parent: QWidget | None = None
    ) -> None:
        """Host *roots*, titled by their keys in *roots* (the back button names them), and build pages with *build*."""
        super().__init__(parent)
        if not roots:
            raise ValueError("A page stack needs at least one root page")
        self._build = build
        self._root_titles = {widget: title for title, widget in roots.items()}
        self._root: QWidget = next(iter(self._root_titles))
        self._entries: list[_Entry] = []
        self._page: QWidget | None = None
        self._swallow_back_release = False
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._header = QWidget(self)
        self._header.setObjectName("AxDevilPageHeader")
        self._header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(Space.M, 0, Space.M, 0)
        header_layout.setSpacing(Space.S)
        self._back_button = _BackButton(self._header)
        self._back_button.clicked.connect(self.back)
        self._title = ElidedLabel("", Qt.TextElideMode.ElideMiddle, self._header)  # Titles end in ids
        TextRole.STRONG.apply(self._title)
        self._previous_button = SmallButton("", Icon.CHEVRON_UP, parent=self._header)
        self._previous_button.setToolTip("Previous in list")
        self._previous_button.clicked.connect(lambda: self.step(-1))
        self._next_button = SmallButton("", Icon.MENU, parent=self._header)
        self._next_button.setToolTip("Next in list")
        self._next_button.clicked.connect(lambda: self.step(1))
        header_layout.addWidget(self._back_button)
        header_layout.addWidget(self._title, 1)
        header_layout.addWidget(self._previous_button)
        header_layout.addWidget(self._next_button)
        layout.addWidget(self._header)
        self._header.hide()

        self._pages = QStackedLayout()
        for widget in self._root_titles:
            self._pages.addWidget(widget)
        self._pages.setCurrentWidget(self._root)
        layout.addLayout(self._pages, 1)
        follow_appearance(self, self._apply_appearance)

    def narrowest_width(self) -> int:
        """Return the narrowest width the breadcrumb header and every root page fit, whichever page shows."""
        widths = [self._header.minimumSizeHint().width()]
        widths.extend(root.minimumSizeHint().width() for root in self._root_titles)
        return max(widths)

    def depth(self) -> int:
        """Return how many drilled pages are stacked on the root; 0 while a root is showing."""
        return len(self._entries)

    def root(self) -> QWidget:
        """Return the root page the stack was last set to, which drilled pages return to."""
        return self._root

    def page(self) -> QWidget | None:
        """Return the drilled page on top, or None while a root is showing."""
        return self._page

    def current_key(self) -> PageKey | None:
        """Return the key of the drilled page on top, or None while a root is showing."""
        return self._entries[-1].key if self._entries else None

    def show_root(self, root: QWidget) -> None:
        """Show *root* and discard every drilled page."""
        if root not in self._root_titles:
            raise ValueError("Not a root page of this stack")
        was_drilled = bool(self._entries)
        if was_drilled:
            self._clear_highlight()
            self._discard_page()
            self._entries.clear()
        self._root = root
        self._pages.setCurrentWidget(root)
        self._header.hide()
        if was_drilled:
            self.pageChanged.emit(None)

    def push(self, key: PageKey, origin: RowSource | None = None) -> None:
        """Open the page of *key* on top of what is showing; *origin* is the root list it was chosen from, if any."""
        self._push(_Entry(key, origin if not self._entries else None))

    def jump(self, key: PageKey, origin: RowSource | None = None) -> None:
        """Open the page of *key*, replacing the page on top when it is of the same kind, else pushing it.

        Without an *origin*, a replaced page keeps the list it was opened from, so ▲▼ and back keep working.
        """
        if self._entries and type(self._entries[-1].key) is type(key):
            self._replace_top(key, self._entries[-1].origin)
        else:
            self.push(key, origin)

    def back(self) -> None:
        """Go back exactly one level: to the page below, rebuilt from its key, or to the root."""
        if not self._entries:
            return
        had_focus = self._holds_focus()
        popped = self._entries.pop()
        self._discard_page()
        if self._entries:
            self._show_page(self._entries[-1].key)
        else:
            self._pages.setCurrentWidget(self._root)
            self._header.hide()
            if popped.origin is not None:
                popped.origin.highlight(popped.key)
        self._after_change(had_focus)

    def step(self, delta: int) -> None:
        """Replace the page on top with the one *delta* rows away in the list it was opened from, if there is one."""
        if not self._entries:
            return
        entry = self._entries[-1]
        if entry.origin is None:
            return
        key = entry.origin.neighbor(entry.key, delta)
        if key is not None:
            self._replace_top(key, entry.origin)
        else:
            self._update_header()  # The list moved on since ▲▼ were last enabled.

    def remap(self, resolve: Callable[[PageKey], PageKey | None]) -> None:
        """Re-point the drilled pages after what they show was replaced: each key becomes ``resolve(key)``.

        The first page whose key resolves to None, and every page above it, is closed. Nothing is rebuilt when no key
        changed.
        """
        kept: list[_Entry] = []
        for entry in self._entries:
            key = resolve(entry.key)
            if key is None:
                break
            kept.append(_Entry(key, entry.origin))
        if kept == self._entries:
            return
        had_focus = self._holds_focus()
        if not kept:
            self._clear_highlight()
        self._discard_page()
        self._entries[:] = kept
        if kept:
            origin = kept[0].origin
            if origin is not None:
                origin.highlight(kept[0].key)
            self._open_top(had_focus)
        else:
            self._pages.setCurrentWidget(self._root)
            self._header.hide()
            self._after_change(had_focus)

    def refresh_steps(self) -> None:
        """Enable ▲▼ again from the list's current order, which changes under an open page as frames arrive.

        The list is only asked when the user steps or a page shows; asking it per frame would rebuild a hidden list.
        """
        if self._entries:
            self._update_header()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Go back one level on Esc or Backspace while a drilled page shows."""
        if self._entries and event.key() in _NAVIGATION_KEYS and not event.modifiers():
            event.accept()
            self.back()
            return
        super().keyPressEvent(event)

    def event(self, event: QEvent) -> bool:
        """Keep Esc and Backspace from becoming application shortcuts while they would go back."""
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and isinstance(event, QKeyEvent)
            and self._entries
            and event.key() in _NAVIGATION_KEYS
            and not event.modifiers()
        ):
            event.accept()
            return True
        return super().event(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Go back one level on the mouse back button pressed anywhere inside the stack."""
        if not isinstance(event, QMouseEvent) or event.button() != Qt.MouseButton.BackButton:
            return False
        if event.type() == QEvent.Type.MouseButtonRelease and self._swallow_back_release:
            self._swallow_back_release = False
            return True
        if event.type() != QEvent.Type.MouseButtonPress or not self._entries:
            return False
        if not (isinstance(watched, QWidget) and (watched is self or self.isAncestorOf(watched))):
            return False
        self._swallow_back_release = True
        self.back()
        return True

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Start watching for the mouse back button; the filter is application-wide, so only while visible."""
        super().showEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        """Stop watching for the mouse back button."""
        super().hideEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def _push(self, entry: _Entry) -> None:
        had_focus = self._holds_focus()
        if self._entries:
            self._discard_page()
        self._entries.append(entry)
        self._open_top(had_focus)

    def _replace_top(self, key: PageKey, origin: RowSource | None) -> None:
        had_focus = self._holds_focus()
        self._discard_page()
        self._entries[-1] = _Entry(key, origin)
        self._open_top(had_focus)

    def _open_top(self, had_focus: bool) -> None:
        entry = self._entries[-1]
        self._show_page(entry.key)
        if entry.origin is not None:
            entry.origin.highlight(entry.key)
        self._after_change(had_focus)

    def _show_page(self, key: PageKey) -> None:
        page = self._build(key)
        self._page = page
        self._pages.addWidget(page)
        self._pages.setCurrentWidget(page)
        self._header.show()
        self._update_header()

    def _discard_page(self) -> None:
        page = self._page
        if page is None:
            return
        self._page = None
        self._pages.removeWidget(page)
        page.hide()
        page.setParent(None)
        page.deleteLater()

    def _clear_highlight(self) -> None:
        if self._entries and self._entries[0].origin is not None:
            self._entries[0].origin.highlight(None)

    def _holds_focus(self) -> bool:
        focus = QApplication.focusWidget()
        return focus is not None and (focus is self or self.isAncestorOf(focus))

    def _after_change(self, had_focus: bool) -> None:
        """Keep keyboard focus inside the stack when it was there, so Esc still reaches it.

        A page that shows takes focus on its main view (its focus proxy), unless the user is working the header
        buttons; a selection made elsewhere, such as on the video, leaves focus where it is.
        """
        if had_focus:
            focus = QApplication.focusWidget()
            in_header = focus is not None and self._header.isAncestorOf(focus) and focus.isVisible()
            if self._page is not None and not in_header:
                self._page.setFocus(Qt.FocusReason.OtherFocusReason)
            focus = QApplication.focusWidget()
            if focus is None or focus is self or not self.isAncestorOf(focus):
                self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.pageChanged.emit(self.current_key())

    def _update_header(self) -> None:
        entry = self._entries[-1]
        previous = self._entries[-2].key.title if len(self._entries) > 1 else self._root_titles[self._root]
        self._back_button.set_previous(previous)
        self._title.set_full_text(entry.key.title)
        origin = entry.origin
        self._previous_button.setEnabled(origin is not None and origin.neighbor(entry.key, -1) is not None)
        self._next_button.setEnabled(origin is not None and origin.neighbor(entry.key, 1) is not None)

    def _apply_appearance(self) -> None:
        self._header.setFixedHeight(Height.PANE_HEADER.px)
        # The title keeps enough of an id to tell it, however narrow the panel is.
        self._title.setMinimumWidth(_TITLE_CHARS * QFontMetrics(TextRole.STRONG.font()).averageCharWidth())
        border = palette_color_css(self.palette(), QPalette.ColorRole.Mid, alpha=0.5)
        self._header.setStyleSheet(
            f"#AxDevilPageHeader {{ border-bottom: 1px solid {border}; background: transparent; }}"
        )
