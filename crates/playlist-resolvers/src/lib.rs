//! Built-in playlist resolvers: settings turned into a list of videos with their overlay files.
//!
//! Resolvers read folders only; opening the media and decoding the overlays belong to their own crates, and item
//! and content ids are assigned by the host. They implement [`handler_api::PlaylistResolver`]; the registry that
//! finds them by id is `handlers`.

mod folder_pair;
mod mot_challenge;

use std::{fs::DirEntry, fs::FileType, sync::Arc};

use handler_api::PlaylistResolver;

/// Every built-in resolver.
pub fn resolvers() -> Vec<Arc<dyn PlaylistResolver>> {
    vec![
        Arc::new(folder_pair::FolderPairResolver::new()),
        Arc::new(mot_challenge::MotChallengeResolver::new()),
    ]
}

/// The type of a directory entry, following a symlink to what it points at; `None` for a broken link.
pub(crate) fn resolved_file_type(entry: &DirEntry) -> Option<FileType> {
    let file_type = entry.file_type().ok()?;
    if file_type.is_symlink() {
        std::fs::metadata(entry.path())
            .ok()
            .map(|meta| meta.file_type())
    } else {
        Some(file_type)
    }
}
