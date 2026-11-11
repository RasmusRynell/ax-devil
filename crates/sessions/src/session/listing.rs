//! The list of sessions a caller could attach to.

use super::{Service, service::Sessions, state::SeatKind, transition::admission};
use crate::{JoinLink, SeatRole, SessionId, common};
use std::sync::Mutex;
use tokio::sync::watch;

/// A line of the list of sessions.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Listing {
    /// Names the session.
    pub id: SessionId,
    /// Whether an Owner seat is connected.
    pub owner_present: bool,
    /// Seats in the session, away ones included.
    pub seats: usize,
    /// The seat attaching would give the caller now.
    pub seat: SeatRole,
    /// The current link while shared. Hand it only to callers allowed to see every session.
    pub link: Option<JoinLink>,
}

/// What `list` answered both kinds of caller at the last publication, and the revision that went with it.
pub(super) struct Listed {
    revision: watch::Sender<u64>,
    last: Mutex<(Vec<Listing>, Vec<Listing>)>,
}

impl Default for Listed {
    fn default() -> Self {
        Self {
            revision: watch::channel(0).0,
            last: Mutex::default(),
        }
    }
}

fn listings<C>(sessions: &Sessions<C>, full_access: bool) -> Vec<Listing> {
    let mut list: Vec<Listing> = sessions
        .iter()
        .filter_map(|(id, live)| {
            let state = &live.state;
            let seat = match admission(state, full_access).ok()? {
                SeatKind::Owner => SeatRole::Owner,
                SeatKind::Guest { .. } => SeatRole::Guest,
            };
            Some(Listing {
                id: *id,
                owner_present: state.owner_present(),
                seats: state.seats.len(),
                seat,
                link: state.sharing.as_ref().map(|sharing| sharing.link.clone()),
            })
        })
        .collect();
    list.sort_by_key(|listing| listing.id.to_string());
    list
}

impl<C: Clone + Send + 'static> Service<C> {
    /// Whether there is no session.
    pub fn is_empty(&self) -> bool {
        common::locked(&self.inner.sessions).is_empty()
    }

    /// The sessions a caller could attach to, by id, each with the seat attaching would give it now.
    pub fn list(&self, full_access: bool) -> Vec<Listing> {
        listings(&common::locked(&self.inner.sessions), full_access)
    }

    /// A counter that rises, from 0, whenever [`list`](Self::list) may answer a caller differently than before. A
    /// change that is not in the list, such as a cursor moving, does not raise it.
    pub fn listing_revisions(&self) -> watch::Receiver<u64> {
        self.inner.listed.revision.subscribe()
    }

    /// Raises the listing's revision for a change of what the list shows that is not in a session, such as whether
    /// other machines can reach this backend.
    pub fn listing_changed(&self) {
        self.inner
            .listed
            .revision
            .send_modify(|revision| *revision += 1);
    }

    /// Raises the listing's revision if the list changed; called under the lock after every change of a session.
    pub(super) fn publish_listing(&self, sessions: &Sessions<C>) {
        let next = (listings(sessions, true), listings(sessions, false));
        let mut last = common::locked(&self.inner.listed.last);
        if *last != next {
            *last = next;
            self.listing_changed();
        }
    }
}
