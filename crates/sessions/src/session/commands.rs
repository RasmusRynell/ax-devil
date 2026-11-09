//! What a seat can ask for, the scope each ask needs, and what each does to the state.

use super::{
    Access, ContentRef, JobOwner, Lease, Refusal, SeatId, SessionState, Sharing, SharingGen, Use,
    jobs::{Work, spawn},
    state::Doc,
    streams,
    transition::{Effects, Fresh},
};
use crate::JoinLink;
use serde::Deserialize;
use std::sync::Arc;
use ts_rs::TS;
use workspace::{Recipe, Source};
use workspace_store::{DocCommand, WorkspaceState, apply};

/// What a command needs a seat to be allowed to do. [`Command::needs`] decides it for every command.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scope {
    /// Change what a window shows.
    View,
    /// Drop the seat's own view and show the presenter's again.
    Follow,
    /// Change the Doc's content.
    Content,
    /// Read the host's files. The owner needs the files grant.
    Host,
    /// Change who the session is shared with.
    Control,
}

/// The owner's control of sharing.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, TS)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
#[ts(rename = "ControlCommand")]
pub enum Control {
    /// "Start sharing": watchers may join, with `access`.
    Share {
        /// What every watcher may do.
        access: Access,
    },
    /// Change what every watcher may do.
    SetAccess {
        /// What every watcher may do.
        access: Access,
    },
    /// Replace the join link; seats already in stay.
    RegenerateLink,
    /// Stop sharing: every watcher is removed. The Session and its owner continue.
    Stop,
}

/// A change a seat asks for, settled by its own acknowledgement. Changes to a seat's view travel the View lane
/// instead.
#[derive(Clone, Debug)]
pub enum Command {
    /// Change the Doc.
    Content(DocCommand),
    /// Go back to following the presenter.
    Follow,
    /// Change sharing.
    Control(Control),
    /// Ask for a ticket to receive a stream.
    RequestTicket(ContentRef),
}

impl Command {
    /// What this command needs. The exhaustive match makes a new command pick its scope.
    pub fn needs(&self) -> Use<'_> {
        match self {
            Self::Content(command) => Use::Command(content_scope(command)),
            Self::Follow => Use::Command(Scope::Follow),
            Self::Control(_) => Use::Command(Scope::Control),
            Self::RequestTicket(key) => Use::Stream(key),
        }
    }
}

fn content_scope(command: &DocCommand) -> Scope {
    match command {
        DocCommand::AddItems { items, .. } => {
            if items.iter().any(|item| reads_host(&item.recipe)) {
                Scope::Host
            } else {
                Scope::Content
            }
        }
        DocCommand::AddResolved { item, .. } => {
            if reads_host(&item.recipe) {
                Scope::Host
            } else {
                Scope::Content
            }
        }
        DocCommand::EditItem { item, .. } => {
            if reads_host(&item.recipe) {
                Scope::Host
            } else {
                Scope::Content
            }
        }
        DocCommand::Replace { .. } | DocCommand::MarkSaved { .. } => Scope::Host,
        DocCommand::OpenViewer(_)
        | DocCommand::CloseViewer(_)
        | DocCommand::Resolve(_)
        | DocCommand::SourceChanged(_)
        | DocCommand::Remove(_)
        | DocCommand::RenameItem { .. }
        | DocCommand::SetHidden { .. }
        | DocCommand::SetLaneMatching { .. }
        | DocCommand::RenameWorkspace { .. }
        | DocCommand::Bookmarks(_)
        | DocCommand::Keep => Scope::Content,
    }
}

/// Whether resolving the recipe reads the host's files.
fn reads_host(recipe: &Recipe) -> bool {
    match recipe.source {
        Source::Video(_) | Source::Playlist(_) => true,
        Source::Live(_) | Source::Unreadable(_) => false,
    }
}

/// Applies `command`, already authorized for `seat` at `lease`.
pub(super) fn run(
    next: &mut SessionState,
    fx: &mut Effects,
    seat: SeatId,
    lease: Lease,
    command: Command,
    fresh: &Fresh,
) -> Result<(), Refusal> {
    match command {
        Command::Content(command) => {
            content(next, fx, command);
            Ok(())
        }
        Command::Follow => {
            follow(next, seat);
            Ok(())
        }
        Command::Control(control) => self::control(next, control, fresh),
        Command::RequestTicket(key) => streams::request(next, seat, lease, key, fresh),
    }
}

/// Replaces the workspace with `workspace` and counts the change; nothing changes when it is equal.
pub(super) fn set_workspace(next: &mut SessionState, workspace: WorkspaceState) {
    if workspace != next.doc.workspace {
        next.doc = Arc::new(Doc { workspace });
        next.doc_rev += 1;
    }
}

pub(super) fn content(next: &mut SessionState, fx: &mut Effects, command: DocCommand) {
    let (workspace, jobs) = apply(&next.doc.workspace, command);
    set_workspace(next, workspace);
    for job in jobs {
        let owner = JobOwner::Doc {
            item: job.item,
            attempt: job.attempt,
        };
        spawn(next, fx, owner, Work::Resolve(job));
    }
}

/// Back to the presenter's view. Authorized only for a seat that follows or explores, so there is nothing to refuse.
fn follow(next: &mut SessionState, seat: SeatId) {
    if let Some(seat) = next.seats.get_mut(&seat) {
        seat.view = None;
    }
}

fn control(next: &mut SessionState, control: Control, fresh: &Fresh) -> Result<(), Refusal> {
    match control {
        Control::Share { access } => {
            if next.sharing.is_some() {
                return Err(Refusal::AlreadySharing);
            }
            next.next_sharing += 1;
            next.sharing = Some(Sharing {
                generation: SharingGen(next.next_sharing),
                access,
                link: JoinLink {
                    session: next.id,
                    secret: fresh.secret.clone(),
                },
            });
        }
        Control::SetAccess { access } => {
            next.sharing.as_mut().ok_or(Refusal::NotSharing)?.access = access;
            let presenter = Arc::clone(&next.presenter);
            let watchers = next
                .seats
                .values_mut()
                .filter(|seat| seat.kind.sharing().is_some());
            for seat in watchers {
                seat.view = match access {
                    // Watchers only follow.
                    Access::Watch => None,
                    Access::Explore => seat.view.take(),
                    Access::Edit => seat.view.take().or_else(|| Some(Arc::clone(&presenter))),
                };
            }
        }
        Control::RegenerateLink => {
            let sharing = next.sharing.as_mut().ok_or(Refusal::NotSharing)?;
            sharing.link.secret = fresh.secret.clone();
        }
        Control::Stop => {
            next.sharing.take().ok_or(Refusal::NotSharing)?;
            next.seats.retain(|_, seat| seat.kind.sharing().is_none());
        }
    }
    Ok(())
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<Control>();
}
