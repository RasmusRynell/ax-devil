//! Configuration ownership without publishing filesystem paths.
use crate::Settings;
use sha2::{Digest, Sha256};

impl Settings {
    /// SHA-256 (hex) of the file saves replace: canonical parent plus the final name, without following a final
    /// symlink. Missing/unresolvable parents have no identity until they can be canonicalized.
    pub fn identity(&self) -> Option<String> {
        let absolute = std::path::absolute(self.path()).ok()?;
        let parent = std::fs::canonicalize(absolute.parent()?).ok()?;
        let path = parent.join(absolute.file_name()?);
        Some(
            Sha256::digest(path.as_os_str().as_encoded_bytes())
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect(),
        )
    }
}
