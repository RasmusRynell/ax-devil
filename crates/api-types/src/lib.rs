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
pub mod workspace;

pub use error::{Audience, ErrorParams, ErrorWire, project};
pub use session::*;

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use ts_rs::TS;

/// The version of the generated contract: a hash `export-types` computes over every TypeScript file it writes for the
/// frontend (the types, the constants and the settings tables, with their doc comments, so a comment-only edit
/// changes it) and stores in `desktop/generated/api.json`. A change to those changes it with no edit; the route
/// table lives in the root crate, which this one cannot read, so a changed route path or method does not. A client
/// that expects another version refuses the backend.
pub fn api_version() -> &'static str {
    static VERSION: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    VERSION.get_or_init(|| {
        let generated: serde_json::Value =
            serde_json::from_str(include_str!("../../../desktop/generated/api.json"))
                .expect("desktop/generated/api.json is valid JSON");
        generated["apiVersion"]
            .as_str()
            .expect("desktop/generated/api.json has an apiVersion")
            .to_owned()
    })
}

/// Raised when the desktop shell's own interface (the preload bridge and its IPC) changes in a way a frontend served
/// by a remote backend cannot use. The shell loads a remote backend only when its [`AppInfo::shell`] matches.
pub const SHELL_API: u32 = 8;

/// Identifies a running backend, so the desktop shell and the development supervisor reuse only a compatible one.
#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct AppInfo {
    /// Always `"ax-devil"`.
    pub app: String,
    /// The backend API version, see [`api_version`]; a client that expects another one refuses this backend.
    pub api: String,
    /// The package version.
    pub version: String,
    /// Whether this is a development build that serves no frontend.
    pub development: bool,
    /// The backend process id.
    pub pid: u32,
    /// The desktop shell interface this backend's frontend needs; compare with the shell's own version.
    pub shell: u32,
    /// Whether the caller of this request is the Owner.
    pub owner: bool,
    /// Whether the caller is an Owner with Full access.
    pub full_access: bool,
}

/// What a client learns of a secret: whether it is set and, for a `$NAME` reference, its name. Never the literal, its
/// length or a mask. A client changes a secret by naming it in a [`SettingsPatch`].
#[derive(Serialize, TS)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum SecretState {
    /// Nothing is set.
    Unset,
    /// A literal is set.
    Literal,
    /// The secret is read from the environment variable `name`.
    Reference {
        /// The variable's name, without the `$`.
        name: String,
    },
}

/// A login of the `scan.logins` setting as a client reads it.
#[derive(Serialize, TS)]
pub struct LoginView {
    /// Names the login for as long as it is saved; a save refers to it by this.
    pub id: String,
    /// The user name, as written.
    pub user: String,
    /// The password.
    pub password: SecretState,
}

/// A login of the `scan.logins` setting as a client sends it, in the list that is saved whole and in order. With an
/// `id` it is that saved login, and a missing `password` keeps its saved one; without an `id` it is a new login, which
/// has a `password`. An unknown `id` is refused, and a saved login left out is removed.
#[derive(Deserialize, TS)]
pub struct LoginEdit {
    /// The saved login this is.
    #[ts(optional)]
    pub id: Option<String>,
    /// The user name, as written.
    pub user: String,
    /// The new password, as written.
    #[ts(optional)]
    pub password: Option<String>,
}

/// Every setting and shortcut as the app uses them: the body of `GET /api/settings` and the reply to a save. The keys
/// and command ids are declared once, in the settings crate, and exported to `generated/settings.ts`.
#[derive(Serialize, TS)]
pub struct SettingsState {
    /// Rises with every write to the settings; a state with a lower one is older. The `Changed` notice of the
    /// settings topic on the Session socket carries the same number.
    #[ts(type = "number")]
    pub revision: u64,
    /// Every setting by dotted key, such as `"camera.host"`, with the default when unset and a `$NAME` reference as
    /// written. A secret, and the `password` of a login, is a [`SecretState`]: never the literal.
    #[ts(
        type = "Record<SettingKey, string | boolean | number | SecretState | Array<LoginView> | { enabled: boolean; timeout_ms: number | null; opacity: number } | { tolerance_ms: number | null } | { filter: Record<string, boolean>; ranges: Record<\"score\" | \"confidence\", { min: number | null; max: number | null }>; expression: string }>"
    )]
    pub values: BTreeMap<String, serde_json::Value>,
    /// What each non-secret setting among `values` that holds a `$NAME` reference gives as it is used (a number for a
    /// whole-number setting), for a form that starts from the value. Only with Full access, since it is the server's
    /// environment; absent for a setting without a reference.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional, type = "Partial<Record<SettingKey, string | number>>")]
    pub resolved: Option<BTreeMap<String, serde_json::Value>>,
    /// The config file.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub file: Option<SettingsFile>,
    /// The key of every command once the user's changes apply; `null` when unbound.
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

/// The body of `PATCH /api/settings`: the keys to set and to reset. A key that is not named is not touched, so the
/// last write to a key wins. A secret is changed only by naming it; the logins are one list of [`LoginEdit`].
#[derive(Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct SettingsPatch {
    /// The freshly compiled revision when changing the default catalog.
    #[serde(default)]
    #[ts(optional)]
    pub catalog_revision: Option<u32>,
    /// The settings to set by dotted key.
    #[serde(default)]
    #[ts(
        optional,
        type = "Partial<Record<SettingKey, string | boolean | number | Array<LoginEdit> | { enabled: boolean; timeout_ms: number | null; opacity: number } | { tolerance_ms: number | null } | { filter: Record<string, boolean>; ranges: Record<\"score\" | \"confidence\", { min: number | null; max: number | null }>; expression: string }>>"
    )]
    pub set: Option<BTreeMap<String, serde_json::Value>>,
    /// The settings to put back to their default.
    #[serde(default)]
    #[ts(optional, type = "Array<SettingKey>")]
    pub reset: Option<Vec<String>>,
    /// The commands to bind: a key, or `null` to unbind.
    #[serde(default)]
    #[ts(optional, type = "Partial<Record<CommandId, string | null>>")]
    pub shortcuts_set: Option<BTreeMap<String, Option<String>>>,
    /// The commands to put back to their own key.
    #[serde(default)]
    #[ts(optional, type = "Array<CommandId>")]
    pub shortcuts_reset: Option<Vec<String>>,
}

/// Visits every type this crate sends to the frontend, for `export-types`.
pub fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<AppInfo>();
    types.visit::<SettingsState>();
    types.visit::<SettingsFile>();
    types.visit::<SettingsPatch>();
    types.visit::<SecretState>();
    types.visit::<LoginView>();
    types.visit::<LoginEdit>();
    bookmarks::visit_api_types(types);
    catalogs::visit_api_types(types);
    content::visit_api_types(types);
    error::visit_api_types(types);
    handlers::visit_api_types(types);
    inspect::visit_api_types(types);
    live::visit_api_types(types);
    playback::visit_api_types(types);
    render::visit_api_types(types);
    report::visit_api_types(types);
    session::visit_api_types(types);
    workspace::visit_api_types(types);
}
