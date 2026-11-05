//! What a setting holds: its type, default and the check a value must pass.

use serde::Serialize;
use serde_json::{Value, json};
use std::path::Path;

/// The longest a kept sample may stay, in milliseconds, which the playback backend enforces.
const MAX_STICKY_MS: u64 = 100_000;

/// The furthest back a previous sample may be, in milliseconds, as the lane menu offers.
const MAX_TOLERANCE_MS: u64 = 100_000;

/// How time-keyed samples are matched in a new view; the value of a [`Kind::Sync`] setting.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct SyncValue {
    /// How far back a previous sample may be, in milliseconds; `None` matches only a sample at the frame's time.
    pub tolerance_ms: Option<u64>,
}

impl SyncValue {
    /// The setting as JSON, which is also how the config file stores it.
    fn to_json(self) -> Value {
        json!({"tolerance_ms": self.tolerance_ms})
    }

    /// The value `value` holds, if it is an object of exactly `tolerance_ms`, null or in range.
    fn read(value: &Value) -> Result<Self, String> {
        let expected = format!(
            "expected tolerance_ms to be null or a whole number from 0 to {MAX_TOLERANCE_MS}"
        );
        let fields = value
            .as_object()
            .filter(|fields| fields.len() == 1)
            .ok_or("expected tolerance_ms")?;
        let tolerance_ms = match fields.get("tolerance_ms") {
            Some(Value::Null) => None,
            Some(tolerance) => Some(
                tolerance
                    .as_u64()
                    .filter(|ms| *ms <= MAX_TOLERANCE_MS)
                    .ok_or(expected)?,
            ),
            None => return Err("expected tolerance_ms".into()),
        };
        Ok(Self { tolerance_ms })
    }
}

/// How sticky overlays start in a new view; the value of a [`Kind::Sticky`] setting.
#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct StickyValue {
    /// Whether samples are kept.
    pub enabled: bool,
    /// How old a kept sample may be, in milliseconds; `None` is without limit.
    pub timeout_ms: Option<u64>,
    /// How strongly a kept sample is drawn, from 0 to 1.
    pub opacity: f64,
}

impl StickyValue {
    /// The setting as JSON, which is also how the config file stores it.
    fn to_json(self) -> Value {
        json!({"enabled": self.enabled, "timeout_ms": self.timeout_ms, "opacity": self.opacity})
    }

    /// The value `value` holds, if it is an object of exactly `enabled`, `timeout_ms` and `opacity` in range.
    fn read(value: &Value) -> Result<Self, String> {
        let fields = value
            .as_object()
            .filter(|fields| fields.len() == 3)
            .ok_or("expected enabled, timeout_ms and opacity")?;
        let enabled = fields
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or("expected enabled to be true or false")?;
        let timeout_ms = match fields.get("timeout_ms") {
            Some(Value::Null) => None,
            Some(timeout) => Some(timeout.as_u64().filter(|ms| *ms <= MAX_STICKY_MS).ok_or(
                format!(
                    "expected timeout_ms to be null or a whole number from 0 to {MAX_STICKY_MS}"
                ),
            )?),
            None => return Err("expected enabled, timeout_ms and opacity".into()),
        };
        let opacity = fields
            .get("opacity")
            .and_then(Value::as_f64)
            .filter(|opacity| (0.0..=1.0).contains(opacity))
            .ok_or("expected opacity to be a number from 0 to 1")?;
        Ok(Self {
            enabled,
            timeout_ms,
            opacity,
        })
    }
}

/// What a setting holds, and its default.
#[derive(Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Kind {
    /// On or off.
    Bool {
        /// The value when the config has none.
        default: bool,
    },
    /// How time-keyed samples are matched in a new view.
    Sync {
        /// The value when the config has none. Must equal `overlay_sync::Tolerance::default()`, which a lane the
        /// backend opens starts with.
        default: SyncValue,
    },
    /// How sticky overlays start in a new view.
    Sticky {
        /// The value when the config has none. Must equal `overlay_sync::Sticky::default()`, which a lane the
        /// backend opens starts with, plus full opacity.
        default: StickyValue,
    },
    /// One of a closed set of names.
    Choice {
        /// Every accepted `(value, label)`.
        options: &'static [(&'static str, &'static str)],
        /// Whether the dialog shows the options as a segmented control, which suits a few modes of one thing.
        toggle: bool,
        /// The value when the config has none; one of the option values.
        default: &'static str,
    },
    /// The name `auto` (the default), or a whole number from `min` to `max`.
    AutoOrWhole {
        /// The smallest accepted number.
        min: u64,
        /// The largest accepted number.
        max: u64,
    },
    /// A whole number from `min` to `max`.
    Whole {
        /// The smallest accepted number.
        min: u64,
        /// The largest accepted number.
        max: u64,
        /// The value when the config has none; within `min` to `max`.
        default: u64,
    },
    /// Any text, including none.
    Text {
        /// The value when the config has none.
        default: &'static str,
    },
    /// A folder the user picked: an absolute path once a leading `~` is the home folder, or empty for the app's
    /// standard folder. Never checked against the disk, so an unplugged drive does not stop startup.
    Folder,
    /// A list of records, each an object that has exactly these text fields.
    Records {
        /// The names of the fields every record has.
        fields: &'static [&'static str],
        /// The records when the config has none, each the values of `fields` in order.
        default: &'static [&'static [&'static str]],
    },
}

