//! Wire types for the decoders and playlist resolvers the app knows.

use crate::ErrorWire;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use ts_rs::TS;

/// The body of `GET /api/handlers`.
#[derive(Serialize, Deserialize, TS)]
pub struct HandlersInfo {
    /// The overlay file decoders.
    pub decoders: Vec<DecoderInfo>,
    /// The playlist resolvers.
    pub resolvers: Vec<ResolverInfo>,
}

/// An overlay file decoder.
#[derive(Serialize, Deserialize, TS)]
pub struct DecoderInfo {
    /// The handler id that selects the decoder.
    pub handler: String,
    /// What the user reads.
    pub display_name: String,
    /// One sentence on what it reads; absent when it has none.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub description: Option<String>,
    /// The file name endings it reads: lowercase, with the dot. Empty means any file.
    pub extensions: Vec<String>,
    /// The options it takes, in the order they are shown; empty for a decoder without any.
    pub options: Vec<FieldInfo>,
    /// Whether it can decode a live stream's overlay, not only a file.
    pub live: bool,
}

/// The body of `GET /api/plugins`: the built-in plugins, then the installed ones, whether or not they loaded.
#[derive(Clone, Default, PartialEq, Serialize, Deserialize, TS)]
pub struct PluginsInfo {
    /// The built-ins, then one per registry entry in install order.
    pub plugins: Vec<PluginInfo>,
    /// Why the registry itself could not be used, in which case no installed plugin loaded.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub problem: Option<String>,
}

/// The body of `POST /api/plugins/install`.
#[derive(Serialize, Deserialize, TS)]
pub struct InstallRequest {
    /// The absolute path of the plugin's folder, or of its `ax-devil-plugin.json`.
    pub path: String,
}

/// The body of `POST /api/plugins/remove`.
#[derive(Serialize, Deserialize, TS)]
pub struct RemoveRequest {
    /// The id of an installed plugin, or the absolute path of its manifest or folder.
    pub target: String,
}

/// The body of `POST /api/plugins/settings`.
#[derive(Serialize, Deserialize, TS)]
pub struct SettingsRequest {
    /// The id of an installed plugin, or the absolute path of its manifest or folder.
    pub plugin: String,
    /// The plugin's settings, checked against its form; replaces the stored ones.
    #[ts(type = "Record<string, unknown>")]
    pub values: serde_json::Map<String, serde_json::Value>,
}

/// The body of `POST /api/plugins/update`.
#[derive(Serialize, Deserialize, TS)]
pub struct UpdateRequest {
    /// The id of an installed plugin, or the absolute path of its manifest or folder.
    pub target: String,
}

/// One plugin.
#[derive(Clone, PartialEq, Serialize, Deserialize, TS)]
pub struct PluginInfo {
    /// Where it comes from.
    pub source: PluginSource,
    /// Whether it loaded.
    pub status: PluginStatus,
}

/// Where a plugin comes from.
#[derive(Clone, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum PluginSource {
    /// It ships with the app.
    BuiltIn,
    /// The user installed it.
    Installed {
        /// The manifest file the registry names.
        manifest: String,
        /// The folder the manifest is in.
        folder: String,
    },
}

/// Whether a plugin can be used.
#[derive(Clone, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum PluginStatus {
    /// It provides handlers.
    Loaded {
        /// The plugin id.
        id: String,
        /// What the user reads.
        name: String,
        /// Free text from the manifest; the app's version for a built-in.
        version: String,
        /// What it provides, in the order it lists them, then what it lists that this build cannot use.
        rows: Vec<ContributionRow>,
        /// The form of the plugin's own settings; empty when it has none.
        settings: Vec<FieldInfo>,
        /// The settings the plugin runs with: the stored values with the defaults filled in.
        #[ts(type = "Record<string, unknown>")]
        values: serde_json::Map<String, serde_json::Value>,
        /// The pages it shows.
        views: Vec<ViewInfo>,
        /// The menu entries and shortcuts it adds.
        commands: Vec<CommandInfo>,
        /// The lane decoders and tasks it provides, which take lanes and options.
        operations: Vec<OperationInfo>,
    },
    /// It provides none.
    Failed {
        /// What is wrong, naming the manifest field when there is one.
        reason: String,
    },
}

