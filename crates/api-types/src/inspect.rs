//! Wire types of an object's inspection: the answer to `Inspect` on the playback socket. The groups and origins of
//! its fields are `scene::inspection`'s own types.

use scene::inspection::{FieldGroup, FieldOrigin, Leaf};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// One leaf of an object's data.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct FieldWire {
    /// Stable digest of the full segmented path, independent of its display preview.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub identity: Option<String>,
    /// The section the field is listed in.
    pub group: FieldGroup,
    /// Who produced the field; only in the Debug group.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub origin: Option<FieldOrigin>,
    /// The field's place in the data; a list index is its decimal digits.
    pub path: Vec<String>,
    /// The leaf: an integer exactly, a float, text, true or false, or null.
    pub value: Leaf,
    /// Ordinal and full UTF-8 size when only a text preview is included.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional, type = "[number, number]")]
    pub cut: Option<[u64; 2]>,
    /// Ordinal and full dotted path size when the path exceeds a page.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional, type = "[number, number]")]
    pub path_cut: Option<[u64; 2]>,
    /// The colour the value is the score of, as red, green and blue; for an entry of a colour attribute.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub rgb: Option<[u8; 3]>,
    /// What to show in place of the value: a score or confidence to two decimals. Copying takes the value.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub text: Option<String>,
    /// The value of a score, a confidence or a colour's score within 0 to 1, drawn as a bar.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub fraction: Option<f64>,
}

/// One leaf of an object a search compares, at the key a search names it by.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct ItemWire {
    /// The lower-case group and the path, dotted: `debug.vendor.id`; a colour adds `rgb.r`, `rgb.g` and `rgb.b`.
    pub key: String,
    /// The leaf.
    pub value: Leaf,
}

/// The leaves of one object a search asked for.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct ObjectItemsWire {
    /// The object's id.
    pub entity: String,
    /// Its leaves at the paths asked for; repeated keys keep every value.
    pub items: Vec<ItemWire>,
}

/// What the backend knows of one object of one sample.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum InspectionWire {
    /// The object's fields, in display order.
    Found {
        /// At most 4096 fields and 8 MiB of paths/text; large texts carry a preview and retrieval metadata.
        fields: Vec<FieldWire>,
        /// Socket-local continuation capability and the next page's ordinal.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(optional)]
        continuation: Option<InspectionContinuation>,
        /// Whether fields were left out.
        truncated: bool,
    },
    /// Inspection exceeds a bounded preparation limit; the reason is safe for an authorized viewer.
    Refused {
        /// Reason shown by the Inspector.
        reason: String,
    },
    /// The sample has no object with this id.
    Absent,
    /// The viewer does not hold this sample under this revision, or its authority no longer reaches it.
    Unavailable,
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<InspectionContinuation>();
    types.visit::<InspectionRead>();
    types.visit::<InspectionRelease>();
    types.visit::<InspectionPin>();
    types.visit::<InspectionReadResult>();
    types.visit::<FieldWire>();
    types.visit::<ItemWire>();
    types.visit::<ObjectItemsWire>();
    types.visit::<InspectionWire>();
}

/// A capability valid only in the socket that issued it, for one held entity/sample revision.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct InspectionContinuation {
    /// Opaque capability; never grants authority beyond the requesting socket.
    pub token: String,
    /// Next field ordinal, absent on the final page.
    pub next: Option<u32>,
}

/// The inspection to release: its capability, or its initial request before the capability arrived.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(untagged)]
pub enum InspectionRelease {
    /// Release only this socket's matching capability.
    Token {
        /// Capability returned by the first inspection reply.
        token: String,
    },
    /// Cancel only the matching initial request, including while its read is running.
    Request {
        /// Generation of the initial `Inspect`, not a continuation's correlation number.
        request: u32,
    },
}

/// One page or text chunk requested through the playback socket.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct InspectionRead {
    /// Correlation number owned by the client's inspection ordering.
    pub request: u32,
    /// Capability from the initial inspection.
    pub token: String,
    /// Field ordinal for text retrieval; absent for a page.
    pub field: Option<u32>,
    /// Retrieve the complete Raw section as JSON, independently of the current page.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub raw: Option<bool>,
    /// Resolve these pinned identities independently of the visible page (at most 64).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub pins: Option<Vec<InspectionPin>>,
    /// Retrieve the dotted path instead of the value.
    #[serde(default)]
    pub path: bool,
    /// Field ordinal for a page, UTF-8 byte offset for a text chunk.
    pub offset: u32,
}

