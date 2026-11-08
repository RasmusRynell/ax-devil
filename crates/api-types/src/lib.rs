//! Types the backend sends to the frontend, exported to TypeScript by `export-types`.

pub mod bookmarks;
pub mod catalogs;
pub mod content;
mod error;
pub mod handlers;
pub mod inspect;
pub mod live;
pub mod playback;
pub mod render;
pub mod report;
mod session;
pub mod window;
pub mod workspace;

pub use error::{Audience, ErrorParams, ErrorWire, project};
pub use session::*;

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use ts_rs::TS;

/// Raised when a route changes in a way an older desktop shell or frontend cannot use, or a client message is added
/// that an older backend would reject (a development backend of this version is reused, an older one is not).
pub const API_VERSION: u32 = 22;

/// Raised when the desktop shell's own interface (the preload bridge and its IPC) changes in a way a frontend served
/// by a remote backend cannot use. The shell loads a remote backend only when its [`AppInfo::shell`] matches.
pub const SHELL_API: u32 = 5;

/// What a caller may do on a backend. Every request is judged by the grants of whoever sent it; they are not roles.
#[derive(Serialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum Grant {
    /// Read and change the Personal settings stored on this backend.
    Preferences,
    /// Read and change the Server settings.
    Configuration,
    /// List shared sessions, own a session and watch any of them.
    Sessions,
    /// Open this machine's own files and videos: the owner's commands that read the host.
    Files,
}

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
    /// The desktop shell interface this backend's frontend needs; compare with the shell's own version.
    pub shell: u32,
    /// What the caller of this request may do.
    pub grants: Vec<Grant>,
}

/// Every setting and shortcut as the app uses them: the body of `GET /api/settings` and the reply to a save. The keys
/// and command ids are declared once, in the settings crate, and exported to `generated/settings.ts`.
#[derive(Serialize, TS)]
pub struct SettingsState {
    /// The settings the caller may read by dotted key, such as `"ui.theme"`, with the default when unset: those of
    /// its scopes, a `$NAME` reference as written and a literal secret masked.
    #[ts(
        type = "Record<SettingKey, string | boolean | number | Array<Record<string, string>> | { enabled: boolean; timeout_ms: number | null; opacity: number } | { tolerance_ms: number | null } | { filter: Record<string, boolean>; ranges: Record<\"score\" | \"confidence\", { min: number | null; max: number | null }>; expression: string }>"
    )]
    pub values: BTreeMap<String, serde_json::Value>,
    /// What each non-secret setting among `values` that holds a `$NAME` reference gives as it is used (a number for a
    /// whole-number setting), for a form that starts from the value. Only for the person at this machine, whose
    /// environment it is; absent for a setting without a reference.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional, type = "Partial<Record<SettingKey, string | number>>")]
    pub resolved: Option<BTreeMap<String, serde_json::Value>>,
    /// The config file, for a caller who may see this machine's files.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub file: Option<SettingsFile>,
    /// The key of every command once the user's changes apply; `null` when unbound. Empty unless the caller holds
    /// the `preferences` grant: shortcuts are Personal.
    #[ts(type = "Record<CommandId, string | null>")]
    pub shortcuts: BTreeMap<String, Option<String>>,
}

/// Where the settings are kept: the file's path on the backend's machine and its format version.
#[derive(Serialize, TS)]
pub struct SettingsFile {
    /// The config file's path.
    pub path: String,
    /// The file format version.
    #[ts(type = "number")]
    pub version: u64,
    /// The log file's path, when this backend writes one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub log: Option<String>,
}

/// The body of `PUT /api/settings`: every setting and shortcut the caller may change, which replace the stored ones.
/// A masked secret sent back keeps the stored secret; a key outside the caller's scopes is refused with 403.
#[derive(Deserialize, TS)]
pub struct SettingsChange {
    /// Every setting of the caller's scopes by dotted key.
    #[ts(
        type = "Record<SettingKey, string | boolean | number | Array<Record<string, string>> | { enabled: boolean; timeout_ms: number | null; opacity: number } | { tolerance_ms: number | null } | { filter: Record<string, boolean>; ranges: Record<\"score\" | \"confidence\", { min: number | null; max: number | null }>; expression: string }>"
    )]
    pub values: BTreeMap<String, serde_json::Value>,
    /// The key of every command; `null` when unbound. Empty unless the caller holds the `preferences` grant.
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
