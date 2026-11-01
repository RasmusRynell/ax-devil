//! What a resolver's settings are: declared data the form and the CLI are generated from.

use foundation::UserError;
use serde_json::Value;

use crate::Settings;

/// The settings a resolver takes.
#[derive(Debug, PartialEq, Eq)]
pub struct ResolverSchema {
    /// The settings in the order they are shown.
    pub fields: &'static [Field],
}

/// One setting.
#[derive(Debug, PartialEq, Eq)]
pub struct Field {
    /// The key in the settings.
    pub key: &'static str,
    /// What the user reads.
    pub label: &'static str,
    /// What kind of value the setting holds.
    pub kind: FieldKind,
    /// Whether resolving fails without it.
    pub required: bool,
    /// How the setting is given on the command line.
    pub cli: Cli,
}

/// What kind of value a setting holds, and so which control it gets.
#[derive(Debug, PartialEq, Eq)]
pub enum FieldKind {
    /// A folder path.
    Folder,
    /// A file path with one of these extensions: lowercase, with the dot.
    File {
        /// The extensions the file may have.
        extensions: &'static [&'static str],
    },
    /// Free text.
    Text,
    /// One file decoder handler id, chosen from the registry.
    Handler,
    /// Several of the values `scan` finds; the key absent means all of them, an explicit list means those.
    Scan {
        /// The settings the scan reads, whose change invalidates its result.
        depends_on: &'static [&'static str],
    },
}

/// How a setting is given on the command line.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cli {
    /// A positional argument, in field order.
    Positional,
    /// A `--name` flag; a `Scan` flag takes a comma-separated list.
    Flag(&'static str),
}

/// One value a scan found.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Choice {
    /// What is stored in the settings.
    pub value: String,
    /// What the user reads.
    pub label: String,
    /// A second line about the value, such as its size; may be empty.
    pub detail: String,
}

impl ResolverSchema {
    /// Whether `settings` fit the schema: required keys are present and values have the right type (text, or a list
    /// of text for a `Scan` field). The error names the key. Keys the schema does not declare are not checked.
    pub fn validate(&self, settings: &Settings) -> Result<(), UserError> {
        for field in self.fields {
            match settings.get(field.key) {
                None | Some(Value::Null) if field.required => {
                    return Err(UserError::new("Required", field.key));
                }
                None | Some(Value::Null) => {}
                Some(value) => check_type(field, value)?,
            }
        }
        Ok(())
    }
}

fn check_type(field: &Field, value: &Value) -> Result<(), UserError> {
    let valid = match (&field.kind, value) {
        (FieldKind::Scan { .. }, Value::Array(items)) => items.iter().all(Value::is_string),
        (FieldKind::Scan { .. }, _) => false,
        (_, Value::String(text)) => !field.required || !text.is_empty(),
        (_, _) => false,
    };
    if valid {
        return Ok(());
    }
    let reason = match field.kind {
        FieldKind::Scan { .. } => "Must be a list of names",
        _ if value.is_string() => "Must not be empty",
        _ => "Must be text",
    };
    Err(UserError::new(reason, field.key))
}
