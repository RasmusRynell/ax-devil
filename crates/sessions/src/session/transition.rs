//! The one pure step: a state and an input make the next state and the effects to run after the lock.

use super::{
    Command, Completion, GrantId, JobId, Lease, Refusal, SeatId, SessionState, Spawn, Ticket, Use,
    authorize::{Standing, authorize, standing},
    commands::{self, Scope, set_workspace},
    jobs,
    layout::Layout,
    state::{Seat, SeatKind},
    streams,
    views::{View, restrict},
};
use crate::{EndReason, MAX_SEATS, Secret};
use foundation::CancelToken;
use overlay_sync::MatchPolicy;
use playback::Authority;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
};
use tokio::time::Instant;
use workspace::store::{DocCommand, Reload, complete};
use workspace::{Content, ContentId, Entry, ItemId};

/// The server's furthest-back time a presenter's clock intent may claim, in microseconds before it is received.
const INTENT_MAX_AGE_US: i64 = 2_000_000;

/// What a transition cannot make up itself: a secret for whatever it mints, and the time.
#[derive(Clone, Debug)]
pub struct Fresh {
    /// A new random secret: the link of a `Share` or `RegenerateLink`, or the ticket of a `RequestTicket`.
    pub secret: Secret,
    /// Now.
    pub now: Instant,
}

impl Fresh {
    /// A new secret and the current time.
    pub fn now() -> Self {
        Self {
            secret: Secret::generate(),
            now: Instant::now(),
        }
    }
}

/// Everything that changes a session.
#[derive(Debug)]
pub enum Input {
    /// A seat's command.
    Command {
        /// Who asks.
        seat: SeatId,
        /// The lease the asker holds.
        lease: Lease,
        /// What it asks.
        command: Command,
        /// Secret and time for the command.
        fresh: Fresh,
    },
    /// A seat's view, from the View lane. Its sequence number is settled whether or not the seat may set a view.
    View {
        /// Who sets it.
        seat: SeatId,
        /// The lease the seat holds.
        lease: Lease,
        /// The view.
        view: View,
        /// The client's sequence number.
        seq: u64,
        /// The server's clock in microseconds, which bounds the presenter's clock intents.
        now_us: i64,
    },
    /// A Guest joins. `secret` is the link's; `None` for a caller allowed to join without one.
    Join {
        /// The link's secret.
        secret: Option<Secret>,
    },
    /// An Owner attaches to the session. It gets an Owner seat, but a session that reads the server's environment
    /// is driven only with Full access: without it the caller joins as a Guest, as [`Input::Join`] without a secret.
    Attach {
        /// Whether the Owner has Full access.
        full_access: bool,
    },
    /// A newer connection takes the seat over: its lease is bumped and the old connection's authority ends.
    Resume {
        /// The seat.
        seat: SeatId,
    },
    /// The seat's connection dropped and the grace period starts.
    Disconnect {
        /// The seat.
        seat: SeatId,
        /// The lease of the connection that dropped.
        lease: Lease,
    },
    /// The seat leaves at once. The last Owner seat leaving ends the session.
    Leave {
        /// The seat.
        seat: SeatId,
        /// Its lease.
        lease: Lease,
    },
    /// The grace period of `lease` ran out.
    GraceExpired {
        /// The seat.
        seat: SeatId,
        /// The lease the timer was started with.
        lease: Lease,
    },
    /// A stream's connection presents its ticket.
    Redeem {
        /// The ticket.
        ticket: Ticket,
        /// Now.
        now: Instant,
    },
    /// The connection holding a grant ended.
    Release {
        /// The grant.
        grant: GrantId,
    },
    /// The files an item was read from changed.
    SourceChanged {
        /// The item.
        item: ItemId,
    },
    /// A job finished. Dropped when the job is no longer registered.
    Complete {
        /// The job.
        job: JobId,
        /// What it produced.
        completion: Completion,
    },
}

/// What a transition produced besides the next state, for its caller.
#[derive(Debug, Default)]
pub enum Outcome {
    /// Nothing to report.
    #[default]
    None,
    /// A seat was taken, by a Guest or an Owner.
    Seated {
        /// Its seat.
        seat: SeatId,
        /// Its lease.
        lease: Lease,
    },
    /// A view was refused. Its sequence number is settled all the same.
    ViewRefused(Refusal),
    /// A ticket was redeemed.
    Opened {
        /// The new grant.
        grant: GrantId,
        /// What the holder may demand.
        authority: Authority,
        /// The Content the stream carries, as it is resolved now (boxed, as it is far larger than the other outcomes).
        content: Box<Content>,
        /// The entry of `content` the stream plays, boxed for the same reason; `None` for a
        /// live video, which has none.
        entry: Option<Box<Entry>>,
        /// How the workspace says each lane of `entry` matches its records to frames; `None` for the default.
        matching: BTreeMap<String, MatchPolicy>,
        /// Whether the holder may learn the host's paths and diagnostic text: it is the owner of the session.
        host_details: bool,
    },
}

