//! The workspace's state and its transitions, as pure functions.
//!
//! The state is an immutable [`WorkspaceState`]. [`apply`] and [`complete`] return the next state, and [`apply`]
//! also the [`ResolveJob`]s the caller must run with [`resolve`]. A job carries the `attempt` its item was waiting on,
//! and [`complete`] drops a result whose item is no longer waiting on that attempt, so a slow resolution never
//! overwrites a newer one. Each committed Content gets a [`ContentGen`]: content ids are stable across
//! re-resolution, so anything bound to a Content binds to its id and generation.
//!
//! Whoever holds the state decides where it lives and who may change it: a Session holds one per session, under its
//! own lock (`sessions::session`). [`rows`] is the sidebar's projection of a state.
//!
//! The state also knows which file the workspace belongs to and what it is compared with to tell unsaved edits
//! ([`Saved`]); reading and writing files is the caller's business.

mod rows;

use foundation::{CancelToken, HostPath, Interrupt, UserError};
use handlers::Handlers;
use overlay_sync::MatchPolicy;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use workspace::{Content, ContentId, ItemId, Recipe, Source, Workspace, WorkspaceItem};

pub use rows::{Row, RowKind, RowStatus, rows};

/// Names one commit of an item's Content. Later commits have larger values, across all items of a state.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct ContentGen(pub u64);

/// What resolving an item has produced so far.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Resolution {
    /// Resolving has not finished; only the result of this attempt is accepted.
    Pending {
        /// The attempt that is running. Every start of resolving gets a new one.
        attempt: u64,
    },
    /// The item's Content, ready for a viewer.
    Resolved {
        /// The Content.
        content: Content,
        /// The commit that produced it.
        generation: ContentGen,
    },
    /// Why the item has no Content. The item stays in the workspace.
    Failed(UserError),
}

/// What the workspace is compared with to tell unsaved edits.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Saved {
    /// The value last opened or saved; an empty Untitled workspace counts as saved.
    As(Workspace),
    /// Restored from a recovery copy: modified until saved. `offer` while Keep/Discard is offered.
    Recovered {
        /// Whether the user has yet to keep or discard the restored changes.
        offer: bool,
    },
}

impl Default for Saved {
    fn default() -> Self {
        Self::As(Workspace::default())
    }
}

/// One immutable snapshot of the workspace and what is known about its items.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct WorkspaceState {
    /// The items, in the order they were added.
    pub workspace: Workspace,
    /// The file Save writes, as the user chose it: a symlink stays the link.
    pub file: Option<HostPath>,
    /// What the workspace is compared with to tell unsaved edits.
    pub saved: Saved,
    /// Counts [`DocCommand::Replace`]; every viewer closes when it changes.
    pub epoch: u64,
    /// The resolution of every item in the workspace.
    pub resolutions: HashMap<ItemId, Resolution>,
    /// Items with a viewer requested: a viewer should open for each once it resolves. Closing a viewer removes the
    /// item, so a frontend that connects late sees what is requested now.
    pub open: HashSet<ItemId>,
    /// Entries and lanes the user hid. A reference to something no item resolves to is kept but hides nothing.
    pub hidden: HashSet<HiddenRef>,
    next_attempt: u64,
    next_generation: u64,
}

/// What the user hid.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum HiddenRef {
    /// An entry, by its content id.
    Entry(ContentId),
    /// A lane of an entry, by the entry's content id and the lane's label.
    Lane(ContentId, String),
}

/// The parts of a [`WorkspaceState`] a reader may see, each by name.
#[derive(Clone, Copy, Debug)]
pub struct WorkspaceParts<'a> {
    /// The items, in the order they were added.
    pub workspace: &'a Workspace,
    /// The file Save writes.
    pub file: &'a Option<HostPath>,
    /// What the workspace is compared with to tell unsaved edits.
    pub saved: &'a Saved,
    /// Counts replacements of the workspace.
    pub epoch: u64,
    /// The resolution of every item in the workspace.
    pub resolutions: &'a HashMap<ItemId, Resolution>,
    /// Items with a viewer requested.
    pub open: &'a HashSet<ItemId>,
    /// Entries and lanes the user hid.
    pub hidden: &'a HashSet<HiddenRef>,
}

