//! The workspace's state and its transitions, as pure functions.
//!
//! The state is an immutable [`WorkspaceState`]. [`apply`] and [`complete`] return the next state, and [`apply`]
//! also the [`ResolveJob`]s the caller must run with [`resolve`]. A job carries the `attempt` its item was waiting on,
//! and [`complete`] drops a result whose item is no longer waiting on that attempt, so a slow resolution never
//! overwrites a newer one. Each committed Content gets a [`ContentGen`]: content ids are stable across
//! re-resolution, so anything bound to a Content binds to its id and generation.
//!
//! Whoever holds the state decides where it lives and who may change it: a Session holds one per session, under its
//! own lock (`sessions::session`).
//!
//! TODO(M3): saving, recovery copies and the row projection come behind this same interface.

use foundation::UserError;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use workspace::{Content, ItemId, Recipe, Workspace, WorkspaceItem};

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

/// One immutable snapshot of the workspace and what is known about its items.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct WorkspaceState {
    /// The items, in the order they were added.
    pub workspace: Workspace,
    /// The resolution of every item in the workspace.
    pub resolutions: HashMap<ItemId, Resolution>,
    /// Items with a viewer requested: a viewer should open for each once it resolves. Closing a viewer removes the
    /// item, so a frontend that connects late sees what is requested now.
    pub open: HashSet<ItemId>,
    next_attempt: u64,
    next_generation: u64,
}

/// The parts of a [`WorkspaceState`] a reader may see, each by name.
#[derive(Clone, Copy, Debug)]
pub struct WorkspaceParts<'a> {
    /// The items, in the order they were added.
    pub workspace: &'a Workspace,
    /// The resolution of every item in the workspace.
    pub resolutions: &'a HashMap<ItemId, Resolution>,
    /// Items with a viewer requested.
    pub open: &'a HashSet<ItemId>,
}

impl WorkspaceState {
    /// The state's parts. The destructure names every field, so a new one does not compile until it is decided here
    /// whether readers that destructure [`WorkspaceParts`] (the projection to watchers) get it.
    pub fn parts(&self) -> WorkspaceParts<'_> {
        let Self {
            workspace,
            resolutions,
            open,
            next_attempt: _,
            next_generation: _,
        } = self;
        WorkspaceParts {
            workspace,
            resolutions,
            open,
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
    /// Requests a viewer for an item. An item whose viewer was not requested resolves again, so the viewer gets
    /// fresh Content. Does nothing for an unknown item.
    OpenViewer(ItemId),
    /// Withdraws the viewer request. Does nothing for an unknown item.
    CloseViewer(ItemId),
}

/// Work a transition asks its caller to run: resolve `recipe`, then hand the result to [`complete`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResolveJob {
    /// The item being resolved.
    pub item: ItemId,
    /// The attempt the item waits on; pass it back to [`complete`].
    pub attempt: u64,
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
                match next
                    .workspace
                    .items
                    .iter_mut()
                    .find(|existing| existing.id == item.id)
                {
                    Some(existing) => *existing = item,
                    None => next.workspace.items.push(item),
                }
            }
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

/// Runs a job. Reads the file system, so callers keep it off an async executor's workers.
pub fn resolve(job: &ResolveJob) -> Result<Content, UserError> {
    job.recipe.resolve(&job.item)
}

#[cfg(test)]
mod tests {
    use super::*;
    use foundation::ErrorCode;
    use std::path::Path;

    fn item(path: &Path) -> WorkspaceItem {
        workspace::intake::video(path).unwrap()
    }

    fn add(
        state: &WorkspaceState,
        items: Vec<WorkspaceItem>,
        open: bool,
    ) -> (WorkspaceState, Vec<ResolveJob>) {
        apply(state, DocCommand::AddItems { items, open })
    }

    /// Resolves the job as the store would and commits it.
    fn finish(state: &WorkspaceState, job: &ResolveJob) -> WorkspaceState {
        complete(state, job.item, job.attempt, resolve(job))
    }

    fn content_id(state: &WorkspaceState, id: ItemId) -> workspace::ContentId {
        match &state.resolutions[&id] {
            Resolution::Resolved { content, .. } => content.id().clone(),
            other => panic!("expected a resolved item, got {other:?}"),
        }
    }

    fn generation(state: &WorkspaceState, id: ItemId) -> ContentGen {
        match &state.resolutions[&id] {
            Resolution::Resolved { generation, .. } => *generation,
            other => panic!("expected a resolved item, got {other:?}"),
        }
    }

    #[test]
    fn items_are_listed_in_the_order_added_and_one_that_cannot_resolve_stays_with_its_reason() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let (opened, listed, broken) = (
            item(&file),
            item(&file),
            item(&folder.path().join("missing.mp4")),
        );
        let (opened_id, listed_id, broken_id) = (opened.id, listed.id, broken.id);

        let (state, first) = add(&WorkspaceState::default(), vec![opened], true);
        let (state, second) = add(&state, vec![listed, broken], false);
        let state = first
            .iter()
            .chain(&second)
            .fold(state, |state, job| finish(&state, job));

        let ids: Vec<ItemId> = state.workspace.items.iter().map(|item| item.id).collect();
        assert_eq!(ids, [opened_id, listed_id, broken_id]);
        let Resolution::Failed(error) = &state.resolutions[&broken_id] else {
            panic!(
                "expected a failure, got {:?}",
                state.resolutions[&broken_id]
            );
        };
        assert_eq!(error.code, ErrorCode::FileNotFound);
        assert_eq!(
            state.open,
            HashSet::from([opened_id]),
            "only adding with open requests a viewer"
        );
    }

    #[test]
    fn a_result_is_dropped_unless_its_item_still_waits_on_that_attempt() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let added = item(&file);
        let id = added.id;
        let (state, first) = add(&WorkspaceState::default(), vec![added.clone()], false);
        let (state, second) = add(&state, vec![added], false);

        assert_eq!(
            state.workspace.items.len(),
            1,
            "adding an item again replaces it"
        );
        assert_ne!(
            first[0].attempt, second[0].attempt,
            "and resolves it under a new attempt"
        );
        let stale = finish(&state, &first[0]);
        assert_eq!(
            stale, state,
            "the replaced attempt's result changes nothing"
        );
        let current = finish(&stale, &second[0]);
        assert_eq!(generation(&current, id), ContentGen(1));
        assert_eq!(
            finish(&current, &second[0]),
            current,
            "a result commits once"
        );
    }

    #[test]
    fn closing_and_reopening_a_viewer_resolves_again_under_a_new_generation_with_the_same_content_id()
     {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let added = item(&file);
        let id = added.id;
        let (state, jobs) = add(&WorkspaceState::default(), vec![added], true);
        let opened = finish(&state, &jobs[0]);
        let first = generation(&opened, id);
        let before = content_id(&opened, id);

        let (closed, jobs) = apply(&opened, DocCommand::CloseViewer(id));
        assert!(jobs.is_empty() && !closed.open.contains(&id));
        assert_eq!(generation(&closed, id), first, "closing keeps the Content");
        let (reopened, jobs) = apply(&closed, DocCommand::OpenViewer(id));
        assert!(reopened.open.contains(&id));
        let reopened = finish(&reopened, &jobs[0]);

        assert!(generation(&reopened, id) > first);
        assert_eq!(content_id(&reopened, id), before);
        let (again, jobs) = apply(&reopened, DocCommand::OpenViewer(id));
        assert!(
            jobs.is_empty() && again == reopened,
            "an open viewer stays as it is"
        );
    }
}