/// What the caller does once the session's lock is released.
#[derive(Debug, Default)]
pub struct Effects {
    /// Jobs to run.
    pub spawn: Vec<Spawn>,
    /// Jobs whose owner moved on: cancel them.
    pub cancel: Vec<CancelToken>,
    /// Grants that stopped passing. They are gone from the state for good: set their authority to
    /// [`Authority::Revoked`].
    pub revoke: Vec<GrantId>,
    /// Grants whose demand changed: tell their holders.
    pub retarget: Vec<(GrantId, Authority)>,
    /// What the input produced.
    pub outcome: Outcome,
}

/// Applies `input` to `state`. A refusal changes nothing: the state is borrowed, and the caller keeps it.
pub fn transition(state: &SessionState, input: Input) -> Result<(SessionState, Effects), Refusal> {
    if state.ended.is_some() {
        return Err(Refusal::SeatGone);
    }
    let mut next = state.clone();
    let mut fx = Effects::default();
    apply(&mut next, &mut fx, input)?;
    settle(&mut next, &mut fx, state.doc_rev);
    if next.visibly_differs(state) {
        next.revision += 1;
    }
    Ok((next, fx))
}

fn apply(next: &mut SessionState, fx: &mut Effects, input: Input) -> Result<(), Refusal> {
    match input {
        Input::Command {
            seat,
            lease,
            command,
            fresh,
        } => {
            authorize(next, seat, lease, command.needs())?;
            commands::run(next, fx, seat, lease, command, &fresh)
        }
        Input::View {
            seat,
            lease,
            view,
            seq,
            now_us,
        } => set_view(next, fx, seat, lease, view, seq, now_us),
        Input::Join { secret } => join(next, fx, secret.as_ref()),
        Input::Attach { full_access } => attach(next, fx, full_access),
        Input::Resume { seat } => {
            let seat = next.seats.get_mut(&seat).ok_or(Refusal::SeatGone)?;
            seat.lease = Lease(seat.lease.0 + 1);
            seat.present = true;
            Ok(())
        }
        Input::Disconnect { seat, lease } => {
            let seat = current(next, seat, lease)?;
            seat.present = false;
            Ok(())
        }
        Input::Leave { seat, lease } => leave(next, seat, lease, EndReason::Stopped),
        Input::GraceExpired { seat, lease } => {
            if current(next, seat, lease)?.present {
                return Err(Refusal::SeatGone);
            }
            leave(next, seat, lease, EndReason::OwnerGone)
        }
        Input::Redeem { ticket, now } => streams::redeem(next, fx, &ticket, now),
        Input::Release { grant } => {
            next.grants.remove(&grant);
            Ok(())
        }
        Input::SourceChanged { item } => {
            commands::content(next, fx, DocCommand::SourceChanged(item));
            Ok(())
        }
        Input::Complete { job, completion } => {
            complete_job(next, job, completion);
            Ok(())
        }
    }
}

/// The seat, if `lease` is its current one.
fn current(next: &mut SessionState, seat: SeatId, lease: Lease) -> Result<&mut Seat, Refusal> {
    next.seats
        .get_mut(&seat)
        .filter(|seat| seat.lease == lease)
        .ok_or(Refusal::SeatGone)
}

/// A seat leaves. The last Owner seat leaving ends the session for `owner_reason`; a Guest never keeps it alive.
fn leave(
    next: &mut SessionState,
    seat: SeatId,
    lease: Lease,
    owner_reason: EndReason,
) -> Result<(), Refusal> {
    let kind = current(next, seat, lease)?.kind;
    let another = next
        .seats
        .iter()
        .any(|(id, other)| *id != seat && other.kind == SeatKind::Owner);
    if kind == SeatKind::Owner && !another {
        next.ended = Some(owner_reason);
    } else {
        next.seats.remove(&seat);
    }
    Ok(())
}

