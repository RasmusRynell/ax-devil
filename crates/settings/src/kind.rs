//! What a setting holds: its type, default and the check a value must pass.

use serde::Serialize;
use serde_json::{Value, json};
use std::{collections::BTreeSet, path::Path};

/// The field of a record that names it, so a save can keep its secret fields without the client holding them.
pub(crate) const ID: &str = "id";

/// An id no other record has: random, so a client holding the id of a removed record never names a later one.
pub(crate) fn fresh_id() -> String {
    let mut bytes = [0u8; 8];
    // Without operating-system randomness there is no sound way to make an id that is not reused.
    getrandom::fill(&mut bytes)
        .unwrap_or_else(|error| panic!("the operating system has no randomness: {error}"));
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

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

/// The most class overrides a saved filter may hold, and the longest class id and expression, as the lane menu offers.
const MAX_FILTER_CLASSES: usize = 1024;
const MAX_CLASS_ID: usize = 256;
const MAX_EXPRESSION: usize = 4096;

/// The object filter a new lane starts with when nothing was saved: no class switched, open ranges, no expression.
/// The default of a [`Kind::ObjectFilter`] setting, whose value has the same shape: `filter` maps class ids to
/// whether they are shown, `ranges` holds the `min` and `max` of `score` and `confidence` (null is open) and
/// `expression` is the filter expression, as a lane holds them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NoFilter;

impl NoFilter {
    /// The setting as JSON, which is also how the config file stores it.
    fn to_json(self) -> Value {
        let open = json!({"min": null, "max": null});
        json!({"filter": {}, "ranges": {"score": open, "confidence": open}, "expression": ""})
    }

    /// Accepts `value` if it is an object filter within the limits above.
    fn read(value: &Value) -> Result<(), String> {
        let fields = value
            .as_object()
            .filter(|fields| fields.len() == 3)
            .ok_or("expected filter, ranges and expression")?;
        let classes = fields
            .get("filter")
            .and_then(Value::as_object)
            .filter(|classes| {
                classes.len() <= MAX_FILTER_CLASSES
                    && classes
                        .iter()
                        .all(|(id, on)| id.len() <= MAX_CLASS_ID && on.is_boolean())
            });
        classes.ok_or("expected filter to map class ids to true or false")?;
        let ranges = fields.get("ranges").and_then(Value::as_object);
        let bounded = |ends: Option<&Value>| {
            ends.and_then(Value::as_object).is_some_and(|ends| {
                ends.len() == 2
                    && ["min", "max"].iter().all(|end| {
                        // Any JSON number: a whole one past i64 is as good a bound as a fraction.
                        ends.get(*end)
                            .is_some_and(|bound| bound.is_null() || bound.is_number())
                    })
            })
        };
        if !ranges.is_some_and(|ranges| {
            ranges.len() == 2
                && ["score", "confidence"]
                    .iter()
                    .all(|m| bounded(ranges.get(*m)))
        }) {
            return Err("expected ranges of score and confidence, each with min and max".into());
        }
        match fields.get("expression").and_then(Value::as_str) {
            Some(text) if text.chars().count() <= MAX_EXPRESSION => Ok(()),
            _ => Err(format!(
                "expected expression to be text of at most {MAX_EXPRESSION} characters"
            )),
        }
    }
}

impl Serialize for NoFilter {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        self.to_json().serialize(serializer)
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
    /// Which objects a new lane shows: the class overrides, score and confidence ranges and expression of its filter.
    ObjectFilter {
        /// The value when the config has none.
        default: NoFilter,
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
    /// The id of an overlay decoder: any text, which the dialog offers the installed live decoders for. A decoder that
    /// is not installed stays the setting, and the Add Live Stream form then has no decoder chosen.
    Decoder {
        /// The value when the config has none.
        default: &'static str,
    },
    /// A folder the user picked: an absolute path once a leading `~` is the home folder, or empty for the app's
    /// standard folder. Never checked against the disk, so an unplugged drive does not stop startup.
    Folder,
    /// A list of records, each an object that has exactly these text fields and an `id` the server assigned, unique in
    /// the list (see `secret.rs`).
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
            Self::ObjectFilter { default } => default.to_json(),
            Self::Choice { default, .. } | Self::Text { default } | Self::Decoder { default } => {
                Value::String((*default).into())
            }
            Self::Whole { default, .. } => Value::from(*default),
            Self::AutoOrWhole { .. } => Value::String("auto".into()),
            Self::Folder => Value::String(String::new()),
            Self::Records { fields, default } => Value::Array(
                default
                    .iter()
                    .enumerate()
                    .map(|(at, values)| {
                        let id = (ID.to_owned(), Value::from(format!("default-{at}")));
                        Value::Object(
                            std::iter::once(id)
                                .chain(fields.iter().zip(*values).map(|(field, value)| {
                                    ((*field).to_owned(), Value::from(*value))
                                }))
                                .collect(),
                        )
                    })
                    .collect(),
            ),
        }
    }

    /// `value` with an id on every record that has none, as a file written before records had ids holds them, and the
    /// default's own ids when the records are the default's. Any other kind is returned as it is.
    pub(crate) fn with_ids(&self, value: Value) -> Value {
        if !matches!(self, Self::Records { .. }) {
            return value;
        }
        let Value::Array(mut records) = value else {
            return value;
        };
        let bare = |records: &[Value]| -> Vec<Value> {
            records
                .iter()
                .map(|record| {
                    let mut record = record.clone();
                    if let Some(fields) = record.as_object_mut() {
                        fields.remove(ID);
                    }
                    record
                })
                .collect()
        };
        if let Value::Array(default) = self.default_value()
            && bare(&records) == bare(&default)
        {
            return Value::Array(default);
        }
        for record in &mut records {
            if let Some(fields) = record.as_object_mut() {
                fields
                    .entry(ID)
                    .or_insert_with(|| Value::String(fresh_id()));
            }
        }
        Value::Array(records)
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
            (Self::Bool { .. }, Value::Bool(_))
            | (Self::Text { .. } | Self::Decoder { .. }, Value::String(_)) => Ok(()),
            (Self::Bool { .. }, _) => Err("expected true or false".into()),
            (Self::Sync { .. }, value) => SyncValue::read(value).map(|_| ()),
            (Self::Sticky { .. }, value) => StickyValue::read(value).map(|_| ()),
            (Self::ObjectFilter { .. }, value) => NoFilter::read(value),
            (Self::Text { .. } | Self::Decoder { .. }, _) => Err("expected text".into()),
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
                        record.len() == fields.len() + 1
                            && record.get(ID).is_some_and(Value::is_string)
                            && fields
                                .iter()
                                .all(|field| record.get(*field).is_some_and(Value::is_string))
                    })
                };
                let ids: BTreeSet<&str> = records
                    .iter()
                    .filter_map(|record| record.get(ID)?.as_str())
                    .collect();
                if records.iter().all(fits) && ids.len() == records.len() {
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
