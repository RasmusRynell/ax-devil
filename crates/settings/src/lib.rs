//! The settings service: loads the config file, validates changes, saves them atomically and holds the one
//! immutable state. Domain code takes settings as values; only the bootstrap wires this service in.
//!
//! The config file is nested JSON (`ui.theme` is `{"ui": {"theme": ...}}`). Keys this version does not declare are
//! logged at load and kept when the file is saved, so another version's settings survive.
//!
//! TODO: notify subscribers when the state is replaced, once something consumes it.

mod table;

use serde_json::{Map, Value};
use std::{
    collections::BTreeMap,
    fmt, fs, io,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, PoisonError},
};
pub use table::{Kind, Setting, TABLE};

/// Why settings could not be loaded, validated or saved; the reason is meant for the user.
#[derive(Debug)]
pub struct Error {
    /// The setting key or file path the reason is about.
    pub location: String,
    /// What is wrong.
    pub reason: String,
    /// Whether the user's input is at fault (as opposed to the disk).
    pub invalid: bool,
}

impl fmt::Display for Error {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(out, "{}: {}", self.location, self.reason)
    }
}

impl std::error::Error for Error {}

/// One immutable snapshot of every declared setting, replaced whole.
#[derive(Debug)]
pub struct State {
    document: Value,
}

impl State {
    /// Every declared setting by dotted key, such as `ui.theme`.
    pub fn values(&self) -> BTreeMap<String, Value> {
        TABLE
            .iter()
            .filter_map(|setting| {
                let value = self.document.pointer(&pointer(setting.key))?;
                Some((setting.key.to_owned(), value.clone()))
            })
            .collect()
    }
}

/// The settings service for one config file.
pub struct Settings {
    path: PathBuf,
    state: Mutex<Arc<State>>,
}

impl Settings {
    /// Loads the config at `path`. A missing file gives the defaults and is created on the first save; a value that
    /// does not fit its setting is logged and replaced by the default; an unreadable or malformed file is an error.
    pub fn load(path: &Path) -> Result<Self, Error> {
        let fail = |reason: String| Error {
            location: path.display().to_string(),
            reason,
            invalid: false,
        };
        let mut document = match fs::read_to_string(path) {
            Ok(text) => match serde_json::from_str::<Value>(&text) {
                Ok(document) if document.is_object() => document,
                Ok(_) => return Err(fail("not a JSON object".into())),
                Err(error) => return Err(fail(error.to_string())),
            },
            Err(error) if error.kind() == io::ErrorKind::NotFound => Value::Object(Map::new()),
            Err(error) => return Err(fail(error.to_string())),
        };
        let unknown = unknown_keys(&document);
        if !unknown.is_empty() {
            tracing::debug!(
                "Config {} has keys this version ignores and keeps: {}",
                path.display(),
                unknown.join(", ")
            );
        }
        for setting in TABLE {
            let value = match document.pointer(&pointer(setting.key)) {
                None => setting.default_value(),
                Some(value) => match setting.validate(value) {
                    Ok(()) => continue,
                    Err(reason) => {
                        tracing::warn!(
                            "Config {}: {}: {reason}; using the default",
                            path.display(),
                            setting.key
                        );
                        setting.default_value()
                    }
                },
            };
            set(&mut document, setting.key, value);
        }
        Ok(Self {
            path: path.to_owned(),
            state: Mutex::new(Arc::new(State { document })),
        })
    }

    /// The current state.
    pub fn state(&self) -> Arc<State> {
        self.state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clone()
    }

    /// Replaces every declared setting with `values`, which must hold exactly the declared keys. Validates all,
    /// writes the file atomically, then swaps the state; on any failure nothing changes.
    pub fn replace(&self, values: &BTreeMap<String, Value>) -> Result<Arc<State>, Error> {
        let invalid = |location: &str, reason: &str| Error {
            location: location.to_owned(),
            reason: reason.to_owned(),
            invalid: true,
        };
        if let Some(key) = values
            .keys()
            .find(|key| !TABLE.iter().any(|setting| setting.key == key.as_str()))
        {
            return Err(invalid(key, "unknown setting"));
        }
        let mut guard = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        let mut document = guard.document.clone();
        for setting in TABLE {
            let value = values
                .get(setting.key)
                .ok_or_else(|| invalid(setting.key, "missing"))?;
            setting
                .validate(value)
                .map_err(|reason| invalid(setting.key, &reason))?;
            set(&mut document, setting.key, value.clone());
        }
        write_atomically(&self.path, &document).map_err(|error| Error {
            location: self.path.display().to_string(),
            reason: error.to_string(),
            invalid: false,
        })?;
        *guard = Arc::new(State { document });
        Ok(guard.clone())
    }
}