/// The result of one bounded continuation request.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum InspectionReadResult {
    /// A replacement window of fields.
    Page {
        /// Bounded fields in stable order.
        fields: Vec<FieldWire>,
        /// Next page ordinal, absent at the end.
        next: Option<u32>,
    },
    /// Pinned fields found in one bounded scan window; absence is final only when `next` is absent.
    Pins {
        /// Requested pin index and its preview; paths cap at 64 KiB and texts at 16 KiB.
        fields: Vec<(u32, FieldWire)>,
        /// Next scan ordinal, absent when every pin has been resolved or the object ends.
        next: Option<u32>,
    },
    /// A UTF-8-aligned piece of a value.
    Chunk {
        /// At most 64 KiB of UTF-8 text.
        text: String,
        /// Full UTF-8 byte count.
        total: u32,
        /// Next byte offset, absent at the end.
        next: Option<u32>,
    },
    /// No data; the cursor is stale, authority ended, or the value exceeds the copy ceiling.
    Refused {
        /// Safe reason shown in the inspector.
        reason: String,
    },
}

/// Inspection limits exported from the owning Scene module to the client.
pub const CONSTANTS: &[(&str, u64, &str)] = &[
    (
        "MAX_INSPECTION_REQUEST_BYTES",
        MAX_INSPECTION_REQUEST_BYTES as u64,
        "Maximum UTF-8 bytes of a playback socket request, including JSON framing.",
    ),
    (
        "MAX_INSPECTION_CHUNK_BYTES",
        scene::inspection::MAX_INSPECTION_CHUNK_BYTES as u64,
        "Maximum UTF-8 bytes in one inspected text chunk.",
    ),
    (
        "MAX_PIN_PATH_BYTES",
        scene::inspection::MAX_PIN_PATH_BYTES as u64,
        "Maximum UTF-8 bytes of saved pin path segments, excluding display separators.",
    ),
    (
        "MAX_INSPECTION_QUERY_PATHS",
        MAX_INSPECTION_QUERY_PATHS as u64,
        "Maximum field paths in one Frame-scope inspection search.",
    ),
    (
        "MAX_INSPECTION_QUERY_PATH_BYTES",
        MAX_INSPECTION_QUERY_PATH_BYTES as u64,
        "Maximum UTF-8 bytes of one Frame-scope inspection query path.",
    ),
    (
        "MAX_INSPECTION_ENTITY_BYTES",
        MAX_INSPECTION_ENTITY_BYTES as u64,
        "Maximum UTF-8 bytes of one inspected entity id.",
    ),
    (
        "MAX_INSPECTION_PINS",
        MAX_INSPECTION_PINS as u64,
        "The maximum pinned inspection fields.",
    ),
    (
        "MAX_COPY_BYTES",
        scene::inspection::MAX_COPY_BYTES as u64,
        "The maximum UTF-8 bytes assembled for one explicit inspection copy.",
    ),
];

/// A pinned field's stable identity; legacy pins name the segmented path instead of its digest.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct InspectionPin {
    /// Section containing the field.
    pub group: FieldGroup,
    /// Full segmented-path SHA-256 when available.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub identity: Option<String>,
    /// Legacy segmented path; digest-based requests send no path text.
    #[serde(default)]
    pub path: Vec<String>,
}
/// The most pin previews retained by one Inspector.
pub const MAX_INSPECTION_PINS: usize = 64;

/// The most query paths in one Frame-scope search.
pub const MAX_INSPECTION_QUERY_PATHS: usize = 64;
/// The most UTF-8 bytes in one Frame-scope query path.
pub const MAX_INSPECTION_QUERY_PATH_BYTES: usize = 1024;
/// The most UTF-8 bytes in an inspected entity id.
pub const MAX_INSPECTION_ENTITY_BYTES: usize = 1024;

/// The socket's message and frame limit, including JSON framing.
pub const MAX_INSPECTION_REQUEST_BYTES: usize = 64 << 10;
