//! A secret is write-only: what a client reads of it says whether it is set and, for a `$NAME` reference, the name,
//! never the literal. A text secret is changed only by naming it in a patch. A list of records (the logins) is saved
//! whole and in order; each record has an `id` the server assigned, and a record sent with its id and no password
//! keeps the stored one.

use crate::{
    env,
    kind::{ID, Kind, fresh_id},
    table::Setting,
};
use serde_json::{Value, json};

/// The field of a record in a secret list that holds the secret.
const PASSWORD: &str = "password";

impl Setting {
    /// What a client learns of the secret `text`: not set, a reference with its name, or a literal and nothing more.
    fn state(&self, text: &str) -> Value {
        if text.is_empty() {
            return json!({"state": "unset"});
        }
        match self.env.then(|| env::reference(text)).flatten() {
            Some(name) => json!({"state": "reference", "name": name}),
            None => json!({"state": "literal"}),
        }
    }

    /// `value` as a client reads it: in a secret row the text, or the `password` of each record, is its state.
    pub fn shown(&self, value: &Value) -> Value {
        if !self.secret {
            return value.clone();
        }
        match value {
            Value::String(text) => self.state(text),
            Value::Array(records) => Value::Array(
                records
                    .iter()
                    .map(|record| {
                        let mut record = record.clone();
                        if let Some(Value::String(password)) = record.get(PASSWORD) {
                            record[PASSWORD] = self.state(password);
                        }
                        record
                    })
                    .collect(),
            ),
            _ => value.clone(),
        }
    }

    /// What a client's `sent` value means given the `stored` one. A list of records is the whole list in its order:
    /// a record with an `id` is that stored record (its fields as sent; a `password` it lacks is the stored one), a
    /// record without one is new and gets an id. An unknown or repeated id is refused.
    pub(crate) fn applied(&self, sent: &Value, stored: Option<&Value>) -> Result<Value, String> {
        let (Kind::Records { .. }, Value::Array(records)) = (&self.kind, sent) else {
            return Ok(sent.clone());
        };
        let before = stored
            .and_then(Value::as_array)
            .map_or(&[][..], Vec::as_slice);
        let mut seen: Vec<&str> = Vec::new();
        let mut kept = Vec::with_capacity(records.len());
        for record in records {
            let Some(fields) = record.as_object() else {
                // Not a record: the check of the result says so.
                kept.push(record.clone());
                continue;
            };
            let mut record = fields.clone();
            match fields.get(ID) {
                None => {
                    record.insert(ID.into(), Value::String(fresh_id()));
                }
                Some(Value::String(id)) => {
                    let old = before
                        .iter()
                        .find(|old| old.get(ID).and_then(Value::as_str) == Some(id))
                        .ok_or("That login is no longer saved.")?;
                    if seen.contains(&id.as_str()) {
                        return Err("A login is listed twice.".into());
                    }
                    seen.push(id);
                    if !fields.contains_key(PASSWORD)
                        && let Some(password) = old.get(PASSWORD)
                    {
                        record.insert(PASSWORD.into(), password.clone());
                    }
                }
                Some(_) => return Err("A login's id is text.".into()),
            }
            kept.push(Value::Object(record));
        }
        Ok(Value::Array(kept))
    }
}
