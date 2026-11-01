//! Window state: where each window was and how big. Its own file, not part of the settings, and lenient: a window
//! that cannot be restored opens at its default size, so a bad file never blocks startup.

use crate::{Error, file};
use serde_json::{Map, Value};
use std::{fs, path::Path};

/// Every saved window by name. A missing, unreadable or malformed file gives none, with a warning.
pub fn load_windows(path: &Path) -> Map<String, Value> {
    let text = match fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Map::new(),
        Err(error) => {
            tracing::warn!("Window state {} cannot be read: {error}", path.display());
            return Map::new();
        }
    };
    match serde_json::from_str(&text) {
        Ok(Value::Object(windows)) => windows,
        _ => {
            tracing::warn!(
                "Window state {} is not a JSON object; ignoring it",
                path.display()
            );
            Map::new()
        }
    }
}

/// Saves `state` as window `name`, keeping the other windows. Callers save one window at a time.
pub fn save_window(path: &Path, name: &str, state: Value) -> Result<(), Error> {
    let mut windows = load_windows(path);
    windows.insert(name.to_owned(), state);
    let mut text = serde_json::to_string_pretty(&windows)
        .map_err(|error| file::fail(path, error.to_string()))?;
    text.push('\n');
    file::write_atomically(path, text.as_bytes())
        .map_err(|error| file::fail(path, error.to_string()))
}
