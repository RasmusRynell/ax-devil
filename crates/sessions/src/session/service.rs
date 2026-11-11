//! The service: every session of one backend behind one lock.
//!
//! Each change is a [`transition`] under the lock; the result is published once and the [`Effects`] run after the
//! lock is released. A seat's lease doubles as the generation of its grace timer, as in the generic registry.

use super::{
    Completer, Completion, GrantId, Handle, JobId, Lease, Overlays, Refusal, Runner, SeatId,
    SessionState, Spawn, Ticket, View, Watcher,
    handle::{Entry, StreamLease},
    roster::{Identity, Presence, RosterEntry, build},
    runner::Read,
    sources::Sources,
    transition::{Effects, Input, Outcome, transition},
};
use crate::{
    CursorEvent, CursorOptions, JoinLink, MAX_SESSIONS, Secret, SessionId, common,
    cursors::CURSOR_BACKLOG,
};
use playback::Authority;
use std::{
    collections::{BTreeMap, HashMap},
    sync::{Arc, Mutex, atomic::AtomicBool},
    time::Duration,
};
use tokio::{
    sync::{broadcast, watch},
    time::Instant,
};
use workspace::store::Resolution;
use workspace::{Content, ItemId, WorkspaceItem};

/// An item as the session that holds it has it.
#[derive(Clone, Debug)]
pub struct HeldItem {
    /// The item, with its recipe as stored.
    pub item: WorkspaceItem,
    /// Whether its viewer was requested: the user started it.
    pub open: bool,
    /// What it resolved to, once it has.
    pub content: Option<Content>,
}

/// What a seat keeps out of the published state.
struct Private {
    token: Secret,
    show: Arc<AtomicBool>,
    presence: Presence,
}

/// Effects collected under the lock and run after it: only the starts of jobs, which may run arbitrary code.
#[derive(Default)]
pub(super) struct Deferred {
    spawn: Vec<(SessionId, Spawn)>,
}

/// What a transition gave back to the code that asked for it.
pub(super) struct Applied {
    pub outcome: Outcome,
    pub stream: Option<watch::Receiver<Authority>>,
}

/// One session as the service holds it.
pub(super) struct Live<C> {
    pub state: Arc<SessionState>,
    tx: watch::Sender<Arc<SessionState>>,
    pub cursors: broadcast::Sender<CursorEvent<C>>,
    roster: watch::Sender<Arc<Vec<RosterEntry>>>,
    private: BTreeMap<SeatId, Private>,
    /// Tells each open stream's holder what it may demand. Gone once the grant is released or revoked.
    streams: BTreeMap<GrantId, watch::Sender<Authority>>,
}

impl<C: Clone> Live<C> {
    /// Runs the transition and installs its state. Revocations, new authority and cancellations take effect before
    /// the state is published, still under the lock, so nobody sees the new state while a stream or a job still
    /// follows the old one; only the starts of jobs are deferred.
    fn apply(&mut self, input: Input, deferred: &mut Deferred) -> Result<Applied, Refusal> {
        let (next, effects) = transition(&self.state, input)?;
        let Effects {
            spawn,
            cancel,
            revoke,
            retarget,
            outcome,
        } = effects;
        let next = Arc::new(next);
        let stream = match &outcome {
            Outcome::Opened {
                grant, authority, ..
            } => {
                let (tx, rx) = watch::channel(*authority);
                self.streams.insert(*grant, tx);
                Some(rx)
            }
            Outcome::None | Outcome::Seated { .. } | Outcome::ViewRefused(_) => None,
        };
        for grant in revoke {
            // The sender goes with the grant, so `Revoked` is the last value its holders will ever see.
            if let Some(tx) = self.streams.remove(&grant) {
                tx.send_replace(Authority::Revoked);
            }
        }
        for (grant, authority) in retarget {
            if let Some(tx) = self.streams.get(&grant) {
                tx.send_replace(authority);
            }
        }
        for token in cancel {
            token.cancel();
        }
        // A released grant has no holder left to tell.
        self.streams.retain(|grant, _| next.has_grant(*grant));
        deferred
            .spawn
            .extend(spawn.into_iter().map(|spawn| (next.id, spawn)));
        let gone: Vec<SeatId> = self
            .private
            .keys()
            .filter(|seat| !next.seats.contains_key(seat))
            .copied()
            .collect();
        for seat in gone {
            // The seat's token dies with it, so nothing can resume it.
            self.private.remove(&seat);
            self.hide_cursor(seat);
        }
        if next.revision != self.state.revision {
            self.tx.send_replace(Arc::clone(&next));
        }
        self.state = next;
        self.publish_roster();
        Ok(Applied { outcome, stream })
    }

