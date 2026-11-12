//! Load-time recovery of playback and view defaults only; device appearance is read by the frontend.

use crate::Setting;
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
