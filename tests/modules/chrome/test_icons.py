"""Bundled icons draw in the current palette; menu buttons keep their menus readable."""

from typing import Any

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.icons import Icon, _render
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


def test_size_qt_cannot_allocate_draws_nothing(qapp: QApplication, qtlog: Any) -> None:
    """A pixmap Qt cannot allocate has no paint engine; rendering it skips painting and logs no QPainter warnings."""
    pixmap = _render(Icon.PLAY, QColor("#ffffff"), 1.5, QSize(16777215, 16777215), 1.0)

    assert pixmap.paintEngine() is None
    assert not qtlog.records


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
    # The same button with the text blanked out by spaces as wide as the word, with and without its icon: what differs
    # is the chevron, whatever frame or background the platform style draws.
    space = button.fontMetrics().horizontalAdvance("\u00a0")
    blank_text = "\u00a0" * round(button.fontMetrics().horizontalAdvance("Filter") / space)
    with_icon, without_icon = MenuButton(blank_text, menu, window), MenuButton(blank_text, menu, window)
    without_icon.setIcon(QIcon())
    with_icon.resize(button.size())
    without_icon.resize(button.size())

    first, second = with_icon.grab().toImage(), without_icon.grab().toImage()
    inked = [
        x
        for x in range(first.width())
        if any(first.pixelColor(x, y) != second.pixelColor(x, y) for y in range(first.height()))
    ]

    assert inked, "the chevron is drawn"
    assert min(inked) > first.width() // 2, "the small chevron sits to the right of the word"
    assert menu.layoutDirection() == Qt.LayoutDirection.LeftToRight
