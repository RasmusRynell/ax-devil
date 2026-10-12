"""Activity bar: the strip of icon buttons along the window's left edge."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QToolButton, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.sidebar_view import SidebarView, ViewGroup
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Height, Space

_ICON_STROKE = 1.75  # A little heavier than the default, so the small icons stay crisp on the bar.
_GROUP_ORDER = tuple(ViewGroup)
_BADGE_SCALE = 0.6  # The warning badge's side, as a share of the icon's.


class _BarButton(QToolButton):
    """A button that can carry a small badge on the lower right corner of its icon."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._badge: QIcon | None = None

    def set_badge(self, badge: QIcon | None) -> None:
        """Show *badge* over the icon, or nothing."""
        self._badge = badge
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Draw the button, then its badge."""
        super().paintEvent(event)
        if self._badge is None:
            return
        icon = self.iconSize()
        side = round(icon.width() * _BADGE_SCALE)
        corner = self.rect().center() + QPoint(icon.width() // 2, icon.height() // 2)
        badge_rect = QRect(0, 0, side, side)
        badge_rect.moveCenter(corner - QPoint(side // 4, side // 4))
        painter = QPainter(self)
        self._badge.paint(painter, badge_rect)
        painter.end()


class ActivityBar(QWidget):
    """A vertical strip of icon buttons: a button per sidebar view, and app-wide actions at the bottom.

    Views are grouped by their ``ViewGroup``, a rule between groups, in the groups' order; the ``APP`` group sits at
    the bottom above the actions. The button of the view on screen is checked, which the owner reports through
    ``set_current_view``; a click on any view button is reported as ``view_requested`` and changes nothing else.
    A view with a warning shows a small warning triangle on its icon, and the warning in its tooltip.
    """

    view_requested = Signal(str)  # view_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilActivityBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._views: list[tuple[SidebarView, _BarButton]] = []
        self._warnings: dict[str, str] = {}
        self._action_buttons: list[_BarButton] = []
        self._dividers: list[QFrame] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, Space.XS, 0, Space.XS)
        self._layout.setSpacing(Space.XS)
        self._top = QVBoxLayout()
        self._bottom = QVBoxLayout()
        self._actions = QVBoxLayout()
        for column in (self._top, self._bottom, self._actions):
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(Space.XS)
        self._layout.addLayout(self._top)
        self._layout.addStretch(1)
        self._layout.addLayout(self._bottom)
        self._layout.addLayout(self._actions)
        follow_appearance(self, self._apply_appearance)

    def add_view(self, view: SidebarView) -> QToolButton:
        """Add *view*'s button in its group; clicking it emits ``view_requested`` with the view's id."""
        button = self._new_button(view.icon, view.title, lambda: self.view_requested.emit(view.view_id))
        button.setCheckable(True)
        self._views.append((view, button))
        self._set_warning(view, button, view.warning())
        if view.warning_changed is not None:
            view.warning_changed.connect(lambda warning: self._set_warning(view, button, warning))
        self._arrange_views()
        return button

    def add_action(self, icon: Icon, tooltip: str, activate: Callable[[], None]) -> QToolButton:
        """Add an action button below the earlier ones at the bottom."""
        button = self._new_button(icon, tooltip, activate)
        self._action_buttons.append(button)
        self._actions.addWidget(button)
        return button

    def set_current_view(self, view_id: str | None) -> None:
        """Check the button of the view on screen; None, while the sidebar is hidden, checks none."""
        for view, button in self._views:
            button.setChecked(view.view_id == view_id)

    def view_button(self, view_id: str) -> QToolButton:
        """Return the button of the view with *view_id*."""
        return next(button for view, button in self._views if view.view_id == view_id)

    def _new_button(self, icon: Icon, tooltip: str, activate: Callable[[], None]) -> _BarButton:
        button = _BarButton(self)
        button.setAutoRaise(True)
        button.setIcon(icon.icon(stroke=_ICON_STROKE))
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False: activate())
        self._size_button(button)
        return button

    def _set_warning(self, view: SidebarView, button: _BarButton, warning: str) -> None:
        self._warnings[view.view_id] = warning
        self._show_look(view, button)

    def _show_look(self, view: SidebarView, button: _BarButton) -> None:
        """Give *button* the view's title, with a warning badge and the warning while the view has one."""
        warning = self._warnings.get(view.view_id, "")
        if warning:
            button.set_badge(Icon.WARNING.icon(StatusColor.WARNING.color(self.palette()), stroke=2.5))
            button.setToolTip(f"{view.title}: {warning}")
        else:
            button.set_badge(None)
            button.setToolTip(view.title)

    def _arrange_views(self) -> None:
        """Lay the view buttons out by group, with a rule between the groups of each column."""
        for divider in self._dividers:
            divider.deleteLater()
        self._dividers.clear()
        ordered = sorted(self._views, key=lambda pair: _GROUP_ORDER.index(pair[0].group))
        for column, at_bottom in ((self._top, False), (self._bottom, True)):
            while column.count():
                column.takeAt(0)
            previous: ViewGroup | None = None
            for view, button in ordered:
                if view.group.at_bottom != at_bottom:
                    continue
                if previous is not None and view.group is not previous:
                    column.addWidget(self._new_divider())
                column.addWidget(button)
                previous = view.group

    def _new_divider(self) -> QFrame:
        divider = QFrame(self)
        divider.setObjectName("AxDevilActivityDivider")
        divider.setFixedHeight(1)
        self._dividers.append(divider)
        return divider

    def _size_button(self, button: QToolButton) -> None:
        # Buttons span the bar, so the checked button's accent edge sits on the window edge.
        side = Height.TITLE_BAR.px
        button.setFixedSize(side + 2 * Space.XS, side)
        icon_side = round(side * 0.6)
        button.setIconSize(QSize(icon_side, icon_side))

    def _apply_appearance(self) -> None:
        for button in (*(button for _, button in self._views), *self._action_buttons):
            self._size_button(button)
        for view, button in self._views:
            self._show_look(view, button)
        palette = self.palette()
        text = palette.ColorRole.WindowText
        border = palette_color_css(palette, palette.ColorRole.Mid, alpha=0.5)
        accent = palette_color_css(palette, palette.ColorRole.Highlight)
        self.setStyleSheet(
            f"""
            #AxDevilActivityBar {{
                background: palette(window);
                border-right: 1px solid {border};
            }}
            #AxDevilActivityBar QToolButton {{
                border: none;
                border-left: 2px solid transparent;
                background: transparent;
            }}
            #AxDevilActivityBar QToolButton:hover {{ background: {palette_color_css(palette, text, alpha=0.08)}; }}
            #AxDevilActivityBar QToolButton:checked {{
                background: {palette_color_css(palette, text, alpha=0.1)};
                border-left: 2px solid {accent};
            }}
            #AxDevilActivityBar QToolButton:checked:hover {{
                background: {palette_color_css(palette, text, alpha=0.16)};
            }}
            #AxDevilActivityDivider {{
                background: {palette_color_css(palette, text, alpha=0.14)};
                margin: {Space.XS}px {Space.L}px;
            }}
            """
        )
