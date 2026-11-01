//! View state and the rules for exploring.
//!
//! The presenter's view is what the owner published. A following seat has no view of its own and shows the
//! presenter's; an exploring seat has its own, which is cut down to what the presenter has open.

use crate::ParticipantId;
use std::collections::HashMap;

/// A view state the sessions crate can hold. What it contains is the adapter's business; the crate only needs to
/// compare it and to cut an exploring view down to what the presenter has open.
pub trait View: Clone + Default + PartialEq + Send + Sync + 'static {
    /// Removes from `self` whatever the presenter does not have open, so an exploring seat never shows content the
    /// owner closed. Runs on every `set_view` and, for every exploring seat, on every publish.
    fn restrict(&mut self, presenter: &Self);
}

/// Whose view.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum ViewKey {
    /// The owner's published view.
    Presenter,
    /// An exploring seat's own view.
    Seat(ParticipantId),
}

/// Every view of one session, and the latest accepted client sequence number of each exploring seat.
#[derive(Clone)]
pub(crate) struct Views<V> {
    views: HashMap<ViewKey, V>,
    seqs: HashMap<ParticipantId, u64>,
}

impl<V: View> Views<V> {
    pub fn new() -> Self {
        Self {
            views: HashMap::from([(ViewKey::Presenter, V::default())]),
            seqs: HashMap::new(),
        }
    }

    pub fn presenter(&self) -> &V {
        &self.views[&ViewKey::Presenter]
    }

    /// What `seat` shows: its own view while exploring, the presenter's otherwise.
    pub fn of(&self, seat: ParticipantId) -> &V {
        self.views
            .get(&ViewKey::Seat(seat))
            .unwrap_or_else(|| self.presenter())
    }

    pub fn exploring(&self, seat: ParticipantId) -> bool {
        self.views.contains_key(&ViewKey::Seat(seat))
    }

    /// The latest `seq` accepted from `seat`; 0 before any.
    pub fn seq(&self, seat: ParticipantId) -> u64 {
        self.seqs.get(&seat).copied().unwrap_or(0)
    }

    /// Replaces the presenter's view and restricts every exploring seat. False when nothing changed.
    pub fn publish(&mut self, view: V) -> bool {
        if *self.presenter() == view {
            return false;
        }
        for (key, seat_view) in &mut self.views {
            if matches!(key, ViewKey::Seat(_)) {
                seat_view.restrict(&view);
            }
        }
        self.views.insert(ViewKey::Presenter, view);
        true
    }

    /// Starts `seat` exploring from the presenter's view.
    pub fn explore(&mut self, seat: ParticipantId) {
        let presenter = self.presenter().clone();
        self.views.entry(ViewKey::Seat(seat)).or_insert(presenter);
    }

    /// Stores an exploring seat's view, restricted, with the client's `seq`. A `seq` older than the latest accepted
    /// is ignored.
    pub fn set(&mut self, seat: ParticipantId, mut view: V, seq: u64) {
        if seq < self.seq(seat) {
            return;
        }
        view.restrict(self.presenter());
        self.views.insert(ViewKey::Seat(seat), view);
        self.seqs.insert(seat, seq);
    }

    /// Puts `seat` back to following. False when it already was.
    pub fn follow(&mut self, seat: ParticipantId) -> bool {
        self.views.remove(&ViewKey::Seat(seat)).is_some()
    }

    /// Puts every seat back to following.
    pub fn follow_all(&mut self) {
        self.views.retain(|key, _| *key == ViewKey::Presenter);
    }

    /// Drops everything held for a seat that left.
    pub fn forget(&mut self, seat: ParticipantId) {
        self.follow(seat);
        self.seqs.remove(&seat);
    }
}
