//! The in-memory workspace store: items resolve in the background and subscribers see each new state.
//!
//! One store holds the one workspace. Every change replaces the whole [`StoreState`], and subscribers always see
//! the latest one, so a slow subscriber skips intermediate states instead of queueing them.
//!
//! TODO(M3): saving, recovery copies and the row projection come behind this same interface.

use foundation::UserError;
use std::{
    collections::{HashMap, HashSet},
    sync::Arc,
};
use tokio::{runtime::Handle, sync::watch};
use workspace::{Content, ItemId, Recipe, Workspace, WorkspaceItem};

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
}

/// The workspace store. Cloning gives another handle to the same store.
#[derive(Clone)]
pub struct Store {
    state: Arc<watch::Sender<Arc<StoreState>>>,
    runtime: Handle,
}

impl Store {
    /// An empty, Untitled workspace. Items resolve on `runtime`.
    pub fn new(runtime: Handle) -> Self {
        Self {
            state: Arc::new(watch::Sender::new(Arc::new(StoreState::default()))),
            runtime,
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
            self.runtime.spawn(async move {
                // Resolving reads the file system, which must not block the runtime's workers.
                let resolution =
                    match tokio::task::spawn_blocking(move || recipe.resolve(&id)).await {
                        Ok(Ok(content)) => Resolution::Resolved(content),
                        Ok(Err(error)) => Resolution::Failed(error),
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
        workspace::intake::video(path).unwrap()
    }

    #[tokio::test]
    async fn an_added_item_is_listed_pending_and_a_subscriber_then_sees_it_resolved() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let store = Store::new(Handle::current());
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
        let store = Store::new(Handle::current());
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
}
