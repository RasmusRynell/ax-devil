"""Bundled icons draw in the current palette; menu buttons keep their menus readable."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QWidget
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


def test_menu_button_puts_the_chevron_after_the_text_and_keeps_the_menu_left_to_right(qtbot: QtBot) -> None:
    window = QWidget()
    window.setLayoutDirection(Qt.LayoutDirection.RightToLeft)  # the menu would inherit this unless the button resets it
    qtbot.addWidget(window)
    menu = QMenu(window)
    button = MenuButton("Filter", menu, window)
    button.resize(button.sizeHint())

    image = button.grab().toImage()
    background = image.pixelColor(0, 0)
    inked = [
        x for x in range(image.width()) if any(image.pixelColor(x, y) != background for y in range(image.height()))
    ]
    runs: list[list[int]] = [[inked[0]]]
    for x in inked[1:]:
        if x - runs[-1][-1] > 4:  # a gap wider than letter spacing separates the text from the chevron
            runs.append([])
        runs[-1].append(x)

    assert len(runs) == 2
    text, chevron = runs
    assert len(chevron) < len(text), "the small chevron sits to the right of the word"
    assert menu.layoutDirection() == Qt.LayoutDirection.LeftToRight
