//! What a save may change without Full access: nothing that reaches outside ax-devil.

use crate::{Change, Error, State, env};
use serde_json::Value;

/// Every text in `value` with the name of the record field that holds it; a text outside a record is its own single
/// field, named "".
fn texts<'a>(value: &'a Value, field: &'a str) -> Vec<(&'a str, &'a str)> {
    match value {
        Value::String(text) => vec![(field, text)],
        Value::Array(items) => items.iter().flat_map(|item| texts(item, field)).collect(),
        Value::Object(fields) => fields
            .iter()
            .flat_map(|(name, item)| texts(item, name))
            .collect(),
        _ => Vec::new(),
    }
}

impl State {
    /// Refuses `change` as a save by a caller without Full access when it would change the value of a `full_access`
    /// setting, or put a `$NAME` reference into any setting, other than one that setting already holds.
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
            // Every row, whether or not it expands references today, and what is stored once masked secrets are put
            // back. A reference the row already holds in the same field may be kept, moved between records or reordered.
            let Ok(effective) = setting.unmasked(new, Some(stored)) else {
                continue;
            };
            let held = texts(stored, "");
            if texts(&effective, "")
                .into_iter()
                .any(|at| env::reference(at.1).is_some() && !held.contains(&at))
            {
                return Err(forbidden(
                    setting.key,
                    "needs full access to refer to an environment variable",
                ));
            }
        }
        Ok(())
    }
}
