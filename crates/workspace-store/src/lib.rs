//! The in-memory workspace store: items resolve in the background and subscribers see each new state.
//!
//! One store holds the one workspace. Every change replaces the whole [`StoreState`], and subscribers always see
//! the latest one, so a slow subscriber skips intermediate states instead of queueing them.
//!
//! TODO(M3): saving and recovery copies come behind this same interface.

mod rows;

use foundation::{CancelToken, Interrupt, UserError};
use handlers::Handlers;
use std::{
    collections::{HashMap, HashSet},
    sync::Arc,
};
use tokio::{runtime::Handle, sync::watch};
use workspace::{Content, ContentId, Entry, ItemId, Recipe, Workspace, WorkspaceItem};

pub use rows::{Row, RowKind, RowStatus, rows};

/// What resolving an item has produced so far.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Resolution {
    /// Resolving has not finished.
    Pending,
    /// The item's Content, ready for a viewer.
    Resolved(Content),
    /// Why the item has no Content. The item stays in the workspace.
    Failed(UserError),
}

/// One immutable snapshot of the workspace and what is known about its items.
#[derive(Clone, Debug, Default)]
pub struct StoreState {
    /// The items, in the order they were added.
    pub workspace: Workspace,
    /// The resolution of every item in the workspace.
    pub resolutions: HashMap<ItemId, Resolution>,
    /// Items that were added with `open`: a viewer should open for each once it resolves. Never cleared, so a
    /// frontend that connects late still sees what was requested.
    pub open: HashSet<ItemId>,
    /// Entries and lanes the user hid. Session state: not written to the workspace file, and a reference to
    /// something no item resolves to is ignored.
    pub hidden: HashSet<HiddenRef>,
}

/// What the user hid.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum HiddenRef {
    /// An entry, by its content id.
    Entry(ContentId),
    /// A lane of an entry, by the entry's content id and the lane's label.
    Lane(ContentId, String),
}

impl StoreState {
    /// The resolved Content with this id, or `None` when no item resolved to it.
    pub fn content(&self, id: &str) -> Option<&Content> {
        self.resolutions
            .values()
            .find_map(|resolution| match resolution {
                Resolution::Resolved(content) if content.id().as_str() == id => Some(content),
                _ => None,
            })
    }

    /// The entry of any resolved Content with this id.
    pub fn entry(&self, content_id: &str) -> Option<&Entry> {
        self.resolutions
            .values()
            .find_map(|resolution| match resolution {
                Resolution::Resolved(content) => content.entry(content_id),
                _ => None,
            })
    }

    /// Whether the user hid this entry or lane.
    pub fn is_hidden(&self, target: &HiddenRef) -> bool {
        self.hidden.contains(target)
    }
}

/// The workspace store. Cloning gives another handle to the same store.
#[derive(Clone)]
pub struct Store {
    state: Arc<watch::Sender<Arc<StoreState>>>,
    runtime: Handle,
    handlers: Handlers,
}

impl Store {
    /// An empty, Untitled workspace. Items resolve on `runtime`, with `handlers` for playlists and overlays.
    pub fn new(runtime: Handle, handlers: Handlers) -> Self {
        Self {
            state: Arc::new(watch::Sender::new(Arc::new(StoreState::default()))),
            runtime,
            handlers,
        }
    }

    /// The current state.
    pub fn snapshot(&self) -> Arc<StoreState> {
        self.state.borrow().clone()
    }

    /// A receiver that holds the current state and is woken by every change after.
    pub fn subscribe(&self) -> watch::Receiver<Arc<StoreState>> {
        self.state.subscribe()
    }

    /// Appends `items` as one change, all pending, then resolves each in the background. With `open`, a viewer
    /// should open for each item once it resolves; only adding opens viewers.
    pub fn add_items(&self, items: Vec<WorkspaceItem>, open: bool) {
        if items.is_empty() {
            return;
        }
        let recipes: Vec<(ItemId, Recipe)> = items
            .iter()
            .map(|item| (item.id, item.recipe.clone()))
            .collect();
        self.state.send_modify(|state| {
            let state = Arc::make_mut(state);
            for item in items {
                state.resolutions.insert(item.id, Resolution::Pending);
                if open {
                    state.open.insert(item.id);
                }
                state.workspace.items.push(item);
            }
        });
        for (id, recipe) in recipes {
            let state = self.state.clone();
            let handlers = self.handlers.clone();
            self.runtime.spawn(async move {
                // Resolving reads the file system, which must not block the runtime's workers.
                let resolve = move || recipe.resolve(&id, &handlers, &CancelToken::new());
                let resolution = match tokio::task::spawn_blocking(resolve).await {
                    Ok(Ok(content)) => Resolution::Resolved(content),
                    Ok(Err(Interrupt::Failed(error))) => Resolution::Failed(error),
                    Ok(Err(Interrupt::Cancelled)) => Resolution::Failed(UserError::new(
                        "Resolving was cancelled",
                        id.to_string(),
                    )),
                    Err(error) => Resolution::Failed(UserError::new(
                        format!("Resolving stopped unexpectedly: {error}"),
                        id.to_string(),
                    )),
                };
                state.send_modify(|state| {
                    Arc::make_mut(state).resolutions.insert(id, resolution);
                });
            });
        }
    }

    /// Appends `item` already resolved to `content`, in one change, without resolving it again. With `open`, a viewer
    /// should open for it.
    pub fn add_resolved(&self, item: WorkspaceItem, content: Content, open: bool) {
        self.state.send_modify(|state| {
            let state = Arc::make_mut(state);
            state
                .resolutions
                .insert(item.id, Resolution::Resolved(content));
            if open {
                state.open.insert(item.id);
            }
            state.workspace.items.push(item);
        });
    }