    /// Publishes the roster of the state and the seats' presence, if it differs from the last one.
    pub fn publish_roster(&self) {
        let next = build(&self.state, |seat| {
            self.private.get(&seat).map(|private| &private.presence)
        });
        self.roster.send_if_modified(|current| {
            let changed = **current != next;
            if changed {
                *current = Arc::new(next);
            }
            changed
        });
    }

    /// Tells the others a seat's cursor is gone. No receivers only means everyone else is gone.
    pub fn hide_cursor(&self, seat: SeatId) {
        let _ = self.cursors.send(CursorEvent {
            participant: seat,
            at: None,
        });
    }

    /// The identity a seat gets when it first sits down: what no seat here has.
    fn new_identity(&self) -> Identity {
        Identity::next(
            self.private
                .values()
                .map(|private| &private.presence.identity),
        )
    }

    /// Gives `seat` a connection with a new token, keeping its `identity`, and publishes the roster.
    fn enter(
        &mut self,
        seat: SeatId,
        lease: Lease,
        options: CursorOptions,
        identity: Identity,
    ) -> Entry {
        let entry = Entry {
            lease,
            token: Secret::generate(),
            show: Arc::new(AtomicBool::new(options.show)),
            share: Arc::new(AtomicBool::new(options.share)),
            rx: self.tx.subscribe(),
            roster: self.roster.subscribe(),
        };
        let private = Private {
            token: entry.token.clone(),
            show: Arc::clone(&entry.show),
            presence: Presence {
                identity,
                share: Arc::clone(&entry.share),
            },
        };
        self.private.insert(seat, private);
        self.publish_roster();
        entry
    }
}

pub(super) struct Inner<C> {
    pub(super) sessions: Mutex<HashMap<SessionId, Live<C>>>,
    grace: Duration,
    runner: Arc<dyn Runner>,
    epoch: Instant,
    pub(super) sources: Sources,
    /// What `list` answers, as it was last published; see [`Service::listing_revisions`].
    pub(super) listed: super::listing::Listed,
}

/// Every session of one backend.
pub struct Service<C: Clone + Send + 'static> {
    pub(super) inner: Arc<Inner<C>>,
}

impl<C: Clone + Send + 'static> Clone for Service<C> {
    fn clone(&self) -> Self {
        Self {
            inner: Arc::clone(&self.inner),
        }
    }
}

pub(super) type Sessions<C> = HashMap<SessionId, Live<C>>;

/// Applies `input` to session `id`, and takes the session out of the service once it has ended.
pub(super) fn apply<C: Clone>(
    sessions: &mut Sessions<C>,
    id: SessionId,
    input: Input,
    deferred: &mut Deferred,
) -> Result<Applied, Refusal> {
    let live = sessions.get_mut(&id).ok_or(Refusal::SeatGone)?;
    let applied = live.apply(input, deferred)?;
    if live.state.ended.is_some() {
        sessions.remove(&id);
    }
    Ok(applied)
}

