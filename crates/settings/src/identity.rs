//! Configuration ownership without publishing filesystem paths.
use crate::Settings;
use sha2::{Digest, Sha256};
use std::{ffi::OsString, io, path::PathBuf};

impl Settings {
    /// SHA-256 (hex) of the file saves replace: canonicalize the nearest existing ancestor and append the missing
    /// parent components and final name unchanged, without following a final symlink. Unusable paths have no identity.
    /// Windows/macOS fold case: on case-sensitive volumes there, paths differing only in letter case count as one configuration.
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
        let mut input = canonical.as_os_str().as_encoded_bytes().to_vec();
        if cfg!(any(windows, target_os = "macos")) {
            let mut folded = Vec::with_capacity(input.len());
            for chunk in input.utf8_chunks() {
                folded.extend_from_slice(chunk.valid().to_lowercase().as_bytes());
                folded.extend_from_slice(chunk.invalid());
            }
            input = folded;
        }
        Some(
            Sha256::digest(input)
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn configuration_identity_folds_letter_case_only_on_windows_and_macos() {
        let folder = tempfile::tempdir().unwrap();
        let first = Settings::load(&folder.path().join("MiXeD-Å/settings.json"))
            .unwrap()
            .identity()
            .unwrap();
        let second = Settings::load(&folder.path().join("mixed-å/SETTINGS.JSON"))
            .unwrap()
            .identity()
            .unwrap();
        assert_eq!(first == second, cfg!(any(windows, target_os = "macos")));
    }
}
