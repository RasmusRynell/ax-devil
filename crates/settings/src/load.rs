//! Interpret saved settings without replacing their recoverable raw values.
use crate::{Error, Kind, Lookup, Scope, Setting, State, preferences, shortcuts};
use serde_json::{Map, Value};
use std::{collections::BTreeMap, sync::Arc};

impl State {
    /// Validates `rest` (a settings file without its `version`) and normalizes it.
    pub(super) fn build(
        version: u64,
        rest: &Map<String, Value>,
        table: &'static [Setting],
        lookup: &Lookup,
    ) -> Result<Self, Error> {
        let invalid = |location: &str, reason: &str| Error {
            location: location.to_owned(),
            reason: reason.to_owned(),
            invalid: true,
            forbidden: false,
        };
        let object = |name: &str| match rest.get(name) {
            None => Ok(Map::new()),
            Some(Value::Object(map)) => Ok(map.clone()),
            Some(_) => Err(invalid(name, "expected an object")),
        };
        let mut stored = object("settings")?;
        let mut values = BTreeMap::new();
        let mut numbers = BTreeMap::new();
        let mut settings = Map::new();
        for setting in table {
            if setting.scope == Scope::Device {
                // A device keeps its own; an old file's value is dropped with the next write.
                stored.remove(setting.key);
                continue;
            }
            let raw = stored.remove(setting.key).map_or_else(
                || setting.kind.default_value(),
                |value| setting.kind.with_ids(value),
            );
            let mut value = raw.clone();
            // Folder paths are checked by fallible resolution, independently of loading settings.
            if let Err(reason) = setting.check(&value, &**lookup)
                && !setting.holds_reference(&value)
                && !matches!(setting.kind, Kind::Folder)
            {
                value = preferences::recover(setting, &raw)
                    .ok_or_else(|| invalid(setting.key, &reason))?;
                tracing::warn!(
                    "Invalid saved preference {}: {reason}; using a recovered value",
                    setting.key
                );
            }
            if matches!(setting.kind, Kind::Whole { .. }) && setting.holds_reference(&value) {
                numbers.insert(setting.key.to_owned(), setting.used(&value, &**lookup));
            }
            if !setting.kind.is_default(&raw) {
                settings.insert(setting.key.to_owned(), raw);
            }
            values.insert(setting.key.to_owned(), value);
        }
        settings.append(&mut stored);

        let mut stored = object("shortcuts")?;
        let mut known = BTreeMap::new();
        let mut shortcut_entries = Map::new();
        for command in shortcuts::COMMANDS {
            let id = command.id.as_ref();
            match stored.remove(id) {
                None => {}
                Some(Value::String(key)) if key.is_empty() => {
                    known.insert(id.to_owned(), None);
                }
                Some(Value::String(key)) => match shortcuts::canonical(&key) {
                    Ok(key) => {
                        known.insert(id.to_owned(), Some(key));
                    }
                    Err(reason) => {
                        shortcut_entries.insert(id.to_owned(), Value::String(key));
                        tracing::warn!("Invalid saved shortcut {id}: {reason}; using the default");
                    }
                },
                Some(Value::Null) => {
                    known.insert(id.to_owned(), None);
                }
                Some(_) => {
                    return Err(invalid(
                        &format!("shortcuts.{id}"),
                        "expected a key or null",
                    ));
                }
            }
        }
        let bound = shortcuts::effective(shortcuts::COMMANDS, &known);
        let overrides = shortcuts::overrides_from(shortcuts::COMMANDS, &bound)?;
        for (id, key) in &overrides {
            shortcut_entries
                .entry(id.clone())
                .or_insert_with(|| key.clone().map_or(Value::Null, Value::String));
        }
        shortcut_entries.append(&mut stored);

        let mut document = rest.clone();
        document.insert("settings".into(), Value::Object(settings));
        document.insert("shortcuts".into(), Value::Object(shortcut_entries));
        Ok(Self {
            table,
            lookup: Arc::clone(lookup),
            version,
            revision: 0,
            values,
            numbers,
            overrides,
            document,
        })
    }
}
