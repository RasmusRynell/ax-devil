//! The one rule for what a seat may do: commands and stream demand alike.
//!
//! Every `match` here is exhaustive with no wildcard arm, so a new [`Use`], [`Scope`], [`StreamKey`] or content kind
//! does not compile until its policy is decided.

use super::{Access, Lease, Scope, SeatId, SeatKind, SessionState, state::Doc};
use playback::{Authority, ClockIntent, Priority};
use std::fmt;
use workspace::{Content, ContentId, ItemId};
use workspace_store::{ContentGen, Resolution, WorkspaceState};

/// A Content at one commit of its resolution.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContentRef {
    /// The Content. Stable across re-resolution.
    pub content: ContentId,
    /// The commit. Re-resolving or reopening gives a new one.
    pub generation: ContentGen,
}

/// What a stream carries. Authority over it ends with the generation in its key.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StreamKey {
    /// The frames of a recorded video.
    Frames(ContentRef),
    /// The current frames of a live item.
    Live {
        /// The item.
        item: ItemId,
        /// The commit of its Content.
        generation: ContentGen,
    },
}

/// What a seat is trying to do.
#[derive(Clone, Debug)]
pub enum Use<'a> {
    /// A command of this scope.
    Command(Scope),
    /// Receive a stream.
    Stream(&'a StreamKey),
    /// Reserved: compute something private to the seat from a Content. No computation exists and who may run one is
    /// undecided, so every seat is refused until a feature defines the policy here.
    Compute(&'a ContentRef),
}

/// How a stream's holder is bound.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Demand {
    /// The holder chooses its own frames, with this priority against other holders.
    Own {
        /// How urgent its frames are.
        priority: Priority,
    },
    /// The holder follows the presenter's clock.
    Follow,
}

/// Why a seat was not allowed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Refusal {
    /// The seat is not in the session, its lease is stale, or the session ended.
    SeatGone,
    /// The seat's role does not allow it.
    NotAllowed,
    /// Sharing is off.
    NotSharing,
    /// Sharing is already on.
    AlreadySharing,
    /// The Content is not open, not resolved, or not at the generation asked for.
    ContentUnavailable,
    /// The seat or the session holds as many streams as it may.
    TooManyStreams,
    /// The ticket is unknown, expired, used, or bound to something that no longer exists.
    BadTicket,
    /// Reserved policy: not decided yet.
    Unsupported,
    /// The link's secret is not the session's current one.
    Link,
    /// The session already has as many seats as it may.
    Full,
}

impl fmt::Display for Refusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::SeatGone => "this seat is no longer in the session",
            Self::NotAllowed => "your role cannot do that",
            Self::NotSharing => "the session is not shared",
            Self::AlreadySharing => "the session is already shared",
            Self::ContentUnavailable => "that content is not open",
            Self::TooManyStreams => "too many streams",
            Self::BadTicket => "that ticket is not valid",
            Self::Unsupported => "that is not supported yet",
            Self::Link => "that link is no longer valid",
            Self::Full => "that session is full",
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

    fn demand(self) -> Demand {
        match self {
            Self::Watching | Self::Following => Demand::Follow,
            Self::Owner | Self::Exploring | Self::Editing => Demand::Own {
                priority: self.priority(),
            },
        }
    }
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

/// Whether `seat` at `lease` may do `use_`, and how a stream it is given is bound. Commands ignore the demand.
///
/// A stream also needs its Content to be open, resolved and at the generation the key names.
pub fn authorize(
    state: &SessionState,
    seat: SeatId,
    lease: Lease,
    use_: Use<'_>,
) -> Result<Demand, Refusal> {
    let standing = standing(state, seat, lease)?;
    match use_ {
        Use::Command(scope) => {
            if standing.allows(scope, state.files) {
                Ok(standing.demand())
            } else {
                Err(Refusal::NotAllowed)
            }
        }
        Use::Stream(key) => {
            item_of(&state.doc, key).ok_or(Refusal::ContentUnavailable)?;
            Ok(match key {
                StreamKey::Frames(_) => standing.demand(),
                // Live frames are the current ones: there is no position to follow or scrub, only a priority.
                StreamKey::Live { .. } => Demand::Own {
                    priority: standing.priority(),
                },
            })
        }
        // TODO: decided with the first private computation (who may compute what, from which Content).
        Use::Compute(_) => Err(Refusal::Unsupported),
    }
}

/// The item whose open, resolved Content `key` names at exactly the generation it carries.
pub(super) fn item_of(doc: &Doc, key: &StreamKey) -> Option<ItemId> {
    let workspace = &doc.workspace;
    match key {
        StreamKey::Frames(of) => workspace.open.iter().copied().find(|item| {
            current(workspace, item).is_some_and(|(content, generation)| {
                content.id() == &of.content && generation == of.generation
            })
        }),
        StreamKey::Live { item, generation } => current(workspace, item)
            .is_some_and(|(content, current)| current == *generation && is_live(content))
            .then_some(*item),
    }
}

/// The Content `key` names, at exactly the generation it carries, while it is open and resolved.
pub(super) fn content_of<'a>(doc: &'a Doc, key: &StreamKey) -> Option<&'a Content> {
    let item = item_of(doc, key)?;
    current(&doc.workspace, &item).map(|(content, _)| content)
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

fn is_live(content: &Content) -> bool {
    match content {
        Content::SeekableVideo(_) => false,
    }
}

/// What the route enforces for a stream bound by `demand`: its own priority, or the presenter's clock for that
/// item (paused at the start while the presenter has not set one).
pub(super) fn authority(state: &SessionState, key: &StreamKey, demand: Demand) -> Authority {
    match demand {
        Demand::Own { priority } => Authority::Own { priority },
        Demand::Follow => {
            let intent = item_of(&state.doc, key)
                .and_then(|item| state.presenter.viewers.get(&item))
                .and_then(|viewer| viewer.clock)
                .unwrap_or(AT_START);
            Authority::Follow { intent }
        }
    }
}

const AT_START: ClockIntent = ClockIntent {
    frame: 0,
    playing: false,
    speed_centi: 100,
    at_us: 0,
};
