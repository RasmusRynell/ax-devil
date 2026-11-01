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

/// One setting of a resolver.
#[derive(Serialize, Deserialize, TS)]
pub struct FieldInfo {
    /// The key in the settings.
    pub key: String,
    /// What the user reads.
    pub label: String,
    /// What kind of value it holds, and so which control it gets.
    pub kind: FieldKindWire,
    /// Whether resolving fails without it.
    pub required: bool,
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
    /// One file decoder handler id, chosen from `HandlersInfo.decoders`.
    Handler,
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
