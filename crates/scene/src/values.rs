use std::{fmt, sync::Arc};

use bytes::Bytes;
use foundation::MediaTime;

use crate::{BoundingBox, Name, SceneError};

/// The class name of an unknown class id `N` is `?N`; this prefix marks it.
pub const UNMAPPED_CLASS: &str = "?";

/// What the source calls an entity; cheap to clone, so events and relations share it.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct EntityId(Arc<str>);

impl EntityId {
    /// An id from non-empty text.
    pub fn new(id: impl Into<Arc<str>>) -> Result<Self, SceneError> {
        let id = id.into();
        if id.is_empty() {
            return Err(SceneError::EmptyEntityId);
        }
        Ok(Self(id))
    }

    /// The id as text.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl From<i64> for EntityId {
    fn from(id: i64) -> Self {
        Self(id.to_string().into())
    }
}

impl From<u64> for EntityId {
    fn from(id: u64) -> Self {
        Self(id.to_string().into())
    }
}

/// What kind of thing an observation shows: `car`, `person` or any other name a source uses.
///
/// The constants are a convenience for names several decoders share, not a closed set.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ClassType(Name);

impl ClassType {
    /// A human.
    pub const HUMAN: Self = Self::from_static("human");
    /// A vehicle.
    pub const VEHICLE: Self = Self::from_static("vehicle");
    /// A vehicle that is none of the more specific kinds.
    pub const VEHICLE_OTHER: Self = Self::from_static("vehicle_other");
    /// A car.
    pub const CAR: Self = Self::from_static("car");
    /// A bus.
    pub const BUS: Self = Self::from_static("bus");
    /// A truck.
    pub const TRUCK: Self = Self::from_static("truck");
    /// A motorbike or similar.
    pub const BIKE: Self = Self::from_static("bike");
    /// A bicycle.
    pub const BICYCLE: Self = Self::from_static("bicycle");
    /// An animal.
    pub const ANIMAL: Self = Self::from_static("animal");
    /// A head.
    pub const HEAD: Self = Self::from_static("head");
    /// A license plate.
    pub const LICENSE_PLATE: Self = Self::from_static("license_plate");
    /// A face.
    pub const FACE: Self = Self::from_static("face");
    /// A bag.
    pub const BAG: Self = Self::from_static("bag");
    /// Something the source could not name.
    pub const UNKNOWN: Self = Self::from_static("unknown");

    /// A class from non-empty text.
    pub fn new(name: impl Into<Name>) -> Result<Self, SceneError> {
        let name = name.into();
        if name.is_empty() {
            return Err(SceneError::EmptyClassType);
        }
        Ok(Self(name))
    }

    /// A class from non-empty static text, for constant tables only: an empty `name` panics, at compile time when
    /// the call is in a `const` or `static` item.
    pub const fn from_static(name: &'static str) -> Self {
        assert!(!name.is_empty(), "class type cannot be empty");
        Self(Name::Borrowed(name))
    }

    /// The class of an id the source's table lacks: `?N`.
    pub fn unmapped(id: i64) -> Self {
        Self(Name::Owned(format!("{UNMAPPED_CLASS}{id}")))
    }

    /// Whether the class is one made by [`ClassType::unmapped`].
    pub fn is_unmapped(&self) -> bool {
        self.0.starts_with(UNMAPPED_CLASS)
    }

    /// The class name.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

macro_rules! text_id {
    ($($type:ty),*) => {$(
        impl fmt::Display for $type {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str(self.as_str())
            }
        }

        impl PartialEq<str> for $type {
            fn eq(&self, other: &str) -> bool {
                self.as_str() == other
            }
        }

        impl PartialEq<&str> for $type {
            fn eq(&self, other: &&str) -> bool {
                self.as_str() == *other
            }
        }
    )*};
}

text_id!(EntityId, ClassType);

