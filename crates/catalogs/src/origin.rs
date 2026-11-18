//! Where a copy of a built-in catalog came from, and whether its body still is what was copied.
//!
//! A copy records `<id>@<hash>` as its `origin`: the built-in's id and a hash of the built-in's body, which is every
//! top-level member but the descriptive `name`, `description` and `origin`. The copy is modified when its own body
//! no longer hashes the same, so renaming or describing a copy does not change it.

use serde_json::Value;

use crate::{CatalogId, built_in::DOCUMENTS};

/// The top-level members that describe a catalog and are not part of its body.
const DESCRIPTIVE: [&str; 3] = ["name", "description", "origin"];

/// The built-in catalog a user catalog was copied from; information only, nothing acts on it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Origin {
    /// The built-in's name, or its recorded id when the app no longer ships it.
    pub based_on: String,
    /// Whether the catalog's body differs from the built-in's as it was when copied.
    pub modified: bool,
}

impl Origin {
    /// The short text that tells where the catalog came from.
    pub fn note(&self) -> String {
        if self.modified {
            format!("copy of {}, modified", self.based_on)
        } else {
            format!("copy of {}", self.based_on)
        }
    }
}

/// The origin to record in a copy of `source_text` of the built-in `id`, or `None` when the text is not JSON.
pub(crate) fn recorded(id: &str, source_text: &str) -> Option<String> {
    Some(format!("{id}@{}", body_hash(source_text)?))
}

/// The origin that `recorded` made in the catalog `text`, called `origin`; `None` for any other text.
pub(crate) fn read(origin: &str, text: &str) -> Option<Origin> {
    let (id, hash) = origin.rsplit_once('@')?;
    let based_on = match CatalogId::parse(id) {
        Some(CatalogId::BuiltIn(name)) => DOCUMENTS
            .iter()
            .find(|(served, _)| *served == name)
            .and_then(|(_, document)| serde_json::from_str::<Value>(document).ok())
            .and_then(|document| document.get("name")?.as_str().map(str::to_owned)),
        _ => None,
    };
    Some(Origin {
        based_on: based_on.unwrap_or_else(|| id.to_owned()),
        modified: body_hash(text)? != hash,
    })
}

/// A hash of `text`'s body: FNV-1a over its canonical form, so the key order and the layout of the file do not matter.
/// Stable, because it is stored; not a defence against anything.
fn body_hash(text: &str) -> Option<String> {
    let Value::Object(mut document) = serde_json::from_str(text).ok()? else {
        return None;
    };
    for key in DESCRIPTIVE {
        document.remove(key);
    }
    let mut canonical = String::new();
    write_canonical(&Value::Object(document), &mut canonical);
    let hash = canonical
        .bytes()
        .fold(0xcbf2_9ce4_8422_2325_u64, |hash, byte| {
            (hash ^ u64::from(byte)).wrapping_mul(0x0100_0000_01b3)
        });
    Some(format!("{hash:016x}"))
}

/// `value` as compact JSON with the members of every object by key.
fn write_canonical(value: &Value, out: &mut String) {
    match value {
        Value::Object(members) => {
            let mut keys: Vec<&String> = members.keys().collect();
            keys.sort();
            out.push('{');
            for (index, key) in keys.into_iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                out.push_str(&Value::from(key.as_str()).to_string());
                out.push(':');
                write_canonical(&members[key], out);
            }
            out.push('}');
        }
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_canonical(item, out);
            }
            out.push(']');
        }
        scalar => out.push_str(&scalar.to_string()),
    }
}

#[cfg(test)]
mod tests;
