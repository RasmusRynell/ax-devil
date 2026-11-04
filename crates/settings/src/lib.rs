//! The settings service: loads the config file, validates changes, saves them atomically and holds the one
//! immutable state. Domain code takes settings as values; only the bootstrap wires this service in.
//!
//! Settings supply defaults at the moment something is created and the value is copied in; nothing created from them
//! refers back to the file. Only shortcuts are read live. Each setting has a [`Scope`], which decides which of them
//! a person may read and change when several share one backend. See the README for the file format and how to
//! change it.
//!
//! A change reaches subscribers as the new state, replaced whole; a subscriber that is busy sees only the newest.

mod env;
mod file;
mod kind;
mod migrate;
mod secret;
pub mod shortcuts;
mod table;
pub mod window;

pub use env::reference;
pub use kind::{Kind, StickyValue};
pub use migrate::VERSION;
pub use secret::MASK;
use serde_json::{Map, Value};
use std::{
    collections::BTreeMap,
    fmt,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, PoisonError},
};
pub use table::{Scope, Setting, TABLE};
use tokio::sync::watch;

/// Why settings could not be loaded, validated or saved; the reason is meant for the user and never contains a
/// setting's value.
#[derive(Debug)]
pub struct Error {
    /// The setting key (`ui.theme`, `shortcuts.exit`) or file path the reason is about.
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

/// Looks up an environment variable by name.
type Lookup = Arc<dyn Fn(&str) -> Option<String> + Send + Sync>;

/// A full replacement of the settings and shortcuts a person may change (see [`Settings::replace`]). The file stores
/// only what differs from the defaults.
#[derive(Clone, Debug)]
pub struct Change {
    /// Every setting in the changed scopes by dotted key, references unresolved. A masked secret keeps its value.
    pub values: BTreeMap<String, Value>,
    /// The key of every command, `None` when unbound; empty unless [`Scope::Personal`] is changed.
    pub shortcuts: BTreeMap<String, Option<String>>,
}

/// One immutable snapshot of every declared setting and shortcut, replaced whole.
pub struct State {
    table: &'static [Setting],
    lookup: Lookup,
    version: u64,
    values: BTreeMap<String, Value>,
    /// The validated shortcut overrides of known commands, in canonical form.
    overrides: BTreeMap<String, Option<String>>,
    /// What the file holds: only values that differ from the default, plus everything this version does not know.
    document: Map<String, Value>,
}

impl fmt::Debug for State {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.debug_struct("State").finish_non_exhaustive()
    }
}

impl State {
    /// Validates `rest` (a settings file without its `version`) and normalizes it.
    fn build(
        version: u64,
        rest: &Map<String, Value>,
        table: &'static [Setting],
        lookup: &Lookup,
    ) -> Result<Self, Error> {
        let invalid = |location: &str, reason: &str| Error {
            location: location.to_owned(),
            reason: reason.to_owned(),
            invalid: true,
        };
        let object = |name: &str| match rest.get(name) {
            None => Ok(Map::new()),
            Some(Value::Object(map)) => Ok(map.clone()),
            Some(_) => Err(invalid(name, "expected an object")),
        };
        let mut stored = object("settings")?;
        let mut values = BTreeMap::new();
        let mut settings = Map::new();
        for setting in table {
            let value = stored
                .remove(setting.key)
                .unwrap_or_else(|| setting.kind.default_value());
            setting
                .check(&value, &**lookup)
                .map_err(|reason| invalid(setting.key, &reason))?;
            if !setting.kind.is_default(&value) {
                settings.insert(setting.key.to_owned(), value.clone());
            }
            values.insert(setting.key.to_owned(), value);
        }
        settings.append(&mut stored);

        let mut stored = object("shortcuts")?;
        let mut known = BTreeMap::new();
        for command in shortcuts::COMMANDS {
            match stored.remove(command.id) {
                None => {}
                Some(Value::String(key)) => {
                    known.insert(command.id.to_owned(), Some(key));
                }
                Some(Value::Null) => {
                    known.insert(command.id.to_owned(), None);
                }
                Some(_) => {
                    return Err(invalid(
                        &format!("shortcuts.{}", command.id),
                        "expected a key or null",
                    ));
                }
            }
        }
        let bound: BTreeMap<String, Option<String>> = shortcuts::effective(&known)
            .into_iter()
            .map(|(id, key)| (id.to_owned(), key))
            .collect();
        let overrides = shortcuts::overrides_from(&bound)?;
        let mut shortcut_entries = Map::new();
        for (id, key) in &overrides {
            shortcut_entries.insert(id.clone(), key.clone().map_or(Value::Null, Value::String));
        }
        shortcut_entries.append(&mut stored);

        let mut document = rest.clone();
        document.insert("settings".into(), Value::Object(settings));
        document.insert("shortcuts".into(), Value::Object(shortcut_entries));
        Ok(Self {
            table,
            lookup: Arc::clone(lookup),
            version,
            values,
            overrides,
            document,
        })
    }

