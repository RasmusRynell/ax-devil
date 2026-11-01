//! Between the share socket's wire types and the sessions crate: the view the sessions share, what a received
//! message means, what a seat is sent, and the token buckets that bound what a seat may send.

use ::sessions::{EndReason, Kind, Mode, Options, Personal, SeatMessage, View};
use api_types::{
    CURSOR_NAME_MAX, CursorElement, CursorPoint, EndedReason, SeatInfo, SeatMode, SeatRole,
    ShareClientMessage, ShareOptions, ShareSnapshot, ViewState,
};
use tokio::time::Instant;

/// The view the share sockets give to the sessions crate.
#[derive(Clone, Default, PartialEq)]
pub struct Shown(pub ViewState);

impl View for Shown {
    fn restrict(&mut self, _presenter: &Self) {
        // Nothing but the sidebar is shared today, and the owner closing it closes nothing an explorer could still
        // show. Once viewers exist, this drops the ones the owner closed.
    }
}

/// A sessions registry of what the share socket carries.
pub type ShareSessions = ::sessions::Sessions<Shown, CursorPoint>;
/// A seat of such a registry.
pub type Seat = ::sessions::Seat<Shown, CursorPoint>;

/// What a received message asks for, once decoded.
pub enum Incoming {
    /// A second `Hello`.
    Hello,
    /// The seat gives up: an owner stops sharing, a watcher leaves.
    Leave,
    /// A message the seat's role decides on.
    Seat(SeatMessage<Shown, CursorPoint>),
    /// A cursor position that cannot be shared as sent.
    Ignore,
}

/// Decodes a message; the role check happens in the sessions crate.
pub fn incoming(message: ShareClientMessage) -> Incoming {
    match message {
        ShareClientMessage::Hello { .. } => Incoming::Hello,
        ShareClientMessage::Leave => Incoming::Leave,
        ShareClientMessage::Publish { state } => Incoming::Seat(SeatMessage::Publish(Shown(state))),
        ShareClientMessage::SetOptions { explore } => {
            Incoming::Seat(SeatMessage::SetOptions(Options { explore }))
        }
        ShareClientMessage::RegenerateLink => Incoming::Seat(SeatMessage::RegenerateLink),
        ShareClientMessage::Explore => Incoming::Seat(SeatMessage::Explore),
        ShareClientMessage::SetView { state, seq } => Incoming::Seat(SeatMessage::SetView {
            view: Shown(state),
            seq,
        }),
        ShareClientMessage::Follow => Incoming::Seat(SeatMessage::Follow),
        ShareClientMessage::Cursor { at: None } => Incoming::Seat(SeatMessage::Cursor(None)),
        ShareClientMessage::Cursor { at: Some(at) } => clamped(at).map_or(Incoming::Ignore, |at| {
            Incoming::Seat(SeatMessage::Cursor(Some(at)))
        }),
        ShareClientMessage::CursorOptions(options) => {
            Incoming::Seat(SeatMessage::CursorOptions(cursor_options(options)))
        }
    }
}

/// Keeps every fraction inside `[0, 1]`; `None` when a name is too long to broadcast to every seat.
fn clamped(at: CursorPoint) -> Option<CursorPoint> {
    let too_long = |text: &str| text.chars().count() > CURSOR_NAME_MAX;
    if too_long(&at.scope)
        || at
            .element
            .as_ref()
            .is_some_and(|element| too_long(&element.id))
    {
        return None;
    }
    Some(CursorPoint {
        x: fraction(at.x),
        y: fraction(at.y),
        element: at.element.map(|element| CursorElement {
            x: fraction(element.x),
            y: fraction(element.y),
            ..element
        }),
        ..at
    })
}

fn fraction(value: f32) -> f32 {
    if value.is_finite() {
        value.clamp(0.0, 1.0)
    } else {
        0.0
    }
}

/// The sessions crate's form of a seat's cursor choices.
pub fn cursor_options(options: api_types::CursorOptions) -> ::sessions::CursorOptions {
    ::sessions::CursorOptions {
        show: options.show,
        share: options.share,
    }
}

fn role(kind: Kind) -> SeatRole {
    match kind {
        Kind::Owner => SeatRole::Owner,
        Kind::Watcher => SeatRole::Watcher,
    }
}

fn mode(mode: Mode) -> SeatMode {
    match mode {
        Mode::Following => SeatMode::Following,
        Mode::Exploring => SeatMode::Exploring,
    }
}

/// What a seat is sent of its session.
pub fn snapshot(personal: &Personal<Shown>) -> ShareSnapshot {
    ShareSnapshot {
        revision: personal.revision,
        name: personal.name.clone(),
        owner: personal.owner.0,
        owner_present: personal.owner_present,
        options: ShareOptions {
            explore: personal.options.explore,
        },
        participants: personal
            .participants
            .iter()
            .map(|participant| SeatInfo {
                id: participant.id.0,
                name: participant.name.clone(),
                color: participant.color,
                role: role(participant.kind),
                mode: mode(participant.mode),
                present: participant.present,
                shares_cursor: participant.shares_cursor,
            })
            .collect(),
        presenter: personal.presenter.0,
        mode: mode(personal.mode),
        view: personal.view.0,
        view_seq: personal.view_seq,
    }
}

/// The wire form of why a session ended for a seat.
pub fn ended(reason: EndReason) -> EndedReason {
    match reason {
        EndReason::Stopped => EndedReason::Stopped,
        EndReason::OwnerGone => EndedReason::OwnerGone,
        EndReason::Replaced => EndedReason::Replaced,
    }
}

/// Allows `rate` takes per second, with a burst of one second's worth.
pub struct Bucket {
    rate: f64,
    tokens: f64,
    at: Instant,
}

impl Bucket {
    /// A full bucket.
    pub fn new(rate: u32) -> Self {
        Self {
            rate: f64::from(rate),
            tokens: f64::from(rate),
            at: Instant::now(),
        }
    }

    /// Takes one token; false when none is left.
    pub fn take(&mut self) -> bool {
        let now = Instant::now();
        let elapsed = now.duration_since(self.at).as_secs_f64();
        self.at = now;
        self.tokens = (self.tokens + elapsed * self.rate).min(self.rate);
        if self.tokens >= 1.0 {
            self.tokens -= 1.0;
            true
        } else {
            false
        }
    }
}
