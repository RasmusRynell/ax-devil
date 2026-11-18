//! The writes to the user's folder: a copy of a catalog and the deletion of one, each listed at once.

use std::sync::Arc;

use foundation::{ErrorCode, UserError};

use super::{BUILT_IN_REVISION, Described, Library, Listed, lock, no_folder, not_found};
use crate::{
    CatalogId, MAX_USER_CATALOGS,
    copy::{copy_of, slug, write_new},
    folder::{Content, ODD_NAME},
};

impl Library {
    /// Saves a copy of the catalog `from` in the user's folder, named `name`, in a new file that is listed at once; its
    /// id. The file is `<slug of name>.json`, else `-2`, `-3` and on, so no file is ever replaced. A copy of a
    /// built-in records it as the `origin`. Blocks on the disk.
    ///
    /// # Errors
    /// The library has no folder, `from` is not a readable catalog, `name` is blank, the folder holds
    /// [`MAX_USER_CATALOGS`] files already, or the file cannot be written.
    pub fn copy(&self, from: &CatalogId, name: &str) -> Result<CatalogId, UserError> {
        self.copy_expecting(from, None, name)
    }

    /// As [`copy`](Self::copy), for a caller that judged the catalog `from` at `revision` (the frontend compiles it):
    /// a source that has another revision now is refused, so a copy is always made from a document that was judged.
    ///
    /// # Errors
    /// As for [`copy`](Self::copy), and the catalog changed since `revision`.
    pub fn copy_at(
        &self,
        from: &CatalogId,
        revision: u32,
        name: &str,
    ) -> Result<CatalogId, UserError> {
        self.copy_expecting(from, Some(revision), name)
    }

    fn copy_expecting(
        &self,
        from: &CatalogId,
        expected: Option<u32>,
        name: &str,
    ) -> Result<CatalogId, UserError> {
        if self.shared.folder.is_none() {
            return Err(no_folder());
        }
        let (source_name, text, origin) = self.source(from, expected)?;
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

    /// What copying `id` starts from: its name, its text, and the origin to record when it is a built-in. Refused when
    /// `expected` is a revision other than the one the catalog has now.
    fn source(
        &self,
        id: &CatalogId,
        expected: Option<u32>,
    ) -> Result<(String, Arc<str>, Option<String>), UserError> {
        let current = |revision: u32| match expected {
            Some(expected) if expected != revision => Err(UserError::new(
                ErrorCode::CatalogInvalid,
                "The catalog changed; try again",
            )),
            _ => Ok(()),
        };
        match id {
            CatalogId::BuiltIn(_) => self
                .shared
                .built_in
                .iter()
                .find(|built_in| built_in.info.id == *id)
                .ok_or_else(not_found)
                .and_then(|built_in| {
                    current(BUILT_IN_REVISION)?;
                    Ok((
                        built_in.info.name.clone(),
                        Arc::clone(&built_in.text),
                        Some(id.to_string()),
                    ))
                }),
            CatalogId::User(file) => match lock(&self.shared.user).files.get(file) {
                Some(Listed {
                    revision,
                    content: Content::Text(text),
                    info,
                }) => {
                    current(*revision)?;
                    Ok((info.name.clone(), Arc::clone(text), None))
                }
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
}
