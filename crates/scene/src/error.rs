use std::fmt;

use crate::Name;

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
    /// A filter option's id was empty.
    EmptyFilterId,
    /// A filter option's label was empty; carries the option's id.
    EmptyFilterLabel(Name),
    /// Two filter options of one policy have the same id.
    DuplicateFilterId(Name),
    /// A time is of another kind (frame number or timestamp) than the clock it must be on.
    TimeKindMismatch,
    /// An observation has neither a frame number nor a timestamp.
    NoObservationTime,
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
            Self::EmptyFilterId => f.write_str("filter option id cannot be empty"),
            Self::EmptyFilterLabel(id) => write!(f, "filter option '{id}' has an empty label"),
            Self::DuplicateFilterId(id) => write!(f, "filter option id '{id}' appears twice"),
            Self::TimeKindMismatch => f.write_str("a time is not on the scene's clock"),
            Self::NoObservationTime => {
                f.write_str("an observation needs a frame number or a timestamp")
            }
            Self::ReversedTimeSlice => f.write_str("time slice ends before it starts"),
            Self::Serial(reason) => write!(f, "scene data: {reason}"),
        }
    }
}

impl std::error::Error for SceneError {}
