//! How the rest of the app reads a setting's value from the state: as used, as stored, as written, or listed for a
//! form, each deciding what a `$NAME` reference gives.

use crate::{Kind, State};
use serde_json::Value;
use std::{collections::BTreeMap, path::PathBuf};

impl State {
    /// What each non-secret setting that holds a `$NAME` reference gives as it is used, by dotted key, for
    /// a form that starts from the value rather than the reference. Not logged: nothing is used yet.
    pub fn references(&self) -> BTreeMap<String, Value> {
        self.rows()
            .filter(|setting| !setting.secret)
            .filter_map(|setting| {
                let value = self.values.get(setting.key)?;
                setting.holds_reference(value).then(|| {
                    (
                        setting.key.to_owned(),
                        setting.used_quietly(value, &*self.lookup),
                    )
                })
            })
            .collect()
    }

    /// The value of the declared setting `key` as it is used: a `$NAME` reference in a setting that takes them is
    /// replaced by the variable's value, empty when it is unset (a number in a whole-number setting), and a folder's
    /// leading `~` by the home folder. A reference that gives a value the setting cannot hold is logged and the
    /// default is used instead.
    pub fn resolved(&self, key: &str) -> Option<Value> {
        let setting = self.rows().find(|setting| setting.key == key)?;
        let value = self.values.get(key)?;
        Some(setting.used(value, &*self.lookup))
    }

    /// The value of the declared setting `key` as stored: a `$NAME` reference as written and a literal secret in full,
    /// for copying into something new (a Live Stream Item starts from the camera defaults). Never show it to a person;
    /// [`values`](Self::values) is the shown form. A whole-number setting that holds a reference gives its number
    /// instead (the default when the reference does not resolve), as read when the state was built, so a caller that
    /// reads numbers never meets text.
    pub fn stored(&self, key: &str) -> Option<&Value> {
        self.numbers.get(key).or_else(|| self.values.get(key))
    }

    /// The value of the declared setting `key` as written, never expanded, for a caller whose references must not read
    /// this machine's environment: a `$NAME` stays the text it is, except in a whole-number setting, which cannot hold
    /// text and gives its default.
    pub fn written(&self, key: &str) -> Option<Value> {
        let setting = self.rows().find(|setting| setting.key == key)?;
        let value = self.values.get(key)?;
        let number = matches!(setting.kind, Kind::Whole { .. });
        Some(if number && setting.holds_reference(value) {
            setting.kind.default_value()
        } else {
            value.clone()
        })
    }

    /// The folder a setting of kind `Folder` holds, as [`resolved`](Self::resolved); `None` when it is empty (the
    /// standard folder) or `key` is not such a setting.
    pub fn folder(&self, key: &str) -> Option<PathBuf> {
        let path = self.resolved(key)?;
        let path = path.as_str()?;
        (!path.is_empty()).then(|| PathBuf::from(path))
    }

    /// The number a whole-number setting holds, as [`resolved`](Self::resolved); `None` for `auto`, or when `key` is not
    /// such a setting.
    pub fn auto_or_whole(&self, key: &str) -> Option<u64> {
        self.resolved(key)?.as_u64()
    }
}