impl WorkspaceState {
    /// The state's parts. The destructure names every field, so a new one does not compile until it is decided here
    /// whether readers that destructure [`WorkspaceParts`] (the projection to watchers) get it.
    pub fn parts(&self) -> WorkspaceParts<'_> {
        let Self {
            workspace,
            file,
            saved,
            epoch,
            resolutions,
            open,
            hidden,
            next_attempt: _,
            next_generation: _,
        } = self;
        WorkspaceParts {
            workspace,
            file,
            saved,
            epoch: *epoch,
            resolutions,
            open,
            hidden,
        }
    }

    /// Whether the workspace differs from what was last opened or saved.
    pub fn modified(&self) -> bool {
        !matches!(&self.saved, Saved::As(saved) if *saved == self.workspace)
    }

    /// Whether the user hid this entry or lane.
    pub fn is_hidden(&self, target: &HiddenRef) -> bool {
        self.hidden.contains(target)
    }

    /// Appends the item, or replaces the one with its id in place.
    fn put(&mut self, item: WorkspaceItem) {
        match self
            .workspace
            .items
            .iter_mut()
            .find(|existing| existing.id == item.id)
        {
            Some(existing) => *existing = item,
            None => self.workspace.items.push(item),
        }
    }

    /// Marks the item pending under a new attempt and returns the job for it.
    fn start(&mut self, item: &WorkspaceItem) -> ResolveJob {
        self.next_attempt += 1;
        let attempt = self.next_attempt;
        self.resolutions
            .insert(item.id, Resolution::Pending { attempt });
        ResolveJob {
            item: item.id,
            attempt,
            name: item.display_name(),
            recipe: item.recipe.clone(),
        }
    }
}

/// A change the workspace accepts.
#[derive(Clone, Debug)]
pub enum DocCommand {
    /// Appends the items, all pending. An item whose id is already in the workspace is replaced in place and
    /// resolves again. With `open`, a viewer should open for each item once it resolves.
    AddItems {
        /// The items to add.
        items: Vec<WorkspaceItem>,
        /// Whether a viewer should open for each.
        open: bool,
    },
    /// Appends an item whose Content is already resolved, and starts no job: the caller resolved it before the item
    /// existed. An item whose id is already in the workspace is replaced in place. With `open`, a viewer should open
    /// for it.
    AddResolved {
        /// The item to add.
        item: WorkspaceItem,
        /// What it resolved to.
        content: Box<Content>,
        /// Whether a viewer should open for it.
        open: bool,
    },
    /// Requests a viewer for an item. An item whose viewer was not requested resolves again, so the viewer gets
    /// fresh Content. Does nothing for an unknown item.
    OpenViewer(ItemId),
    /// Withdraws the viewer request. Does nothing for an unknown item.
    CloseViewer(ItemId),
    /// Hides or shows an entry or lane.
    SetHidden {
        /// What to hide or show.
        target: HiddenRef,
        /// Whether it is hidden.
        hidden: bool,
    },
    /// Sets how a lane matches its file's records to frames, in the item's recipe, so a saved workspace keeps it.
    /// Does nothing for an unknown item or a slot no entry of its resolved Content has, and resolves nothing: the
    /// Content does not depend on it.
    SetLaneMatching {
        /// The item.
        item: ItemId,
        /// The lane's slot, which names it in every entry.
        slot: String,
        /// The lane's matching.
        matching: MatchPolicy,
    },
    /// Makes `workspace` current: every item pending and resolving again, `open` and `hidden` empty, `epoch` + 1.
    /// `next_attempt` and `next_generation` keep counting up, so a [`ContentGen`] is never reused.
    Replace {
        /// The new workspace.
        workspace: Workspace,
        /// The file it belongs to.
        file: Option<HostPath>,
        /// What it is compared with.
        saved: Saved,
    },
    /// `workspace`, the snapshot that was written, is now saved in `file`. Edits made after the snapshot stay
    /// modified.
    MarkSaved {
        /// The snapshot that was written.
        workspace: Workspace,
        /// Where it was written.
        file: HostPath,
    },
    /// Dismisses the Keep/Discard offer of a restored copy; does nothing otherwise.
    Keep,
}

/// Work a transition asks its caller to run: resolve `recipe`, then hand the result to [`complete`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResolveJob {
    /// The item being resolved.
    pub item: ItemId,
    /// The attempt the item waits on; pass it back to [`complete`].
    pub attempt: u64,
    /// The item's name at the time, for a failure that speaks of it.
    pub name: String,
    /// What to resolve.
    pub recipe: Recipe,
}