impl Kind {
    /// The value used when the config has none.
    pub fn default_value(&self) -> Value {
        match self {
            Self::Bool { default } => Value::Bool(*default),
            Self::Sync { default } => default.to_json(),
            Self::Sticky { default } => default.to_json(),
            Self::Choice { default, .. } | Self::Text { default } => {
                Value::String((*default).into())
            }
            Self::Whole { default, .. } => Value::from(*default),
            Self::AutoOrWhole { .. } => Value::String("auto".into()),
            Self::Folder => Value::String(String::new()),
            Self::Records { fields, default } => Value::Array(
                default
                    .iter()
                    .map(|values| {
                        Value::Object(
                            fields
                                .iter()
                                .zip(*values)
                                .map(|(field, value)| ((*field).to_owned(), Value::from(*value)))
                                .collect(),
                        )
                    })
                    .collect(),
            ),
        }
    }

    /// Whether `value` is the default. A number is compared by what it is, so `1` and `1.0` are the same opacity.
    pub fn is_default(&self, value: &Value) -> bool {
        match self {
            Self::Sticky { default } => StickyValue::read(value).is_ok_and(|read| read == *default),
            _ => *value == self.default_value(),
        }
    }

    /// Accepts `value` only if it has this kind's type and is one of its options. The reason never contains the value.
    pub fn validate(&self, value: &Value) -> Result<(), String> {
        match (self, value) {
            (Self::Bool { .. }, Value::Bool(_)) | (Self::Text { .. }, Value::String(_)) => Ok(()),
            (Self::Bool { .. }, _) => Err("expected true or false".into()),
            (Self::Sync { .. }, value) => SyncValue::read(value).map(|_| ()),
            (Self::Sticky { .. }, value) => StickyValue::read(value).map(|_| ()),
            (Self::Text { .. }, _) => Err("expected text".into()),
            (Self::Choice { options, .. }, Value::String(name))
                if options.iter().any(|(option, _)| option == name) =>
            {
                Ok(())
            }
            (Self::Choice { options, .. }, _) => {
                let names: Vec<&str> = options.iter().map(|(option, _)| *option).collect();
                Err(format!("expected one of {}", names.join(", ")))
            }
            (Self::Folder, Value::String(path))
                if path.is_empty() || Path::new(path).is_absolute() =>
            {
                Ok(())
            }
            (Self::Folder, _) => Err("expected an absolute folder path".into()),
            (Self::Whole { min, max, .. }, value) => match value.as_u64() {
                Some(number) if (*min..=*max).contains(&number) => Ok(()),
                _ => Err(format!("expected a whole number from {min} to {max}")),
            },
            (Self::Records { fields, .. }, Value::Array(records)) => {
                let fits = |record: &Value| {
                    record.as_object().is_some_and(|record| {
                        record.len() == fields.len()
                            && fields
                                .iter()
                                .all(|field| record.get(*field).is_some_and(Value::is_string))
                    })
                };
                if records.iter().all(fits) {
                    Ok(())
                } else {
                    Err(format!(
                        "expected a list of records with the text fields {}",
                        fields.join(", ")
                    ))
                }
            }
            (Self::Records { .. }, _) => Err("expected a list".into()),
            (Self::AutoOrWhole { .. }, Value::String(name)) if name == "auto" => Ok(()),
            (Self::AutoOrWhole { min, max }, value) => match value.as_u64() {
                Some(number) if (*min..=*max).contains(&number) => Ok(()),
                _ => Err(format!(
                    "expected auto or a whole number from {min} to {max}"
                )),
            },
        }
    }
}
