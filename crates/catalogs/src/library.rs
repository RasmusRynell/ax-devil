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

use foundation::{ErrorCode, UserError};
use serde::Deserialize;
use tokio::sync::watch;

use crate::{
    CatalogId, MAX_USER_CATALOGS,
    built_in::DOCUMENTS,
    copy::{copy_of, slug, write_new},
    folder::{Change, Content, Folder, ODD_NAME},
};

/// What the revision of every built-in catalog is; the user's files count up from 2.
const BUILT_IN_REVISION: u32 = 1;
/// The next revision of a user catalog. One counter for the whole process, so a file that is replaced or deleted and
/// written again never gets a revision a viewer already holds.
static NEXT_REVISION: AtomicU32 = AtomicU32::new(BUILT_IN_REVISION + 1);

/// `name` as Python's `str.casefold` returns it, the key catalogs are ordered by (Python compares code points, as
/// `String` does). Full case folding is the uppercase mapping followed by the lowercase one for every character
/// (`ß` and `ſ` become `ss` and `s`, `ς` becomes `σ`) except three that the Unicode `CaseFolding.txt` treats apart:
/// dotless `ı` and Cherokee fold to themselves or to uppercase, and capital `ẞ` folds to `ss`.
fn casefold(name: &str) -> String {
    name.chars()
        .flat_map(|c| match c {
            '\u{131}' => vec![c],
            '\u{1E9E}' => vec!['s', 's'],
            // Cherokee folds to uppercase, so that the letters of its two cases meet.
            '\u{13A0}'..='\u{13FD}' | '\u{AB70}'..='\u{ABBF}' => c.to_uppercase().collect(),
            _ => c.to_uppercase().flat_map(char::to_lowercase).collect(),
        })
        .collect()
}