/// One thing a plugin lists: a handler, or one it lists that this build cannot use.
#[derive(Clone, PartialEq, Serialize, Deserialize, TS)]
pub struct ContributionRow {
    /// The manifest `kind`: `decoder`, `playlist_resolver`, `task`, `view` or `command`; for a skipped row whatever
    /// the manifest says.
    pub kind: String,
    /// The handler id; empty for a skipped row that has none.
    pub id: String,
    /// What the user reads.
    pub name: String,
    /// What else tells it apart: a decoder's file extensions, a view's place, a command's target. May be empty.
    pub detail: String,
    /// Why it is not used; absent for a handler that runs.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub skipped: Option<String>,
}

/// A page a plugin shows.
#[derive(Clone, PartialEq, Serialize, Deserialize, TS)]
pub struct ViewInfo {
    /// The view id, unique within its plugin.
    pub id: String,
    /// What the user reads.
    pub title: String,
    /// The name of an icon.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub icon: Option<String>,
    /// Where it is shown: `sidebar` or `video`.
    pub place: String,
    /// Where the page is served, `/views/<token>/<document>`. The token lasts until the plugins reload.
    pub url: String,
}

/// A menu entry and shortcut a plugin adds.
#[derive(Clone, PartialEq, Serialize, Deserialize, TS)]
pub struct CommandInfo {
    /// `<plugin id>.<command id>`.
    pub id: String,
    /// What the user reads.
    pub title: String,
    /// What it applies to: `app`, `item` or `lane`.
    pub on: String,
    /// The default key, such as `Ctrl+Alt+E`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub key: Option<String>,
    /// The id of the plugin's own decoder, task or view it runs.
    pub runs: String,
}

/// A lane decoder or task a plugin provides.
#[derive(Clone, PartialEq, Serialize, Deserialize, TS)]
pub struct OperationInfo {
    /// The handler id.
    pub id: String,
    /// `decoder` or `task`.
    pub kind: String,
    /// What the user reads.
    pub name: String,
    /// `(key, label)` of each lane it reads, in order.
    pub lanes: Vec<(String, String)>,
    /// Whether it is also given the video.
    pub video: bool,
    /// The options it takes, in the order they are shown.
    pub options: Vec<FieldInfo>,
}

/// A playlist resolver and the form its settings take.
#[derive(Serialize, Deserialize, TS)]
pub struct ResolverInfo {
    /// The id the resolver is named by.
    pub id: String,
    /// What the user reads.
    pub display_name: String,
    /// One sentence on what the resolver does.
    pub description: String,
    /// The name of the installed plugin that provides it; absent for a built-in.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub plugin: Option<String>,
    /// The settings, in the order they are shown.
    pub fields: Vec<FieldInfo>,
}

/// One setting of a resolver or plugin, or option of a decoder or task.
#[derive(Clone, PartialEq, Serialize, Deserialize, TS)]
pub struct FieldInfo {
    /// The key in the settings.
    pub key: String,
    /// What the user reads.
    pub label: String,
    /// What kind of value it holds, and so which control it gets.
    pub kind: FieldKindWire,
    /// Whether resolving fails without it, once the default is filled in.
    pub required: bool,
    /// The value used when none is given; absent when there is none.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(type = "unknown", optional)]
    pub default: Option<serde_json::Value>,
}

/// What kind of value a setting holds.
#[derive(Clone, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "kind")]
pub enum FieldKindWire {
    /// A folder path.
    Folder,
    /// A file path with one of these extensions.
    File {
        /// Lowercase, with the dot.
        extensions: Vec<String>,
    },
    /// Free text.
    Text,
    /// A whole number of at least `min`.
    Integer {
        /// The smallest value allowed.
        min: i64,
    },
    /// One file decoder handler id, chosen from `HandlersInfo.decoders`.
    Handler,
    /// The options of the decoder chosen in the `Handler` field `handler_field`: a map under this key, edited with
    /// that decoder's `DecoderInfo.options`.
    DecoderOptions {
        /// The key of the `Handler` field.
        handler_field: String,
    },
    /// Several of the values a scan finds; absent means all of them. With `count` the scan only reports how many it
    /// finds, the key holds no value and is never sent.
    Scan {
        /// The settings the scan reads, whose change invalidates its result.
        depends_on: Vec<String>,
        /// What a count-only scan says; absent for a scan whose values are picked.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(optional)]
        count: Option<ScanCountInfo>,
    },
}

/// The sentences of a count-only scan.
#[derive(Clone, PartialEq, Serialize, Deserialize, TS)]
pub struct ScanCountInfo {
    /// Shown while a setting the scan reads is empty.
    pub missing: String,
    /// Shown when the scan found something; `{count}` stands for how many.
    pub found: String,
    /// Shown when the scan found nothing.
    pub none: String,
}

