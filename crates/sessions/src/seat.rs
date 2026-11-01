//! The handles a connection holds: [`Owner`] and [`Watcher`], over one shared [`Connection`].
//!
//! A watcher's handle has no publish method, so it cannot change shared state; the same rule holds for messages
//! arriving as data, through [`Role::apply`]. Dropping a handle is a disconnect and starts the grace period.

use crate::{
    CursorOptions, Cursors, JoinLink, Mode, Options, ParticipantId, SessionId, Snapshots, View,
    Violation,
    registry::{Entry, Registry, Session, Shared, disconnect, lock},
    secret::Secret,
};
use std::{
    ops::Deref,
    sync::{Arc, atomic::AtomicBool},
};
use tokio::sync::{broadcast, watch};

/// A message a seat can send, whichever role it has; [`Role::apply`] accepts the ones its role allows.
#[derive(Clone, Debug, PartialEq)]
pub enum SeatMessage<V, C> {
    /// Owner: replace the presenter's view.
    Publish(V),
    /// Owner: change the session's options.
    SetOptions(Options),
    /// Owner: invalidate the join link and make a new one.
    RegenerateLink,
    /// Following watcher: start exploring.
    Explore,
    /// Exploring watcher: set the seat's own view; `seq` is echoed back once accepted.
    SetView {
        /// The view.
        view: V,
        /// The client's sequence number for this edit.
        seq: u64,
    },
    /// Exploring watcher: go back to following.
    Follow,
    /// Anyone: move or hide the seat's cursor.
    Cursor(Option<C>),
    /// Anyone: change the seat's cursor options.
    CursorOptions(CursorOptions),
}

/// Applies a received message to the seat if its role allows it. A message from the wrong role returns
/// [`Violation::Role`] and changes nothing. A handle whose lease is stale silently does nothing.
pub trait Role<V, C> {
    /// Applies `message` as this seat.
    fn apply(&self, message: SeatMessage<V, C>) -> Result<(), Violation>;
}

/// What owners and watchers share: identity, feeds and cursors. Dropping it disconnects the seat.
pub struct Connection<V: View, C: Clone + Send + 'static> {
    registry: Shared<V, C>,
    session: SessionId,
    participant: ParticipantId,
    lease: u64,
    token: Secret,
    show: Arc<AtomicBool>,
    rx: watch::Receiver<Arc<crate::registry::State<V>>>,
}

