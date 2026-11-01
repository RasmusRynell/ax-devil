//! What a seat receives: personalised snapshots and cursor events.

use crate::{
    EndReason, JoinLink, Kind, Mode, Options, Participant, ParticipantId, View, registry::State,
};
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use tokio::sync::{broadcast, watch};

/// Everything one seat sees of its session at one revision, including its own mode and view.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Personal<V> {
    /// Counts applied changes to the whole session, including other seats' views.
    pub revision: u64,
    /// The session's name.
    pub name: String,
    /// The owner's seat.
    pub owner: ParticipantId,
    /// Whether the owner is connected.
    pub owner_present: bool,
    /// What the owner allows.
    pub options: Options,
    /// Everyone in the session, oldest first.
    pub participants: Vec<Participant>,
    /// The view the owner published.
    pub presenter: V,
    /// The seat this snapshot is for.
    pub you: ParticipantId,
    /// Whether this seat follows or explores.
    pub mode: Mode,
    /// What this seat shows: its own view while exploring, the presenter's otherwise.
    pub view: V,
    /// The latest `seq` of this seat's `set_view` the session accepted; 0 before any.
    pub view_seq: u64,
    /// The current join link; the owner's snapshot only.
    pub link: Option<JoinLink>,
}

impl<V: View> Personal<V> {
    /// The same content, ignoring the revision.
    fn same_content(&self, other: &Self) -> bool {
        Self {
            revision: other.revision,
            ..self.clone()
        } == *other
    }
}

impl<V: View> State<V> {
    fn personal(&self, you: ParticipantId) -> Option<Personal<V>> {
        let own = self.seat(you)?;
        let participants = self
            .seats
            .iter()
            .map(|seat| Participant {
                id: seat.id,
                name: seat.name.clone(),
                color: seat.color,
                kind: seat.kind,
                mode: if self.views.exploring(seat.id) {
                    Mode::Exploring
                } else {
                    Mode::Following
                },
                present: seat.present,
                shares_cursor: seat.shares,
            })
            .collect();
        Some(Personal {
            revision: self.revision,
            name: self.name.clone(),
            owner: self.owner,
            owner_present: self.seat(self.owner).is_some_and(|seat| seat.present),
            options: self.options,
            participants,
            presenter: self.views.presenter().clone(),
            you,
            mode: if self.views.exploring(you) {
                Mode::Exploring
            } else {
                Mode::Following
            },
            view: self.views.of(you).clone(),
            view_seq: self.views.seq(you),
            link: (own.kind == Kind::Owner).then(|| self.link.clone()),
        })
    }
}

/// One step of a seat's snapshot feed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Update<V> {
    /// The seat's snapshot changed.
    Snapshot(Personal<V>),
    /// The feed is over: close the socket.
    Ended(EndReason),
}

/// A seat's feed of [`Personal`] snapshots, latest only: a slow reader skips intermediate revisions.
pub struct Snapshots<V> {
    rx: watch::Receiver<Arc<State<V>>>,
    you: ParticipantId,
    lease: u64,
    last: Option<Personal<V>>,
    ended: Option<EndReason>,
}

impl<V: View> Snapshots<V> {
    pub(crate) fn new(rx: watch::Receiver<Arc<State<V>>>, you: ParticipantId, lease: u64) -> Self {
        Self {
            rx,
            you,
            lease,
            last: None,
            ended: None,
        }
    }

    /// The seat's latest snapshot, which counts as seen. Send this first, then wait on [`next`](Self::next).
    pub fn current(&mut self) -> Update<V> {
        let state = Arc::clone(&self.rx.borrow_and_update());
        self.derive(&state, false)
            .unwrap_or(Update::Ended(EndReason::Stopped))
    }

    /// Waits for the next snapshot that differs for this seat: another seat's view changing is not one. After the
    /// session ends or a newer connection replaces this seat, it returns the end at once, every time.
    pub async fn next(&mut self) -> Update<V> {
        loop {
            if let Some(reason) = self.ended {
                return Update::Ended(reason);
            }
            if self.rx.changed().await.is_err() {
                return Update::Ended(self.ended.unwrap_or(EndReason::Stopped));
            }
            let state = Arc::clone(&self.rx.borrow_and_update());
            if let Some(update) = self.derive(&state, true) {
                return update;
            }
        }
    }

    /// The update for `state`; with `skip_same`, `None` when nothing visible to this seat changed.
    fn derive(&mut self, state: &State<V>, skip_same: bool) -> Option<Update<V>> {
        let ended = match state.seat(self.you) {
            Some(seat) if seat.lease != self.lease => Some(EndReason::Replaced),
            Some(_) => state.ended,
            None => Some(state.ended.unwrap_or(EndReason::Stopped)),
        };
        if let Some(reason) = ended {
            self.ended = Some(reason);
            return Some(Update::Ended(reason));
        }
        let personal = state.personal(self.you)?;
        if skip_same
            && self
                .last
                .as_ref()
                .is_some_and(|last| last.same_content(&personal))
        {
            return None;
        }
        self.last = Some(personal.clone());
        Some(Update::Snapshot(personal))
    }
}

/// A participant's cursor moved, or left the window (`at` is `None`).
#[derive(Clone, Debug, PartialEq)]
pub struct CursorEvent<C> {
    /// Whose cursor.
    pub participant: ParticipantId,
    /// Where it is now.
    pub at: Option<C>,
}

/// A seat's feed of other seats' cursors. A seat with "show" off receives none, and a slow reader loses the oldest
/// events and nothing else.
pub struct Cursors<C> {
    pub(crate) rx: broadcast::Receiver<CursorEvent<C>>,
    pub(crate) you: ParticipantId,
    pub(crate) show: Arc<AtomicBool>,
}

impl<C: Clone> Cursors<C> {
    /// The next cursor event of another seat, or `None` once the session has ended.
    pub async fn recv(&mut self) -> Option<CursorEvent<C>> {
        loop {
            match self.rx.recv().await {
                Ok(event) if event.participant != self.you && self.show.load(Ordering::Relaxed) => {
                    return Some(event);
                }
                Ok(_) | Err(broadcast::error::RecvError::Lagged(_)) => {}
                Err(broadcast::error::RecvError::Closed) => return None,
            }
        }
    }
}
