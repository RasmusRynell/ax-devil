//! How a secret setting is shown and kept: the literal in it is masked for a person. A text secret is changed only
//! by naming it in a patch; a record in a list of logins is sent whole, and its masked password is put back from the
//! stored value.

use crate::{env, table::Setting};
use serde_json::Value;

/// Shown in place of a literal secret; a masked password sent back in a record keeps the stored one.
pub const MASK: &str = "********";

/// The field of a record in a secret list that holds the secret.
const PASSWORD: &str = "password";

/// Whether two records of a secret list are the same login: every field but the password is equal.
fn same_login(a: &Value, b: &Value) -> bool {
    fn others(record: &Value) -> Option<Vec<(&String, &Value)>> {
        record.as_object().map(|fields| {
            fields
                .iter()
                .filter(|(name, _)| *name != PASSWORD)
                .collect()
        })
    }
    others(a) == others(b)
}

impl Setting {
    /// Whether `text` is the secret itself rather than empty or a `$NAME` reference to it.
    fn literal(&self, text: &str) -> bool {
        !text.is_empty() && !(self.env && env::reference(text).is_some())
    }

    /// `value` as shown to a person: the literal in a secret row is masked, everything else as written. In a secret
    /// list of records the literal in each record's `password` field is masked.
    pub fn shown(&self, value: &Value) -> Value {
        if !self.secret {
            return value.clone();
        }
        match value {
            Value::String(text) if self.literal(text) => Value::String(MASK.into()),
            Value::Array(records) => Value::Array(
                records
                    .iter()
                    .map(|record| {
                        let mut record = record.clone();
                        if let Some(Value::String(password)) = record.get_mut(PASSWORD)
                            && self.literal(password)
                        {
                            *password = MASK.into();
                        }
                        record
                    })
                    .collect(),
            ),
            _ => value.clone(),
        }
    }

    /// What a person's `sent` value means once the masked passwords in it are put back from the `stored` value: a
    /// masked password in a record keeps the stored password of the record at
    /// the same position when its other fields are unchanged, else of an unclaimed record with the same other fields,
    /// else of the unclaimed record at the same position (the user changed another field of it). A stored record
    /// keeps its password for one row only, and a masked secret with nothing to keep is refused rather than stored
    /// empty. So is one whose login is stored more than once while the rows sent for it are not as many: which of the
    /// passwords it stands for cannot be told.
    pub(crate) fn unmasked(&self, sent: &Value, stored: Option<&Value>) -> Result<Value, String> {
        const REENTER: &str = "Re-enter the password; the saved one cannot be kept for this entry.";
        if !self.secret {
            return Ok(sent.clone());
        }
        match (sent, stored) {
            (Value::Array(records), stored) => {
                let before = stored
                    .and_then(Value::as_array)
                    .map_or(&[][..], Vec::as_slice);
                let masked = |record: &Value| record.get(PASSWORD).is_some_and(|p| p == MASK);
                // Several stored records of one login differ only in their passwords, so which one a masked row stands
                // for is known only while the rows of that login keep their number and order: refuse otherwise.
                for record in records.iter().filter(|record| masked(record)) {
                    let stored = before.iter().filter(|old| same_login(old, record)).count();
                    let sent = records.iter().filter(|row| same_login(row, record)).count();
                    if stored > 1 && sent != stored {
                        return Err(match record.get("user").and_then(Value::as_str) {
                            Some(user) => format!("Re-enter the password for {user}."),
                            None => REENTER.into(),
                        });
                    }
                }
                // The stored record each masked row takes its password from.
                let mut taken: Vec<Option<usize>> = vec![None; records.len()];
                let mut claimed = vec![false; before.len()];
                for (row, record) in records.iter().enumerate() {
                    if masked(record) && before.get(row).is_some_and(|old| same_login(old, record))
                    {
                        taken[row] = Some(row);
                        claimed[row] = true;
                    }
                }
                for (row, record) in records.iter().enumerate() {
                    if !masked(record) || taken[row].is_some() {
                        continue;
                    }
                    let same = (0..before.len())
                        .find(|&old| !claimed[old] && same_login(&before[old], record));
                    let moved = Some(row).filter(|&old| claimed.get(old) == Some(&false));
                    if let Some(old) = same.or(moved) {
                        taken[row] = Some(old);
                        claimed[old] = true;
                    }
                }
                records
                    .iter()
                    .zip(&taken)
                    .map(|(record, old)| {
                        let mut record = record.clone();
                        if masked(&record) {
                            let kept = old
                                .and_then(|old| before[old].get(PASSWORD))
                                .ok_or(REENTER)?;
                            record[PASSWORD] = kept.clone();
                        }
                        Ok(record)
                    })
                    .collect::<Result<Vec<_>, &str>>()
                    .map(Value::Array)
                    .map_err(str::to_owned)
            }
            _ => Ok(sent.clone()),
        }
    }
}