impl<V: View, C: Clone + Send + 'static> Connection<V, C> {
    pub(crate) fn new(
        registry: Shared<V, C>,
        session: SessionId,
        participant: ParticipantId,
        entry: Entry,
        rx: watch::Receiver<Arc<crate::registry::State<V>>>,
    ) -> Self {
        Self {
            registry,
            session,
            participant,
            lease: entry.lease,
            token: entry.token,
            show: entry.show,
            rx,
        }
    }

    /// This seat.
    pub fn participant(&self) -> ParticipantId {
        self.participant
    }

    /// The session this seat is in.
    pub fn session(&self) -> SessionId {
        self.session
    }

    /// Takes this seat back after a disconnect, once. Resuming replaces it with a new token.
    pub fn resume_token(&self) -> &Secret {
        &self.token
    }

    /// This seat's snapshot feed, starting at the latest state.
    pub fn snapshots(&self) -> Snapshots<V> {
        let mut rx = self.rx.clone();
        rx.mark_unchanged();
        Snapshots::new(rx, self.participant, self.lease)
    }

    /// What this seat shows right now: its own view while exploring, the presenter's otherwise.
    pub fn own_view(&self) -> V {
        self.rx.borrow().views.of(self.participant).clone()
    }

    /// Other seats' cursor events, filtered by this seat's "show" option.
    pub fn cursors(&self) -> Cursors<C> {
        let rx = match lock(&self.registry).sessions.get(&self.session) {
            Some(session) => session.subscribe_cursors(),
            // The session ended: a receiver whose sender is gone reports the end.
            None => broadcast::channel(1).1,
        };
        Cursors {
            rx,
            you: self.participant,
            show: Arc::clone(&self.show),
        }
    }

    /// Tells the others where this seat's cursor is, or hides it (`None`). Nothing is sent while "share" is off.
    pub fn move_cursor(&self, at: Option<C>) {
        self.act(|session, me| {
            if session.seat_mut(me).is_some_and(|seat| seat.shares) {
                session.send_cursor(me, at);
            }
        });
    }

    /// Changes what this seat shows and receives. Switching "share" off hides the seat's cursor once.
    pub fn set_cursor_options(&self, options: CursorOptions) {
        self.act(|session, me| {
            session.set_show(me, options.show);
            let Some(seat) = session.seat_mut(me) else {
                return;
            };
            let was_sharing = std::mem::replace(&mut seat.shares, options.share);
            if was_sharing && !options.share {
                session.send_cursor(me, None);
            }
            if was_sharing != options.share {
                session.publish();
            }
        });
    }

    /// Runs `change` on the session under the lock, unless the session ended or this handle's lease is stale.
    fn act<R>(&self, change: impl FnOnce(&mut Session<V, C>, ParticipantId) -> R) -> Option<R> {
        let mut guard = lock(&self.registry);
        let session = self.live(&mut guard)?;
        Some(change(session, self.participant))
    }

    fn live<'a>(&self, registry: &'a mut Registry<V, C>) -> Option<&'a mut Session<V, C>> {
        let session = registry.sessions.get_mut(&self.session)?;
        let lease = session.seat_mut(self.participant)?.lease;
        (lease == self.lease).then_some(session)
    }
}

impl<V: View, C: Clone + Send + 'static> Drop for Connection<V, C> {
    fn drop(&mut self) {
        disconnect(&self.registry, self.session, self.participant, self.lease);
    }
}

/// The seat that created the session: the only one that changes what it shows. Ends the session when it leaves.
pub struct Owner<V: View, C: Clone + Send + 'static>(Connection<V, C>);

impl<V: View, C: Clone + Send + 'static> Deref for Owner<V, C> {
    type Target = Connection<V, C>;

    fn deref(&self) -> &Connection<V, C> {
        &self.0
    }
}

impl<V: View, C: Clone + Send + 'static> Owner<V, C> {
    pub(crate) fn new(connection: Connection<V, C>) -> Self {
        Self(connection)
    }

    /// Replaces the presenter's view and restricts every exploring seat to it.
    pub fn publish(&self, view: V) {
        self.0.act(|session, _| {
            if session.state.views.publish(view) {
                session.publish();
            }
        });
    }

    /// Changes the session's options. Turning explore off moves every exploring seat back to following.
    pub fn set_options(&self, options: Options) {
        self.0.act(|session, _| {
            if session.state.options == options {
                return;
            }
            session.state.options = options;
            if !options.explore {
                session.state.views.follow_all();
            }
            session.publish();
        });
    }

    /// The join link that currently admits a watcher.
    pub fn link(&self) -> JoinLink {
        let state = self.0.rx.borrow();
        state.link.clone()
    }

    /// Invalidates the join link and returns a new one. Seats already in stay.
    pub fn regenerate_link(&self) -> JoinLink {
        let id = self.0.session;
        self.0
            .act(|session, _| {
                session.state.link = JoinLink {
                    session: id,
                    secret: Secret::generate(),
                };
                session.publish();
                session.state.link.clone()
            })
            .unwrap_or_else(|| self.link())
    }

    /// Stops sharing: the session ends at once and every seat gets `Stopped`.
    pub fn leave(self) {
        let mut guard = lock(&self.0.registry);
        if self.0.live(&mut guard).is_some() {
            guard.end(self.0.session, crate::EndReason::Stopped);
        }
    }
}

