//! The list of sessions a caller could attach to.

use super::{Service, state::SeatKind, transition::admission};
use crate::{JoinLink, SeatRole, SessionId, common};

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

impl<C: Clone + Send + 'static> Service<C> {
    /// Whether there is no session.
    pub fn is_empty(&self) -> bool {
        common::locked(&self.inner.sessions).is_empty()
    }

    /// The sessions a caller could attach to, by id, each with the seat attaching would give it now.
    pub fn list(&self, full_access: bool) -> Vec<Listing> {
        let sessions = common::locked(&self.inner.sessions);
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
}
