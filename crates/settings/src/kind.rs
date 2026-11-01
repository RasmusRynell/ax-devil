//! What a setting holds: its type, default and the check a value must pass.

use serde::Serialize;
use serde_json::Value;

/// What a setting holds, and its default.
#[derive(Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Kind {
    /// On or off.
    Bool {
        /// The value when the config has none.
        default: bool,
    },
    /// One of a closed set of names.
    Choice {
        /// Every accepted `(value, label)`.
        options: &'static [(&'static str, &'static str)],
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
    /// Any text, including none.
    Text {
        /// The value when the config has none.
        default: &'static str,
    },
}

impl Kind {
    /// The value used when the config has none.
    pub fn default_value(&self) -> Value {
        match self {
            Self::Bool { default } => Value::Bool(*default),
            Self::Choice { default, .. } | Self::Text { default } => {
                Value::String((*default).into())
            }
            Self::AutoOrWhole { .. } => Value::String("auto".into()),
        }
    }

    /// Accepts `value` only if it has this kind's type and is one of its options. The reason never contains the value.
    pub fn validate(&self, value: &Value) -> Result<(), String> {
        match (self, value) {
            (Self::Bool { .. }, Value::Bool(_)) | (Self::Text { .. }, Value::String(_)) => Ok(()),
            (Self::Bool { .. }, _) => Err("expected true or false".into()),
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
