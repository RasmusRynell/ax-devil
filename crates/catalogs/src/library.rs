//! The library of catalogs: what the picker lists and the document each id names. The built-ins never change; the
//! user's folder is polled and its files come and go.

use std::{
    collections::BTreeMap,
    path::PathBuf,
    sync::{
        Arc, Mutex, MutexGuard, PoisonError,
        atomic::{AtomicU32, Ordering},
    },
};

use serde::Deserialize;
use tokio::sync::watch;

use crate::{
    CatalogId,
    built_in::DOCUMENTS,
    folder::{Change, Content, Folder},
};

/// What the revision of every built-in catalog is; the user's files count up from 2.
const BUILT_IN_REVISION: u32 = 1;
/// The next revision of a user catalog. One counter for the whole process, so a file that is replaced or deleted and
/// written again never gets a revision a viewer already holds.
static NEXT_REVISION: AtomicU32 = AtomicU32::new(BUILT_IN_REVISION + 1);

/// What the catalog picker shows about a catalog.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CatalogInfo {
    /// Which catalog.
    pub id: CatalogId,
    /// What the user reads.
    pub name: String,
    /// What the catalog is for.
    pub description: String,
}

/// What [`Library::document`] has for an id.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Served {
    /// The catalog's text, as of `revision`.
    Document {
        /// Counts the catalog's changes; a later revision is never lower than an earlier one.
        revision: u32,
        /// The document, not yet judged: the frontend's compiler does that.
        text: Arc<str>,
    },
    /// The catalog exists but cannot be served: too large, or not text.
    Unreadable {
        /// As for [`Served::Document`].
        revision: u32,
        /// In words for the user.
        problem: String,
    },
    /// There is no such catalog. `revision` is 0 for one that never existed here, else the revision it was deleted at.
    Missing {
        /// The deletion's revision, or 0.
        revision: u32,
    },
}

impl Served {
    /// The revision, whichever of the three this is.
    pub fn revision(&self) -> u32 {
        match self {
            Self::Document { revision, .. }
            | Self::Unreadable { revision, .. }
            | Self::Missing { revision } => *revision,
        }
    }
}

/// What a catalog document says about itself.
#[derive(Deserialize)]
struct Described {
    name: Option<String>,
    description: Option<String>,
}

/// A built-in catalog.
struct BuiltIn {
    info: CatalogInfo,
    text: Arc<str>,
}

/// A file of the user's folder as last read.
struct Listed {
    revision: u32,
    content: Content,
    info: CatalogInfo,
}

/// What the user's folder holds now.
#[derive(Default)]
struct User {
    files: BTreeMap<String, Listed>,
    /// The revision each recently deleted file was deleted at, so a viewer that asks about it is told it is gone.
    gone: BTreeMap<String, u32>,
}

struct Shared {
    built_in: Vec<BuiltIn>,
    /// Only the polls touch the folder, one at a time; nothing that serves a request waits for the disk.
    folder: Option<Mutex<Folder>>,
    user: Mutex<User>,
    /// Counts the changes to what is listed or served.
    generation: watch::Sender<u64>,
}

/// The catalogs available. Cheap to clone; clones share what they hold.
#[derive(Clone)]
pub struct Library {
    shared: Arc<Shared>,
}

impl Library {
    /// The library of the catalogs built into the app, and no folder.
    pub fn built_in() -> Self {
        Self::new(None)
    }

    /// The built-in catalogs and the catalog files in `folder`, which is read now, once, and by [`rescan`](Self::rescan)
    /// after that. The folder need not exist yet. Blocks while it reads the files, at most
    /// [`MAX_USER_CATALOGS`](crate::MAX_USER_CATALOGS) of at most [`MAX_CATALOG_BYTES`](crate::MAX_CATALOG_BYTES).
    pub fn with_folder(folder: PathBuf) -> Self {
        let library = Self::new(Some(Folder::new(folder)));
        library.poll(true);
        library
    }

    fn new(folder: Option<Folder>) -> Self {
        let built_in = DOCUMENTS
            .iter()
            .map(|(name, document)| {
                let id = CatalogId::BuiltIn(name);
                let info = match serde_json::from_str::<Described>(document) {
                    Ok(Described {
                        name: Some(name),
                        description: Some(description),
                    }) => CatalogInfo {
                        id,
                        name,
                        description,
                    },
                    _ => CatalogInfo {
                        name: (*name).to_owned(),
                        description: "The catalog does not describe itself".into(),
                        id,
                    },
                };
                BuiltIn {
                    info,
                    text: (*document).into(),
                }
            })
            .collect();
        Self {
            shared: Arc::new(Shared {
                built_in,
                folder: folder.map(Mutex::new),
                user: Mutex::default(),
                generation: watch::channel(0).0,
            }),
        }
    }

