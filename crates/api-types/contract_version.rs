//! The API version, shared by `build.rs` (which bakes it into the backend) and `export-types` (which writes it for the
//! desktop shell): the first 16 hex digits of the SHA-256 of the generated TypeScript on disk, each text after its
//! name. Both read the same files, so the backend and the shell agree, and no commit stores the hash for two
//! pull requests to edit.

use sha2::{Digest, Sha256};
use std::path::Path;

/// The version of the TypeScript the frontend is built from under `generated`: every file in `types/` in name order,
/// then `api.ts`, `settings.ts` and `routes.ts`. Line endings do not matter, so a checkout's conversion changes nothing.
pub fn contract_version(generated: &Path) -> std::io::Result<String> {
    let mut stems = Vec::new();
    for entry in std::fs::read_dir(generated.join("types"))? {
        if let Some(stem) = entry?
            .file_name()
            .to_str()
            .and_then(|name| name.strip_suffix(".ts"))
        {
            stems.push(stem.to_owned());
        }
    }
    stems.sort();
    let mut hash = Sha256::new();
    for (name, file) in stems
        .iter()
        .map(|stem| (stem.as_str(), format!("types/{stem}.ts")))
        .chain([
            ("api", "api.ts".to_owned()),
            ("settings", "settings.ts".to_owned()),
            ("routes", "routes.ts".to_owned()),
        ])
    {
        let text = std::fs::read_to_string(generated.join(file))?.replace("\r\n", "\n");
        hash.update(format!("{name}\0{}\0{text}", text.len()));
    }
    Ok(hash
        .finalize()
        .iter()
        .take(8)
        .map(|byte| format!("{byte:02x}"))
        .collect())
}