    /// What this version does not know and keeps: top-level keys, settings and command ids.
    fn unknown(&self) -> Vec<String> {
        let mut found: Vec<String> = Vec::new();
        for (name, value) in &self.document {
            match (name.as_str(), value) {
                ("settings" | "shortcuts", Value::Object(entries)) => {
                    found.extend(entries.keys().filter_map(|key| {
                        let known = if name == "settings" {
                            self.table.iter().any(|setting| setting.key == key)
                        } else {
                            self.overrides.contains_key(key)
                                || shortcuts::COMMANDS.iter().any(|command| command.id == key)
                        };
                        (!known).then(|| format!("{name}.{key}"))
                    }));
                }
                _ => found.push(name.clone()),
            }
        }
        found
    }

    /// The settings in `scopes` by dotted key, such as `ui.theme`, with the default when the file has none, as shown
    /// to a person: a `$NAME` reference as written, a literal secret masked. See [`resolved`](Self::resolved) for
    /// the value as it is used.
    pub fn values(&self, scopes: &[Scope]) -> BTreeMap<String, Value> {
        self.table
            .iter()
            .filter(|setting| scopes.contains(&setting.scope))
            .filter_map(|setting| {
                let value = self.values.get(setting.key)?;
                Some((setting.key.to_owned(), setting.shown(value)))
            })
            .collect()
    }

    /// The key of every command once the user's overrides apply; `None` when unbound. Shortcuts belong to
    /// [`Scope::Personal`].
    pub fn shortcuts(&self) -> BTreeMap<&'static str, Option<String>> {
        shortcuts::effective(&self.overrides)
    }

    /// The value of the declared setting `key` as it is used: a `$NAME` reference in a setting that takes them is
    /// replaced by the variable's value, empty when it is unset.
    pub fn resolved(&self, key: &str) -> Option<Value> {
        let setting = self.table.iter().find(|setting| setting.key == key)?;
        let value = self.values.get(key)?;
        Some(if setting.env {
            env::resolve(value, &*self.lookup)
        } else {
            value.clone()
        })
    }

    /// The value of the declared setting `key` as stored: a `$NAME` reference as written and a literal secret in full,
    /// for copying into something new (a Live Stream Item starts from the camera defaults). Never show it to a person;
    /// [`values`](Self::values) is the shown form.
    pub fn stored(&self, key: &str) -> Option<&Value> {
        self.values.get(key)
    }

    /// The folder a setting of kind `Folder` holds; `None` when it is empty (the standard folder) or `key` is not such
    /// a setting.
    pub fn folder(&self, key: &str) -> Option<PathBuf> {
        let path = self.values.get(key)?.as_str()?;
        (!path.is_empty()).then(|| PathBuf::from(path))
    }

    /// The number a setting of kind `AutoOrWhole` holds; `None` for `auto`, or when `key` is not such a setting.
    pub fn auto_or_whole(&self, key: &str) -> Option<u64> {
        self.values.get(key)?.as_u64()
    }
}

/// The settings service for one config file.
pub struct Settings {
    path: PathBuf,
    state: watch::Sender<Arc<State>>,
    /// Held while a change is validated, saved and published, so changes apply one at a time.
    writing: Mutex<()>,
}

impl Settings {
    /// Loads the config at `path`. A missing file gives the defaults and is created on the first save. An older file
    /// is copied to `<name>.v<N>.json`, migrated and written back. A file that is malformed, has no integer
    /// `version`, is newer than this build, or holds a known setting or shortcut that does not validate is an
    /// error naming the file and the key. Keys this version does not know are logged and kept.
    pub fn load(path: &Path) -> Result<Self, Error> {
        Self::open(
            path,
            TABLE,
            migrate::MIGRATIONS,
            Arc::new(|name| std::env::var(name).ok()),
        )
    }

    fn open(
        path: &Path,
        table: &'static [Setting],
        chain: &[migrate::Migration],
        lookup: Lookup,
    ) -> Result<Self, Error> {
        let current = chain.len() as u64 + 1;
        let (state, migrated) = match file::read(path, current)? {
            None => (State::build(current, &Map::new(), table, &lookup), false),
            Some(mut read) => {
                let from = read.version;
                if from < current {
                    file::backup(path, from, &read.bytes)?;
                    migrate::migrate(&mut read.document, from, chain).map_err(|reason| {
                        file::fail(path, format!("cannot migrate the file: {reason}"))
                    })?;
                }
                (
                    State::build(current, &read.document, table, &lookup),
                    from < current,
                )
            }
        };
        let state = state
            .map_err(|error| file::fail(path, format!("{}: {}", error.location, error.reason)))?;
        let unknown = state.unknown();
        if !unknown.is_empty() {
            tracing::warn!(
                "Config {} has entries this version does not know and keeps: {}",
                path.display(),
                unknown.join(", ")
            );
        }
        if migrated {
            file::write(path, state.version, &state.document)?;
        }
        Ok(Self {
            path: path.to_owned(),
            state: watch::Sender::new(Arc::new(state)),
            writing: Mutex::new(()),
        })
    }

