//! Which user catalogs a watcher may be shown. A user catalog is the host's file, so the watchers of a Session see one
//! only after a host viewer of that Session asked for it, until the viewer lets go; the built-ins are always
//! shown. A scope names the Session.

use super::{CatalogInfo, Library, Served, lock};
use crate::CatalogId;
use std::{collections::BTreeMap, sync::Mutex};

/// How many lends hold each user file of each scope.
#[derive(Default)]
pub(super) struct Lent(Mutex<BTreeMap<(String, String), usize>>);

/// A host viewer's hold on a catalog it asked for. While any exists for a user catalog, the watchers of its scope are
/// shown it; dropping the last one takes it away again.
pub struct Lend {
    library: Library,
    /// The scope and the user file; `None` for a built-in, which needs no lend.
    held: Option<(String, String)>,
}

impl Library {
    /// Records that a host viewer of `scope` asked for the catalog `id`, until the result is dropped.
    pub fn lend(&self, scope: &str, id: &CatalogId) -> Lend {
        let held = match id {
            CatalogId::User(file) => Some((scope.to_owned(), file.clone())),
            CatalogId::BuiltIn(_) => None,
        };
        if let Some(key) = &held {
            let first = {
                let mut lent = lock(&self.shared.lent.0);
                let count = lent.entry(key.clone()).or_default();
                *count += 1;
                *count == 1
            };
            if first {
                self.shared.generation.send_modify(|count| *count += 1);
            }
        }
        Lend {
            library: self.clone(),
            held,
        }
    }

    /// [`documents`](Self::documents) as a watcher of `scope` is told them: the built-ins and the user catalogs a host
    /// viewer of the scope asked for.
    pub fn shared_documents(&self, scope: &str) -> Vec<CatalogInfo> {
        let mut documents = self.documents();
        documents.retain(|info| self.is_shown(scope, &info.id));
        documents
    }

    /// [`document`](Self::document) as a watcher of `scope` is served it: a user catalog no host viewer of the scope
    /// holds is missing.
    pub fn shared_document(&self, scope: &str, id: &CatalogId) -> Served {
        if self.is_shown(scope, id) {
            self.document(id)
        } else {
            Served::Missing { revision: 0 }
        }
    }

    fn is_shown(&self, scope: &str, id: &CatalogId) -> bool {
        match id {
            CatalogId::BuiltIn(_) => true,
            CatalogId::User(file) => {
                lock(&self.shared.lent.0).contains_key(&(scope.to_owned(), file.clone()))
            }
        }
    }
}

impl Drop for Lend {
    fn drop(&mut self) {
        let Some(key) = &self.held else { return };
        let last = {
            let mut lent = lock(&self.library.shared.lent.0);
            let Some(count) = lent.get_mut(key) else {
                return;
            };
            *count -= 1;
            let last = *count == 0;
            if last {
                lent.remove(key);
            }
            last
        };
        if last {
            self.library
                .shared
                .generation
                .send_modify(|count| *count += 1);
        }
    }
}
