//! Types the backend sends to the frontend, exported to TypeScript by `export-types`.

pub mod playback;
pub mod window;
pub mod workspace;

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use ts_rs::TS;

/// Raised when a route changes in a way an older desktop shell or frontend cannot use.
pub const API_VERSION: u32 = 2;

/// Identifies a running backend, so the desktop shell and the development supervisor reuse only a compatible one.
#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct AppInfo {
    /// Always `"ax-devil"`.
    pub app: String,
    /// The backend API version; a client that expects another one refuses this backend.
    pub api: u32,
    /// The package version.
    pub version: String,
    /// Whether this is a development build that serves no frontend.
    pub development: bool,
    /// The backend process id.
    pub pid: u32,
}

/// Every setting and shortcut as the app uses them: the body of `GET /api/settings` and the reply to a save. The keys
/// and command ids are declared once, in the settings crate, and exported to `generated/settings.ts`.
#[derive(Serialize, TS)]
pub struct SettingsState {
    /// Every declared setting by dotted key, such as `"ui.theme"`, with its default when unset. A `$NAME`
    /// reference is returned as written.
    #[ts(type = "Record<SettingKey, string | boolean | number>")]
    pub values: BTreeMap<String, serde_json::Value>,
    /// The key of every command once the user's changes apply; `null` when unbound.
    #[ts(type = "Record<CommandId, string | null>")]
    pub shortcuts: BTreeMap<String, Option<String>>,
    /// The settings changed since the app started that only apply after a restart.
    #[ts(type = "Array<SettingKey>")]
    pub restart: Vec<String>,
}

/// The body of `PUT /api/settings`: every setting and shortcut, which replace the stored ones.
#[derive(Deserialize, TS)]
pub struct SettingsChange {
    /// Every declared setting by dotted key.
    #[ts(type = "Record<SettingKey, string | boolean | number>")]
    pub values: BTreeMap<String, serde_json::Value>,
    /// The key of every command; `null` when unbound.
    #[ts(type = "Record<CommandId, string | null>")]
    pub shortcuts: BTreeMap<String, Option<String>>,
}

/// What a failed request returns: the reason, shown where the user acted, and what it is about.
#[derive(Serialize, TS)]
pub struct ApiError {
    /// What is wrong, in words for the user.
    pub reason: String,
    /// The setting key (`ui.theme`), `shortcuts.<command id>`, or file the reason is about.
    pub location: String,
}
