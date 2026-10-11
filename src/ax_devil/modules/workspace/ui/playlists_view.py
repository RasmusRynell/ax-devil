"""The Playlists view: the playlists in the workspace, and the resolvers that can add more."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QShowEvent
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import ListRow, muted_label
from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.chrome.sidebar_view import SidebarView, ViewGroup
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import KindColor, StatusColor
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.chrome.view_panel import ViewPanel
from ax_devil.modules.workspace.core import Content

PLAYLISTS_VIEW_ID = "playlists"
SHOWN_ENTRIES = 20
"""How many entries an expanded playlist lists; one row counts the rest."""


class PlaylistState(Enum):
    """How far a playlist got, with the mark its row shows and whether it can be rescanned."""

    READY = (Icon.PLAYLIST, KindColor.PLAYLIST, True)
    PENDING = (Icon.PLAYLIST, KindColor.MUTED, False)
    FAILED = (Icon.WARNING, StatusColor.WARNING, True)

    def __init__(self, icon: Icon, mark: KindColor | StatusColor, rescannable: bool) -> None:
        self.icon = icon
        self.mark = mark
        self.rescannable = rescannable


@dataclass(frozen=True)
class EntryListing:
    """One entry of a listed playlist: its name, muted detail, and the playlist and index that open it."""

    title: str
    detail: str
    content: Content
    index: int


@dataclass(frozen=True)
class PlaylistListing:
    """One playlist row: *key* tells it apart across updates, *item_id* is the item a rescan resolves again."""

    key: str
    item_id: str
    title: str
    subtitle: str
    state: PlaylistState
    entries: tuple[EntryListing, ...] = ()


@dataclass(frozen=True)
class ResolverListing:
    """A loaded playlist resolver the user can add playlists from."""

    resolver_id: str
    title: str
    description: str


class PlaylistsView(ViewPanel):
    """List the playlists and the resolvers it is given, and report what the user asks for.

    Clicking a playlist shows or hides its entries; clicking an entry asks to open it. The view holds no workspace
    state beyond which playlists are expanded.
    """

    rescan_requested = Signal(str)  # item id
    entry_requested = Signal(object, int)  # Content, entry index
    add_requested = Signal(str)  # resolver id
    shown = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        self._count = muted_label("")
        super().__init__("Playlists", header_widget=self._count, parent=parent)
        self._playlists: tuple[PlaylistListing, ...] = ()
        self._resolvers: tuple[ResolverListing, ...] = ()
        self._expanded: set[str] = set()
        content = QWidget()
        self._rows = QVBoxLayout(content)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        scroll.setWidget(content)
        self.add(scroll)
        self.body.setStretchFactor(scroll, 1)
        self.add_padded(muted_label("More resolvers come from plugins."), top=Space.S, bottom=Space.M)
        follow_appearance(self, self._render)

    def sidebar_view(self) -> SidebarView:
        """Return this view as a sidebar view among the sources."""
        return SidebarView(PLAYLISTS_VIEW_ID, "Playlists", Icon.PLAYLIST, ViewGroup.SOURCES, self)

    def set_listings(self, playlists: Sequence[PlaylistListing], resolvers: Sequence[ResolverListing]) -> None:
        """List *playlists* in the workspace, and *resolvers* to add more from; playlists still listed stay expanded."""
        self._playlists = tuple(playlists)
        self._resolvers = tuple(resolvers)
        self._expanded &= {playlist.key for playlist in self._playlists}
        self._render()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Report that the view came on screen, so its owner can bring it up to date."""
        super().showEvent(event)
        self.shown.emit()

    def _toggle(self, key: str) -> None:
        self._expanded ^= {key}
        self._render()

    def _render(self) -> None:
        """Rebuild the rows; icons are drawn for the palette at hand, so a theme change rebuilds them too."""
        while (item := self._rows.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.hide()
                widget.deleteLater()
        self._count.setText(str(len(self._playlists)))
        self._count.setVisible(bool(self._playlists))
        palette = self.palette()
        if self._playlists:
            self._add_row(SectionCaption("In workspace", len(self._playlists), KindColor.PLAYLIST.color))
        for playlist in self._playlists:
            self._add_playlist(playlist)
        if self._resolvers:
            self._add_row(SectionCaption("Add from", len(self._resolvers), KindColor.MUTED.color))
        resolver_icon = Icon.PLAYLIST.icon(KindColor.MUTED.color(palette))
        for resolver in self._resolvers:
            add = SmallButton("Add", Icon.ADD)
            add.clicked.connect(lambda _=False, resolver_id=resolver.resolver_id: self.add_requested.emit(resolver_id))
            self._add_row(
                ListRow(resolver.title, icon=resolver_icon, subtitle=resolver.description, trailing=[add], wrap=True)
            )
        self._rows.addStretch(1)

    def _add_row(self, widget: QWidget) -> None:
        """Append *widget* shown at once; rows left to show later would be laid out as an empty list first, and the
        list would lose its scroll position."""
        self._rows.addWidget(widget)
        widget.show()

    def _add_playlist(self, playlist: PlaylistListing) -> None:
        state = playlist.state
        trailing: list[QWidget] = []
        if state.rescannable:
            rescan = SmallButton("", Icon.REFRESH)
            rescan.setToolTip("Rescan")
            rescan.clicked.connect(lambda _=False, item_id=playlist.item_id: self.rescan_requested.emit(item_id))
            trailing.append(rescan)
        expanded = playlist.key in self._expanded and bool(playlist.entries)
        row = ListRow(
            playlist.title,
            icon=state.icon.icon(state.mark.color(self.palette())),
            subtitle=playlist.subtitle,
            trailing=trailing,
            active=expanded,
        )
        if playlist.entries:
            row.activated.connect(lambda key=playlist.key: self._toggle(key))
        self._add_row(row)
        if not expanded:
            return
        entry_icon = Icon.VIDEO.icon(KindColor.VIDEO.color(self.palette()))
        for entry in playlist.entries[:SHOWN_ENTRIES]:
            entry_row = ListRow(entry.title, icon=entry_icon, detail=entry.detail, indent=Space.XL)
            entry_row.activated.connect(
                lambda content=entry.content, index=entry.index: self.entry_requested.emit(content, index)
            )
            self._add_row(entry_row)
        hidden = len(playlist.entries) - SHOWN_ENTRIES
        if hidden > 0:
            # An empty icon keeps the text in line with the entry names above.
            more = ListRow(f"{hidden} more", icon=QIcon(), indent=Space.XL, dim=True)
            more.setAttribute(Qt.WidgetAttribute.WA_Hover, False)
            self._add_row(more)