/// What the catalog picker shows about a catalog.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CatalogInfo {
    /// Which catalog.
    pub id: CatalogId,
    /// What the user reads.
    pub name: String,
    /// What the catalog is for; empty when the document does not say.
    pub description: String,
    /// Why the catalog is not usable as a catalog, when it is not: unreadable, not JSON, no name, or a file name to
    /// change. `None` for a valid one and for every built-in.
    pub problem: Option<String>,
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
    /// Only the polls, copies and deletes touch the folder, one at a time; nothing that serves a request waits for the
    /// disk.
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
                        problem: None,
                    },
                    _ => CatalogInfo {
                        name: (*name).to_owned(),
                        description: "The catalog does not describe itself".into(),
                        id,
                        problem: None,
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

    /// The catalogs the playback socket serves: the built-ins, Standard first; then the user's valid files by name,
    /// case-folded as Python's `str.casefold`, then by file name; then the invalid ones (`problem` set) by file name. A file is named by what
    /// its document says, or by its file name when the document does not.
    pub fn documents(&self) -> Vec<CatalogInfo> {
        let user = lock(&self.shared.user);
        // `files` is by file name and the sort is stable, so a file name breaks a tie.
        let mut files: Vec<&CatalogInfo> = user.files.values().map(|listed| &listed.info).collect();
        files.sort_by_cached_key(|info| {
            (
                info.problem.is_some(),
                info.problem.is_none().then(|| casefold(&info.name)),
            )
        });
        self.shared
            .built_in
            .iter()
            .map(|built_in| &built_in.info)
            .chain(files)
            .cloned()
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

    /// Saves a copy of the catalog `from` in the user's folder, named `name`, in a new file that is listed at once; its
    /// id. The file is `<slug of name>.json`, else `-2`, `-3` and on, so no file is ever replaced. A copy of a
    /// built-in records it as the `origin`. Blocks on the disk.
    ///
    /// # Errors
    /// The library has no folder, `from` is not a readable catalog, `name` is blank, the folder holds
    /// [`MAX_USER_CATALOGS`] files already, or the file cannot be written.
    pub fn copy(&self, from: &CatalogId, name: &str) -> Result<CatalogId, UserError> {
        if self.shared.folder.is_none() {
            return Err(no_folder());
        }
        let (source_name, text, origin) = self.source(from)?;
        self.save(&text, &source_name, name, origin.as_deref())
    }

    /// Saves a copy of the catalog document `text`, which does not come from the library (a file elsewhere), as
    /// [`copy`](Self::copy) does; no `origin` is recorded. The copy is based on the document's own `name`.
    ///
    /// # Errors
    /// As for [`copy`](Self::copy), and `text` is not a catalog document with a name and a description.
    pub fn copy_document(&self, text: &str, name: &str) -> Result<CatalogId, UserError> {
        if self.shared.folder.is_none() {
            return Err(no_folder());
        }
        let source_name = serde_json::from_str::<Described>(text)
            .ok()
            .and_then(|described| described.name)
            .unwrap_or_default();
        self.save(text, &source_name, name, None)
    }

    /// Writes the copy of `text` named `name` into a new file of the folder and lists it.
    fn save(
        &self,
        text: &str,
        source_name: &str,
        name: &str,
        origin: Option<&str>,
    ) -> Result<CatalogId, UserError> {
        let Some(folder) = &self.shared.folder else {
            return Err(no_folder());
        };
        let copy = copy_of(text, source_name, name, origin)?;
        let mut folder = lock(folder);
        if lock(&self.shared.user).files.len() >= MAX_USER_CATALOGS {
            return Err(UserError::new(
                ErrorCode::CatalogInvalid,
                format!(
                    "The catalog folder holds {MAX_USER_CATALOGS} catalogs, the most that are listed"
                ),
            ));
        }
        let file = write_new(folder.path(), &slug(name), &copy)?;
        folder.adopt(&file);
        // Listed as written: the next poll reads the same text, which is no change.
        lock(&self.shared.user).set(file.clone(), Content::Text(copy.into()));
        self.shared.generation.send_modify(|count| *count += 1);
        Ok(CatalogId::User(file))
    }

    /// Deletes the user's catalog `id` from the folder; it is listed as missing at once. Blocks on the disk.
    ///
    /// # Errors
    /// `id` is a built-in, is not listed, or is a file whose name cannot be an id (rename it first), or the file
    /// cannot be removed.
    pub fn delete(&self, id: &CatalogId) -> Result<(), UserError> {
        let CatalogId::User(file) = id else {
            return Err(UserError::new(
                ErrorCode::CatalogInvalid,
                "Built-in catalogs cannot be deleted",
            ));
        };
        let Some(folder) = &self.shared.folder else {
            return Err(no_folder());
        };
        let folder = lock(folder);
        match lock(&self.shared.user).files.get(file) {
            None => return Err(not_found()),
            Some(Listed {
                content: Content::Problem(problem),
                ..
            }) if problem == ODD_NAME => {
                return Err(UserError::new(
                    ErrorCode::CatalogInvalid,
                    "The file name has characters a catalog name cannot have; rename the file first",
                ));
            }
            Some(_) => {}
        }
        match std::fs::remove_file(folder.path().join(file)) {
            Err(error) if error.kind() != std::io::ErrorKind::NotFound => {
                return Err(UserError::new(
                    ErrorCode::SaveFailed,
                    format!("Cannot delete the catalog: {error}"),
                ));
            }
            _ => {}
        }
        if lock(&self.shared.user).remove(file) {
            self.shared.generation.send_modify(|count| *count += 1);
        }
        Ok(())
    }

    /// What copying `id` starts from: its name, its text, and the origin to record when it is a built-in.
    fn source(&self, id: &CatalogId) -> Result<(String, Arc<str>, Option<String>), UserError> {
        match id {
            CatalogId::BuiltIn(_) => self
                .shared
                .built_in
                .iter()
                .find(|built_in| built_in.info.id == *id)
                .map(|built_in| {
                    (
                        built_in.info.name.clone(),
                        Arc::clone(&built_in.text),
                        Some(id.to_string()),
                    )
                })
                .ok_or_else(not_found),
            CatalogId::User(file) => match lock(&self.shared.user).files.get(file) {
                Some(Listed {
                    content: Content::Text(text),
                    info,
                    ..
                }) => Ok((info.name.clone(), Arc::clone(text), None)),
                Some(Listed {
                    content: Content::Problem(problem),
                    ..
                }) => Err(UserError::new(
                    ErrorCode::CatalogInvalid,
                    format!("Cannot copy: {problem}"),
                )),
                None => Err(not_found()),
            },
        }
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

fn no_folder() -> UserError {
    UserError::new(ErrorCode::CatalogInvalid, "There is no catalog folder")
}

fn not_found() -> UserError {
    UserError::new(ErrorCode::FileNotFound, "There is no such catalog")
}

/// What the picker shows of a user's file: the document's own name and description, else the file name; and the
/// reason the file is not a usable catalog, if it is not.
fn describe(file: &str, content: &Content) -> CatalogInfo {
    let (name, description, problem) = match content {
        Content::Text(text) => match serde_json::from_str::<Described>(text) {
            Ok(Described {
                name: Some(name),
                description,
            }) if !name.trim().is_empty() => (Some(name), description.unwrap_or_default(), None),
            Ok(Described { description, .. }) => (
                None,
                description.unwrap_or_default(),
                Some("The catalog has no name".to_owned()),
            ),
            Err(error) => (None, String::new(), Some(format!("Not a catalog: {error}"))),
        },
        Content::Problem(problem) => (None, String::new(), Some(problem.clone())),
    };
    CatalogInfo {
        id: CatalogId::User(file.to_owned()),
        name: name.unwrap_or_else(|| file.to_owned()),
        description,
        problem,
    }
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

#[cfg(test)]
mod tests;
