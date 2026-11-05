//! Between the Session socket's wire types and the sessions crate: what a received command asks for, and the cursor
//! and token-bucket limits on what a seat may send.

use crate::network::Network;
use crate::routes::playback::policy_from_wire;
use crate::routes::resolve::Staged;
use api_types::{
    CURSOR_NAME_MAX, CursorElement, CursorPoint, SessionCommand, WorkspaceCommand,
    live::LiveProtocol,
    workspace::{HiddenTarget, NewItem},
};
use foundation::{ErrorCode, ItemId};
use handlers::Handlers;
use sessions::session::{Command, Refusal, Scope};
use std::path::Path;
use tokio::time::Instant;
use workspace::{
    LiveRecipe, Secret, Source, Workspace, WorkspaceItem,
    intake::{LiveDefaults, NewLive, NewOverlay},
};
use workspace_store::{DocCommand, HiddenRef};

/// The scope a wire command needs, known without looking at what it names, so the seat is authorized before the
/// command's content is checked and a refusal never tells a seat whether a path or resolver exists. `None` for a
/// ticket request, which the Session authorizes against the Content it names. Mirrors `Command::needs`.
pub(super) fn scope(wire: &SessionCommand) -> Option<Scope> {
    match wire {
        SessionCommand::Content { command } => Some(match command {
            api_types::DocCommand::AddItems { items, .. } => {
                // A video reads the host's files; a live stream is only a recipe until its viewer opens.
                let reads_host = items.iter().any(|item| match item {
                    NewItem::Video { .. } => true,
                    NewItem::Live(_) => false,
                });
                // A stream of a scanned camera carries the login the scan signed in with, which only the owner has.
                let found = items
                    .iter()
                    .any(|item| matches!(item, NewItem::Live(new) if new.found.is_some()));
                if reads_host {
                    Scope::Host
                } else if found {
                    Scope::Control
                } else {
                    Scope::Content
                }
            }
            api_types::DocCommand::AddResolved { .. } => Scope::Host,
            api_types::DocCommand::OpenViewer { .. }
            | api_types::DocCommand::CloseViewer { .. }
            | api_types::DocCommand::Resolve { .. }
            | api_types::DocCommand::Remove { .. }
            | api_types::DocCommand::SetHidden { .. }
            | api_types::DocCommand::SetLaneMatching { .. } => Scope::Content,
        }),
        SessionCommand::Follow => Some(Scope::Follow),
        SessionCommand::Control { .. } => Some(Scope::Control),
        SessionCommand::RequestTicket { .. } => None,
        // New replaces the workspace, which only a seat with the files grant may.
        SessionCommand::Workspace { command } => Some(match command {
            WorkspaceCommand::Keep => Scope::Content,
            WorkspaceCommand::New
            | WorkspaceCommand::Open { .. }
            | WorkspaceCommand::Save { .. }
            | WorkspaceCommand::Discard => Scope::Host,
        }),
    }
}

/// What a valid command asks the backend to do.
pub(super) enum Request {
    /// A change to the session.
    Session(Command),
    /// A workspace file command, which the adapter runs itself.
    Workspace(WorkspaceCommand),
}

/// The request a client made. A command whose own content is not valid, such as a path that names no file or a
/// resolver the app does not have, is `Invalid` before it reaches the Session.
pub(super) fn command(
    wire: SessionCommand,
    handlers: &Handlers,
    staged: &Staged,
    session: &str,
    live: &LiveDefaults,
    network: &Network,
    saved: Saved<'_>,
) -> Result<Request, Refusal> {
    Ok(match wire {
        SessionCommand::Content { command } => Request::Session(Command::Content(doc_command(
            command, handlers, staged, session, live, network, saved,
        )?)),
        SessionCommand::Follow => Request::Session(Command::Follow),
        SessionCommand::Control { control } => Request::Session(Command::Control(control)),
        SessionCommand::RequestTicket { key } => Request::Session(Command::RequestTicket(key)),
        SessionCommand::Workspace { command } => Request::Workspace(workspace_command(command)?),
    })
}

/// Paths are absolute, and Save As names a workspace file.
fn workspace_command(wire: WorkspaceCommand) -> Result<WorkspaceCommand, Refusal> {
    let valid = match &wire {
        WorkspaceCommand::Open { path } => Path::new(path).is_absolute(),
        WorkspaceCommand::Save {
            path: Some(path), ..
        } => Path::new(path).is_absolute() && path.ends_with(workspace_files::SUFFIX),
        WorkspaceCommand::New
        | WorkspaceCommand::Save { path: None, .. }
        | WorkspaceCommand::Keep
        | WorkspaceCommand::Discard => true,
    };
    if valid {
        Ok(wire)
    } else {
        Err(Refusal::Invalid)
    }
}