    /// Hides or unhides an entry or lane; subscribers are notified once, and only when it changed.
    pub fn set_hidden(&self, target: HiddenRef, hidden: bool) {
        self.state.send_if_modified(|state| {
            if state.hidden.contains(&target) == hidden {
                return false;
            }
            let hidden_refs = &mut Arc::make_mut(state).hidden;
            if hidden {
                hidden_refs.insert(target);
            } else {
                hidden_refs.remove(&target);
            }
            true
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{path::Path, time::Duration};
    use tokio::sync::watch::Receiver;

    async fn settled(receiver: &mut Receiver<Arc<StoreState>>) -> Arc<StoreState> {
        let wait = receiver.wait_for(|state| {
            state
                .resolutions
                .values()
                .all(|resolution| *resolution != Resolution::Pending)
        });
        tokio::time::timeout(Duration::from_secs(10), wait)
            .await
            .unwrap()
            .unwrap()
            .clone()
    }

    fn item(path: &Path) -> WorkspaceItem {
        workspace::intake::video(path, &[], &Handlers::built_in()).unwrap()
    }

    #[tokio::test]
    async fn an_added_item_is_listed_pending_and_a_subscriber_then_sees_it_resolved() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let store = Store::new(Handle::current(), Handlers::built_in());
        let mut receiver = store.subscribe();
        let added = item(&file);
        let id = added.id;

        store.add_items(vec![added], true);

        // The runtime here runs one thread, so nothing resolves until the test waits.
        let pending = receiver.borrow_and_update().clone();
        assert_eq!(pending.workspace.name, None);
        assert_eq!(pending.workspace.items[0].id, id);
        assert_eq!(pending.resolutions[&id], Resolution::Pending);
        assert!(pending.content(&id.to_string()).is_none());

        let resolved = settled(&mut receiver).await;
        let Resolution::Resolved(content) = &resolved.resolutions[&id] else {
            panic!(
                "expected a resolved item, got {:?}",
                resolved.resolutions[&id]
            );
        };
        assert_eq!(resolved.content(content.id().as_str()), Some(content));
        assert_eq!(store.snapshot().resolutions[&id], resolved.resolutions[&id]);
    }

    #[tokio::test]
    async fn an_unreadable_item_stays_listed_with_its_reason_and_only_adding_with_open_requests_a_viewer()
     {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let missing = folder.path().join("missing.mp4");
        let store = Store::new(Handle::current(), Handlers::built_in());
        let (opened, listed, broken) = (item(&file), item(&file), item(&missing));
        let (opened_id, listed_id, broken_id) = (opened.id, listed.id, broken.id);

        store.add_items(vec![opened], true);
        store.add_items(vec![listed, broken], false);
        let state = settled(&mut store.subscribe()).await;

        let ids: Vec<ItemId> = state.workspace.items.iter().map(|item| item.id).collect();
        assert_eq!(ids, [opened_id, listed_id, broken_id], "in the order added");
        let Resolution::Failed(error) = &state.resolutions[&broken_id] else {
            panic!(
                "expected a failure, got {:?}",
                state.resolutions[&broken_id]
            );
        };
        assert_eq!(error.location, missing.display().to_string());
        assert_eq!(state.open, HashSet::from([opened_id]));
    }

    fn resolved(item: &WorkspaceItem) -> Content {
        item.recipe
            .resolve(&item.id, &Handlers::built_in(), &CancelToken::new())
            .unwrap_or_else(|_| panic!("the fixture item resolves"))
    }

    #[tokio::test]
    async fn adding_a_resolved_item_commits_its_content_without_resolving_it_again() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let added = item(&file);
        let content = resolved(&added);
        // A resolution now would fail: the file is gone.
        std::fs::remove_file(&file).unwrap();
        let store = Store::new(Handle::current(), Handlers::built_in());
        let mut receiver = store.subscribe();

        store.add_resolved(added.clone(), content.clone(), true);
        assert!(receiver.has_changed().unwrap(), "one change");
        tokio::time::sleep(Duration::from_millis(50)).await;

        let state = store.snapshot();
        assert_eq!(state.resolutions[&added.id], Resolution::Resolved(content));
        assert_eq!(state.workspace.items.len(), 1);
        assert_eq!(state.workspace.items[0], added);
        assert!(state.open.contains(&added.id));
        receiver.borrow_and_update();
        assert!(
            !receiver.has_changed().unwrap(),
            "nothing resolved afterwards"
        );
    }

    #[tokio::test]
    async fn hiding_notifies_once_per_change_and_a_reference_to_nothing_is_kept_but_ignored() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let added = item(&file);
        let entry = ContentId::of_item(&added.id);
        let store = Store::new(Handle::current(), Handlers::built_in());
        store.add_resolved(added.clone(), resolved(&added), false);
        let mut receiver = store.subscribe();
        receiver.borrow_and_update();
        let mut notified = |target: &HiddenRef, hidden: bool| {
            store.set_hidden(target.clone(), hidden);
            let changed = receiver.has_changed().unwrap();
            receiver.borrow_and_update();
            changed
        };
        let orphan = HiddenRef::Lane(entry.clone(), "gone".into());
        let target = HiddenRef::Entry(entry);

        assert!(notified(&target, true), "hiding notifies");
        assert!(!notified(&target, true), "hiding again changes nothing");
        assert!(store.snapshot().is_hidden(&target));
        assert!(notified(&orphan, true));
        let listed = rows(&store.snapshot());
        assert_eq!(listed.iter().filter(|row| row.hidden).count(), 1);
        assert!(notified(&target, false), "showing notifies");
        assert!(!notified(&target, false));
        assert!(rows(&store.snapshot()).iter().all(|row| !row.hidden));
    }
}
