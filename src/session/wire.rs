//! Between the Session socket's wire types and the sessions crate: what a received command asks for, and the cursor
//! and token-bucket limits on what a seat may send.

use api_types::{CURSOR_NAME_MAX, CursorElement, CursorPoint, SessionCommand, workspace::NewItem};
use sessions::session::{Command, Refusal};
use std::path::Path;
use tokio::time::Instant;
use workspace_store::DocCommand;

/// The command a client asked for. A command whose own content is not valid, such as a path that names no file, is
/// `Invalid` before it reaches the Session.
pub(super) fn command(wire: SessionCommand) -> Result<Command, Refusal> {
    Ok(match wire {
        SessionCommand::Content { command } => Command::Content(doc_command(command)?),
        SessionCommand::Follow => Command::Follow,
        SessionCommand::Control { control } => Command::Control(control),
        SessionCommand::RequestTicket { key } => Command::RequestTicket(key),
    })
}

fn doc_command(wire: api_types::DocCommand) -> Result<DocCommand, Refusal> {
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
                    Refusal::Invalid
                })?;
            DocCommand::AddItems { items, open }
        }
        api_types::DocCommand::OpenViewer { item } => DocCommand::OpenViewer(item),
        api_types::DocCommand::CloseViewer { item } => DocCommand::CloseViewer(item),
    })
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
