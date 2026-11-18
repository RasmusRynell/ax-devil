//! The only conversion of a configured folder to a filesystem path: failures never select another location.
use crate::{Error, Kind, Settings, State, file};
use std::path::{Path, PathBuf};

impl State {
    /// Resolves a folder, choosing `standard` only for a literal empty setting. Unusable references and paths
    /// are errors naming the written value, never its expanded contents. The path need not exist yet.
    pub fn folder(&self, key: &str, standard: &Path) -> Result<PathBuf, Error> {
        let invalid = |reason| Error {
            location: key.to_owned(),
            reason,
            invalid: true,
            forbidden: false,
        };
        let Some((setting, raw)) = self
            .rows()
            .find(|row| row.key == key && matches!(row.kind, Kind::Folder))
            .and_then(|row| self.values.get(key).map(|raw| (row, raw)))
        else {
            return Err(invalid("not a folder setting".to_owned()));
        };
        let expanded = setting.expand(raw, &*self.lookup);
        let text = expanded.as_str().ok_or_else(|| invalid(format!("Invalid storage directory: expected a folder path. Change {key} in this configuration file.")))?;
        let refused = |problem| {
            invalid(format!(
                "Invalid storage directory: {raw} {problem}. Change {key} in this configuration file{} to an absolute folder path.",
                raw.as_str()
                    .and_then(crate::reference)
                    .map_or_else(String::new, |name| format!(" or set {name}"))
            ))
        };
        if text.is_empty() && setting.holds_reference(raw) {
            return Err(refused("is empty or unset"));
        }
        let path = if text.is_empty() {
            standard.to_owned()
        } else {
            PathBuf::from(text)
        };
        if !path.is_absolute() {
            return Err(refused("does not resolve to an absolute folder path"));
        }
        Ok(path)
    }
}

impl Settings {
    /// Resolves the folder through the shared path rule, adding the configuration file to a refusal.
    pub fn folder(&self, key: &str, standard: &Path) -> Result<PathBuf, Error> {
        self.state()
            .folder(key, standard)
            .map_err(|error| file::fail(self.path(), error.to_string()))
    }
}
