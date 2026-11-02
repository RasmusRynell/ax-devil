//! The Scene model decoders produce and the class filter policy that goes with it.
//!
//! Values are built by value and validated on the way in: leaf values are newtypes whose `new` refuses what the
//! model cannot show, so composites only check the rules between their parts. A [`Scene`] is shared as an `Arc` and
//! never changes after it is built.
//!
//! [`serial`] writes Scenes as JSON (goldens, the plugin wire) and postcard (cache artifacts) from one data model.

mod error;
mod filter;
mod geometry;
pub mod inspection;
mod observation;
mod scene;
pub mod serial;
mod time;
mod value;
mod values;

use std::fmt;

pub use error::SceneError;
pub use filter::{ClassRule, FilterChoice, FilterOption, FilterPolicy, SceneView};
pub use geometry::{BoundingBox, Geometry, Point, Polygon};
pub use observation::{Classification, Observation};
pub use scene::{Entity, EntityRelation, Event, Operation, OperationKind, Scene};
pub use time::{Coverage, MatchBasis, SceneTime, TimeKind, TimeSlice};
pub use value::{Fields, MAX_VALUE_DEPTH, Name, Value};
pub use values::{
    ClassType, Confidence, EntityId, GeographicCoordinates, Image, ImageVelocity, Measurements,
    MotionState, Score, SphericalCoordinates, UNMAPPED_CLASS, WorldVelocity,
};

/// The version of the Scene model: a decoder declares the version it builds Scenes for.
///
/// The major number changes when a value is removed, renamed or loses a meaning or a permission, and the minor number
/// when one is added.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct SceneVersion {
    /// Changes when old decoders' output stops being valid.
    pub major: u16,
    /// Changes when something is added.
    pub minor: u16,
}

/// The version of the Scene model in this build.
pub const SCENE_VERSION: SceneVersion = SceneVersion { major: 4, minor: 0 };

impl SceneVersion {
    /// Whether a decoder built for version `decoder` produces Scenes this version reads: the same major version,
    /// and no newer minor.
    pub fn accepts(self, decoder: SceneVersion) -> bool {
        self.major == decoder.major && decoder.minor <= self.minor
    }
}

impl fmt::Display for SceneVersion {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}.{}", self.major, self.minor)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_decoder_must_match_the_major_version_and_not_be_newer() {
        let app = SceneVersion { major: 4, minor: 1 };
        let version = |major, minor| SceneVersion { major, minor };

        assert!(app.accepts(version(4, 0)) && app.accepts(version(4, 1)));
        assert!(!app.accepts(version(4, 2)) && !app.accepts(version(3, 0)));
        assert_eq!(SCENE_VERSION.to_string(), "4.0");
    }
}
