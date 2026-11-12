//! Configuration ownership without publishing filesystem paths.
use crate::Settings;
use sha2::{Digest, Sha256};
use std::{ffi::OsString, io, path::PathBuf};

impl Settings {
    /// SHA-256 (hex) of the file saves replace: canonicalize the nearest existing ancestor and append the missing
    /// parent components and final name unchanged, without following a final symlink. Unusable paths have no identity.
    pub fn identity(&self) -> Option<String> {
        let absolute = std::path::absolute(self.path()).ok()?;
        let mut ancestor = absolute.parent()?.to_owned();
        let mut remaining: Vec<OsString> = vec![absolute.file_name()?.to_owned()];
        let mut canonical: PathBuf = loop {
            match std::fs::canonicalize(&ancestor) {
                Ok(path) if path.is_dir() => break path,
                Ok(_) => return None,
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    // A dangling parent symlink cannot currently be used for saves.
                    if std::fs::symlink_metadata(&ancestor)
                        .is_ok_and(|m| m.file_type().is_symlink())
                    {
                        return None;
                    }
                    remaining.push(ancestor.components().next_back()?.as_os_str().to_owned());
                    ancestor = ancestor.parent()?.to_owned();
                }
                Err(_) => return None,
            }
        };
        for component in remaining.into_iter().rev() {
            canonical.push(component);
        }
        Some(
            Sha256::digest(canonical.as_os_str().as_encoded_bytes())
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect(),
        )
    }
}