macro_rules! finite_number {
    ($($(#[$doc:meta])* $type:ident, $what:literal;)*) => {$(
        $(#[$doc])*
        #[derive(Clone, Copy, Debug, PartialEq, PartialOrd)]
        pub struct $type(f64);

        impl $type {
            /// The number `value`, which must be finite.
            pub fn new(value: f64) -> Result<Self, SceneError> {
                if !value.is_finite() {
                    return Err(SceneError::NonFinite { what: $what, value });
                }
                Ok(Self(value))
            }

            /// The number as the source gave it.
            pub fn value(self) -> f64 {
                self.0
            }

            /// The number as plain text with two decimals, such as `0.87`.
            pub fn text(self) -> String {
                format!("{:.2}", self.0)
            }

            /// The number when it lies within 0 to 1.
            pub fn fraction(self) -> Option<f64> {
                (0.0..=1.0).contains(&self.0).then_some(self.0)
            }
        }
    )*};
}

finite_number! {
    /// A decoder's own score for a class: any finite number, in whatever range the decoder uses.
    Score, "score";
    /// How likely a source says a class is right: any finite number, in whatever range the source uses.
    Confidence, "confidence";
}

/// Whether an entity is moving.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MotionState {
    /// Moving.
    Moving,
    /// Standing still.
    Stationary,
    /// The source does not say.
    Unknown,
}

/// A picture of an entity at one time.
#[derive(Clone, Debug, PartialEq)]
pub struct Image {
    /// When the picture was taken.
    pub timestamp: MediaTime,
    /// Where in the frame the picture was cut from.
    pub bounding_box: BoundingBox,
    /// The encoded picture.
    pub data: Bytes,
}

/// Speed in the image, in normalized units per second.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ImageVelocity {
    /// Horizontal speed.
    pub vx: f64,
    /// Vertical speed.
    pub vy: f64,
}

/// Velocity in the world.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct WorldVelocity {
    /// Climb angle.
    pub pitch: f64,
    /// Speed.
    pub speed: f64,
    /// Heading.
    pub yaw: f64,
}

/// A place on Earth.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct GeographicCoordinates {
    /// Degrees north.
    pub latitude: f64,
    /// Degrees east.
    pub longitude: f64,
    /// Metres above sea level, when known.
    pub elevation: Option<f64>,
}

/// A place relative to the camera.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SphericalCoordinates {
    /// Distance from the camera.
    pub distance: f64,
    /// Angle around the vertical axis.
    pub azimuth: f64,
    /// Angle above the horizon.
    pub elevation: f64,
}

/// What a source measured about an observation; each part is absent when the source lacks it.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Measurements {
    /// Velocity in the image.
    pub image_velocity: Option<ImageVelocity>,
    /// Velocity in the world.
    pub world_velocity: Option<WorldVelocity>,
    /// Place on Earth.
    pub geographic: Option<GeographicCoordinates>,
    /// Place relative to the camera.
    pub world: Option<SphericalCoordinates>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn score_and_confidence_take_any_finite_number() {
        for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(Score::new(bad).is_err() && Confidence::new(bad).is_err());
        }
        let (inside, outside) = (Score::new(0.874), Confidence::new(2.5));
        assert_eq!(
            inside.map(|s| (s.text(), s.fraction())),
            Ok(("0.87".into(), Some(0.874)))
        );
        assert_eq!(
            outside.map(|c| (c.text(), c.fraction())),
            Ok(("2.50".into(), None))
        );
    }

    #[test]
    fn ids_and_classes_are_never_empty_and_compare_with_text() {
        assert_eq!(EntityId::new(""), Err(SceneError::EmptyEntityId));
        assert_eq!(ClassType::new(""), Err(SceneError::EmptyClassType));
        assert_eq!(EntityId::from(12_i64), "12");
        assert_eq!(ClassType::unmapped(99), "?99");
        assert!(ClassType::unmapped(99).is_unmapped() && !ClassType::CAR.is_unmapped());
    }
}
