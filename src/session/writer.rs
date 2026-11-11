//! The writer half of a Session socket: `Welcome`, then the Doc, the seat's part, the roster and the cursors as they
//! change, and the pings and heartbeats that show the link is up. It never waits on the reader, so a stalled send
//! cannot hold up incoming messages.
//!
//! The feed keeps only the latest state, so a slow client skips intermediate revisions. Whatever it receives is
//! consistent: the Doc comes before any `Seat` whose `doc_rev` is newer, so a view never points at a Doc the client
//! has not got.

use super::exit::{Exit, send, send_frame};
use crate::AppState;
use api_types::{Audience, CursorPoint, SeatAccess, SessionServerMessage, SharingInfo};
use axum::extract::ws::Message;
use futures_util::Sink;
use sessions::{
    EndReason, SeatMode,
    session::{Can, Change, Handle, RosterEntry, SeatKind, SessionState, View, can},
};
use std::{convert::Infallible, sync::Arc, time::Duration};
use tokio::{
    sync::{mpsc, watch},
    time::{Instant, MissedTickBehavior, interval_at},
};

/// How often the server pings.
const PING_INTERVAL: Duration = Duration::from_secs(10);
/// How often the server sends `Heartbeat`, for browsers that cannot see pings.
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(10);

/// What this seat sees besides the Doc, so a state that changed only for other seats sends nothing.
#[derive(PartialEq)]
struct Mine {
    doc_rev: u64,
    presenter_rev: u64,
    access: SeatAccess,
    can: Can,
    mode: SeatMode,
    sharing: Option<SharingInfo>,
    view: View,
    views_done: u64,
}

/// What one socket has sent of the session.
struct Sent {
    doc_rev: Option<u64>,
    mine: Option<Mine>,
}

/// Sends what the seat should see until the session ends for it, the link breaks or `replies` closes.
/// `replies` carries the answers the reader has for its client.
pub(super) async fn run<S: Sink<Message> + Unpin>(
    state: &AppState,
    sink: &mut S,
    handle: &Handle<CursorPoint>,
    mut replies: mpsc::Receiver<SessionServerMessage>,
) -> Result<Infallible, Exit> {
    let mut feed = handle.feed();
    let mut cursors = handle.cursors();
    let mut roster = handle.roster();
    let first = match feed.current() {
        Change::State(first) => first,
        Change::Ended(reason) => return Err(Exit::Ended(reason)),
    };
    let seat = first.seats.get(&handle.seat());
    let audience = super::project::audience_of(&first, handle.seat(), handle.lease());
    let welcome = SessionServerMessage::Welcome {
        session: handle.session().to_string(),
        seat: handle.seat().0,
        resume: handle.resume_token().to_string(),
        views_done: seat.map_or(0, |seat| seat.views_done),
        server_us: state.sessions.now_us(),
    };
    send(sink, &welcome).await?;
    let mut sent = Sent {
        doc_rev: None,
        mine: None,
    };
    send_parts(state, sink, handle, &first, audience, &mut sent).await?;
    send_roster(sink, &mut roster).await?;

    let ticker = |period: Duration| {
        let mut ticker = interval_at(Instant::now() + period, period);
        ticker.set_missed_tick_behavior(MissedTickBehavior::Delay);
        ticker
    };
    let mut heartbeat = ticker(HEARTBEAT_INTERVAL);
    let mut ping = ticker(PING_INTERVAL);
    let mut cursors_open = true;
    loop {
        tokio::select! {
            change = feed.next() => match change {
                Change::State(next) => {
                    send_parts(state, sink, handle, &next, audience, &mut sent).await?;
                }
                Change::Ended(reason) => return Err(Exit::Ended(reason)),
            },
            changed = roster.changed() => {
                // The roster's sender lives as long as the session; without it the session ended and the feed says so.
                if changed.is_ok() {
                    send_roster(sink, &mut roster).await?;
                }
            },
            reply = replies.recv() => match reply {
                Some(reply) => send(sink, &reply).await?,
                None => return Err(Exit::Gone),
            },
            event = cursors.recv(), if cursors_open => match event {
                Some(event) => {
                    let message = SessionServerMessage::Cursor {
                        participant: event.participant.0,
                        at: event.at,
                    };
                    send(sink, &message).await?;
                }
                // The session ended; its feed says so.
                None => cursors_open = false,
            },
            _ = heartbeat.tick() => {
                let message = SessionServerMessage::Heartbeat { server_us: state.sessions.now_us() };
                send(sink, &message).await?;
            },
            _ = ping.tick() => send_frame(sink, Message::Ping(Vec::new().into())).await?,
        }
    }
}

/// The Doc, if it changed since the last one sent, then this seat's part, if that changed.
async fn send_parts<S: Sink<Message> + Unpin>(
    state: &AppState,
    sink: &mut S,
    handle: &Handle<CursorPoint>,
    session: &SessionState,
    audience: Audience,
    sent: &mut Sent,
) -> Result<(), Exit> {
    let Some(seat) = session.seats.get(&handle.seat()) else {
        return Err(Exit::Ended(EndReason::Stopped));
    };
    if sent.doc_rev != Some(session.doc_rev) {
        let doc = super::project::project(&session.doc, audience, session.expands);
        let message = SessionServerMessage::Doc {
            doc_rev: session.doc_rev,
            doc,
        };
        send(sink, &message).await?;
        sent.doc_rev = Some(session.doc_rev);
    }
    let owner = matches!(seat.kind, SeatKind::Owner);
    let mine = Mine {
        doc_rev: session.doc_rev,
        presenter_rev: session.presenter_rev,
        access: match (&seat.kind, &session.sharing) {
            (SeatKind::Owner, _) => SeatAccess::Owner,
            (SeatKind::Guest { .. }, Some(sharing)) => sharing.access.into(),
            // A Guest never outlives its sharing; if it somehow did, watching is the least it could do.
            (SeatKind::Guest { .. }, None) => SeatAccess::Watch,
        },
        // A seat the table no longer knows can do nothing; the feed ends it next.
        can: can(session, handle.seat(), seat.lease).unwrap_or_default(),
        mode: seat.mode(),
        sharing: session.sharing.as_ref().map(|sharing| SharingInfo {
            access: sharing.access,
            // Only an Owner seat holds the link.
            link: owner
                .then(|| state.hosting.join_url(&sharing.link))
                .flatten(),
            link_problem: owner.then(|| state.hosting.link_problem()).flatten(),
        }),
        view: (**seat.view.as_ref().unwrap_or(&session.presenter)).clone(),
        views_done: seat.views_done,
    };
    if sent.mine.as_ref() == Some(&mine) {
        return Ok(());
    }
    let message = SessionServerMessage::Seat {
        rev: session.revision,
        doc_rev: mine.doc_rev,
        presenter_rev: mine.presenter_rev,
        access: mine.access,
        can: mine.can,
        mode: mine.mode,
        sharing: mine.sharing.clone(),
        view: mine.view.clone(),
        views_done: mine.views_done,
    };
    send(sink, &message).await?;
    sent.mine = Some(mine);
    Ok(())
}

async fn send_roster<S: Sink<Message> + Unpin>(
    sink: &mut S,
    roster: &mut watch::Receiver<Arc<Vec<RosterEntry>>>,
) -> Result<(), Exit> {
    let participants = roster.borrow_and_update().to_vec();
    send(sink, &SessionServerMessage::Roster { participants }).await
}