/// The body of `POST /api/resolvers/{id}/scan`.
#[derive(Serialize, Deserialize, TS)]
pub struct ScanRequest {
    /// The `Scan` field to find values for.
    pub field: String,
    /// The settings entered so far.
    #[ts(type = "Record<string, unknown>")]
    pub settings: serde_json::Map<String, serde_json::Value>,
}

/// The body of `POST /api/resolvers/{id}/resolve`.
#[derive(Serialize, Deserialize, TS)]
pub struct ResolveRequest {
    /// The Session of the window that asks; only a seat of that Session can add the resolved item.
    pub session: String,
    /// The settings the resolver's schema declares.
    #[ts(type = "Record<string, unknown>")]
    pub settings: serde_json::Map<String, serde_json::Value>,
}

/// The answer to a resolve: the resolved item, held by the backend until a `DocCommand::AddResolved` adds it.
#[derive(Serialize, Deserialize, TS)]
pub struct ResolvedInfo {
    /// The item's id.
    #[ts(type = "string")]
    pub item: foundation::ItemId,
}

/// The body of `POST /api/tasks/{id}/run`: the lanes of one entry, by the key the task reads them under.
#[derive(Serialize, Deserialize, TS)]
pub struct TaskRunRequest {
    /// The Session of the window that asks.
    pub session: String,
    /// The item that holds the entry.
    pub item: String,
    /// The entry whose lanes the task reads.
    pub entry: String,
    /// The slot of the lane given to each input key the task declares.
    pub lanes: BTreeMap<String, String>,
    /// Whether the task is given the entry's video; it must match what the task declares.
    pub video: bool,
    /// The options the task declares.
    #[ts(type = "Record<string, unknown>")]
    pub options: serde_json::Map<String, serde_json::Value>,
}

/// How far a task is, as it last said.
#[derive(Serialize, Deserialize, TS)]
pub struct TaskProgressInfo {
    /// Units finished.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional, type = "number")]
    pub done: Option<u64>,
    /// Units in all, when the task knows.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional, type = "number")]
    pub total: Option<u64>,
    /// What it is doing now.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub text: Option<String>,
}

/// One line of the answer to a task run, a stream of JSON lines: any number of `progress`, then one `done` or
/// `error`.
#[derive(Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum TaskLine {
    /// The task reported how far it is.
    Progress(TaskProgressInfo),
    /// The task finished.
    Done {},
    /// The task did not finish.
    Error(ErrorWire),
}

/// The query of `GET /api/lanes/scenes`: up to [`MAX_LANE_SAMPLES`] samples of one lane of an entry, from sample
/// number `from`.
#[derive(Serialize, Deserialize, TS)]
pub struct LaneScenesQuery {
    /// The Session of the window that asks.
    pub session: String,
    /// The item that holds the entry.
    pub item: String,
    /// The entry.
    pub entry: String,
    /// The lane's slot.
    pub slot: String,
    /// The number of the first sample.
    pub from: u32,
    /// How many samples; at most [`MAX_LANE_SAMPLES`].
    pub count: u32,
}

/// The most samples one `GET /api/lanes/scenes` answers.
pub const MAX_LANE_SAMPLES: u32 = 2_000;

/// One value a scan found.
#[derive(Serialize, Deserialize, TS)]
pub struct ChoiceInfo {
    /// What is stored in the settings.
    pub value: String,
    /// What the user reads.
    pub label: String,
    /// A second line about the value; may be empty.
    pub detail: String,
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<HandlersInfo>();
    types.visit::<DecoderInfo>();
    types.visit::<PluginsInfo>();
    types.visit::<InstallRequest>();
    types.visit::<RemoveRequest>();
    types.visit::<UpdateRequest>();
    types.visit::<SettingsRequest>();
    types.visit::<PluginInfo>();
    types.visit::<PluginSource>();
    types.visit::<PluginStatus>();
    types.visit::<ContributionRow>();
    types.visit::<ViewInfo>();
    types.visit::<CommandInfo>();
    types.visit::<OperationInfo>();
    types.visit::<ResolverInfo>();
    types.visit::<FieldInfo>();
    types.visit::<FieldKindWire>();
    types.visit::<ScanCountInfo>();
    types.visit::<ScanRequest>();
    types.visit::<ResolveRequest>();
    types.visit::<ResolvedInfo>();
    types.visit::<ChoiceInfo>();
    types.visit::<TaskRunRequest>();
    types.visit::<TaskProgressInfo>();
    types.visit::<TaskLine>();
    types.visit::<LaneScenesQuery>();
}
