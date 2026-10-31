//! Types the backend sends to the frontend, exported to TypeScript by `export-types`.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use ts_rs::TS;

/// Raised when a route changes in a way an older desktop shell or frontend cannot use.
pub const API_VERSION: u32 = 1;

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

/// Every setting by dotted key, such as `"ui.theme"`. `PUT /api/settings` replaces all of them; the keys and their
/// allowed values are declared once, in the settings crate.
#[derive(Serialize, Deserialize, TS)]
pub struct Settings(
    #[ts(type = "Record<string, string | boolean>")] pub BTreeMap<String, serde_json::Value>,
);

/// What a failed request returns: the reason, shown where the user acted, and what it is about.
#[derive(Serialize, TS)]
pub struct ApiError {
    /// What is wrong, in words for the user.
    pub reason: String,
    /// The setting key or file the reason is about.
    pub location: String,
}
