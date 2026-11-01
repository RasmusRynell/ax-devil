//! The registry: every session behind one lock, and the grace timers of seats whose connection dropped.
//!
//! Every change completes under the lock, then publishes the whole session state once. A seat's lease doubles as the
//! generation counter of its grace timer: a timer fires only for the lease it was started with, and `resume` bumps
//! the lease, so a timer that outlived its disconnect does nothing.

use crate::{
    CursorEvent, CursorOptions, EndReason, JoinLink, Kind, MAX_SEATS, Options, Owner,
    ParticipantId, Seat, SessionError, SessionId, Summary, View, Watcher, seat::Connection,
    secret::Secret, view::Views,
};
use std::{
    collections::HashMap,
    sync::{
        Arc, Mutex, MutexGuard, PoisonError,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tokio::sync::{broadcast, watch};

/// Cursor events a slow receiver may fall behind by before it starts missing them.
const CURSOR_BACKLOG: usize = 64;

/// One seat as the session holds it.
#[derive(Clone)]
pub(crate) struct SeatState {
    pub id: ParticipantId,
    pub name: String,
    pub color: u8,
    pub kind: Kind,
    pub present: bool,
    /// Bumped by every resume; handles and timers of an older lease do nothing.
    pub lease: u64,
    pub shares: bool,
}

/// The whole publishable state of a session; every seat derives its own snapshot from it.
#[derive(Clone)]
pub(crate) struct State<V> {
    pub revision: u64,
    pub name: String,
    pub owner: ParticipantId,
    pub options: Options,
    pub link: JoinLink,
    pub ended: Option<EndReason>,
    pub seats: Vec<SeatState>,
    pub views: Views<V>,
}

impl<V> State<V> {
    pub fn seat(&self, id: ParticipantId) -> Option<&SeatState> {
        self.seats.iter().find(|seat| seat.id == id)
    }

    fn seat_mut(&mut self, id: ParticipantId) -> Option<&mut SeatState> {
        self.seats.iter_mut().find(|seat| seat.id == id)
    }
}

/// What a seat keeps out of the published state.
struct Private {
    token: Secret,
    show: Arc<AtomicBool>,
}

/// What a new or resumed handle needs.
pub(crate) struct Entry {
    pub lease: u64,
    pub token: Secret,
    pub show: Arc<AtomicBool>,
}

pub(crate) struct Session<V, C> {
    pub state: State<V>,
    private: HashMap<ParticipantId, Private>,
    tx: watch::Sender<Arc<State<V>>>,
    cursors: broadcast::Sender<CursorEvent<C>>,
}

impl<V: View, C: Clone> Session<V, C> {
    /// Counts a change and tells every seat.
    pub fn publish(&mut self) {
        self.state.revision += 1;
        self.tx.send_replace(Arc::new(self.state.clone()));
    }

    pub fn subscribe(&self) -> watch::Receiver<Arc<State<V>>> {
        self.tx.subscribe()
    }

    pub fn subscribe_cursors(&self) -> broadcast::Receiver<CursorEvent<C>> {
        self.cursors.subscribe()
    }

    /// Tells the others where `participant`'s cursor is. No receivers only means everyone else is gone.
    pub fn send_cursor(&self, participant: ParticipantId, at: Option<C>) {
        let _ = self.cursors.send(CursorEvent { participant, at });
    }

    pub fn seat_mut(&mut self, id: ParticipantId) -> Option<&mut SeatState> {
        self.state.seat_mut(id)
    }

    pub fn set_show(&self, id: ParticipantId, show: bool) {
        if let Some(private) = self.private.get(&id) {
            private.show.store(show, Ordering::Relaxed);
        }
    }

    /// Seats a new participant, named and colored by what the session has free.
    fn enter(&mut self, id: ParticipantId, kind: Kind, cursors: CursorOptions) -> Entry {
        let seats = &self.state.seats;
        let number = (1u32..)
            .find(|n| {
                let name = format!("Guest {n}");
                seats.iter().all(|seat| seat.name != name)
            })
            .unwrap_or_default();
        let name = format!("Guest {number}");
        let color = (0..=u8::MAX)
            .find(|color| seats.iter().all(|seat| seat.color != *color))
            .unwrap_or(0);
        if kind == Kind::Owner {
            self.state.name.clone_from(&name);
        }
        self.state.seats.push(SeatState {
            id,
            name,
            color,
            kind,
            present: true,
            lease: 0,
            shares: cursors.share,
        });
        let entry = Entry {
            lease: 0,
            token: Secret::generate(),
            show: Arc::new(AtomicBool::new(cursors.show)),
        };
        self.private.insert(
            id,
            Private {
                token: entry.token.clone(),
                show: Arc::clone(&entry.show),
            },
        );
        self.publish();
        entry
    }

    /// Removes a seat that left or whose grace ran out.
    pub fn remove_seat(&mut self, id: ParticipantId) {
        self.state.seats.retain(|seat| seat.id != id);
        self.state.views.forget(id);
        self.private.remove(&id);
        self.send_cursor(id, None);
        self.publish();
    }
}

pub(crate) struct Registry<V, C> {
    pub sessions: HashMap<SessionId, Session<V, C>>,
    participants: u32,
    grace: Duration,
}

pub(crate) type Shared<V, C> = Arc<Mutex<Registry<V, C>>>;

pub(crate) fn lock<V, C>(registry: &Shared<V, C>) -> MutexGuard<'_, Registry<V, C>> {
    // Every change completes before the lock is released, so a panic elsewhere leaves the registry consistent.
    registry.lock().unwrap_or_else(PoisonError::into_inner)
}

impl<V: View, C: Clone> Registry<V, C> {
    /// Ends a session now: its seats see `reason`, and its link and tokens die with it.
    pub fn end(&mut self, id: SessionId, reason: EndReason) {
        if let Some(mut session) = self.sessions.remove(&id) {
            session.state.ended = Some(reason);
            session.publish();
        }
    }
}

/// Starts the grace period of a seat whose live handle was dropped. Does nothing for a stale lease.
pub(crate) fn disconnect<V, C>(
    registry: &Shared<V, C>,
    session_id: SessionId,
    participant: ParticipantId,
    lease: u64,
) where
    V: View,
    C: Clone + Send + 'static,
{
    let grace = {
        let mut guard = lock(registry);
        let grace = guard.grace;
        let Some(session) = guard.sessions.get_mut(&session_id) else {
            return;
        };
        match session.seat_mut(participant) {
            Some(seat) if seat.lease == lease => seat.present = false,
            _ => return,
        }
        session.send_cursor(participant, None);
        session.publish();
        grace
    };
    match tokio::runtime::Handle::try_current() {
        Ok(runtime) => {
            let registry = Arc::clone(registry);
            runtime.spawn(async move {
                tokio::time::sleep(grace).await;
                expire(&registry, session_id, participant, lease);
            });
        }
        // No timer can run, so the grace period cannot be kept.
        Err(_) => expire(registry, session_id, participant, lease),
    }
}

/// The grace period of `lease` ran out: an away owner ends the session, an away watcher loses its seat.
fn expire<V: View, C: Clone>(
    registry: &Shared<V, C>,
    session_id: SessionId,
    participant: ParticipantId,
    lease: u64,
) {
    let mut guard = lock(registry);
    let Some(session) = guard.sessions.get_mut(&session_id) else {
        return;
    };
    let Some(seat) = session.seat_mut(participant) else {
        return;
    };
    if seat.lease != lease || seat.present {
        return;
    }
    if seat.kind == Kind::Owner {
        guard.end(session_id, EndReason::OwnerGone);
    } else {
        session.remove_seat(participant);
    }
}

/// Every session of one backend.
pub struct Sessions<V: View, C: Clone + Send + 'static> {
    registry: Shared<V, C>,
}

