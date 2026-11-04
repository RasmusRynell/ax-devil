//! The one rule for what a seat may do: commands and stream demand alike.
//!
//! Every `match` here is exhaustive with no wildcard arm, so a new [`Use`], [`Scope`] or content kind does not compile
//! until its policy is decided.

use super::{Access, Lease, Scope, SeatId, SeatKind, SessionState, state::Doc, views::shown_entry};
use playback::{Authority, ClockIntent, Priority};
use serde::{Deserialize, Serialize};
use std::fmt;
use ts_rs::TS;
use workspace::{Content, ContentId, Entry, ItemId};
use workspace_store::{ContentGen, Resolution, WorkspaceState};

/// A stream a seat asks a ticket for: one entry of the Content at one commit of its resolution. Authority over the
/// stream ends with the generation, and the Content's kind decides what a seat may demand of it. A video's one entry
/// has the Content's own id.
#[derive(Clone, Debug, PartialEq, Eq, Deserialize, TS)]
pub struct ContentRef {
    /// The Content's id. Stable across re-resolution.
    #[ts(type = "string")]
    pub content: ContentId,
    /// The commit of the Content's resolution, as the Doc names it. Re-resolving or reopening gives a new one.
    #[ts(type = "number")]
    pub generation: ContentGen,
    /// The entry the stream plays; for a video or a live video, the Content's own id.
    #[ts(type = "string")]
    pub entry: ContentId,
}

/// What a seat is trying to do.
#[derive(Clone, Debug)]
pub enum Use<'a> {
    /// A command of this scope.
    Command(Scope),
    /// Receive the stream of this Content.
    Stream(&'a ContentRef),
}

/// Why a command or a connection was refused. A code and nothing else: the client words it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, TS)]
#[ts(rename = "RefusalCode")]
pub enum Refusal {
    /// The seat is not in the Session, its connection was replaced, or the Session ended.
    SeatGone,
    /// The seat's standing does not allow it.
    NotAllowed,
    /// Sharing is off.
    NotSharing,
    /// Sharing is already on.
    AlreadySharing,
    /// The Content is not open, not resolved, or not at the generation asked for.
    ContentUnavailable,
    /// The seat or the Session holds as many streams as it may.
    TooManyStreams,
    /// The ticket is unknown, expired, used, or bound to something that no longer exists.
    BadTicket,
    /// The link is no longer valid.
    Link,
    /// The Session already has as many seats as it may, or the server as many Sessions.
    Full,
    /// The input is not valid: text that is not a session id, secret, ticket or join link, or a command's own content
    /// that was refused before it reached the Session, such as a path that names no file.
    Invalid,
}

impl fmt::Display for Refusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::SeatGone => "that session or seat no longer exists",
            Self::NotAllowed => "your role cannot do that",
            Self::NotSharing => "the session is not shared",
            Self::AlreadySharing => "the session is already shared",
            Self::ContentUnavailable => "that content is not open",
            Self::TooManyStreams => "too many streams",
            Self::BadTicket => "that ticket is not valid",
            Self::Link => "that link is no longer valid",
            Self::Full => "that session or server is full",
            Self::Invalid => "that is not a session id, link or ticket",
        })
    }
}

impl std::error::Error for Refusal {}

/// A seat's standing: the row of the role table it is in.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Standing {
    Owner,
    Watching,
    Following,
    Exploring,
    Editing,
}

impl Standing {
    fn allows(self, scope: Scope, files: bool) -> bool {
        match scope {
            // Back to the presenter's view: a seat that follows, or explores a view it may drop. An editing seat's
            // view is its own to keep, and a watching seat has none.
            Scope::Follow => match self {
                Self::Owner | Self::Following | Self::Exploring => true,
                Self::Watching | Self::Editing => false,
            },
            Scope::View => match self {
                Self::Owner | Self::Following | Self::Exploring | Self::Editing => true,
                Self::Watching => false,
            },
            Scope::Content => match self {
                Self::Owner | Self::Editing => true,
                Self::Watching | Self::Following | Self::Exploring => false,
            },
            Scope::Host => match self {
                Self::Owner => files,
                Self::Watching | Self::Following | Self::Exploring | Self::Editing => false,
            },
            Scope::Control => match self {
                Self::Owner => true,
                Self::Watching | Self::Following | Self::Exploring | Self::Editing => false,
            },
        }
    }

    fn priority(self) -> Priority {
        match self {
            Self::Owner => Priority::High,
            Self::Watching | Self::Following | Self::Exploring | Self::Editing => Priority::Low,
        }
    }
}

/// What a seat may do, one flag per kind of command a window offers a control for; sent to the seat so its UI disables
/// what the backend would refuse. Derived from the same role table as `authorize`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, TS)]
pub struct Can {
    /// Change what the seat's window shows.
    pub view: bool,
    /// Change the Doc's content.
    pub content: bool,
    /// Read the host's files.
    pub host: bool,
    /// Change who the session is shared with.
    pub control: bool,
}

/// What `seat` at `lease` may do, or `SeatGone` when it is not a current seat of a live session.
pub fn can(state: &SessionState, seat: SeatId, lease: Lease) -> Result<Can, Refusal> {
    let standing = standing(state, seat, lease)?;
    let allows = |scope| standing.allows(scope, state.files);
    Ok(Can {
        view: allows(Scope::View),
        content: allows(Scope::Content),
        host: allows(Scope::Host),
        control: allows(Scope::Control),
    })
}