fn pointer(key: &str) -> String {
    format!("/{}", key.replace('.', "/"))
}

/// Sets the dotted `key`, replacing any non-object in the way.
fn set(document: &mut Value, key: &str, value: Value) {
    let mut node = document;
    for part in key.split('.') {
        if !node.is_object() {
            *node = Value::Object(Map::new());
        }
        node = &mut node[part];
    }
    *node = value;
}

/// Dotted paths of every leaf of `document` that the table does not declare.
fn unknown_keys(document: &Value) -> Vec<String> {
    fn walk(prefix: &str, node: &Value, found: &mut Vec<String>) {
        match node {
            Value::Object(map) if !map.is_empty() => {
                for (name, child) in map {
                    let path = if prefix.is_empty() {
                        name.clone()
                    } else {
                        format!("{prefix}.{name}")
                    };
                    walk(&path, child, found);
                }
            }
            _ if prefix.is_empty() || TABLE.iter().any(|setting| setting.key == prefix) => {}
            _ => found.push(prefix.to_owned()),
        }
    }
    let mut found = Vec::new();
    walk("", document, &mut found);
    found
}

/// Writes the document next to `path` and renames it into place, so a crash never leaves half a file.
fn write_atomically(path: &Path, document: &Value) -> io::Result<()> {
    let folder = path.parent().unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(folder)?;
    let mut name = path.file_name().unwrap_or_default().to_owned();
    name.push(".tmp");
    let temporary = folder.join(name);
    let mut text = serde_json::to_string_pretty(document).map_err(io::Error::other)?;
    text.push('\n');
    let result = fs::File::create(&temporary)
        .and_then(|mut file| {
            io::Write::write_all(&mut file, text.as_bytes())?;
            file.sync_all()
        })
        .and_then(|()| fs::rename(&temporary, path));
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn values(theme: &str) -> BTreeMap<String, Value> {
        BTreeMap::from([
            ("ui.theme".into(), json!(theme)),
            ("ui.quick_setup_done".into(), json!(true)),
        ])
    }

    #[test]
    fn every_default_is_valid_for_its_own_setting() {
        for setting in TABLE {
            assert_eq!(
                setting.validate(&setting.default_value()),
                Ok(()),
                "{}",
                setting.key
            );
        }
    }

    #[test]
    fn a_saved_change_survives_a_restart_and_keeps_keys_from_other_versions() {
        let folder = tempfile::tempdir().unwrap();
        let path = folder.path().join("configs/default.json");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(
            &path,
            r#"{"ui": {"theme": "sepia", "text_size": "large", "window": {"custom_frame": true}}, "shortcuts": {}}"#,
        )
        .unwrap();

        let settings = Settings::load(&path).unwrap();
        assert_eq!(
            settings.state().values()["ui.theme"],
            "auto",
            "an invalid value falls back to the default"
        );

        settings.replace(&values("dark")).unwrap();
        let saved: Value = serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(saved["ui"]["window"]["custom_frame"], true);
        assert_eq!(
            saved["ui"]["text_size"], "large",
            "a removed setting stays in the file"
        );
        assert_eq!(saved["shortcuts"], json!({}));
        assert_eq!(
            Settings::load(&path).unwrap().state().values(),
            values("dark")
        );
        assert!(!path.with_extension("json.tmp").exists());
    }

    #[test]
    fn a_rejected_replace_changes_neither_state_nor_file() {
        let folder = tempfile::tempdir().unwrap();
        let path = folder.path().join("default.json");
        let settings = Settings::load(&path).unwrap();

        let error = settings.replace(&values("neon")).unwrap_err();
        assert_eq!(error.location, "ui.theme");
        assert!(error.invalid);
        let mut unknown = values("dark");
        unknown.insert("ui.zoom".into(), json!(2));
        assert_eq!(settings.replace(&unknown).unwrap_err().location, "ui.zoom");
        let mut missing = values("dark");
        missing.remove("ui.quick_setup_done");
        assert_eq!(
            settings.replace(&missing).unwrap_err().location,
            "ui.quick_setup_done"
        );

        assert_eq!(settings.state().values()["ui.theme"], "auto");
        assert!(!path.exists());
    }

    #[test]
    fn a_malformed_file_is_an_error_naming_the_file() {
        let folder = tempfile::tempdir().unwrap();
        let path = folder.path().join("default.json");
        fs::write(&path, "[1, 2").unwrap();
        let error = Settings::load(&path).err().unwrap();
        assert_eq!(error.location, path.display().to_string());
    }
}
