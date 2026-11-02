//! Wire types of an object's inspection: the answer to `Inspect` on the playback socket. The groups and origins of
//! its fields are `scene::inspection`'s own types.

use scene::inspection::{FieldGroup, FieldOrigin};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// One leaf of an object's data.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct FieldWire {
    /// The section the field is listed in.
    pub group: FieldGroup,
    /// Who produced the field; only in the Debug group.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub origin: Option<FieldOrigin>,
    /// The field's place in the data; a list index is its decimal digits.
    pub path: Vec<String>,
    /// The leaf: a number, text, true or false, or `null`.
    #[ts(type = "null | boolean | number | string")]
    pub value: serde_json::Value,
}

/// What the backend knows of one object of one sample.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum InspectionWire {
    /// The object's fields, in display order.
    Found {
        /// At most 4096; a text is cut at 4096 characters.
        fields: Vec<FieldWire>,
        /// Whether fields were left out or a text was cut.
        truncated: bool,
    },
    /// The sample has no object with this id.
    Absent,
    /// The viewer does not hold this sample under this revision, or its authority no longer reaches it.
    Unavailable,
}