/// The standing of `seat` at `lease`, or `SeatGone` when it is not a current seat of a live session.
pub(super) fn standing(
    state: &SessionState,
    seat: SeatId,
    lease: Lease,
) -> Result<Standing, Refusal> {
    if state.ended.is_some() {
        return Err(Refusal::SeatGone);
    }
    let seat = state
        .seats
        .get(&seat)
        .filter(|seat| seat.lease == lease)
        .ok_or(Refusal::SeatGone)?;
    match seat.kind {
        SeatKind::Owner => Ok(Standing::Owner),
        SeatKind::Watcher { sharing } => {
            let current = state
                .sharing
                .as_ref()
                .filter(|current| current.generation == sharing)
                .ok_or(Refusal::SeatGone)?;
            Ok(match current.access {
                Access::Watch => Standing::Watching,
                Access::Explore if seat.view.is_some() => Standing::Exploring,
                Access::Explore => Standing::Following,
                Access::Edit => Standing::Editing,
            })
        }
    }
}

/// Whether `seat` at `lease` may do `use_`. A stream also needs its Content to be open, resolved and at the
/// generation the key names.
pub fn authorize(
    state: &SessionState,
    seat: SeatId,
    lease: Lease,
    use_: Use<'_>,
) -> Result<(), Refusal> {
    match use_ {
        Use::Command(scope) => {
            if standing(state, seat, lease)?.allows(scope, state.files) {
                Ok(())
            } else {
                Err(Refusal::NotAllowed)
            }
        }
        Use::Stream(key) => authorize_stream(state, seat, lease, key).map(drop),
    }
}

/// What `seat` at `lease` may demand of the stream of the entry `key` names, as it is told and as the route enforces
/// it: its own priority, or the presenter's clock for that item (paused at the start while the presenter has not set
/// one). A seat that follows may only stream the entry the presenter shows of the item. Refused like [`authorize`]
/// with `Use::Stream`.
pub fn authorize_stream(
    state: &SessionState,
    seat: SeatId,
    lease: Lease,
    key: &ContentRef,
) -> Result<Authority, Refusal> {
    let standing = standing(state, seat, lease)?;
    let (item, content, _) = open_stream(&state.doc, key).ok_or(Refusal::ContentUnavailable)?;
    let follow = || {
        let presented = state.presenter.viewers.get(&item);
        let shown = shown_entry(
            &state.doc.workspace,
            &item,
            presented.and_then(|viewer| viewer.entry.as_ref()),
        );
        if shown.is_none_or(|shown| shown.id != key.entry) {
            return Err(Refusal::ContentUnavailable);
        }
        let intent = presented
            .and_then(|viewer| viewer.clock)
            .unwrap_or(AT_START);
        Ok(Authority::Follow { intent })
    };
    let own = Authority::Own {
        priority: standing.priority(),
    };
    // What a seat may demand of a stream depends on what the stream carries.
    match content {
        // Recordings stream one entry at a time; a seat that follows gets the presenter's clock.
        Content::SeekableVideo(_) | Content::Playlist(_) => match standing {
            Standing::Watching | Standing::Following => follow(),
            Standing::Owner | Standing::Exploring | Standing::Editing => Ok(own),
        },
        // A live feed has no clock and no entry to present: any seat may watch while the item is open, and one that
        // follows holds `Follow` authority, which only watches. The intent is unused.
        Content::LiveVideo(_) => Ok(match standing {
            Standing::Watching | Standing::Following => Authority::Follow { intent: AT_START },
            Standing::Owner | Standing::Exploring | Standing::Editing => own,
        }),
    }
}

/// The item, Content and entry `key` names, at exactly the generation it carries, while the item is open and
/// resolved. A live video has no entry: its one stream is named by the Content's own id in both places, and the entry
/// is `None`.
pub(super) fn open_stream<'a>(
    doc: &'a Doc,
    key: &ContentRef,
) -> Option<(ItemId, &'a Content, Option<&'a Entry>)> {
    let workspace = &doc.workspace;
    workspace.open.iter().copied().find_map(|item| {
        let (content, generation) = current(workspace, &item)?;
        if content.id() != &key.content || generation != key.generation {
            return None;
        }
        let entry = match content {
            Content::SeekableVideo(_) | Content::Playlist(_) => Some(content.entry(&key.entry)?),
            Content::LiveVideo(_) => {
                if key.entry != key.content {
                    return None;
                }
                None
            }
        };
        Some((item, content, entry))
    })
}

/// The Content of `item` and its commit, while the item is open and resolved.
fn current<'a>(workspace: &'a WorkspaceState, item: &ItemId) -> Option<(&'a Content, ContentGen)> {
    if !workspace.open.contains(item) {
        return None;
    }
    match workspace.resolutions.get(item)? {
        Resolution::Resolved {
            content,
            generation,
        } => Some((content, *generation)),
        Resolution::Pending { .. } | Resolution::Failed(_) => None,
    }
}

const AT_START: ClockIntent = ClockIntent {
    frame: 0,
    playing: false,
    speed_centi: 100,
    at_us: 0,
};
