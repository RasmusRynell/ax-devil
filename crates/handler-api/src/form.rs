//! The one settings schema: what a decoder's options and a resolver's settings are, as data the form, the command
//! line and the validation are generated from.

use std::{collections::BTreeMap, path::Path};

use foundation::{ErrorCode, Location, UserError};
use serde_json::{Map, Value};

/// The settings or options a handler takes.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Form {
    /// The fields in the order they are shown.
    pub fields: Vec<Field>,
}

/// One setting.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Field {
    /// The key in the settings.
    pub key: String,
    /// What the user reads.
    pub label: String,
    /// What kind of value the setting holds.
    pub kind: FieldKind,
    /// Whether the handler fails without a value (after the default is filled in).
    pub required: bool,
    /// The value used when none is given.
    pub default: Option<Value>,
    /// How the setting is given on the command line.
    pub cli: Cli,
}

/// What kind of value a setting holds, and so which control it gets.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FieldKind {
    /// A folder path.
    Folder,
    /// A file path with one of these extensions: lowercase, with the dot.
    File {
        /// The extensions the file may have.
        extensions: Vec<String>,
    },
    /// Free text.
    Text,
    /// A whole number of at least `min`.
    Integer {
        /// The smallest value allowed.
        min: i64,
    },
    /// One decoder id, chosen from the registry.
    Handler,
    /// Several of the values `scan` finds; the key absent means all of them, an explicit list means those.
    Scan {
        /// The settings the scan reads, whose change invalidates its result.
        depends_on: Vec<String>,
    },
}

/// How a setting is given on the command line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Cli {
    /// A positional argument, in field order.
    Positional,
    /// A `--name` flag; a `Scan` flag takes a comma-separated list.
    Flag(String),
}

impl Field {
    /// An optional setting without a default, given as the flag `--<key>` with `_` written as `-`.
    pub fn new(key: &str, label: &str, kind: FieldKind) -> Self {
        Self {
            key: key.to_owned(),
            label: label.to_owned(),
            kind,
            required: false,
            default: None,
            cli: Cli::Flag(key.replace('_', "-")),
        }
    }

    /// The same setting, which must have a value.
    #[must_use]
    pub fn required(mut self) -> Self {
        self.required = true;
        self
    }

    /// The same setting, given on the command line as a positional argument.
    #[must_use]
    pub fn positional(mut self) -> Self {
        self.cli = Cli::Positional;
        self
    }
}

/// Which of the values a scan found a setting names.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Selection {
    /// Every value.
    All,
    /// Only these values.
    Only(Vec<String>),
}

/// Settings that fit a [`Form`]: required keys are present, defaults are filled in and every declared value has
/// its kind. Keys the form does not declare are kept for [`unknown_keys`](Self::unknown_keys) and nothing else.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Values {
    map: Map<String, Value>,
    declared: Vec<String>,
}

impl Form {
    /// `raw` checked against the form: a missing or null value becomes the default, a required one without either
    /// is an error, and the rest must have its field's kind. The error names the key.
    pub fn validate(&self, raw: &Map<String, Value>) -> Result<Values, UserError> {
        let mut map = raw.clone();
        for field in &self.fields {
            match raw.get(&field.key).filter(|value| !value.is_null()) {
                Some(value) => check(field, value)?,
                None => match &field.default {
                    Some(default) => {
                        check(field, default)?;
                        map.insert(field.key.clone(), default.clone());
                    }
                    None if field.required => return Err(invalid_field("Required", &field.key)),
                    None => {
                        map.remove(&field.key);
                    }
                },
            }
        }
        Ok(Values {
            map,
            declared: self.fields.iter().map(|field| field.key.clone()).collect(),
        })
    }
}

fn check(field: &Field, value: &Value) -> Result<(), UserError> {
    let reason = match (&field.kind, value) {
        (FieldKind::Scan { .. }, Value::Array(items)) if items.iter().all(Value::is_string) => None,
        (FieldKind::Scan { .. }, _) => Some("Must be a list of names".to_owned()),
        (FieldKind::Integer { min }, value) => match value.as_i64() {
            Some(number) if number >= *min => None,
            Some(_) => Some(format!("Must be at least {min}")),
            None => Some("Must be a whole number".to_owned()),
        },
        (_, Value::String(text)) if field.required && text.is_empty() => {
            Some("Must not be empty".to_owned())
        }
        (_, Value::String(_)) => None,
        (_, _) => Some("Must be text".to_owned()),
    };
    reason.map_or(Ok(()), |reason| Err(invalid_field(&reason, &field.key)))
}

/// A settings value that does not fit, about the field `key`.
pub fn invalid_field(reason: &str, key: &str) -> UserError {
    UserError::new(ErrorCode::PlaylistInvalid, reason).at(Location::field(key))
}

impl Values {
    /// The text of a text, folder, file or handler setting.
    pub fn text(&self, key: &str) -> Option<&str> {
        self.map.get(key)?.as_str()
    }

    /// The number of an integer setting.
    pub fn integer(&self, key: &str) -> Option<i64> {
        self.map.get(key)?.as_i64()
    }

    /// The path of a folder or file setting.
    pub fn path(&self, key: &str) -> Option<&Path> {
        self.text(key).map(Path::new)
    }

    /// What a scan setting names: everything when it is absent, else the listed values.
    pub fn selection(&self, key: &str) -> Selection {
        match self.map.get(key) {
            Some(Value::Array(names)) => Selection::Only(
                names
                    .iter()
                    .filter_map(|name| name.as_str().map(str::to_owned))
                    .collect(),
            ),
            _ => Selection::All,
        }
    }

    /// The given keys the form does not declare, in key order.
    pub fn unknown_keys(&self) -> Vec<&str> {
        self.map
            .keys()
            .filter(|key| !self.declared.contains(key))
            .map(String::as_str)
            .collect()
    }

    /// The declared keys only, with defaults filled in: what a handler receives.
    pub fn declared(&self) -> Map<String, Value> {
        self.map
            .iter()
            .filter(|(key, _)| self.declared.contains(key))
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect()
    }

    /// The declared values as JSON text with the keys in order, so equal values make equal text: an explicit
    /// default, an omitted key and an undeclared key all give one scene cache.
    pub fn canonical(&self) -> String {
        let ordered: BTreeMap<_, _> = self.declared().into_iter().collect();
        Value::Object(ordered.into_iter().collect()).to_string()
    }
}
