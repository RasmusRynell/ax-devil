//! The settings service: loads the config file, validates changes, saves them atomically and holds the one
//! immutable state. Domain code takes settings as values; only the bootstrap wires this service in.
//!
//! Settings supply defaults at the moment something is created and the value is copied in; nothing created from them
//! refers back to the file. Only shortcuts are read live. Each setting has a [`Scope`], which decides which of them
//! a person may read and change when several share one backend; the server holds no Device setting. See the README for the file format and how to
//! change it.
//!
//! A change names the keys it sets and resets; any other key keeps the value it has now, whoever wrote it. It reaches
//! subscribers as the new state, replaced whole, with a rising revision; a subscriber that is busy sees only the
//! newest.

mod access;
mod env;
mod expand;
mod file;
mod kind;
mod load;
mod migrate;
mod preferences;
mod read;
mod secret;
pub mod shortcuts;
mod table;

pub use env::reference;
pub use kind::{Kind, NoFilter, StickyValue, SyncValue};
pub use migrate::VERSION;
use serde_json::{Map, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, PoisonError},
};
pub use table::{Scope, Setting, TABLE};
use tokio::sync::watch;

/// Why settings could not be loaded, validated or saved; the reason is meant for the user and never contains a
/// setting's value, except the non-secret storage roots needed to diagnose a failed start.
#[derive(Debug)]
pub struct Error {
    /// The setting key (`camera.host`, `shortcuts.exit`) or file path the reason is about.
    pub location: String,
    /// What is wrong.
    pub reason: String,
    /// Whether the user's input is at fault (as opposed to the disk).
    pub invalid: bool,
    /// Whether the caller lacks the Full access the change needs.
    pub forbidden: bool,
}

impl fmt::Display for Error {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(out, "{}: {}", self.location, self.reason)
    }
}

impl std::error::Error for Error {}

/// Looks up an environment variable by name.
type Lookup = Arc<dyn Fn(&str) -> Option<String> + Send + Sync>;

/// A change to the settings and shortcuts a person may change (see [`Settings::patch`]): only the keys it names change.
/// The file stores only what differs from the defaults.
#[derive(Clone, Debug, Default)]
pub struct Patch {
    /// The settings to set by dotted key, references unresolved. A list of records is the whole list; a record with an `id` and no password keeps the stored password.
    pub set: BTreeMap<String, Value>,
    /// The settings to put back to their default.
    pub reset: BTreeSet<String>,
    /// The commands to bind: a key, or `None` to unbind.
    pub shortcuts_set: BTreeMap<String, Option<String>>,
    /// The commands to put back to their own key.
    pub shortcuts_reset: BTreeSet<String>,
}

/// One immutable snapshot of every declared setting and shortcut, replaced whole.
pub struct State {
    table: &'static [Setting],
    lookup: Lookup,
    version: u64,
    /// Rises with every write; see [`revision`](Self::revision).
    revision: u64,
    values: BTreeMap<String, Value>,
    /// The number each whole-number setting that holds a reference gives, read when the state was built; see
    /// [`stored`](Self::stored).
    numbers: BTreeMap<String, Value>,
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
    /// Raw settings, including recoverable preferences, so a patch changes only the keys it names.
    fn saved_values(&self) -> BTreeMap<String, Value> {
        self.document
            .get("settings")
            .and_then(Value::as_object)
            .map(|values| values.clone().into_iter().collect())
            .unwrap_or_default()
    }

