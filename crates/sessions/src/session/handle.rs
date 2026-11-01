//! What a connection holds: a seat's [`Handle`] with its [`Feed`], and a stream's [`StreamLease`].

use super::{
    Command, GrantId, Lease, Refusal, SeatId, SessionState, Ticket,
    roster::RosterEntry,
    service::{Service, apply},
    transition::{Fresh, Input, Outcome},
    views::View,
};
use crate::{CursorOptions, Cursors, EndReason, Secret, SessionId};
use playback::Authority;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use tokio::sync::{broadcast, watch};
use workspace::Content;
use workspace::Entry as ContentEntry;

/// What a new or resumed handle needs.
pub(super) struct Entry {
    pub lease: Lease,
    pub token: Secret,
    pub show: Arc<AtomicBool>,
    pub share: Arc<AtomicBool>,
    pub rx: watch::Receiver<Arc<SessionState>>,
    pub roster: watch::Receiver<Arc<Vec<RosterEntry>>>,
}

/// What a command answered with, once allowed.
#[derive(Debug)]
pub enum Reply {
    /// Done.
    Done,
    /// The ticket asked for. Its holder presents it once to open the stream.
    Ticket(Ticket),
}

/// One seat's connection. Dropping it disconnects the seat: it is away for the grace period.
pub struct Handle<C: Clone + Send + 'static> {
    service: Service<C>,
    session: SessionId,
    seat: SeatId,
    lease: Lease,
    token: Secret,
    show: Arc<AtomicBool>,
    share: Arc<AtomicBool>,
    rx: watch::Receiver<Arc<SessionState>>,
    roster: watch::Receiver<Arc<Vec<RosterEntry>>>,
}

impl<C: Clone + Send + 'static> Handle<C> {
    pub(super) fn new(service: Service<C>, session: SessionId, seat: SeatId, entry: Entry) -> Self {
        Self {
            service,
            session,
            seat,
            lease: entry.lease,
            token: entry.token,
            show: entry.show,
            share: entry.share,
            rx: entry.rx,
            roster: entry.roster,
        }
    }

    /// This seat.
    pub fn seat(&self) -> SeatId {
        self.seat
    }

    /// The session this seat is in.
    pub fn session(&self) -> SessionId {
        self.session
    }

    /// The lease this connection holds.
    pub fn lease(&self) -> Lease {
        self.lease
    }

    /// Takes this seat back after a disconnect, once. Resuming replaces it with a new token.
    pub fn resume_token(&self) -> &Secret {
        &self.token
    }

    /// The latest published state of the session.
    pub fn state(&self) -> Arc<SessionState> {
        Arc::clone(&self.rx.borrow())
    }

    /// This seat's feed of the session's state, starting at the latest.
    pub fn feed(&self) -> Feed {
        let mut rx = self.rx.clone();
        rx.mark_unchanged();
        Feed {
            rx,
            seat: self.seat,
            lease: self.lease,
            ended: None,
        }
    }

    /// Asks for `command` as this seat. A refusal changes nothing.
    pub fn command(&self, command: Command) -> Result<Reply, Refusal> {
        let fresh = Fresh::now();
        let ticket = Ticket(fresh.secret.clone());
        let wants_ticket = matches!(command, Command::RequestTicket(_));
        let input = Input::Command {
            seat: self.seat,
            lease: self.lease,
            command,
            fresh,
        };
        self.service
            .locked(|sessions, deferred| apply(sessions, self.session, input, deferred))?;
        Ok(if wants_ticket {
            Reply::Ticket(ticket)
        } else {
            Reply::Done
        })
    }

    /// Sets this seat's view, from the View lane. A refused view still settles its `seq`, so the client stops
    /// showing it.
    pub fn view(&self, view: View, seq: u64) -> Result<(), Refusal> {
        let input = Input::View {
            seat: self.seat,
            lease: self.lease,
            view,
            seq,
            now_us: self.service.now_us(),
        };
        let applied = self
            .service
            .locked(|sessions, deferred| apply(sessions, self.session, input, deferred))?;
        match applied.outcome {
            Outcome::ViewRefused(refusal) => Err(refusal),
            Outcome::None | Outcome::Joined { .. } | Outcome::Opened { .. } => Ok(()),
        }
    }

    /// Who is in the session, oldest first: the latest roster, and a signal when it changes. It changes with the
    /// seats, their modes and presence, and with a seat's "share" cursor option; cursors do not touch it.
    pub fn roster(&self) -> watch::Receiver<Arc<Vec<RosterEntry>>> {
        self.roster.clone()
    }

    /// Other seats' cursor events, filtered by this seat's "show" option.
    pub fn cursors(&self) -> Cursors<C> {
        let rx = self
            .service
            .locked(|sessions, _| {
                sessions
                    .get(&self.session)
                    .map(|live| live.cursors.subscribe())
            })
            // The session ended: a receiver whose sender is gone reports the end.
            .unwrap_or_else(|| broadcast::channel(1).1);
        Cursors {
            rx,
            you: self.seat,
            show: Arc::clone(&self.show),
        }
    }

    /// Tells the others where this seat's cursor is, or hides it (`None`). Nothing is sent while "share" is off.
    pub fn move_cursor(&self, at: Option<C>) {
        if !self.share.load(Ordering::Relaxed) {
            return;
        }
        self.service.locked(|sessions, _| {
            let Some(live) = sessions.get(&self.session) else {
                return;
            };
            let current = live.state.seats.get(&self.seat);
            if current.is_some_and(|seat| seat.lease == self.lease) {
                // No receivers only means everyone else is gone.
                let _ = live.cursors.send(crate::CursorEvent {
                    participant: self.seat,
                    at,
                });
            }
        });
    }

    /// Changes what this seat shows and receives. Switching "share" off hides the seat's cursor once.
    pub fn set_cursor_options(&self, options: CursorOptions) {
        self.show.store(options.show, Ordering::Relaxed);
        let was_sharing = self.share.swap(options.share, Ordering::Relaxed);
        if was_sharing != options.share {
            self.service.locked(|sessions, _| {
                if let Some(live) = sessions.get(&self.session) {
                    live.publish_roster();
                    if !options.share {
                        live.hide_cursor(self.seat);
                    }
                }
            });
        }
    }

    /// Leaves at once; the seat is not kept for the grace period. The owner leaving ends the session.
    pub fn leave(self) {
        let input = Input::Leave {
            seat: self.seat,
            lease: self.lease,
        };
        // A refusal means the seat was replaced or is gone already.
        let _ = self
            .service
            .locked(|sessions, deferred| apply(sessions, self.session, input, deferred));
    }
}

