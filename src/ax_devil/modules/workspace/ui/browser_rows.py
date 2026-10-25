"""Sidebar browser rows: the data every sidebar view shows of a Workspace item or its Content.

The rows also carry what every sidebar view shows of them, the actions a row offers and whether it is idle, so the
views render one look and one menu without deciding it again. ``browser_projection`` builds them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Literal

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.theme import KindColor, StatusColor
from ax_devil.modules.workspace.core.consideration import ConsiderationItemRef, OnScreenWorkspaceItem
from ax_devil.modules.workspace.core.content import (
    Content,
)
from ax_devil.modules.workspace.core.item_base import WorkspaceItem
from ax_devil.modules.workspace.core.item_info import (
    WorkspaceItemInfo,
)
from ax_devil.modules.workspace.ui.live_stream_status import LiveStreamStatus

WorkspaceBrowserIconKind = Literal["video", "live_video", "playlist", "overlay", "unavailable"]

# Each kind keeps one icon and color, so live streams, videos, and playlists read apart in a mixed workspace.
KIND_MARKS: dict[WorkspaceBrowserIconKind, tuple[Icon, KindColor | StatusColor]] = {
    "live_video": (Icon.LIVE_VIDEO, KindColor.LIVE),
    "video": (Icon.VIDEO, KindColor.VIDEO),
    "playlist": (Icon.PLAYLIST, KindColor.PLAYLIST),
    "overlay": (Icon.OVERLAY, KindColor.OVERLAY),
    "unavailable": (Icon.WARNING, StatusColor.WARNING),
}


class BrowserSection(Enum):
    """A titled group of top-level rows, in the order the browser lists them, with the icon kind it is marked by.

    An *addable* section lists things the Workspace does not hold yet, which its rows' Add adds; its mark is muted.
    A section listed without rows says its *empty_text* under its caption, or is hidden when it has none. A section
    *lit_by_live_stream* marks its caption in its kind's color only while one of its rows has a live stream.
    """

    LIVE = ("Live", "live_video", False, "", True)
    VIDEOS = ("Videos", "video", False)
    PLAYLISTS = ("Playlists", "playlist", False)
    OTHER = ("Other", "unavailable", False)
    NETWORK = ("On the network", "live_video", True, "No new cameras")
    """Cameras a network scan found."""

    def __init__(
        self,
        title: str,
        icon_kind: WorkspaceBrowserIconKind,
        addable: bool,
        empty_text: str = "",
        lit_by_live_stream: bool = False,
    ) -> None:
        self.title = title
        self.icon_kind = icon_kind
        self.addable = addable
        self.empty_text = empty_text
        self.lit_by_live_stream = lit_by_live_stream


HELD_SECTIONS = frozenset(section for section in BrowserSection if not section.addable)
"""The sections of what the Workspace holds."""


@dataclass(frozen=True, slots=True)
class Facet:
    """Something the Filter can hide rows by: a *value* in a *group*, such as Network Speaker under Type."""

    group: str
    value: str


class RowActionGroup(Enum):
    """The set of related actions a row's menu keeps together, split from the next by a separator."""

    OPEN = "open"
    INSPECT = "inspect"
    VISIBILITY = "visibility"
    EDIT = "edit"