fn join(next: &mut SessionState, fx: &mut Effects, secret: Option<&Secret>) -> Result<(), Refusal> {
    // A link to a session that is not shared is as dead as a link with the wrong secret.
    let sharing = next.sharing.as_ref().ok_or(if secret.is_some() {
        Refusal::Link
    } else {
        Refusal::NotSharing
    })?;
    if secret.is_some_and(|secret| *secret != sharing.link.secret) {
        return Err(Refusal::Link);
    }
    if next.seats.len() >= MAX_SEATS {
        return Err(Refusal::Full);
    }
    let seat = next.seat_guest(sharing.generation);
    fx.outcome = Outcome::Seated {
        seat,
        lease: Lease(0),
    };
    Ok(())
}

fn attach(next: &mut SessionState, fx: &mut Effects, full_access: bool) -> Result<(), Refusal> {
    let seat = match admission(next, full_access)? {
        SeatKind::Owner => next.seat_owner(),
        SeatKind::Guest { sharing } => next.seat_guest(sharing),
    };
    fx.outcome = Outcome::Seated {
        seat,
        lease: Lease(0),
    };
    Ok(())
}

/// The seat an Owner attaching to the session would get now, or why none: an Owner seat, except in a session that
/// reads the server's environment and a caller without Full access, who gets a Guest seat if the session is shared.
/// [`Input::Attach`] and the list of sessions both ask this, so they cannot disagree.
pub(super) fn admission(state: &SessionState, full_access: bool) -> Result<SeatKind, Refusal> {
    let guest = if state.expands && !full_access {
        Some(
            state
                .sharing
                .as_ref()
                .ok_or(Refusal::NotSharing)?
                .generation,
        )
    } else {
        None
    };
    if state.seats.len() >= MAX_SEATS {
        return Err(Refusal::Full);
    }
    Ok(guest.map_or(SeatKind::Owner, |sharing| SeatKind::Guest { sharing }))
}

/// Settles `seq` and applies the view if the seat may set one, else reports the refusal in the outcome. Only a seat
/// that is gone is refused outright: it has no sequence number to settle.
fn set_view(
    next: &mut SessionState,
    fx: &mut Effects,
    seat: SeatId,
    lease: Lease,
    view: View,
    seq: u64,
    now_us: i64,
) -> Result<(), Refusal> {
    let standing = standing(next, seat, lease)?;
    let allowed = authorize(next, seat, lease, Use::Command(Scope::View)).and_then(|()| {
        // A layout beyond the pane limit, or one that is not a tree of distinct viewers, is not shown by anyone.
        if view.layout.as_ref().is_none_or(Layout::is_valid) {
            Ok(())
        } else {
            Err(Refusal::Invalid)
        }
    });
    let current = next.seats.get_mut(&seat).ok_or(Refusal::SeatGone)?;
    if seq <= current.views_done {
        // Overwritten by a later view already finished.
        return Ok(());
    }
    current.views_done = seq;
    if let Err(refusal) = allowed {
        fx.outcome = Outcome::ViewRefused(refusal);
        return Ok(());
    }
    match standing {
        Standing::Owner => {
            // Owner views are whole snapshots: concurrent updates can undo another Owner's pane change.
            // Distinguishing a close from a stale omission needs a base revision or explicit pane operations;
            // an incoming view therefore never closes an item just because it omits its pane.
            let view = keep_overlays(
                keep_viewports(
                    clamp_intents(view, &next.presenter, now_us),
                    &next.presenter,
                ),
                &next.presenter,
            );
            let published = restrict(&view, &next.doc);
            if published != *next.presenter {
                next.presenter = Arc::new(published);
                next.presenter_rev += 1;
                next.presenter_by = Some(seat);
            }
        }
        Standing::Following | Standing::Exploring => {
            let stored = next.seats.get(&seat).and_then(|seat| seat.view.as_ref());
            let kept = stored.unwrap_or(&next.presenter);
            let own = Arc::new(restrict(
                &keep_overlays(keep_viewports(view, kept), kept),
                &next.doc,
            ));
            if let Some(seat) = next.seats.get_mut(&seat) {
                seat.view = Some(own);
            }
        }
        // Refused above.
        Standing::Watching => {}
    }
    Ok(())
}

/// The presenter's clock intents are stamped in server time, so none may claim to be from the future or from long
/// ago. An intent the presenter already had is kept as it was stored: the window re-sends the current intent with
/// every other view change, and only a changed one carries a new stamp.
fn clamp_intents(mut view: View, published: &View, now_us: i64) -> View {
    for (id, viewer) in &mut view.viewers {
        let stored = published.viewers.get(id).and_then(|viewer| viewer.clock);
        if let Some(clock) = &mut viewer.clock
            && stored != Some(*clock)
        {
            clock.at_us = clock
                .at_us
                .clamp(now_us.saturating_sub(INTENT_MAX_AGE_US), now_us);
        }
    }
    view
}

