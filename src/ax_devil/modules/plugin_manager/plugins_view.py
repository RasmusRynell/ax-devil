"""The Plugins view: what loaded, what failed and why, and adding, updating, removing and reloading plugins."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette, QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.banner import Banner
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import ListRow, muted_label
from ax_devil.modules.chrome.row_list import RowList
from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.chrome.sidebar_view import SidebarView, ViewGroup
from ax_devil.modules.chrome.small_button import ButtonKind, SmallButton
from ax_devil.modules.chrome.theme import SEE_THROUGH, KindColor, StatusColor
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.chrome.view_panel import ViewPanel
from ax_devil.modules.plugin_manager.add_plugin_dialog import AddPluginDialog
from ax_devil.modules.plugin_manager.catalog import (
    PluginEntry,
    PluginGroup,
    PluginPart,
    Reads,
    Section,
    current_catalog,
)
from ax_devil.modules.plugin_manager.plugin_changes import PluginChange, PluginChanges
from ax_devil.modules.plugin_system import ApplicationPluginLoader

PLUGINS_VIEW_ID = "plugins"

_ICON_PX = 16
_READS_ICON_PX = 14
_ROW_EDGE_PX = 2  # The accent edge left of a ListRow's content.
_READS_ICONS = {Reads.FILE: Icon.FILE, Reads.LIVE: Icon.BROADCAST}


def _muted(palette: QPalette) -> QColor:
    return palette.color(QPalette.ColorRole.PlaceholderText)


def _muted_line(text: str) -> ElidedLabel:
    """Return a small muted line that elides *text* to its width; the tooltip has it whole."""
    line = ElidedLabel(text, Qt.TextElideMode.ElideRight)
    TextRole.SMALL.apply(line)
    line.setStyleSheet("color: palette(placeholder-text);")
    return line


@dataclass(frozen=True)
class _Look:
    """How a section's rows are marked: their icon in its color, and the color of the section's caption mark."""

    icon: Icon
    color: Callable[[QPalette], QColor]
    mark: Callable[[QPalette], QColor]


_LOOKS = {
    Section.FAILED: _Look(Icon.WARNING, StatusColor.WARNING.color, StatusColor.WARNING.color),
    Section.INSTALLED: _Look(Icon.PLUGIN, StatusColor.SUCCESS.color, KindColor.INFO.color),
    Section.BUILT_IN: _Look(Icon.PLUGIN, _muted, KindColor.MUTED.color),
}
_RESOLVER_LOOK = {section: _Look(Icon.PLAYLIST, look.color, look.mark) for section, look in _LOOKS.items()}


def _failed_warning(groups: tuple[PluginGroup, ...]) -> str:
    failed = sum(len(group.entries) for group in groups if group.section is Section.FAILED)
    return f"{failed} failed to load" if failed else ""


class PluginsView(ViewPanel):
    """List the plugins by section and run plugin changes; the list is read again whenever the view is shown.

    Changes apply when the app restarts: once one succeeds, a banner offers the restart, which ``restart_requested``
    asks the window for. ``warning_changed`` carries what to flag on the view's button: the failed plugins, or empty.
    """

    restart_requested = Signal()
    warning_changed = Signal(str)

    def __init__(
        self,
        changes: PluginChanges | None = None,
        catalog: Callable[[], tuple[PluginGroup, ...]] = current_catalog,
        reload: Callable[[], None] = ApplicationPluginLoader.reload_plugins,
        parent: QWidget | None = None,
    ) -> None:
        reload_button = SmallButton("", Icon.REFRESH)
        reload_button.setToolTip("Reload plugins")
        reload_button.setAccessibleName("Reload plugins")
        self._add_button = SmallButton("Add", Icon.ADD, ButtonKind.PRIMARY)
        self._add_button.setToolTip("Add plugin")
        header_buttons = QWidget()
        header_buttons.setStyleSheet(SEE_THROUGH)
        header_layout = QHBoxLayout(header_buttons)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(Space.S)
        header_layout.addWidget(reload_button)
        header_layout.addWidget(self._add_button)
        super().__init__("Plugins", header_widget=header_buttons, parent=parent)
        self._changes = changes if changes is not None else PluginChanges(parent=self)
        self._catalog = catalog
        self._reload = reload
        self._groups: tuple[PluginGroup, ...] = ()
        self._failure: tuple[PluginChange, str] | None = None
        self._change_buttons: list[QWidget] = []

        self._banner = Banner("Restart to apply the plugin changes", "Restart")
        self._banner.action.clicked.connect(self.restart_requested)
        self._banner_holder = self.add_padded(self._banner, top=Space.M, bottom=Space.S)

        self._rows = RowList(self)
        self.add(self._rows, 1)

        self._add_button.clicked.connect(self._add_plugin)
        self._install_status = _muted_line("")
        self._install_status_holder = self.add_padded(self._install_status, top=Space.S, bottom=Space.S)
        self.add_padded(muted_label("Installs and updates apply after a restart."), top=0, bottom=Space.M)

        reload_button.clicked.connect(self._reload_plugins)
        self._changes.started.connect(self._on_change_started)
        self._changes.finished.connect(self._on_change_finished)
        follow_appearance(self, self._render)

    def sidebar_view(self) -> SidebarView:
        """Return this view as an application view of the sidebar."""
        return SidebarView(
            PLUGINS_VIEW_ID,
            "Plugins",
            Icon.PLUGIN,
            ViewGroup.APP,
            self,
            warning=self.warning,
            warning_changed=self.warning_changed,
        )

    def warning(self) -> str:
        """Return how many plugins failed to load, or an empty string when none did."""
        return _failed_warning(self._catalog())

    def refresh(self) -> None:
        """Read the plugins again and list them."""
        self._groups = self._catalog()
        self._render()
        self.warning_changed.emit(_failed_warning(self._groups))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """List the plugins as they are now, including failures recorded since the view was last shown."""
        super().showEvent(event)
        self.refresh()

    def _reload_plugins(self) -> None:
        self._reload()
        self.refresh()

    def _add_plugin(self) -> None:
        with AddPluginDialog(self) as dialog:
            if dialog.exec() != AddPluginDialog.DialogCode.Accepted or not dialog.source():
                return
            source = dialog.source()
        self._changes.start(PluginChange.install(source))

    def _on_change_started(self) -> None:
        self._failure = None
        self._render()

    def _on_change_finished(self, change: PluginChange, reason: str) -> None:
        self._failure = (change, reason) if reason else None
        self.refresh()

    def _render(self) -> None:
        """List the sections read last, with the state of the change that is running or has failed."""
        self._rows.clear()
        self._change_buttons = [self._add_button, self._banner.action]
        for group in self._groups:
            self._rows.add(SectionCaption(group.section.title, len(group.entries), _LOOKS[group.section].mark))
            for entry in group.entries:
                self._add_row(entry)

        running = self._changes.running
        for button in self._change_buttons:
            button.setEnabled(running is None)
        self._banner_holder.setVisible(self._changes.restart_needed)
        install_status = ""
        if running is not None and not running.name:
            install_status = running.kind.progress
        elif self._failure is not None and not self._failure[0].name:
            install_status = self._failure[1]
        self._install_status.set_full_text(install_status)
        self._install_status_holder.setVisible(bool(install_status))

    def _add_row(self, entry: PluginEntry) -> None:
        running = self._changes.running
        subtitle = running.kind.progress if running is not None and running.name == entry.name else entry.subtitle
        look = (_RESOLVER_LOOK if entry.resolver else _LOOKS)[entry.section]
        trailing = self._reads_icons(entry.reads)
        if entry.requirement is not None:
            trailing.append(self._more_button(entry, entry.requirement))
        icon = look.icon.icon(look.color(self.palette()))
        self._rows.add(
            ListRow(entry.name, icon=icon, subtitle=subtitle, detail=entry.detail, trailing=trailing, wrap=True)
        )
        for part in entry.parts:
            self._add_part(part)
        if self._failure is not None and self._failure[0].name == entry.name:
            self._rows.add(self._failure_line(self._failure[1]))

    def _add_part(self, part: PluginPart) -> None:
        """List what a plugin gives, in under its row: a decoder with where it reads from, or a resolver."""
        icon = Icon.PLAYLIST if part.resolver else _READS_ICONS[part.reads[0]]
        self._rows.add(
            ListRow(
                part.name,
                icon=icon.icon(_muted(self.palette())),
                subtitle=part.description,
                trailing=self._reads_icons(part.reads),
                indent=_ICON_PX + Space.M,
                wrap=True,
            )
        )

    def _reads_icons(self, reads: tuple[Reads, ...]) -> list[QWidget]:
        """Return a small muted icon for each place a decoder reads from, with a tooltip saying which."""
        icons: list[QWidget] = []
        for source in reads:
            mark = QLabel()
            mark.setPixmap(_READS_ICONS[source].icon(_muted(self.palette())).pixmap(_READS_ICON_PX, _READS_ICON_PX))
            mark.setToolTip(source.value)
            icons.append(mark)
        return icons

    def _more_button(self, entry: PluginEntry, requirement: str) -> QToolButton:
        button = QToolButton()
        menu = QMenu(button)
        menu.addAction("Update", lambda: self._changes.start(PluginChange.update(entry.name, requirement)))
        menu.addAction("Remove", lambda: self._changes.start(PluginChange.remove(entry.name)))
        button.setIcon(Icon.MORE.icon())
        button.setAutoRaise(True)
        button.setToolTip("Update or remove")
        button.setAccessibleName(f"Update or remove {entry.name}")
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self._change_buttons.append(button)
        return button

    def _failure_line(self, reason: str) -> QWidget:
        """Return *reason* as a muted line under a row, lined up with the row's title."""
        line = _muted_line(reason)
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(_ROW_EDGE_PX + Space.M + _ICON_PX + Space.M, 0, Space.M, Space.XS)
        layout.addWidget(line)
        return holder
