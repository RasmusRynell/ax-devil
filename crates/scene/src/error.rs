use std::fmt;

use crate::{EntityId, Name};

/// Why a Scene value was refused: words for the user.
#[derive(Clone, Debug, PartialEq)]
#[non_exhaustive]
pub enum SceneError {
    /// An entity id was empty.
    EmptyEntityId,
    /// A class type was empty.
    EmptyClassType,
    /// A relation type was empty.
    EmptyRelationType,
    /// A number that must be finite was NaN or infinite.
    NonFinite {
        /// What the number was, such as `score`.
        what: &'static str,
        /// The number.
        value: f64,
    },
    /// A box's right edge is left of its left edge, or its bottom edge is above its top edge.
    InvertedBox,
    /// A polygon had fewer than three points; carries how many it had.
    TooFewPolygonPoints(usize),
    /// An observation's source data and debug data use the same key.
    DetailKeyOverlap(Name),
    /// Two entities in one Scene have the same id.
    DuplicateEntity(EntityId),
    /// A time is of another kind (frame number or timestamp) than the time it must agree with.
    TimeKindMismatch,
    /// A time slice ends before it starts.
    ReversedTimeSlice,
    /// Serialized Scene data could not be read or written; carries the reason.
    Serial(String),
}

impl fmt::Display for SceneError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyEntityId => f.write_str("entity id cannot be empty"),
            Self::EmptyClassType => f.write_str("class type cannot be empty"),
            Self::EmptyRelationType => f.write_str("relation type cannot be empty"),
            Self::NonFinite { what, value } => write!(f, "{what} must be finite, got {value}"),
            Self::InvertedBox => f.write_str("box maxima must not be below minima"),
            Self::TooFewPolygonPoints(count) => {
                write!(f, "polygon needs at least 3 points, got {count}")
            }
            Self::DetailKeyOverlap(key) => {
                write!(f, "source data and debug data both use the key '{key}'")
            }
            Self::DuplicateEntity(id) => write!(f, "entity id '{id}' appears twice in one scene"),
            Self::TimeKindMismatch => {
                f.write_str("frame numbers and timestamps cannot be mixed in one scene")
            }
            Self::ReversedTimeSlice => f.write_str("time slice ends before it starts"),
            Self::Serial(reason) => write!(f, "scene data: {reason}"),
        }
    }
}

impl std::error::Error for SceneError {}
