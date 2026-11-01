//! Stream authority: tickets, grants, and the re-check that keeps them honest.
//!
//! A seat asks for a ticket naming a stream; the stream's own connection redeems it once and gets a grant. Every
//! transition re-authorizes every ticket and grant, so one that no longer passes is removed in the same step and
//! reported in the transition's effects.

use super::{
    ContentRef, GrantId, Lease, Refusal, SeatId, SeatKind, SessionState, SharingGen, Use,
    authorize::{authorize, authorize_stream, open_entry},
    transition::{Effects, Fresh, Outcome},
};
use crate::Secret;
use playback::Authority;
use std::{fmt, str::FromStr, time::Duration};
use tokio::time::Instant;

/// How long a ticket can be redeemed.
pub const TICKET_TTL: Duration = Duration::from_secs(10);
/// The most streams (granted and still ticketed) one seat holds.
const MAX_STREAMS_PER_SEAT: usize = 8;
/// The most streams the whole session holds.
const MAX_STREAMS_PER_SESSION: usize = 64;

/// A bearer ticket for one stream: 128 random bits, single use, never put in a URL or a log.
#[derive(Clone, PartialEq, Eq)]
pub struct Ticket(pub(super) Secret);

impl fmt::Debug for Ticket {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Ticket(..)")
    }
}

/// The 32 hex digits a ticket travels as.
impl fmt::Display for Ticket {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl FromStr for Ticket {
    type Err = Refusal;

    fn from_str(text: &str) -> Result<Self, Refusal> {
        text.parse::<Secret>()
            .map(Self)
            .map_err(|_| Refusal::BadTicket)
    }
}

/// A ticket issued and not yet redeemed, bound to the seat, lease and sharing it was asked by and to one stream.
#[derive(Clone, Debug)]
pub(super) struct PendingTicket {
    secret: Secret,
    seat: SeatId,
    lease: Lease,
    sharing: Option<SharingGen>,
    key: ContentRef,
    expires: Instant,
}

/// One authorized stream, held by the connection that redeemed its ticket.
#[derive(Clone, Debug)]
pub(super) struct Grant {
    seat: SeatId,
    lease: Lease,
    sharing: Option<SharingGen>,
    key: ContentRef,
    /// What the holder was last told it may demand.
    authority: Authority,
}

impl SessionState {
    /// Whether some ticket of this session is `ticket`. Compares every ticket, so time does not say which matched.
    pub(super) fn has_ticket(&self, ticket: &Ticket) -> bool {
        self.tickets
            .iter()
            .fold(false, |found, pending| (pending.secret == ticket.0) | found)
    }

    fn streams_of(&self, seat: SeatId) -> usize {
        let granted = self.grants.values().filter(|grant| grant.seat == seat);
        let ticketed = self.tickets.iter().filter(|ticket| ticket.seat == seat);
        granted.count() + ticketed.count()
    }
}

/// Issues a ticket whose secret is `fresh.secret`, if `seat` may receive `key` and has room for another stream.
pub(super) fn request(
    next: &mut SessionState,
    seat: SeatId,
    lease: Lease,
    key: ContentRef,
    fresh: &Fresh,
) -> Result<(), Refusal> {
    next.tickets.retain(|ticket| ticket.expires > fresh.now);
    if next.streams_of(seat) >= MAX_STREAMS_PER_SEAT
        || next.grants.len() + next.tickets.len() >= MAX_STREAMS_PER_SESSION
    {
        return Err(Refusal::TooManyStreams);
    }
    let sharing = next.seats.get(&seat).and_then(|seat| seat.kind.sharing());
    next.tickets.push(PendingTicket {
        secret: fresh.secret.clone(),
        seat,
        lease,
        sharing,
        key,
        expires: fresh.now + TICKET_TTL,
    });
    Ok(())
}

/// Spends `ticket` into a grant, if it is still valid for the seat and stream it was issued for.
pub(super) fn redeem(
    next: &mut SessionState,
    fx: &mut Effects,
    ticket: &Ticket,
    now: Instant,
) -> Result<(), Refusal> {
    let mut found = None;
    for (at, pending) in next.tickets.iter().enumerate() {
        if pending.secret == ticket.0 {
            found = Some(at);
        }
    }
    let pending = next.tickets.remove(found.ok_or(Refusal::BadTicket)?);
    let current = next
        .seats
        .get(&pending.seat)
        .and_then(|seat| seat.kind.sharing());
    if now >= pending.expires || current != pending.sharing {
        return Err(Refusal::BadTicket);
    }
    let authority = authorize_stream(next, pending.seat, pending.lease, &pending.key)?;
    let (_, content, entry) =
        open_entry(&next.doc, &pending.key).ok_or(Refusal::ContentUnavailable)?;
    let (content, entry) = (content.clone(), Box::new(entry.clone()));
    let host_details = next.files
        && next
            .seats
            .get(&pending.seat)
            .is_some_and(|seat| matches!(seat.kind, SeatKind::Owner));
    next.next_grant += 1;
    let grant = GrantId(next.next_grant);
    next.grants.insert(
        grant,
        Grant {
            seat: pending.seat,
            lease: pending.lease,
            sharing: pending.sharing,
            key: pending.key,
            authority,
        },
    );
    fx.outcome = Outcome::Opened {
        grant,
        authority,
        content,
        entry,
        host_details,
    };
    Ok(())
}

/// The authority `grant` has now, or `None` when it no longer passes: its seat is gone or on another lease or
/// sharing, or [`authorize`] refuses its stream.
fn authority_now(state: &SessionState, grant: &Grant) -> Option<Authority> {
    let seat = state.seats.get(&grant.seat)?;
    if seat.lease != grant.lease || seat.kind.sharing() != grant.sharing {
        return None;
    }
    authorize_stream(state, grant.seat, grant.lease, &grant.key).ok()
}

/// Re-authorizes every ticket and grant after a transition. A failing ticket disappears; a failing grant is removed
/// for good and reported in `fx.revoke`; a grant whose demand changed is reported in `fx.retarget`.
pub(super) fn sweep(next: &mut SessionState, fx: &mut Effects) {
    let valid: Vec<bool> = next
        .tickets
        .iter()
        .map(|ticket| {
            let seat = next.seats.get(&ticket.seat);
            seat.is_some_and(|seat| seat.kind.sharing() == ticket.sharing)
                && authorize(next, ticket.seat, ticket.lease, Use::Stream(&ticket.key)).is_ok()
        })
        .collect();
    let mut valid = valid.into_iter();
    next.tickets.retain(|_| valid.next().unwrap_or(false));

    let mut moved = Vec::new();
    for (id, grant) in &next.grants {
        match authority_now(next, grant) {
            None => fx.revoke.push(*id),
            Some(now) if now != grant.authority => moved.push((*id, now)),
            Some(_) => {}
        }
    }
    for id in &fx.revoke {
        next.grants.remove(id);
    }
    for (id, now) in moved {
        if let Some(grant) = next.grants.get_mut(&id) {
            grant.authority = now;
            fx.retarget.push((id, now));
        }
    }
}