impl<C: Clone + Send + 'static> Drop for Handle<C> {
    fn drop(&mut self) {
        self.service.disconnect(self.session, self.seat, self.lease);
    }
}

/// One step of a seat's feed.
#[derive(Clone, Debug)]
pub enum Change {
    /// The session changed in a way some seat can see. The caller projects what this seat may see of it.
    State(Arc<SessionState>),
    /// The feed is over: close the socket.
    Ended(EndReason),
}

/// A seat's feed of session states, latest only: a slow reader skips intermediate revisions.
pub struct Feed {
    rx: watch::Receiver<Arc<SessionState>>,
    seat: SeatId,
    lease: Lease,
    ended: Option<EndReason>,
}

impl Feed {
    /// The latest state, which counts as seen. Send this first, then wait on [`next`](Self::next).
    pub fn current(&mut self) -> Change {
        let state = Arc::clone(&self.rx.borrow_and_update());
        self.derive(state)
    }

    /// Waits for the next state. After the session ends, the seat leaves it, or a newer connection replaces this
    /// seat, it returns the end at once, every time.
    pub async fn next(&mut self) -> Change {
        if self.ended.is_none() && self.rx.changed().await.is_err() {
            self.ended = Some(EndReason::Stopped);
        }
        if let Some(reason) = self.ended {
            return Change::Ended(reason);
        }
        let state = Arc::clone(&self.rx.borrow_and_update());
        self.derive(state)
    }

    fn derive(&mut self, state: Arc<SessionState>) -> Change {
        let ended = match state.seats.get(&self.seat) {
            Some(seat) if seat.lease != self.lease => Some(EndReason::Replaced),
            Some(_) => state.ended,
            None => Some(state.ended.unwrap_or(EndReason::Stopped)),
        };
        match ended {
            Some(reason) => {
                self.ended = Some(reason);
                Change::Ended(reason)
            }
            None => Change::State(state),
        }
    }
}

/// One authorized stream. Dropping it releases the grant.
pub struct StreamLease {
    grant: GrantId,
    authority: watch::Receiver<Authority>,
    content: Content,
    entry: ContentEntry,
    host_details: bool,
    release: Option<Box<dyn FnOnce() + Send + Sync>>,
}

impl StreamLease {
    pub(super) fn new(
        grant: GrantId,
        authority: watch::Receiver<Authority>,
        content: Content,
        entry: ContentEntry,
        host_details: bool,
        release: impl FnOnce() + Send + Sync + 'static,
    ) -> Self {
        Self {
            grant,
            authority,
            content,
            entry,
            host_details,
            release: Some(Box::new(release)),
        }
    }

    /// Names the grant.
    pub fn grant(&self) -> GrantId {
        self.grant
    }

    /// The Content the stream carries, as it was resolved when the ticket was redeemed.
    pub fn content(&self) -> &Content {
        &self.content
    }

    /// The entry of [`content`](Self::content) the stream plays, as it was resolved when the ticket was redeemed.
    pub fn entry(&self) -> &ContentEntry {
        &self.entry
    }

    /// Whether the holder may learn the host's paths and diagnostic text, or only public error codes.
    pub fn host_details(&self) -> bool {
        self.host_details
    }

    /// What the holder may demand, now and as it changes. [`Authority::Revoked`] is final.
    pub fn authority(&self) -> watch::Receiver<Authority> {
        self.authority.clone()
    }
}

impl Drop for StreamLease {
    fn drop(&mut self) {
        if let Some(release) = self.release.take() {
            release();
        }
    }
}
