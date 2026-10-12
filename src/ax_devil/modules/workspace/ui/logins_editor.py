"""Edit the logins the network scan tries, in order: a username and password per row, added and removed by button."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import SEE_THROUGH
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.device_discovery.network_scan import Login
from ax_devil.modules.settings.config_manager import is_environment_reference

_VISIBLE_ROWS = 5


class LoginsEditor(QWidget):
    """A username and a password field per login, each with a remove button, then a button that adds a login.

    Five rows show at once; more scroll. A password shows as dots unless it is a ``$VARIABLE`` reference, as other
    secret settings do. ``logins`` leaves out a row without a username, and ``edited`` reports a finished edit or a
    removed row.
    """

    edited = Signal()

    def __init__(self, logins: Sequence[Login] = (), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[QWidget, QLineEdit, QLineEdit]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.S)
        rows = QWidget()
        rows.setStyleSheet(SEE_THROUGH)
        self._list = QVBoxLayout(rows)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(Space.S)
        self._list.addStretch(1)
        self._scroll = QScrollArea()
        self._scroll.setWidget(rows)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(SEE_THROUGH)
        # Deferred, so the rows have taken the new text size before the list measures them.
        follow_appearance(self._scroll, lambda: QTimer.singleShot(0, self, self._fit_rows))
        layout.addWidget(self._scroll)
        add = SmallButton("Add login", Icon.ADD)
        add.clicked.connect(lambda: self._add_row(Login("", "")).setFocus())
        layout.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        self.set_logins(logins)

    def set_logins(self, logins: Sequence[Login]) -> None:
        """Show *logins*, in order, in place of the rows shown."""
        for row, _username, _password in self._rows:
            self._list.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._rows = []
        for login in logins:
            self._add_row(login)
        self._fit_rows()

    def _fit_rows(self) -> None:
        """Make the list as high as its rows, up to five."""
        shown = min(len(self._rows), _VISIBLE_ROWS)
        row_height = self._rows[0][0].sizeHint().height() if self._rows else 0
        self._scroll.setFixedHeight(shown * row_height + max(0, shown - 1) * Space.S)

    def logins(self) -> tuple[Login, ...]:
        """Return the logins entered, in order."""
        return tuple(
            Login(username.text().strip(), password.text())
            for _row, username, password in self._rows
            if username.text().strip()
        )

    def _add_row(self, login: Login) -> QLineEdit:
        """Add a row showing *login*, and return its username field."""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(Space.S)
        username = QLineEdit(login.username, row)
        username.setPlaceholderText("Username")
        username.setAccessibleName("Username")
        password = QLineEdit(login.password, row)
        password.setPlaceholderText("Password")
        password.setAccessibleName("Password")
        password.textChanged.connect(lambda text: _show_password(password, text))
        _show_password(password, login.password)
        for field in (username, password):
            TextRole.SMALL.apply(field)
            follow_appearance(field, lambda field=field: field.setFixedHeight(Height.ROW.px))
            field.editingFinished.connect(self.edited)
            row_layout.addWidget(field, 1)
        remove = SmallButton("", Icon.REMOVE)
        remove.setToolTip("Remove this login")
        remove.clicked.connect(lambda: self._remove(row))
        row_layout.addWidget(remove)
        self._list.insertWidget(len(self._rows), row)
        self._rows.append((row, username, password))
        self._fit_rows()
        return username

    def _remove(self, row: QWidget) -> None:
        self._rows = [entry for entry in self._rows if entry[0] is not row]
        self._list.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._fit_rows()
        self.edited.emit()


def _show_password(edit: QLineEdit, text: str) -> None:
    """Show a ``$VARIABLE`` reference as written, and any other password as dots."""
    edit.setEchoMode(QLineEdit.EchoMode.Normal if is_environment_reference(text) else QLineEdit.EchoMode.Password)