fn doc_command(
    wire: api_types::DocCommand,
    handlers: &Handlers,
    staged: &Staged,
    session: &str,
    live: &LiveDefaults,
    network: &Network,
    saved: Saved<'_>,
) -> Result<DocCommand, Refusal> {
    Ok(match wire {
        api_types::DocCommand::AddItems { items, open } => {
            // The intake checks every entry first, so a request that has one bad entry adds nothing.
            let items = items
                .iter()
                .map(|new| intake(new, handlers, live, network, saved))
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| {
                    tracing::debug!("An item was refused: {}", error.reason);
                    Refusal::Invalid
                })?;
            DocCommand::AddItems { items, open }
        }
        // The scope was checked before this lookup, so a seat without the grant cannot use up a staged item.
        api_types::DocCommand::AddResolved { item, label } => {
            let (item, content) = staged.take(session, &item).ok_or(Refusal::Invalid)?;
            DocCommand::AddResolved {
                item: Box::new(workspace::intake::with_label(item, label.as_deref())),
                content: Box::new(content),
                open: true,
            }
        }
        api_types::DocCommand::OpenViewer { item } => DocCommand::OpenViewer(item),
        api_types::DocCommand::CloseViewer { item } => DocCommand::CloseViewer(item),
        api_types::DocCommand::Resolve { item } => DocCommand::Resolve(item),
        api_types::DocCommand::Remove { item } => DocCommand::Remove(item),
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
fn intake(
    new: &NewItem,
    handlers: &Handlers,
    live: &LiveDefaults,
    network: &Network,
    saved: Saved<'_>,
) -> Result<WorkspaceItem, foundation::UserError> {
    match new {
        NewItem::Video {
            path,
            label,
            overlays,
        } => {
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
                .map(|item| workspace::intake::with_label(item, label.as_deref()))
        }
        NewItem::Live(new) => {
            let mut new = new.clone();
            let found = new.found.take();
            let like = new.like.take();
            let mut new = NewLive::from(*new);
            if let Some(address) = found {
                scanned(&mut new, &address, network, live)?;
            } else if let Some(recipe) = like.and_then(|item| saved.live(item)) {
                new.copy_login(recipe, live, workspace::intake::LoginUse::Stream)?;
            }
            workspace::intake::live(new, live, handlers)
        }
    }
}

/// The logins saved with the workspace's streams, for the seat that may copy them.
#[derive(Clone, Copy)]
pub(super) struct Saved<'a> {
    workspace: &'a Workspace,
    /// Whether the seat is the session's owner; any other copies no login.
    owner: bool,
}

impl<'a> Saved<'a> {
    pub(super) fn new(workspace: &'a Workspace, owner: bool) -> Self {
        Self { workspace, owner }
    }

    /// The recipe of the live stream `item`, when the seat may copy its login.
    fn live(self, item: ItemId) -> Option<&'a LiveRecipe> {
        if !self.owner {
            return None;
        }
        self.workspace
            .items
            .iter()
            .find(|candidate| candidate.id == item)
            .and_then(|found| match &found.recipe.source {
                Source::Live(recipe) => Some(recipe),
                _ => None,
            })
    }
}

/// Points `new` at the camera the scan signed in to at `address`, with the login that worked, unless `new` names a
/// stream URL on another server. The password is written into the recipe here and nowhere else: it is neither logged
/// nor named in the refusal.
fn scanned(
    new: &mut NewLive,
    address: &str,
    network: &Network,
    defaults: &LiveDefaults,
) -> Result<(), foundation::UserError> {
    let Some((row, login)) = network.signed_in(address) else {
        return Err(foundation::UserError::new(
            ErrorCode::WorkspaceInvalid,
            "The scan has not signed in to that camera",
        ));
    };
    let standard = match row.scheme {
        LiveProtocol::Http => 80,
        LiveProtocol::Https => 443,
    };
    new.host = Some(row.address);
    new.protocol = Some(row.scheme.into());
    new.port = Some(if row.port == standard {
        0
    } else {
        row.port.into()
    });
    // The login goes where the stream's RTSP goes, so a stream URL elsewhere than the camera gets none of it.
    new.check_login_destination(&new.camera_endpoint(defaults), defaults)?;
    new.username = Some(login.user);
    new.password = Some(Secret::new(login.password.expose()));
    Ok(())
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
