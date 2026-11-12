//! Preference recovery and service-startup validation of storage roots; device appearance belongs to the frontend.

use crate::{Error, Setting, Settings, file};
use serde_json::{Value, json};

/// Recover only preferences whose Python readers tolerate malformed saved values.
pub(crate) fn recover(setting: &Setting, raw: &Value) -> Option<Value> {
    match setting.key {
        "playback.video_cache" | "overlays.default_sync" | "overlays.default_object_filter" => {
            Some(setting.kind.default_value())
        }
        "overlays.default_sticky" => {
            // Python accepts extra fields and a missing timeout, and clamps finite opacity. Invalid
            // enabled/timeout/opacity types discard the whole preference, not individual neighbors.
            let normalized = raw
                .get("opacity")
                .and_then(Value::as_f64)
                .filter(|opacity| opacity.is_finite())
                .map(|opacity| {
                    json!({
                        "enabled": raw.get("enabled"),
                        "timeout_ms": raw.get("timeout_ms"),
                        "opacity": opacity.clamp(0.0, 1.0),
                    })
                });
            Some(
                normalized
                    .filter(|value| setting.kind.validate(value).is_ok())
                    .unwrap_or_else(|| setting.kind.default_value()),
            )
        }
        _ => None,
    }
}

/// Storage roots must never silently fall back when their reference cannot be used.
fn storage(
    setting: &Setting,
    raw: &Value,
    lookup: &dyn Fn(&str) -> Option<String>,
) -> Result<(), String> {
    let expanded = setting.expand(raw, lookup);
    let text = expanded.as_str().ok_or_else(|| format!("Invalid storage directory: expected a folder path. Change {} in this configuration file.", setting.key))?;
    if (text.is_empty() && setting.holds_reference(raw))
        || (!text.is_empty() && !std::path::Path::new(text).is_absolute())
    {
        // A misplaced reference may name a credential: report its name, never its contents.
        return Err(format!(
            "Invalid storage directory: {raw} {}. Change {} in this configuration file{} to an absolute folder path.",
            if text.is_empty() {
                "is empty or unset"
            } else {
                "does not resolve to an absolute folder path"
            },
            setting.key,
            raw.as_str()
                .and_then(crate::reference)
                .map_or_else(String::new, |name| format!(" or set {name}"))
        ));
    }
    Ok(())
}

impl Settings {
    /// Refuses unusable storage roots before starting services; independent commands can load settings without using them.
    pub fn validate_storage(&self) -> Result<(), Error> {
        let state = self.state();
        for setting in state.rows().filter(|setting| is_storage(setting)) {
            let raw = &state.values[setting.key];
            storage(setting, raw, &*state.lookup)
                .map_err(|reason| file::fail(self.path(), format!("{}: {reason}", setting.key)))?;
        }
        Ok(())
    }
}

pub(crate) fn is_storage(setting: &Setting) -> bool {
    matches!(setting.key, "storage.cache_dir" | "storage.catalog_dir")
}
