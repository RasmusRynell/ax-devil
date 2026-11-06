//! What a stored value means where it is used: a `$NAME` reference replaced by the variable's value, a number read
//! from it, a folder's leading `~` replaced by the home folder.

use crate::{env, kind::Kind, table::Setting};
use serde_json::Value;
use std::path::Path;

/// `path` with a leading `~` or `~/` replaced by the home folder `lookup` names; anything else, or a missing home
/// folder, leaves it as written.
fn home_expanded(path: &str, lookup: &dyn Fn(&str) -> Option<String>) -> String {
    let rest = match path.strip_prefix('~') {
        Some("") => "",
        Some(rest) if rest.starts_with('/') => rest.trim_start_matches('/'),
        _ => return path.to_owned(),
    };
    let Some(home) = ["HOME", "USERPROFILE"]
        .into_iter()
        .find_map(lookup)
        .filter(|home| !home.is_empty())
    else {
        return path.to_owned();
    };
    Path::new(&home).join(rest).to_string_lossy().into_owned()
}

impl Setting {
    /// `value` as it is used. In a row that takes references, a whole-value `$NAME` is replaced by what `lookup`
    /// gives for it, empty when unset; in a whole-number row the text is read as the number it holds, else left as
    /// text for the kind to refuse. A folder's leading `~` becomes the home folder.
    pub(crate) fn expand(&self, value: &Value, lookup: &dyn Fn(&str) -> Option<String>) -> Value {
        let reference = value.as_str().and_then(env::reference).filter(|_| self.env);
        let expanded = match (reference, &self.kind) {
            (Some(name), Kind::Whole { .. }) => {
                let text = lookup(name).unwrap_or_default();
                text.trim()
                    .parse::<u64>()
                    .map_or(Value::String(text), Value::from)
            }
            (Some(name), _) => Value::String(lookup(name).unwrap_or_default()),
            (None, _) => value.clone(),
        };
        match (&self.kind, &expanded) {
            (Kind::Folder, Value::String(path)) => Value::String(home_expanded(path, lookup)),
            _ => expanded,
        }
    }

    /// `value` as it is used (see [`expand`](Self::expand)); a value the setting cannot hold, such as a reference to
    /// a missing variable, is logged and the default is used instead, and a reference to text that gives nothing is
    /// logged and is empty text. The log never contains the value.
    pub(crate) fn used(&self, value: &Value, lookup: &dyn Fn(&str) -> Option<String>) -> Value {
        self.used_logging(value, lookup, true)
    }

    /// [`used`](Self::used) without the log, for what is shown or looked at rather than used.
    pub(crate) fn used_quietly(
        &self,
        value: &Value,
        lookup: &dyn Fn(&str) -> Option<String>,
    ) -> Value {
        self.used_logging(value, lookup, false)
    }

    fn used_logging(
        &self,
        value: &Value,
        lookup: &dyn Fn(&str) -> Option<String>,
        log: bool,
    ) -> Value {
        let used = self.expand(value, lookup);
        if self.kind.validate(&used).is_err() {
            if log {
                tracing::warn!(
                    "{} does not resolve to a valid value; using the default",
                    self.key
                );
            }
            return self.kind.default_value();
        }
        if log && used.as_str() == Some("") && self.holds_reference(value) {
            tracing::warn!("{} refers to a variable that is unset or empty", self.key);
        }
        used
    }

    /// Whether `value` is a whole-value `$NAME` reference in a row that takes them.
    pub(crate) fn holds_reference(&self, value: &Value) -> bool {
        self.env && value.as_str().and_then(env::reference).is_some()
    }

    /// Accepts `value` if it fits the kind once expanded (see [`expand`](Self::expand)), so what is checked is what
    /// will be used. A reference to a folder must give one. The reason never contains the value.
    pub(crate) fn check(
        &self,
        value: &Value,
        lookup: &dyn Fn(&str) -> Option<String>,
    ) -> Result<(), String> {
        let reference = value.as_str().and_then(env::reference).filter(|_| self.env);
        let expanded = self.expand(value, lookup);
        let checked = match (&self.kind, reference, expanded.as_str()) {
            (Kind::Folder, Some(_), Some("")) => Err("expected a non-empty folder path".into()),
            // A choice is read when used, where what is not one of the options gives the default, as in the reference.
            (Kind::Choice { .. }, Some(_), _) => Ok(()),
            _ => self.kind.validate(&expanded),
        };
        checked.map_err(|reason| match reference {
            Some(name) => format!("{reason} (from ${name})"),
            None => reason,
        })
    }
}