/// Applies `command`, returning the next state and the jobs to run for it.
pub fn apply(state: &WorkspaceState, command: DocCommand) -> (WorkspaceState, Vec<ResolveJob>) {
    let mut next = state.clone();
    let mut jobs = Vec::new();
    match command {
        DocCommand::AddItems { items, open } => {
            for item in items {
                if open {
                    next.open.insert(item.id);
                }
                jobs.push(next.start(&item));
                next.put(item);
            }
        }
        DocCommand::AddResolved {
            item,
            content,
            open,
        } => {
            if open {
                next.open.insert(item.id);
            }
            next.next_generation += 1;
            next.resolutions.insert(
                item.id,
                Resolution::Resolved {
                    content: *content,
                    generation: ContentGen(next.next_generation),
                },
            );
            next.put(item);
        }
        DocCommand::OpenViewer(id) => {
            let known = next.workspace.items.iter().find(|item| item.id == id);
            if let Some(item) = known.cloned()
                && next.open.insert(id)
                && !matches!(next.resolutions.get(&id), Some(Resolution::Pending { .. }))
            {
                jobs.push(next.start(&item));
            }
        }
        DocCommand::CloseViewer(id) => {
            next.open.remove(&id);
        }
        DocCommand::SetHidden { target, hidden } => {
            if hidden {
                next.hidden.insert(target);
            } else {
                next.hidden.remove(&target);
            }
        }
        DocCommand::Replace {
            workspace,
            file,
            saved,
        } => {
            next.workspace = workspace;
            next.file = file;
            next.saved = saved;
            next.epoch += 1;
            next.open.clear();
            next.hidden.clear();
            next.resolutions.clear();
            for item in next.workspace.items.clone() {
                jobs.push(next.start(&item));
            }
        }
        DocCommand::MarkSaved { workspace, file } => {
            next.saved = Saved::As(workspace);
            next.file = Some(file);
        }
        DocCommand::Keep => {
            if let Saved::Recovered { offer } = &mut next.saved {
                *offer = false;
            }
        }
        DocCommand::SetLaneMatching {
            item,
            slot,
            matching,
        } => {
            // A resolved item knows its slots; one that is resolving or failed has no content to check against, so
            // the user's choice is kept for the next one.
            let allowed = match next.resolutions.get(&item) {
                Some(Resolution::Resolved { content, .. }) => content
                    .entries()
                    .iter()
                    .any(|entry| entry.lane_slots().contains(&slot.as_str())),
                _ => true,
            };
            // An unreadable item is written back exactly as read, so it takes no matching.
            if allowed
                && let Some(existing) = next
                    .workspace
                    .items
                    .iter_mut()
                    .find(|existing| existing.id == item)
                && !matches!(existing.recipe.source, Source::Unreadable(_))
            {
                existing.recipe.lane_matching.insert(slot, matching);
            }
        }
    }
    (next, jobs)
}

/// Commits the result of resolving `item` under `attempt`. A result whose item no longer waits on that attempt is
/// dropped: the returned state equals `state`.
pub fn complete(
    state: &WorkspaceState,
    item: ItemId,
    attempt: u64,
    result: Result<Content, UserError>,
) -> WorkspaceState {
    let mut next = state.clone();
    if next.resolutions.get(&item) != Some(&Resolution::Pending { attempt }) {
        return next;
    }
    let resolution = match result {
        Ok(content) => {
            next.next_generation += 1;
            Resolution::Resolved {
                content,
                generation: ContentGen(next.next_generation),
            }
        }
        Err(error) => Resolution::Failed(error),
    };
    next.resolutions.insert(item, resolution);
    next
}

/// Runs a job with the installed `handlers`. Reads the file system, so callers keep it off an async executor's
/// workers. A cancelled job gives [`Interrupt::Cancelled`], which the caller reports to no one.
pub fn resolve(
    job: &ResolveJob,
    handlers: &Handlers,
    cancel: &CancelToken,
) -> Result<Content, Interrupt<UserError>> {
    job.recipe.resolve(&job.item, &job.name, handlers, cancel)
}

#[cfg(test)]
mod tests;
