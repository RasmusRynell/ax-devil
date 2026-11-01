//! The one declaration of every setting. Defaults, parsing and validation are generated from it.

use serde_json::Value;

/// Shown in place of a literal secret; sending it back keeps the stored secret.
pub const MASK: &str = "********";

/// Who a setting belongs to, which decides who may read and change it on a backend that several people use.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scope {
    /// How one person's app behaves, such as the theme.
    Personal,
    /// What the server works with: device and broker defaults, folders, cache.
    Server,
}

impl Scope {
    /// Every scope, for the person who owns the backend.
    pub const ALL: [Self; 2] = [Self::Personal, Self::Server];
}

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
    /// The name `auto` (the default), or a whole number from `min` to `max`.
    AutoOrWhole {
        /// The smallest accepted number.
        min: u64,
        /// The largest accepted number.
        max: u64,
    },
    /// A credential: a literal, which is shown masked, or a `$NAME` reference to an environment variable, which is
    /// shown as written. Empty by default.
    Secret,
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
    /// Who the setting belongs to.
    pub scope: Scope,
}

/// Every setting the app has.
pub const TABLE: &[Setting] = &[
    Setting {
        key: "ui.theme",
        kind: Kind::Choice {
            options: &["auto", "light", "dark"],
            default: "auto",
        },
        live: true,
        label: "Theme",
        scope: Scope::Personal,
    },
    Setting {
        key: "ui.quick_setup_done",
        kind: Kind::Bool { default: false },
        live: true,
        label: "Quick Setup done",
        scope: Scope::Personal,
    },
    Setting {
        key: "playback.video_cache",
        // Auto, or MiB shared by every open video. The upper bound is 1 TiB.
        kind: Kind::AutoOrWhole {
            min: 256,
            max: 1024 * 1024,
        },
        live: true,
        label: "Memory for video caching (MiB)",
        scope: Scope::Server,
    },
];

impl Setting {
    /// The value used when the config has none.
    pub fn default_value(&self) -> Value {
        match self.kind {
            Kind::Bool { default } => Value::Bool(default),
            Kind::Choice { default, .. } => Value::String(default.into()),
            Kind::AutoOrWhole { .. } => Value::String("auto".into()),
            Kind::Secret => Value::String(String::new()),
        }
    }

    /// `value` as shown to a person: a literal secret is masked, everything else as written.
    pub fn shown(&self, value: &Value) -> Value {
        match (&self.kind, value) {
            (Kind::Secret, Value::String(text)) if !text.is_empty() && !text.starts_with('$') => {
                Value::String(MASK.into())
            }
            _ => value.clone(),
        }
    }

    /// Whether a person sent back the masked secret, which means "unchanged".
    pub fn is_masked(&self, value: &Value) -> bool {
        matches!(self.kind, Kind::Secret) && value == MASK
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
            (Kind::AutoOrWhole { .. }, Value::String(name)) if name == "auto" => Ok(()),
            (Kind::AutoOrWhole { min, max }, value) => match value.as_u64() {
                Some(number) if (*min..=*max).contains(&number) => Ok(()),
                _ => Err(format!(
                    "expected auto or a whole number from {min} to {max}"
                )),
            },
            (Kind::Secret, Value::String(_)) => Ok(()),
            (Kind::Secret, _) => Err("expected text".into()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_literal_secret_is_masked_and_a_reference_is_shown_as_written() {
        let secret = Setting {
            key: "broker.password",
            kind: Kind::Secret,
            live: false,
            label: "Password",
            scope: Scope::Server,
        };
        assert_eq!(secret.shown(&"hunter2".into()), MASK);
        assert_eq!(secret.shown(&"$BROKER_PASSWORD".into()), "$BROKER_PASSWORD");
        assert_eq!(secret.shown(&"".into()), "");
        assert!(secret.is_masked(&MASK.into()));
        assert!(!TABLE[0].is_masked(&MASK.into()));
    }
}