/// Every viewer of `view` shows the viewport `stored` has for the same item: a viewport changes only through its own
/// command, and a view sent whole is built from a copy that may be older than another seat's zoom.
fn keep_viewports(mut view: View, stored: &View) -> View {
    for (id, viewer) in &mut view.viewers {
        viewer.viewport = stored
            .viewers
            .get(id)
            .filter(|kept| kept.item == viewer.item)
            .and_then(|kept| kept.viewport);
    }
    view
}

/// `view` with the overlays `stored` has: they change only through their own command.
fn keep_overlays(mut view: View, stored: &View) -> View {
    view.overlays = stored.overlays;
    view
}

/// Entries surviving settlement under the replacement Content that still need validation.
pub(super) fn uncovered(
    state: &SessionState,
    job: JobId,
    completion: &Completion,
) -> Vec<ContentId> {
    let Some(entry) = state.jobs.get(&job) else {
        return Vec::new();
    };
    let super::JobOwner::Doc { item, attempt } = entry.owner;
    let Completion::Resolved {
        result: Ok(content),
        validated,
    } = completion
    else {
        return Vec::new();
    };
    if !matches!(state.doc.workspace.reloads.get(&item), Some(Reload::Running { attempt: running, .. }) if *running == attempt)
    {
        return Vec::new();
    }
    // Preview the actual store commit so hidden refs and selection use the same state as settlement.
    let doc = super::state::Doc {
        workspace: complete(&state.doc.workspace, item, attempt, Ok(content.clone())),
    };
    let (presenter, orphaned) = restricted_presenter(&state.presenter, &doc);
    if orphaned.contains(&item) {
        return Vec::new();
    }
    let mut missing = Vec::new();
    for view in std::iter::once(presenter).chain(
        state
            .seats
            .values()
            .filter_map(|seat| seat.view.as_ref())
            .map(|view| restrict(view, &doc)),
    ) {
        for viewer in view.viewers.values().filter(|viewer| viewer.item == item) {
            if let Some(id) = &viewer.entry
                && !validated.contains(id)
                && !missing.contains(id)
            {
                missing.push(id.clone());
            }
        }
    }
    missing
}

fn complete_job(next: &mut SessionState, job: JobId, completion: Completion) {
    match (jobs::finish(next, job), completion) {
        (Some(super::JobOwner::Doc { item, attempt }), Completion::Resolved { result, .. }) => {
            let workspace = complete(&next.doc.workspace, item, attempt, result);
            set_workspace(next, workspace);
        }
        (None, Completion::Resolved { .. }) => {}
    }
}

/// Restricts the Presenter View and identifies items whose last presenter pane is retired.
fn restricted_presenter(published: &View, doc: &super::state::Doc) -> (View, BTreeSet<ItemId>) {
    let presenter = restrict(published, doc);
    let kept: BTreeSet<_> = presenter
        .viewers
        .values()
        .map(|viewer| viewer.item)
        .collect();
    let orphaned = published
        .viewers
        .values()
        .map(|viewer| viewer.item)
        .filter(|item| !kept.contains(item) && doc.workspace.open.contains(item))
        .collect();
    (presenter, orphaned)
}

/// Brings everything derived back in line with the new state: views inside the Doc, jobs that are still wanted, and
/// tickets and grants that still pass.
fn settle(next: &mut SessionState, fx: &mut Effects, old_doc_rev: u64) {
    if next.doc_rev != old_doc_rev {
        // Losing the last presenter pane closes its item in the same publication.
        let (presenter, orphaned) = restricted_presenter(&next.presenter, &next.doc);
        for item in orphaned {
            commands::content(next, fx, DocCommand::CloseViewer(item));
        }
        if presenter != *next.presenter {
            next.presenter = Arc::new(presenter);
            next.presenter_rev += 1;
            next.presenter_by = None;
        }
        let doc = Arc::clone(&next.doc);
        for seat in next.seats.values_mut() {
            if let Some(view) = &seat.view {
                let restricted = restrict(view, &doc);
                if restricted != **view {
                    seat.view = Some(Arc::new(restricted));
                }
            }
        }
    }
    jobs::sweep(next, fx);
    streams::sweep(next, fx);
}