impl<C: Clone + Send + 'static> Service<C> {
    /// An empty service. Seats keep their place for `grace` after their connection drops; `runner` runs the jobs
    /// sessions ask for.
    pub fn new(grace: Duration, runner: Arc<dyn Runner>) -> Self {
        Self {
            inner: Arc::new(Inner {
                sessions: Mutex::new(HashMap::new()),
                grace,
                runner,
                epoch: Instant::now(),
                sources: Sources::default(),
                listed: super::listing::Listed::default(),
            }),
        }
    }

    /// The server's clock in microseconds: monotonic, counted from the service's start. The time every
    /// `ClockIntent` and time sync is stamped in.
    pub fn now_us(&self) -> i64 {
        i64::try_from(self.inner.epoch.elapsed().as_micros()).unwrap_or(i64::MAX)
    }

    /// Runs `change` on the sessions under the lock, then the effects it deferred once the lock is released.
    pub(super) fn locked<R>(&self, change: impl FnOnce(&mut Sessions<C>, &mut Deferred) -> R) -> R {
        self.changing(true, change)
    }

    pub(super) fn changing<R>(
        &self,
        listed: bool,
        change: impl FnOnce(&mut Sessions<C>, &mut Deferred) -> R,
    ) -> R {
        let mut deferred = Deferred::default();
        let mut sessions = common::locked(&self.inner.sessions);
        let result = change(&mut sessions, &mut deferred);
        if listed {
            self.publish_listing(&sessions);
        }
        drop(sessions);
        self.run(deferred);
        result
    }

    fn run(&self, deferred: Deferred) {
        for (session, spawn) in deferred.spawn {
            let job = spawn.job;
            let service = self.clone();
            let done = Completer {
                complete: Box::new(move |completion, read| {
                    service.finish(session, job, completion, read);
                }),
                watcher: Watcher::default(),
            };
            self.inner.runner.run(spawn, done);
        }
    }

    /// Commits the result of a job. Dropped when the session is gone, the job is no longer registered, or its owner
    /// has moved on.
    pub fn complete(&self, session: SessionId, job: JobId, completion: Completion) {
        self.finish(session, job, completion, None);
    }

    /// Commits the result of a job, and with it what the job read: recorded only when the job was still registered,
    /// so a superseded job never replaces the sources of the one that is current.
    fn finish(&self, session: SessionId, job: JobId, completion: Completion, read: Read) {
        self.locked(|sessions, deferred| {
            let wanted = sessions
                .get(&session)
                .is_some_and(|live| live.state.jobs.contains_key(&job));
            // A refusal means the session ended: the result is dropped.
            let applied = apply(
                sessions,
                session,
                Input::Complete { job, completion },
                deferred,
            );
            if let (true, Ok(_), Some((item, watch))) = (wanted, applied, read) {
                self.inner.sources.record(session, item, watch);
            }
        });
    }

    /// The item `id` of whichever session holds it. Item ids are unique across sessions.
    pub fn find_item(&self, id: ItemId) -> Option<HeldItem> {
        let sessions = common::locked(&self.inner.sessions);
        sessions.values().find_map(|live| {
            let parts = live.state.doc.workspace.parts();
            let item = parts.workspace.items.iter().find(|item| item.id == id)?;
            Some(HeldItem {
                item: item.clone(),
                open: parts.open.contains(&id),
                content: match parts.resolutions.get(&id) {
                    Some(Resolution::Resolved { content, .. }) => Some(content.clone()),
                    _ => None,
                },
            })
        })
    }

    /// The session's state as it changes, or `None` for a session that is gone. The last value stays readable after
    /// the session ends.
    pub fn subscribe(&self, id: SessionId) -> Option<watch::Receiver<Arc<SessionState>>> {
        common::locked(&self.inner.sessions)
            .get(&id)
            .map(|live| live.tx.subscribe())
    }

    /// Starts a session for a new window. Its first Owner seat is the returned handle; nothing is shared. With `expands`
    /// its items' references read the environment. The Presenter View starts with `overlays`.
    /// Refused with [`Refusal::Full`] at [`MAX_SESSIONS`], counted under the same lock that inserts, so
    /// sessions in their grace period hold a place.
    pub fn create(
        &self,
        expands: bool,
        cursors: CursorOptions,
        overlays: Overlays,
    ) -> Result<Handle<C>, Refusal> {
        let mut sessions = common::locked(&self.inner.sessions);
        if sessions.len() >= MAX_SESSIONS {
            return Err(Refusal::Full);
        }
        let id = loop {
            let id = SessionId::generate();
            if !sessions.contains_key(&id) {
                break id;
            }
        };
        let mut state = SessionState::new(id, expands);
        state.presenter = Arc::new(View {
            overlays,
            ..View::default()
        });
        let seat = state.seat_owner();
        let state = Arc::new(state);
        let mut live = Live {
            tx: watch::channel(Arc::clone(&state)).0,
            state,
            cursors: broadcast::channel(CURSOR_BACKLOG).0,
            roster: watch::channel(Arc::default()).0,
            private: BTreeMap::new(),
            streams: BTreeMap::new(),
        };
        let identity = live.new_identity();
        let entry = live.enter(seat, Lease(0), cursors, identity);
        sessions.insert(id, live);
        self.publish_listing(&sessions);
        Ok(Handle::new(self.clone(), id, seat, entry))
    }

    /// Seats a Guest in the session the link opens, if its secret is the current one.
    pub fn join(&self, link: &JoinLink, cursors: CursorOptions) -> Result<Handle<C>, Refusal> {
        self.seat(
            link.session,
            Input::Join {
                secret: Some(link.secret.clone()),
            },
            cursors,
        )
    }

    /// Seats an Owner in session `id`: a Guest instead when it reads the environment and the caller lacks Full access.
    pub fn attach(
        &self,
        id: SessionId,
        full_access: bool,
        cursors: CursorOptions,
    ) -> Result<Handle<C>, Refusal> {
        self.seat(id, Input::Attach { full_access }, cursors)
    }

    fn seat(
        &self,
        id: SessionId,
        input: Input,
        cursors: CursorOptions,
    ) -> Result<Handle<C>, Refusal> {
        self.locked(|sessions, deferred| {
            let applied = apply(sessions, id, input, deferred)?;
            let Outcome::Seated { seat, lease } = applied.outcome else {
                return Err(Refusal::SeatGone);
            };
            let live = sessions.get_mut(&id).ok_or(Refusal::SeatGone)?;
            let identity = live.new_identity();
            let entry = live.enter(seat, lease, cursors, identity);
            Ok(Handle::new(self.clone(), id, seat, entry))
        })
    }

    /// Takes back the seat that holds `token`, within its grace period or while another connection still has it.
    /// The old connection is replaced, its streams end, the old token dies, and the returned handle carries the new
    /// one.
    pub fn resume(&self, token: &Secret) -> Result<Handle<C>, Refusal> {
        self.locked(|sessions, deferred| {
            // Compares every token, so the time taken does not say how many leading digits matched.
            let mut found = None;
            for (session, live) in sessions.iter() {
                for (seat, private) in &live.private {
                    if private.token == *token {
                        found = Some((*session, *seat));
                    }
                }
            }
            let (id, seat) = found.ok_or(Refusal::SeatGone)?;
            apply(sessions, id, Input::Resume { seat }, deferred)?;
            let live = sessions.get_mut(&id).ok_or(Refusal::SeatGone)?;
            let lease = live
                .state
                .seats
                .get(&seat)
                .map(|seat| seat.lease)
                .ok_or(Refusal::SeatGone)?;
            let private = live.private.get(&seat).ok_or(Refusal::SeatGone)?;
            let options = CursorOptions {
                show: private.show.load(std::sync::atomic::Ordering::Relaxed),
                share: private
                    .presence
                    .share
                    .load(std::sync::atomic::Ordering::Relaxed),
            };
            let identity = private.presence.identity;
            let entry = live.enter(seat, lease, options, identity);
            Ok(Handle::new(self.clone(), id, seat, entry))
        })
    }

    /// Spends `ticket`, once, into a stream the caller holds until it drops the lease.
    pub fn redeem(&self, ticket: &Ticket) -> Result<StreamLease, Refusal> {
        let (session, grant, authority, content, entry, matching, host_details) =
            self.locked(|sessions, deferred| {
                let id = sessions
                    .iter()
                    .find(|(_, live)| live.state.has_ticket(ticket))
                    .map(|(id, _)| *id)
                    .ok_or(Refusal::BadTicket)?;
                let input = Input::Redeem {
                    ticket: ticket.clone(),
                    now: Instant::now(),
                };
                let applied = apply(sessions, id, input, deferred)?;
                match (applied.outcome, applied.stream) {
                    (
                        Outcome::Opened {
                            grant,
                            content,
                            entry,
                            matching,
                            host_details,
                            ..
                        },
                        Some(authority),
                    ) => Ok((id, grant, authority, content, entry, matching, host_details)),
                    _ => Err(Refusal::BadTicket),
                }
            })?;
        let service = self.clone();
        Ok(StreamLease::new(
            (session, grant),
            authority,
            *content,
            entry.map(|entry| *entry),
            matching,
            host_details,
            move || {
                service.locked(|sessions, deferred| {
                    // A session that already ended has released everything.
                    let _ = apply(sessions, session, Input::Release { grant }, deferred);
                });
            },
        ))
    }

    /// The seat's connection dropped: it is away for the grace period, then loses its seat (the last Owner seat ending
    /// the session). Does nothing for a lease that was replaced.
    pub(super) fn disconnect(&self, session: SessionId, seat: SeatId, lease: Lease) {
        let away = self.locked(|sessions, deferred| {
            let input = Input::Disconnect { seat, lease };
            let away = apply(sessions, session, input, deferred).is_ok();
            if let Some(live) = sessions.get(&session).filter(|_| away) {
                live.hide_cursor(seat);
            }
            away
        });
        if away {
            let service = self.clone();
            common::after(self.inner.grace, move || {
                service.locked(|sessions, deferred| {
                    let input = Input::GraceExpired { seat, lease };
                    // A refusal means the seat came back or is gone.
                    let _ = apply(sessions, session, input, deferred);
                });
            });
        }
    }
}