    /// What this version does not know and keeps: top-level keys, settings and command ids.
    fn unknown(&self) -> Vec<String> {
        let mut found: Vec<String> = Vec::new();
        for (name, value) in &self.document {
            match (name.as_str(), value) {
                ("settings" | "shortcuts", Value::Object(entries)) => {
                    found.extend(entries.keys().filter_map(|key| {
                        let known = if name == "settings" {
                            self.rows().any(|setting| setting.key == key)
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

    /// The settings the server holds: every row but the Device ones.
    fn rows(&self) -> impl Iterator<Item = &'static Setting> {
        self.table
            .iter()
            .filter(|setting| setting.scope != Scope::Device)
    }

    /// Every setting by dotted key, such as `camera.host`, with the default when the file has none, as a
    /// client reads it: a `$NAME` reference as written, a secret as its state, which never holds the literal. See
    /// [`resolved`](Self::resolved) for the value as it is used.
    pub fn values(&self) -> BTreeMap<String, Value> {
        self.rows()
            .filter_map(|setting| {
                let value = self.values.get(setting.key)?;
                Some((setting.key.to_owned(), setting.shown(value)))
            })
            .collect()
    }

    /// The format version of the file this state is written to.
    pub fn version(&self) -> u64 {
        self.version
    }

    /// Rises with every write, so a client can tell which of two states is newer. It starts at the time the service
    /// loaded, in milliseconds, so a restarted backend never repeats a revision a client already holds.
    pub fn revision(&self) -> u64 {
        self.revision
    }

    /// The key of every command once the user's overrides apply; `None` when unbound. Shortcuts belong to
    /// [`Scope::Personal`].
    pub fn shortcuts(&self) -> BTreeMap<&'static str, Option<String>> {
        shortcuts::effective(&self.overrides)
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
    /// `version`, or is newer than this build is an error naming the file. Recoverable preferences keep their
    /// raw saved values; service startup checks storage through [`validate_storage`](Self::validate_storage). Other invalid values fail with the key. Unknown keys are logged and kept.
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
        let mut state = state
            .map_err(|error| file::fail(path, format!("{}: {}", error.location, error.reason)))?;
        state.revision = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |since| u64::try_from(since.as_millis()).unwrap_or(0));
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

    /// The config file this service reads and writes.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// The current state.
    pub fn state(&self) -> Arc<State> {
        self.state.borrow().clone()
    }

    /// A receiver that holds the current state and is woken by every successful write.
    pub fn subscribe(&self) -> watch::Receiver<Arc<State>> {
        self.state.subscribe()
    }

    /// Applies `patch` to the current state: each key it names changes, every other key keeps its value, so the last
    /// write to a key wins. Validates the result, writes the
    /// file atomically with only what differs from the defaults (and keeps what this version does not know), then
    /// swaps the state; on any failure nothing changes.
    pub fn patch(&self, patch: &Patch) -> Result<Arc<State>, Error> {
        self.patch_if(patch, |_| Ok(()))
    }

    /// As [`patch`](Self::patch), but `allow` first sees the stored state under the lock that orders every change, so
    /// a check that depends on the stored values and on what other writers do holds when the write happens.
    pub fn patch_if(
        &self,
        patch: &Patch,
        allow: impl FnOnce(&State) -> Result<(), Error>,
    ) -> Result<Arc<State>, Error> {
        let _writing = self.writing.lock().unwrap_or_else(PoisonError::into_inner);
        let current = self.state();
        allow(&current)?;
        let invalid = |location: &str, reason: &str| Error {
            location: location.to_owned(),
            reason: reason.to_owned(),
            invalid: true,
            forbidden: false,
        };
        if let Some(key) = patch
            .set
            .keys()
            .chain(&patch.reset)
            .find(|key| !current.rows().any(|setting| setting.key == key.as_str()))
        {
            return Err(invalid(key, "unknown setting"));
        }
        if let Some(key) = patch.reset.iter().find(|key| patch.set.contains_key(*key)) {
            return Err(invalid(key, "named twice"));
        }
        let mut values = current.saved_values();
        for setting in current.rows() {
            let value = match patch.set.get(setting.key) {
                Some(value) => value.clone(),
                None if patch.reset.contains(setting.key) => setting.kind.default_value(),
                None => continue,
            };
            let kept = setting
                .applied(&value, current.values.get(setting.key))
                .map_err(|reason| invalid(setting.key, &reason))?;
            setting
                .check(&kept, &*current.lookup)
                .map_err(|reason| invalid(setting.key, &reason))?;
            values.insert(setting.key.to_owned(), kept);
        }
        let mut bound: BTreeMap<String, Option<String>> = shortcuts::effective(&current.overrides)
            .into_iter()
            .map(|(id, key)| (id.to_owned(), key))
            .collect();
        for id in &patch.shortcuts_reset {
            let location = format!("shortcuts.{id}");
            let command = shortcuts::COMMANDS
                .iter()
                .find(|command| command.id == id.as_str())
                .ok_or_else(|| invalid(&location, "is not a command"))?;
            if patch.shortcuts_set.contains_key(id) {
                return Err(invalid(&location, "named twice"));
            }
            bound.insert(id.clone(), command.key.map(str::to_owned));
        }
        bound.extend(patch.shortcuts_set.clone());
        let overrides = shortcuts::overrides_from(&bound)?;
        self.write(&current, values, overrides, patch)
    }

    /// Changes the one setting `key` and nothing else. Under the same lock that orders every change, `edit` sees the
    /// effective value as it is now and answers the value to store, or `None` to leave the setting alone and get the
    /// current state back. It works on raw values (a `$NAME` reference as written, a secret as stored), so no secret
    /// passes through it, and no other change falls between the read and the write. The stored value is validated as
    /// by [`patch`](Self::patch); on any failure nothing changes.
    pub fn update(
        &self,
        key: &str,
        edit: impl FnOnce(&Value) -> Option<Value>,
    ) -> Result<Arc<State>, Error> {
        let _writing = self.writing.lock().unwrap_or_else(PoisonError::into_inner);
        let current = self.state();
        let invalid = |location: &str, reason: &str| Error {
            location: location.to_owned(),
            reason: reason.to_owned(),
            invalid: true,
            forbidden: false,
        };
        let Some(stored) = current.values.get(key) else {
            return Err(invalid(key, "unknown setting"));
        };
        let Some(value) = edit(stored) else {
            return Ok(current);
        };
        if let Some(setting) = current.rows().find(|setting| setting.key == key) {
            setting
                .check(&value, &*current.lookup)
                .map_err(|reason| invalid(key, &reason))?;
        }
        let mut values = current.saved_values();
        values.insert(key.to_owned(), value);
        let overrides = current.overrides.clone();
        self.write(&current, values, overrides, &Patch::default())
    }

    /// Validates `values` and `overrides` on top of `current`, writes the file and publishes the new state. The caller
    /// holds the `writing` lock.
    fn write(
        &self,
        current: &State,
        values: BTreeMap<String, Value>,
        overrides: BTreeMap<String, Option<String>>,
        patch: &Patch,
    ) -> Result<Arc<State>, Error> {
        let mut rest = current.document.clone();
        let settings = Map::from_iter(values);
        let mut shortcut_entries: Map<String, Value> = overrides
            .into_iter()
            .map(|(id, key)| (id, key.map_or(Value::Null, Value::String)))
            .collect();
        if let Some(Value::Object(kept)) = rest.get("shortcuts") {
            shortcut_entries.extend(
                kept.iter()
                    .filter(|(id, _)| {
                        !patch.shortcuts_set.contains_key(*id)
                            && !patch.shortcuts_reset.contains(*id)
                    })
                    .map(|(id, value)| (id.clone(), value.clone())),
            );
        }
        rest.insert("settings".into(), Value::Object(settings));
        rest.insert("shortcuts".into(), Value::Object(shortcut_entries));

        let mut state = State::build(current.version, &rest, current.table, &current.lookup)?;
        state.revision = current.revision + 1;
        let state = Arc::new(state);
        file::write(&self.path, state.version, &state.document)?;
        self.state.send_replace(Arc::clone(&state));
        Ok(state)
    }
}

#[cfg(test)]
mod patch_tests;
#[cfg(test)]
mod reference_tests;
#[cfg(test)]
mod scope_tests;
#[cfg(test)]
mod tests;
