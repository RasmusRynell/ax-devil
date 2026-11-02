//! Between the Session socket's wire types and the sessions crate: what a received command asks for, and the cursor
//! and token-bucket limits on what a seat may send.

use crate::routes::playback::policy_from_wire;
use api_types::{
    CURSOR_NAME_MAX, CursorElement, CursorPoint, SessionCommand,
    workspace::{HiddenTarget, NewItem},
};
use handlers::Handlers;
use sessions::session::{Command, Refusal, Scope};
use std::path::Path;
use tokio::time::Instant;
use workspace::{WorkspaceItem, intake::NewOverlay};
use workspace_store::{DocCommand, HiddenRef};

/// The scope a wire command needs, known without looking at what it names, so the seat is authorized before the
/// command's content is checked and a refusal never tells a seat whether a path or resolver exists. `None` for a
/// ticket request, which the Session authorizes against the Content it names. Mirrors `Command::needs`.
pub(super) fn scope(wire: &SessionCommand) -> Option<Scope> {
    match wire {
        SessionCommand::Content { command } => Some(match command {
            api_types::DocCommand::AddItems { items, .. } => {
                // Every kind of item reads the host's files, to open the file or to resolve the playlist.
                let reads_host = items.iter().any(|item| match item {
                    NewItem::Video { .. } | NewItem::Playlist { .. } => true,
                });
                if reads_host {
                    Scope::Host
                } else {
                    Scope::Content
                }
            }
            api_types::DocCommand::OpenViewer { .. }
            | api_types::DocCommand::CloseViewer { .. }
            | api_types::DocCommand::SetHidden { .. }
            | api_types::DocCommand::SetLaneMatching { .. } => Scope::Content,
        }),
        SessionCommand::Follow => Some(Scope::Follow),
        SessionCommand::Control { .. } => Some(Scope::Control),
        SessionCommand::RequestTicket { .. } => None,
    }
}

/// The command a client asked for. A command whose own content is not valid, such as a path that names no file or a
/// resolver the app does not have, is `Invalid` before it reaches the Session.
pub(super) fn command(wire: SessionCommand, handlers: &Handlers) -> Result<Command, Refusal> {
    Ok(match wire {
        SessionCommand::Content { command } => Command::Content(doc_command(command, handlers)?),
        SessionCommand::Follow => Command::Follow,
        SessionCommand::Control { control } => Command::Control(control),
        SessionCommand::RequestTicket { key } => Command::RequestTicket(key),
    })
}

fn doc_command(wire: api_types::DocCommand, handlers: &Handlers) -> Result<DocCommand, Refusal> {
    Ok(match wire {
        api_types::DocCommand::AddItems { items, open } => {
            // The intake checks every entry first, so a request that has one bad entry adds nothing.
            let items = items
                .iter()
                .map(|new| intake(new, handlers))
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| {
                    tracing::debug!("An item was refused: {}", error.reason);
                    Refusal::Invalid
                })?;
            DocCommand::AddItems { items, open }
        }
        api_types::DocCommand::OpenViewer { item } => DocCommand::OpenViewer(item),
        api_types::DocCommand::CloseViewer { item } => DocCommand::CloseViewer(item),
        api_types::DocCommand::SetHidden { target, hidden } => DocCommand::SetHidden {
            target: hidden_ref(target),
            hidden,
        },
        api_types::DocCommand::SetLaneMatching { item, slot, policy } => {
            DocCommand::SetLaneMatching {
                item,
                slot,
                matching: policy_from_wire(policy).map_err(|reason| {
                    tracing::debug!("A lane matching was refused: {reason}");
                    Refusal::Invalid
                })?,
            }
        }
    })
}

/// The item the intake makes of what the client asked to add: the same rules for every kind and every way in.
fn intake(new: &NewItem, handlers: &Handlers) -> Result<WorkspaceItem, foundation::UserError> {
    match new {
        NewItem::Video { path, overlays } => {
            let overlays: Vec<_> = overlays
                .iter()
                .map(|overlay| NewOverlay {
                    path: overlay.path.clone().into(),
                    handler: overlay.handler.clone(),
                    label: overlay.label.clone(),
                    options: overlay.options.clone(),
                })
                .collect();
            workspace::intake::video(Path::new(path), &overlays, handlers)
        }
        NewItem::Playlist {
            resolver,
            settings,
            label,
        } => workspace::intake::playlist(resolver, settings.clone(), label.clone(), handlers),
    }
}

fn hidden_ref(target: HiddenTarget) -> HiddenRef {
    match target.lane {
        Some(lane) => HiddenRef::Lane(target.entry, lane),
        None => HiddenRef::Entry(target.entry),
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
