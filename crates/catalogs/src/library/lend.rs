//! Which user catalogs a watcher may be shown. A user catalog is the host's file, so a watcher sees one only while a
//! lane of the host draws with it; the built-ins are always shown.

use super::{CatalogInfo, Library, Served, lock};
use crate::CatalogId;
use std::{collections::BTreeMap, sync::Mutex};

/// How many lends hold each user file.
#[derive(Default)]
pub(super) struct Lent(Mutex<BTreeMap<String, usize>>);

/// A host lane's use of a catalog. While any exists for a user catalog, watchers are shown it; dropping the last one
/// takes it away again.
pub struct Lend {
    library: Library,
    /// The user file; `None` for a built-in, which needs no lend.
    file: Option<String>,
}

impl Library {
    /// Records that a host lane draws with the catalog `id`, until the result is dropped.
    pub fn lend(&self, id: &CatalogId) -> Lend {
        let file = match id {
            CatalogId::User(file) => Some(file.clone()),
            CatalogId::BuiltIn(_) => None,
        };
        if let Some(file) = &file {
            let first = {
                let mut lent = lock(&self.shared.lent.0);
                let count = lent.entry(file.clone()).or_default();
                *count += 1;
                *count == 1
            };
            if first {
                self.shared.generation.send_modify(|count| *count += 1);
            }
        }
        Lend {
            library: self.clone(),
            file,
        }
    }

    /// [`documents`](Self::documents) as a watcher is told them: the built-ins and the user catalogs a host lane uses.
    pub fn shared_documents(&self) -> Vec<CatalogInfo> {
        let mut documents = self.documents();
        documents.retain(|info| self.is_shown(&info.id));
        documents
    }

    /// [`document`](Self::document) as a watcher is served it: a user catalog no host lane uses is missing.
    pub fn shared_document(&self, id: &CatalogId) -> Served {
        if self.is_shown(id) {
            self.document(id)
        } else {
            Served::Missing { revision: 0 }
        }
    }

    fn is_shown(&self, id: &CatalogId) -> bool {
        match id {
            CatalogId::BuiltIn(_) => true,
            CatalogId::User(file) => lock(&self.shared.lent.0).contains_key(file),
        }
    }
}

impl Drop for Lend {
    fn drop(&mut self) {
        let Some(file) = &self.file else { return };
        let last = {
            let mut lent = lock(&self.library.shared.lent.0);
            let Some(count) = lent.get_mut(file) else {
                return;
            };
            *count -= 1;
            let last = *count == 0;
            if last {
                lent.remove(file);
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