impl<V: View, C: Clone + Send + 'static> Clone for Sessions<V, C> {
    fn clone(&self) -> Self {
        Self {
            registry: Arc::clone(&self.registry),
        }
    }
}

impl<V: View, C: Clone + Send + 'static> Sessions<V, C> {
    /// An empty registry whose seats keep their place for `grace` after their connection drops.
    pub fn new(grace: Duration) -> Self {
        Self {
            registry: Arc::new(Mutex::new(Registry {
                sessions: HashMap::new(),
                participants: 0,
                grace,
            })),
        }
    }

    /// The sessions that exist, by name.
    pub fn list(&self) -> Vec<Summary> {
        let guard = lock(&self.registry);
        let mut list: Vec<Summary> = guard
            .sessions
            .iter()
            .map(|(id, session)| Summary {
                id: *id,
                name: session.state.name.clone(),
                owner_present: session
                    .state
                    .seat(session.state.owner)
                    .is_some_and(|seat| seat.present),
                participants: session.state.seats.len(),
                link: session.state.link.clone(),
            })
            .collect();
        list.sort_by(|a, b| {
            a.name
                .cmp(&b.name)
                .then_with(|| a.id.to_string().cmp(&b.id.to_string()))
        });
        list
    }

    /// Starts a session owned by the returned seat. Sessions are private until someone calls this, and it ends with
    /// the owner.
    pub fn create(&self, cursors: CursorOptions) -> Owner<V, C> {
        let mut guard = lock(&self.registry);
        let id = loop {
            let id = SessionId::generate();
            if !guard.sessions.contains_key(&id) {
                break id;
            }
        };
        let owner = guard.next_participant();
        let state = State {
            revision: 0,
            name: String::new(),
            owner,
            options: Options::default(),
            link: JoinLink {
                session: id,
                secret: Secret::generate(),
            },
            ended: None,
            seats: Vec::new(),
            views: Views::new(),
        };
        let mut session = Session {
            tx: watch::channel(Arc::new(state.clone())).0,
            state,
            private: HashMap::new(),
            cursors: broadcast::channel(CURSOR_BACKLOG).0,
        };
        let entry = session.enter(owner, Kind::Owner, cursors);
        let connection = self.connect(&session, id, owner, entry);
        guard.sessions.insert(id, session);
        Owner::new(connection)
    }

    /// Seats a watcher in session `id`. For callers allowed to see every session; everyone else uses [`join`].
    ///
    /// [`join`]: Self::join
    pub fn watch(
        &self,
        id: SessionId,
        cursors: CursorOptions,
    ) -> Result<Watcher<V, C>, SessionError> {
        self.seat_watcher(id, None, cursors)
    }

    /// Seats a watcher in the session the link opens, if its secret is the current one.
    pub fn join(
        &self,
        link: &JoinLink,
        cursors: CursorOptions,
    ) -> Result<Watcher<V, C>, SessionError> {
        self.seat_watcher(link.session, Some(&link.secret), cursors)
    }

    fn seat_watcher(
        &self,
        id: SessionId,
        secret: Option<&Secret>,
        cursors: CursorOptions,
    ) -> Result<Watcher<V, C>, SessionError> {
        let mut guard = lock(&self.registry);
        let participant = guard.next_participant();
        let session = guard.sessions.get_mut(&id).ok_or(SessionError::NotFound)?;
        if secret.is_some_and(|secret| *secret != session.state.link.secret) {
            return Err(SessionError::Rejected);
        }
        if session.state.seats.len() >= MAX_SEATS {
            return Err(SessionError::Full);
        }
        let entry = session.enter(participant, Kind::Watcher, cursors);
        Ok(Watcher::new(self.connect(session, id, participant, entry)))
    }

    /// Takes back the seat that holds `token`, within its grace period or while another connection still has it.
    /// The old connection gets `Replaced`, the old token dies, and the returned seat carries the new one.
    pub fn resume(&self, token: &Secret) -> Result<Seat<V, C>, SessionError> {
        let mut guard = lock(&self.registry);
        // Compares every token, so the time taken does not say how many leading digits matched.
        let mut found = None;
        for (session_id, session) in &guard.sessions {
            for (participant, private) in &session.private {
                if private.token == *token {
                    found = Some((*session_id, *participant));
                }
            }
        }
        let (session_id, participant) = found.ok_or(SessionError::NotFound)?;
        let session = guard
            .sessions
            .get_mut(&session_id)
            .ok_or(SessionError::NotFound)?;
        let token = Secret::generate();
        let Some(private) = session.private.get_mut(&participant) else {
            return Err(SessionError::NotFound);
        };
        private.token = token.clone();
        let show = Arc::clone(&private.show);
        let Some(seat) = session.seat_mut(participant) else {
            return Err(SessionError::NotFound);
        };
        seat.lease += 1;
        seat.present = true;
        let (kind, lease) = (seat.kind, seat.lease);
        session.publish();
        let entry = Entry { lease, token, show };
        let connection = self.connect(session, session_id, participant, entry);
        Ok(match kind {
            Kind::Owner => Seat::Owner(Owner::new(connection)),
            Kind::Watcher => Seat::Watcher(Watcher::new(connection)),
        })
    }

    fn connect(
        &self,
        session: &Session<V, C>,
        id: SessionId,
        participant: ParticipantId,
        entry: Entry,
    ) -> Connection<V, C> {
        Connection::new(
            Arc::clone(&self.registry),
            id,
            participant,
            entry,
            session.subscribe(),
        )
    }
}

impl<V, C> Registry<V, C> {
    fn next_participant(&mut self) -> ParticipantId {
        self.participants = self.participants.wrapping_add(1);
        ParticipantId(self.participants)
    }
}
