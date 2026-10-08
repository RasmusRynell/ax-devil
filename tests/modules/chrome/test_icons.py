"""Bundled icons draw in the current palette; menu buttons keep their menus readable."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.chrome.theme import apply_theme


def _same_color(colors: list[QColor], expected: QColor) -> bool:
    """Return whether every color matches ``expected`` up to antialiasing rounding."""
    rgb = (expected.red(), expected.green(), expected.blue())
    return all(max(abs(a - b) for a, b in zip((c.red(), c.green(), c.blue()), rgb)) <= 2 for c in colors)


def _opaque_colors(icon: QIcon, mode: QIcon.Mode = QIcon.Mode.Normal) -> list[QColor]:
    image = icon.pixmap(32, 32, mode).toImage()
    return [image.pixelColor(x, y) for y in range(32) for x in range(32) if image.pixelColor(x, y).alpha() > 200]


@pytest.mark.parametrize("icon", list(Icon))
def test_every_icon_draws(icon: Icon, qapp: QApplication) -> None:
    assert _opaque_colors(icon.icon())


@pytest.mark.usefixtures("restore_app_appearance")
def test_icon_without_color_follows_theme_changes(qapp: QApplication) -> None:
    """One icon object recolors with the theme, so widgets need no retint on appearance changes."""
    icon = Icon.PIN.icon()
    for mode in ("dark", "light"):
        apply_theme(mode)
        expected = qapp.palette().color(QPalette.ColorRole.WindowText)
        assert _same_color(_opaque_colors(icon), expected), mode


def test_fixed_color_icon_dims_when_disabled(qapp: QApplication) -> None:
    icon = Icon.PLAY.icon(QColor("#ff0000"))

    assert _same_color(_opaque_colors(icon), QColor("#ff0000"))
    assert not _opaque_colors(icon, QIcon.Mode.Disabled)


def test_menu_button_keeps_menu_left_to_right(qtbot: QtBot) -> None:
    menu = QMenu()
    button = MenuButton("Filter", menu)
    qtbot.addWidget(button)

    assert button.menu() is menu
    assert menu.layoutDirection() == Qt.LayoutDirection.LeftToRight
