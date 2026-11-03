//! Wire types for the decoders and playlist resolvers the app knows.

use serde::{Deserialize, Serialize};
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
    /// The file extensions it reads: lowercase, with the dot.
    pub extensions: Vec<String>,
    /// The options it takes, in the order they are shown; empty for a decoder without any.
    pub options: Vec<FieldInfo>,
    /// The id of the plugin that provides it; absent for the built-in decoders.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub plugin: Option<String>,
}

/// The body of `GET /api/plugins`: the plugins the user installed, whether or not they loaded.
#[derive(Clone, Default, Serialize, Deserialize, TS)]
pub struct PluginsInfo {
    /// One per registry entry, in install order.
    pub plugins: Vec<PluginInfo>,
    /// Why the registry itself could not be used, in which case no plugin loaded.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub problem: Option<String>,
}

impl PluginsInfo {
    /// Whether `plugin` is the id of a loaded plugin.
    pub fn provides(&self, plugin: &str) -> bool {
        self.plugins
            .iter()
            .any(|entry| matches!(&entry.status, PluginStatus::Loaded { id, .. } if id == plugin))
    }
}

/// One installed plugin.
#[derive(Clone, Serialize, Deserialize, TS)]
pub struct PluginInfo {
    /// The manifest file the registry names.
    pub manifest: String,
    /// The folder the manifest is in.
    pub folder: String,
    /// Whether it loaded.
    pub status: PluginStatus,
}

/// Whether an installed plugin can be used.
#[derive(Clone, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum PluginStatus {
    /// It provides handlers.
    Loaded {
        /// The plugin id.
        id: String,
        /// What the user reads.
        name: String,
        /// Free text from the manifest.
        version: String,
        /// The file extensions its decoders read: lowercase, with the dot.
        extensions: Vec<String>,
    },
    /// It provides none.
    Failed {
        /// What is wrong, naming the manifest field when there is one.
        reason: String,
    },
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
    /// The settings, in the order they are shown.
    pub fields: Vec<FieldInfo>,
}

/// One setting of a resolver or option of a decoder.
#[derive(Serialize, Deserialize, TS)]
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
#[derive(Serialize, Deserialize, TS)]
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
    /// Several of the values a scan finds; absent means all of them.
    Scan {
        /// The settings the scan reads, whose change invalidates its result.
        depends_on: Vec<String>,
    },
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
