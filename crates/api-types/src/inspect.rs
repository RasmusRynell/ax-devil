//! Wire types of an object's inspection: the answer to `Inspect` on the playback socket.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Where in the inspector a field is listed; the variants are in display order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum FieldGroupWire {
    /// What the object is: class, confidence, scores.
    Classification,
    /// Where it moves: speed, direction, track.
    Motion,
    /// Where it is: box, polygon.
    Geometry,
    /// What the file and the decoder carried along; each field has an origin.
    Debug,
    /// Everything else the observation holds.
    Raw,
}

/// Who produced a field in the Debug group.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum FieldOriginWire {
    /// The file's own data, as the source wrote it.
    Source,
    /// What the decoder computed from it.
    Decoder,
}

/// One leaf of an object's data.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct FieldWire {
    /// The section the field is listed in.
    pub group: FieldGroupWire,
    /// Who produced the field; only in the Debug group.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub origin: Option<FieldOriginWire>,
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