impl<V: View, C: Clone + Send + 'static> Role<V, C> for Owner<V, C> {
    fn apply(&self, message: SeatMessage<V, C>) -> Result<(), Violation> {
        match message {
            SeatMessage::Publish(view) => self.publish(view),
            SeatMessage::SetOptions(options) => self.set_options(options),
            SeatMessage::RegenerateLink => {
                self.regenerate_link();
            }
            SeatMessage::Cursor(at) => self.move_cursor(at),
            SeatMessage::CursorOptions(options) => self.set_cursor_options(options),
            SeatMessage::Explore | SeatMessage::SetView { .. } | SeatMessage::Follow => {
                return Err(Violation::Role);
            }
        }
        Ok(())
    }
}

/// Anyone else in a session. It cannot change shared state, only its own view, and only if the owner allows it.
pub struct Watcher<V: View, C: Clone + Send + 'static>(Connection<V, C>);

impl<V: View, C: Clone + Send + 'static> Deref for Watcher<V, C> {
    type Target = Connection<V, C>;

    fn deref(&self) -> &Connection<V, C> {
        &self.0
    }
}

impl<V: View, C: Clone + Send + 'static> Watcher<V, C> {
    pub(crate) fn new(connection: Connection<V, C>) -> Self {
        Self(connection)
    }

    /// Starts exploring from the presenter's view. Fails while the owner does not allow it.
    pub fn explore(&self) -> Result<(), Violation> {
        self.0
            .act(|session, me| {
                if !session.state.options.explore {
                    return Err(Violation::ExploreOff);
                }
                if !session.state.views.exploring(me) {
                    session.state.views.explore(me);
                    session.publish();
                }
                Ok(())
            })
            .unwrap_or(Ok(()))
    }

    /// Sets this seat's own view, restricted to what the owner has open; `seq` comes back in the seat's snapshot.
    /// Fails unless the seat is exploring.
    pub fn set_view(&self, view: V, seq: u64) -> Result<(), Violation> {
        self.0
            .act(|session, me| {
                if !session.state.views.exploring(me) {
                    return Err(Violation::NotExploring);
                }
                session.state.views.set(me, view, seq);
                session.publish();
                Ok(())
            })
            .unwrap_or(Ok(()))
    }

    /// Goes back to showing the presenter's view.
    pub fn follow(&self) {
        self.0.act(|session, me| {
            if session.state.views.follow(me) {
                session.publish();
            }
        });
    }

    /// Whether this seat follows or explores, as of the latest state.
    pub fn mode(&self) -> Mode {
        if self.0.rx.borrow().views.exploring(self.0.participant) {
            Mode::Exploring
        } else {
            Mode::Following
        }
    }

    /// Leaves at once; the seat is not kept for the grace period.
    pub fn leave(self) {
        self.0.act(|session, me| session.remove_seat(me));
    }
}

impl<V: View, C: Clone + Send + 'static> Role<V, C> for Watcher<V, C> {
    fn apply(&self, message: SeatMessage<V, C>) -> Result<(), Violation> {
        match message {
            SeatMessage::Explore => self.explore()?,
            SeatMessage::SetView { view, seq } => self.set_view(view, seq)?,
            SeatMessage::Follow => self.follow(),
            SeatMessage::Cursor(at) => self.move_cursor(at),
            SeatMessage::CursorOptions(options) => self.set_cursor_options(options),
            SeatMessage::Publish(_) | SeatMessage::SetOptions(_) | SeatMessage::RegenerateLink => {
                return Err(Violation::Role);
            }
        }
        Ok(())
    }
}

/// A resumed seat, in whichever role it had.
pub enum Seat<V: View, C: Clone + Send + 'static> {
    /// The session's owner.
    Owner(Owner<V, C>),
    /// A watcher.
    Watcher(Watcher<V, C>),
}

impl<V: View, C: Clone + Send + 'static> Role<V, C> for Seat<V, C> {
    fn apply(&self, message: SeatMessage<V, C>) -> Result<(), Violation> {
        match self {
            Self::Owner(owner) => owner.apply(message),
            Self::Watcher(watcher) => watcher.apply(message),
        }
    }
}