    /// The catalogs the playback socket serves: the built-ins, Standard first, then the user's files by file name.
    /// Named by what their documents say, or by the file name when a document does not say.
    pub fn documents(&self) -> Vec<CatalogInfo> {
        let user = lock(&self.shared.user);
        self.shared
            .built_in
            .iter()
            .map(|built_in| built_in.info.clone())
            .chain(user.files.values().map(|listed| listed.info.clone()))
            .collect()
    }

    /// What the catalog `id` names is now.
    pub fn document(&self, id: &CatalogId) -> Served {
        match id {
            CatalogId::BuiltIn(name) => self
                .shared
                .built_in
                .iter()
                .find(|built_in| built_in.info.id == CatalogId::BuiltIn(name))
                .map_or(Served::Missing { revision: 0 }, |built_in| {
                    Served::Document {
                        revision: BUILT_IN_REVISION,
                        text: Arc::clone(&built_in.text),
                    }
                }),
            CatalogId::User(file) => {
                let user = lock(&self.shared.user);
                match user.files.get(file) {
                    Some(Listed {
                        revision,
                        content: Content::Text(text),
                        ..
                    }) => Served::Document {
                        revision: *revision,
                        text: Arc::clone(text),
                    },
                    Some(Listed {
                        revision,
                        content: Content::Problem(problem),
                        ..
                    }) => Served::Unreadable {
                        revision: *revision,
                        problem: problem.clone(),
                    },
                    None => Served::Missing {
                        revision: user.gone.get(file).copied().unwrap_or(0),
                    },
                }
            }
        }
    }

    /// The catalog a new lane starts with: Standard.
    pub fn default_id(&self) -> CatalogId {
        CatalogId::BuiltIn(DOCUMENTS[0].0)
    }

    /// Wakes whenever what is listed or served changed; the value only counts the changes.
    pub fn changes(&self) -> watch::Receiver<u64> {
        self.shared.generation.subscribe()
    }

    /// One poll of the user's folder; true when something listed or served changed. A file that changed is read only
    /// after it looked the same on two polls in a row, and one that vanished is gone only after it was missing on two.
    /// Blocks on the disk.
    pub fn rescan(&self) -> bool {
        self.poll(false)
    }

    fn poll(&self, first: bool) -> bool {
        let Some(folder) = &self.shared.folder else {
            return false;
        };
        // Held until the changes are listed, so two polls never list theirs out of order.
        let mut folder = lock(folder);
        let changes = folder.poll(first);
        let mut user = lock(&self.shared.user);
        let mut changed = false;
        for change in changes {
            changed |= match change {
                Change::Set(file, content) => user.set(file, content),
                Change::Remove(file) => user.remove(&file),
            };
        }
        drop(user);
        if changed {
            self.shared.generation.send_modify(|count| *count += 1);
        }
        changed
    }
}

impl User {
    /// Lists `file` as holding `content`, under a new revision unless that is what it held; whether it changed.
    fn set(&mut self, file: String, content: Content) -> bool {
        if self
            .files
            .get(&file)
            .is_some_and(|listed| listed.content == content)
        {
            return false;
        }
        let info = describe(&file, &content);
        self.gone.remove(&file);
        self.files.insert(
            file,
            Listed {
                revision: next_revision(),
                content,
                info,
            },
        );
        true
    }

    /// Drops `file`, remembering its deletion; whether it was listed.
    fn remove(&mut self, file: &str) -> bool {
        if self.files.remove(file).is_none() {
            return false;
        }
        self.gone.insert(file.to_owned(), next_revision());
        if self.gone.len() > crate::MAX_USER_CATALOGS
            && let Some(oldest) = self
                .gone
                .iter()
                .min_by_key(|(_, revision)| **revision)
                .map(|(file, _)| file.clone())
        {
            self.gone.remove(&oldest);
        }
        true
    }
}

fn next_revision() -> u32 {
    NEXT_REVISION.fetch_add(1, Ordering::Relaxed)
}

/// What the picker shows of a user's file: the document's own name and description, else the file name and the
/// reason.
fn describe(file: &str, content: &Content) -> CatalogInfo {
    let id = CatalogId::User(file.to_owned());
    let (name, description) = match content {
        Content::Text(text) => match serde_json::from_str::<Described>(text) {
            Ok(Described { name, description }) => (
                name.filter(|name| !name.trim().is_empty()),
                description.unwrap_or_default(),
            ),
            Err(error) => (None, format!("Not a catalog: {error}")),
        },
        Content::Problem(problem) => (None, problem.clone()),
    };
    CatalogInfo {
        id,
        name: name.unwrap_or_else(|| file.to_owned()),
        description,
    }
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

#[cfg(test)]
mod tests;