class RowAction(Enum):
    """Something the user can do with a row, in the order a row's menu lists it, grouped by *group*.

    Each action has its menu text and hover text; an *inline* one is also the button at the row's right, with its own
    button text and icon.
    """

    OPEN = (
        "Open",
        "",
        None,
        "Replace the active preview, or open beside the active pinned pane",
        False,
        RowActionGroup.OPEN,
    )
    OPEN_TO_SIDE = (
        "Open to the Side",
        "",
        None,
        "Open pinned in a new split (Ctrl+double-click)",
        False,
        RowActionGroup.OPEN,
    )
    UNHIDE_AND_OPEN = (
        "Unhide and open",
        "Unhide and open",
        None,
        "Unhide this lane and focus it",
        True,
        RowActionGroup.OPEN,
    )
    START_STREAM = ("Start stream", "Start stream", None, "", True, RowActionGroup.OPEN)
    STOP_STREAM = ("Stop stream", "Stop stream", None, "", True, RowActionGroup.OPEN)
    ADD = ("Add", "Add", Icon.ADD, "", True, RowActionGroup.OPEN)
    INFORMATION = ("Information", "", None, "", False, RowActionGroup.INSPECT)
    EXPORT_VIDEO = ("Export video", "", None, "", False, RowActionGroup.INSPECT)
    HIDE = ("Hide", "", None, "Skip it in playback", False, RowActionGroup.VISIBILITY)
    UNHIDE = ("Unhide", "", None, "Play it again", False, RowActionGroup.VISIBILITY)
    EDIT = ("Edit", "", None, "", False, RowActionGroup.EDIT)
    REMOVE = ("Remove", "", None, "", False, RowActionGroup.EDIT)

    def __init__(
        self,
        menu_text: str,
        button_text: str,
        icon: Icon | None,
        tooltip: str,
        inline: bool,
        group: RowActionGroup,
    ) -> None:
        self.menu_text = menu_text
        self.button_text = button_text
        self.icon = icon
        self.tooltip = tooltip
        self.inline = inline
        self.group = group


@dataclass(frozen=True, slots=True)
class OfferedAction:
    """A *action* a row offers; with a *disabled_reason* it is listed but cannot be done, and the reason says why."""

    action: RowAction
    disabled_reason: str = ""


@dataclass(frozen=True, slots=True)
class BrowserOpenTarget:
    """Content and optional exact entry and lane selected by a browser row."""

    content: Content
    entry_index: int | None
    lane_index: int | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceBrowserRow:
    """Explicit row data rendered by every sidebar view's section tree.

    The projection resolves the final detail before publication; the second line, search, and accessibility use the
    same text. Hovering shows the row's facts, location, and state.
    """

    row_id: str
    label: str
    icon_kind: WorkspaceBrowserIconKind
    activation_target: BrowserOpenTarget | None = None
    consideration_ref: ConsiderationItemRef | None = None
    item: WorkspaceItem | None = None
    """The item a top-level row stands for, which the row can edit or remove; None for nested rows."""
    unavailable_reason: str | None = None
    """Why the row's item could not open; such a row shows the reason instead of opening."""
    warning: str | None = None
    """A problem with the row's item that does not stop it from opening, shown on hover."""
    information_factory: Callable[[], WorkspaceItemInfo] | None = None
    export_target: OnScreenWorkspaceItem | None = None
    is_considered: bool = True
    is_open: bool = False
    children: tuple[WorkspaceBrowserRow, ...] = ()
    location: str | None = None
    detail: str = ""
    """The final muted text shown under the label; empty for none, which keeps the row one line high."""
    wrap_detail: bool = False
    """Whether the detail may take multiple lines instead of being elided."""
    facts: tuple[str, ...] = ()
    """Lines that open the hover text, starting with what kind of row this is."""
    actions: tuple[OfferedAction, ...] = ()
    """The actions the row offers, in menu order; the first inline one is the button at the row's right."""
    add: Callable[[], None] | None = None
    """What the Add action of a row for something the Workspace does not hold does: add it, or ask how."""
    idle: bool = False
    """Whether the row's thing is not running: a camera no viewer shows, or an item still loading."""
    stream: LiveStreamStatus | None = None
    """How a camera's stream is doing, which marks the row and ends its detail; None for a row of no camera."""
    search_terms: str = ""
    """More text the search matches beyond what the row shows, such as a found camera's serial number."""
    facets: frozenset[Facet] = frozenset()
    """What the Filter can hide the row by."""
    expanded_at_first: bool = False
    """Whether the row shows the rows under it until the user collapses it."""

    def offered(self, action: RowAction) -> OfferedAction | None:
        """Return *action* as the row offers it, or None when it does not."""
        return next((offered for offered in self.actions if offered.action is action), None)

    @property
    def inline_action(self) -> OfferedAction | None:
        """Return the action shown as a button on the row, if it offers one."""
        return next((offered for offered in self.actions if offered.action.inline), None)

    @property
    def is_available(self) -> bool:
        """Return whether the row stands for something the Workspace does not hold yet, which its Add adds."""
        return self.offered(RowAction.ADD) is not None

    @property
    def is_mark_muted(self) -> bool:
        """Return whether the row's kind mark is drawn muted instead of in its kind's color."""
        return self.idle or self.is_available

    @property
    def mark(self) -> tuple[Icon, KindColor | StatusColor]:
        """Return the icon and color role of the row's mark: its stream's state or else its kind; muted when idle."""
        icon, color = self.stream.mark if self.stream is not None else KIND_MARKS[self.icon_kind]
        return icon, KindColor.MUTED if self.is_mark_muted else color

    @property
    def is_dimmed(self) -> bool:
        """Return whether the row's name is dimmed: it is idle, hidden from playback, or cannot open."""
        return self.idle or not self.is_considered or self.unavailable_reason is not None

    @property
    def search_text(self) -> str:
        """Return the text the search field matches: the label, detail and search terms."""
        return " ".join(part for part in (self.label, self.detail, self.search_terms) if part)

    @property
    def tooltip(self) -> str:
        """Return the hover text below the row's name: its facts, why it is unavailable, its location, whether
        it is hidden from playback, and whether it is open."""
        lines = [
            *self.facts,
            self.unavailable_reason or "",
            self.warning or "",
            self.location or "",
            "Hidden: skipped in playback" if not self.is_considered else "",
            "Open in a viewer" if self.is_open else "",
        ]
        return "\n".join(line for line in lines if line)