    /// The current state.
    pub fn state(&self) -> Arc<State> {
        self.state.borrow().clone()
    }

    /// A receiver that holds the current state and is woken by every successful [`replace`](Self::replace).
    pub fn subscribe(&self) -> watch::Receiver<Arc<State>> {
        self.state.subscribe()
    }

    /// Replaces every setting in `scopes`, and the shortcuts when `scopes` holds [`Scope::Personal`], with `change`,
    /// which must hold exactly those settings and only known commands; a masked secret keeps its stored value.
    /// Validates all, writes the file atomically with only what differs from the defaults (and keeps what this
    /// version does not know and the settings of other scopes), then swaps the state; on any failure nothing changes.
    pub fn replace(&self, scopes: &[Scope], change: &Change) -> Result<Arc<State>, Error> {
        let _writing = self.writing.lock().unwrap_or_else(PoisonError::into_inner);
        let current = self.state();
        let invalid = |location: &str, reason: &str| Error {
            location: location.to_owned(),
            reason: reason.to_owned(),
            invalid: true,
        };
        let chosen = || {
            current
                .table
                .iter()
                .filter(|setting| scopes.contains(&setting.scope))
        };
        if let Some(key) = change
            .values
            .keys()
            .find(|key| !chosen().any(|setting| setting.key == key.as_str()))
        {
            return Err(invalid(key, "unknown setting"));
        }
        let mut values = current.values.clone();
        for setting in chosen() {
            let value = change
                .values
                .get(setting.key)
                .ok_or_else(|| invalid(setting.key, "missing"))?;
            let kept = setting
                .unmasked(value, current.values.get(setting.key))
                .map_err(|reason| invalid(setting.key, &reason))?;
            values.insert(setting.key.to_owned(), kept);
        }
        let overrides = if scopes.contains(&Scope::Personal) {
            shortcuts::overrides_from(&change.shortcuts)?
        } else if let Some(id) = change.shortcuts.keys().next() {
            return Err(invalid(&format!("shortcuts.{id}"), "not yours to change"));
        } else {
            current.overrides.clone()
        };
        self.write(&current, values, overrides)
    }

    /// Changes the one setting `key` and nothing else. Under the same lock that orders every change, `edit` sees the
    /// stored value as it is now and answers the value to store, or `None` to leave the setting alone and get the
    /// current state back. It works on raw values (a `$NAME` reference as written, a secret as stored), so no secret
    /// passes through it, and no other change falls between the read and the write. The stored value is validated as
    /// by [`replace`](Self::replace); on any failure nothing changes.
    pub fn update(
        &self,
        key: &str,
        edit: impl FnOnce(&Value) -> Option<Value>,
    ) -> Result<Arc<State>, Error> {
        let _writing = self.writing.lock().unwrap_or_else(PoisonError::into_inner);
        let current = self.state();
        let Some(stored) = current.values.get(key) else {
            return Err(Error {
                location: key.to_owned(),
                reason: "unknown setting".to_owned(),
                invalid: true,
            });
        };
        let Some(value) = edit(stored) else {
            return Ok(current);
        };
        let mut values = current.values.clone();
        values.insert(key.to_owned(), value);
        let overrides = current.overrides.clone();
        self.write(&current, values, overrides)
    }

    /// Validates `values` and `overrides` on top of `current`, writes the file and publishes the new state. The caller
    /// holds the `writing` lock.
    fn write(
        &self,
        current: &State,
        values: BTreeMap<String, Value>,
        overrides: BTreeMap<String, Option<String>>,
    ) -> Result<Arc<State>, Error> {
        let mut rest = current.document.clone();
        let mut settings = Map::from_iter(values);
        if let Some(Value::Object(kept)) = rest.get("settings") {
            settings.extend(
                kept.iter()
                    .filter(|(key, _)| !current.values.contains_key(*key))
                    .map(|(k, v)| (k.clone(), v.clone())),
            );
        }
        let mut shortcut_entries = Map::new();
        if let Some(Value::Object(kept)) = rest.get("shortcuts") {
            let unknown = kept.iter().filter(|(id, _)| {
                !shortcuts::COMMANDS
                    .iter()
                    .any(|command| command.id == id.as_str())
            });
            shortcut_entries.extend(unknown.map(|(k, v)| (k.clone(), v.clone())));
        }
        shortcut_entries.extend(
            overrides
                .into_iter()
                .map(|(id, key)| (id, key.map_or(Value::Null, Value::String))),
        );
        rest.insert("settings".into(), Value::Object(settings));
        rest.insert("shortcuts".into(), Value::Object(shortcut_entries));

        let state = Arc::new(State::build(
            current.version,
            &rest,
            current.table,
            &current.lookup,
        )?);
        file::write(&self.path, state.version, &state.document)?;
        self.state.send_replace(Arc::clone(&state));
        Ok(state)
    }
}

#[cfg(test)]
mod scope_tests;
#[cfg(test)]
mod tests;
