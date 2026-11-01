//! Between the Session socket's wire types and the sessions crate: what a received command asks for, what a seat is
//! sent, and the token bucket that bounds what a seat may send.

use api_types::{
    CURSOR_NAME_MAX, CursorElement, CursorPoint, RefusalCode, SeatInfo, SessionCommand,
    workspace::NewItem,
};
use sessions::session::{Command, ContentRef, RosterEntry, StreamKey};
use std::path::Path;
use tokio::time::Instant;
use workspace::{ContentId, ItemId};
use workspace_store::{ContentGen, DocCommand};

/// The command a client asked for. A command whose own content is not valid, such as an unreadable item id or a path
/// that names no file, is `Invalid` before it reaches the Session.
pub(super) fn command(wire: SessionCommand) -> Result<Command, RefusalCode> {
    Ok(match wire {
        SessionCommand::Content { command } => Command::Content(doc_command(command)?),
        SessionCommand::Follow => Command::Follow,
        SessionCommand::Control { control } => Command::Control(control.into()),
        SessionCommand::RequestTicket { key } => Command::RequestTicket(stream_key(key)?),
    })
}

fn doc_command(wire: api_types::DocCommand) -> Result<DocCommand, RefusalCode> {
    let item = |text: &str| ItemId::parse(text).ok_or(RefusalCode::Invalid);
    Ok(match wire {
        api_types::DocCommand::AddItems { items, open } => {
            // The intake checks every entry first, so a request that has one bad entry adds nothing.
            let items = items
                .iter()
                .map(|new| match new {
                    NewItem::Video { path } => workspace::intake::video(Path::new(path)),
                })
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| {
                    tracing::debug!("An item was refused: {}", error.reason);
                    RefusalCode::Invalid
                })?;
            DocCommand::AddItems { items, open }
        }
        api_types::DocCommand::OpenViewer { item: text } => DocCommand::OpenViewer(item(&text)?),
        api_types::DocCommand::CloseViewer { item: text } => DocCommand::CloseViewer(item(&text)?),
    })
}

fn stream_key(wire: api_types::StreamKey) -> Result<StreamKey, RefusalCode> {
    Ok(match wire {
        api_types::StreamKey::Frames {
            content,
            generation,
        } => StreamKey::Frames(ContentRef {
            content: ContentId::parse(&content).ok_or(RefusalCode::Invalid)?,
            generation: ContentGen(generation),
        }),
        api_types::StreamKey::Live { item, generation } => StreamKey::Live {
            item: ItemId::parse(&item).ok_or(RefusalCode::Invalid)?,
            generation: ContentGen(generation),
        },
    })
}

/// One seat of the roster as the wire carries it.
pub(super) fn seat_info(entry: &RosterEntry) -> SeatInfo {
    let RosterEntry {
        seat,
        name,
        color,
        kind,
        mode,
        present,
        shares_cursor,
    } = entry;
    SeatInfo {
        id: seat.0,
        name: name.clone(),
        color: *color,
        role: (*kind).into(),
        mode: (*mode).into(),
        present: *present,
        shares_cursor: *shares_cursor,
    }
}

/// The sessions crate's form of a seat's cursor choices.
pub(super) fn cursor_options(options: api_types::CursorOptions) -> sessions::CursorOptions {
    sessions::CursorOptions {
        show: options.show,
        share: options.share,
    }
}

/// Keeps every fraction inside `[0, 1]`; `None` when a name is too long to broadcast to every seat.
pub(super) fn clamped(at: CursorPoint) -> Option<CursorPoint> {
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

/// Allows `rate` takes per second, with a burst of one second's worth.
pub(super) struct Bucket {
    rate: f64,
    tokens: f64,
    at: Instant,
}

impl Bucket {
    /// A full bucket.
    pub(super) fn new(rate: u32) -> Self {
        Self {
            rate: f64::from(rate),
            tokens: f64::from(rate),
            at: Instant::now(),
        }
    }

    /// Takes one token; false when none is left.
    pub(super) fn take(&mut self) -> bool {
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