@dataclass(frozen=True, slots=True)
class SectionAction:
    """A button beside a section's caption: *text* and *icon*, *tooltip* on hover, and *run* when it is pressed."""

    text: str
    icon: Icon
    run: Callable[[], None]
    tooltip: str = ""


@dataclass(frozen=True, slots=True)
class WorkspaceBrowserSection:
    """One browser heading and the item rows it groups; the count comes from those rows.

    A section with an *action* shows it beside its caption.
    """

    kind: BrowserSection
    rows: tuple[WorkspaceBrowserRow, ...]
    action: SectionAction | None = None

    @property
    def always_shown(self) -> bool:
        """Return whether the section's caption stays listed without a row shown: it has an action, or has no rows
        and its kind says an empty text instead."""
        return self.action is not None or (not self.rows and bool(self.kind.empty_text))

    @property
    def mark_color(self) -> KindColor | StatusColor:
        """Return the color of the caption's mark: its kind's, muted when the section is addable or lit by a live
        stream and none of its rows has one."""
        kind = self.kind
        unlit = kind.lit_by_live_stream and not any(row.stream is not None and row.stream.any_live for row in self.rows)
        return KindColor.MUTED if kind.addable or unlit else KIND_MARKS[kind.icon_kind][1]


@dataclass(frozen=True, slots=True)
class RowOrder:
    """A named order for the rows of a view: rows sort by what *key* returns, and equal keys keep the order given."""

    label: str
    key: Callable[[WorkspaceBrowserRow], tuple[Any, ...]]

    def arrange(self, sections: Sequence[WorkspaceBrowserSection]) -> tuple[WorkspaceBrowserSection, ...]:
        """Return *sections* with the top-level rows of each in this order; the rows under a row keep theirs, so a
        playlist's entries stay in playlist order."""
        return tuple(replace(section, rows=tuple(sorted(section.rows, key=self.key))) for section in sections)


WORKSPACE_ORDER = RowOrder("Workspace order", lambda _row: ())
"""Rows as the workspace, or whatever feeds the view, lists them."""
BY_NAME = RowOrder("Name", lambda row: (row.label.casefold(),))
