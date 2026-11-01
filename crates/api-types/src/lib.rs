//! Types the backend sends to the frontend, exported to TypeScript by `export-types`.

mod error;
mod mirror;
pub mod playback;
mod session;
mod view;
pub mod workspace;

pub use error::{Audience, ErrorCode, ErrorParams, ErrorWire, project};
pub use mirror::InvalidItemId;
pub use session::*;
pub use view::*;

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use ts_rs::TS;

/// Raised when a route changes in a way an older desktop shell or frontend cannot use.
pub const API_VERSION: u32 = 5;

/// Raised when the desktop shell's own interface (the preload bridge and its IPC) changes in a way a frontend served
/// by a remote backend cannot use. The shell loads a remote backend only when its [`AppInfo::shell`] matches.
pub const SHELL_API: u32 = 2;

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

/// Every setting by dotted key, such as `"ui.theme"`. `PUT /api/settings` replaces all of them; the keys and their
/// allowed values are declared once, in the settings crate.
#[derive(Serialize, Deserialize, TS)]
pub struct Settings(
    #[ts(type = "Record<string, string | boolean | number>")]
    pub  BTreeMap<String, serde_json::Value>,
);

/// What a failed request returns: the reason, shown where the user acted, and what it is about.
#[derive(Serialize, TS)]
pub struct ApiError {
    /// What is wrong, in words for the user.
    pub reason: String,
    /// The setting key or file the reason is about.
    pub location: String,
}

/// What the sidebar shows; part of the view a session shares.
#[derive(Serialize, Deserialize, TS, Clone, Copy, Debug, Default, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum SidebarView {
    /// The whole Workspace.
    #[default]
    Workspace,
    /// Live streams.
    Live,
    /// Offline videos.
    Offline,
    /// Bookmarks.
    Bookmarks,
    /// Plugins.
    Plugins,
    /// The session this client is in, and the sessions it can watch.
    Sessions,
}
