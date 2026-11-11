//! The state of one session. Immutable between transitions: [`transition`](super::transition) returns the next one.

use super::{
    Access, GrantId, JobId, Lease, SeatId, SharingGen,
    jobs::JobEntry,
    streams::{Grant, PendingTicket},
    views::View,
};
use crate::{EndReason, JoinLink, SeatMode, SessionId};
use std::{collections::BTreeMap, sync::Arc};
use workspace::store::WorkspaceState;

/// What the session is about, for everyone it is shared with. Text a user wrote in it is shared on purpose;
/// anything else reaches a Guest only through a projection that names every field.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Doc {
    /// The items, how they resolved, and which have a viewer open. Annotations and live state join as slices.
    pub workspace: WorkspaceState,
}

/// Who a seat is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SeatKind {
    /// Drives the Presenter View with every other Owner seat; has no view of its own.
    Owner,
    /// Joined while sharing was on, under that "Start sharing".
    Guest {
        /// The sharing the seat joined under. A seat of an older one is not a seat.
        sharing: SharingGen,
    },
}

impl SeatKind {
    /// The sharing the seat joined under; `None` for an Owner seat.
    pub fn sharing(&self) -> Option<SharingGen> {
        match self {
            Self::Owner => None,
            Self::Guest { sharing } => Some(*sharing),
        }
    }
}

/// One seat of a session.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Seat {
    /// Bumped by every resume; handles, tickets, grants and timers of an older lease do nothing.
    pub lease: Lease,
    /// Owner or Guest.
    pub kind: SeatKind,
    /// False while the connection is gone and the grace period runs.
    pub present: bool,
    /// The seat's own view while it explores; `None` while it follows. An Owner seat and a Guest that may only
    /// watch never have one.
    pub view: Option<Arc<View>>,
    /// The highest View sequence number finished for this seat: accepted, refused or overwritten.
    pub views_done: u64,
}

impl Seat {
    /// Whose view the seat shows.
    pub fn mode(&self) -> SeatMode {
        if self.view.is_some() {
            SeatMode::Exploring
        } else {
            SeatMode::Following
        }
    }
}

/// Sharing is on: what Guests may do and the link that admits them.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Sharing {
    /// This "Start sharing". Never reused.
    pub generation: SharingGen,
    /// What every Guest may do.
    pub access: Access,
    /// The link that admits a Guest.
    pub link: JoinLink,
}

/// Everything about one session at one moment.
#[derive(Clone, Debug)]
pub struct SessionState {
    /// Counts changes a seat can see: Doc, presenter view, seats, sharing and the end. Grants, tickets and jobs do
    /// not count.
    pub revision: u64,
    /// Names the session.
    pub id: SessionId,
    /// The Session was created by an Owner with Full access, so the references in its items read this machine's
    /// environment; otherwise they stay literal text.
    pub expands: bool,
    /// What the session is about.
    pub doc: Arc<Doc>,
    /// Counts changes to the Doc.
    pub doc_rev: u64,
    /// The Presenter View, which every Owner seat sets.
    pub presenter: Arc<View>,
    /// Counts changes to the presenter's view.
    pub presenter_rev: u64,
    /// The Owner seat whose command made the current presenter view; `None` when the server did, such as when an item
    /// the view shows is removed. Stamped where the view changes, never taken from a client.
    pub presenter_by: Option<SeatId>,
    /// Every seat, away ones included.
    pub seats: BTreeMap<SeatId, Seat>,
    /// `Some` while an Owner shares the session.
    pub sharing: Option<Sharing>,
    /// Why the session is over, once it is.
    pub ended: Option<EndReason>,
    pub(super) next_seat: u32,
    pub(super) next_sharing: u64,
    pub(super) next_grant: u64,
    pub(super) next_job: u64,
    /// The record of what is authorized. A grant removed is never given back.
    pub(super) grants: BTreeMap<GrantId, Grant>,
    pub(super) tickets: Vec<PendingTicket>,
    pub(super) jobs: BTreeMap<JobId, JobEntry>,
}

impl SessionState {
    /// A session with no seat yet, an empty Doc and an empty presenter view, not shared.
    pub fn new(id: SessionId, expands: bool) -> Self {
        Self {
            revision: 0,
            id,
            expands,
            doc: Arc::default(),
            doc_rev: 0,
            presenter: Arc::default(),
            presenter_rev: 0,
            presenter_by: None,
            seats: BTreeMap::new(),
            sharing: None,
            ended: None,
            next_seat: 1,
            next_sharing: 0,
            next_grant: 0,
            next_job: 0,
            grants: BTreeMap::new(),
            tickets: Vec::new(),
            jobs: BTreeMap::new(),
        }
    }

    /// Seats a new Owner seat, present, on lease 0. The caller has checked the seat limit.
    pub fn seat_owner(&mut self) -> SeatId {
        self.seat(SeatKind::Owner)
    }

    /// Seats a new Guest under `sharing`, present, on lease 0. The caller has checked the seat limit.
    pub(super) fn seat_guest(&mut self, sharing: SharingGen) -> SeatId {
        self.seat(SeatKind::Guest { sharing })
    }

    fn seat(&mut self, kind: SeatKind) -> SeatId {
        let id = SeatId(self.next_seat);
        self.next_seat += 1;
        self.seats.insert(
            id,
            Seat {
                lease: Lease(0),
                kind,
                present: true,
                view: None,
                views_done: 0,
            },
        );
        id
    }

    /// Whether an Owner seat is connected.
    pub fn owner_present(&self) -> bool {
        self.seats
            .values()
            .any(|seat| seat.kind == SeatKind::Owner && seat.present)
    }

    /// What a seat can see differs between `self` and `other`.
    pub(super) fn visibly_differs(&self, other: &Self) -> bool {
        self.doc_rev != other.doc_rev
            || self.presenter_rev != other.presenter_rev
            || self.seats != other.seats
            || self.sharing != other.sharing
            || self.ended != other.ended
    }

    /// The grant `id`, if it was not removed.
    pub(super) fn has_grant(&self, id: GrantId) -> bool {
        self.grants.contains_key(&id)
    }
}
