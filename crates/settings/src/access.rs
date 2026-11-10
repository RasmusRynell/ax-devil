//! What a save may change without Full access: nothing that reaches outside ax-devil.

use crate::{Change, Error, State, env};
use serde_json::Value;

/// Every text in `value`, whole or inside a list of records.
fn texts(value: &Value) -> Vec<&str> {
    match value {
        Value::String(text) => vec![text],
        Value::Array(items) => items.iter().flat_map(texts).collect(),
        _ => Vec::new(),
    }
}

impl State {
    /// Refuses `change` as a save by a caller without Full access when it would change the value of a `full_access`
    /// setting, or put a `$NAME` reference into a setting that takes them, other than one that setting already holds.
    /// A save that sends the stored values again passes.
    pub fn refuse_without_full_access(&self, change: &Change) -> Result<(), Error> {
        let forbidden = |key: &str, reason: &str| Error {
            location: key.to_owned(),
            reason: reason.to_owned(),
            invalid: false,
            forbidden: true,
        };
        for setting in self.table {
            let (Some(new), Some(stored)) =
                (change.values.get(setting.key), self.values.get(setting.key))
            else {
                continue;
            };
            if setting.full_access && new != stored {
                return Err(forbidden(setting.key, "needs full access to change"));
            }
            if setting.env {
                let held = texts(stored);
                if texts(new)
                    .into_iter()
                    .any(|text| env::reference(text).is_some() && !held.contains(&text))
                {
                    return Err(forbidden(
                        setting.key,
                        "needs full access to refer to an environment variable",
                    ));
                }
            }
        }
        Ok(())
    }
}
