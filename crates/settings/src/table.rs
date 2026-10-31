//! The one declaration of every setting. Defaults, parsing and validation are generated from it.

use serde_json::Value;

/// What a setting holds, and its default.
pub enum Kind {
    /// On or off.
    Bool {
        /// The value when the config has none.
        default: bool,
    },
    /// One of a closed set of names.
    Choice {
        /// Every accepted name.
        options: &'static [&'static str],
        /// The value when the config has none; one of `options`.
        default: &'static str,
    },
}

/// One declared setting.
pub struct Setting {
    /// Dotted path in the config file, such as `ui.theme`.
    pub key: &'static str,
    /// What the setting holds.
    pub kind: Kind,
    /// Whether a change applies without a restart.
    pub live: bool,
    /// The name the settings dialog shows.
    pub label: &'static str,
}

/// Every setting the app has. Scope is not declared yet: all of these are app preferences.
pub const TABLE: &[Setting] = &[
    Setting {
        key: "ui.theme",
        kind: Kind::Choice {
            options: &["auto", "light", "dark"],
            default: "auto",
        },
        live: true,
        label: "Theme",
    },
    Setting {
        key: "ui.quick_setup_done",
        kind: Kind::Bool { default: false },
        live: true,
        label: "Quick Setup done",
    },
];

impl Setting {
    /// The value used when the config has none.
    pub fn default_value(&self) -> Value {
        match self.kind {
            Kind::Bool { default } => Value::Bool(default),
            Kind::Choice { default, .. } => Value::String(default.into()),
        }
    }

    /// Accepts `value` only if it has this setting's type and is one of its options.
    pub fn validate(&self, value: &Value) -> Result<(), String> {
        match (&self.kind, value) {
            (Kind::Bool { .. }, Value::Bool(_)) => Ok(()),
            (Kind::Bool { .. }, _) => Err("expected true or false".into()),
            (Kind::Choice { options, .. }, Value::String(name))
                if options.contains(&name.as_str()) =>
            {
                Ok(())
            }
            (Kind::Choice { options, .. }, _) => {
                Err(format!("expected one of {}", options.join(", ")))
            }
        }
    }
}
