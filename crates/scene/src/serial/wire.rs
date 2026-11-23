//! Small measurement structs that read and write identically; the larger shapes are in `write.rs` and `read.rs`.

use serde::{Deserialize, Serialize};

use crate::{GeographicCoordinates, ImageVelocity, SphericalCoordinates, WorldVelocity};

macro_rules! wire_struct {
    ($($wire:ident <-> $model:ident { $($field:ident),* })*) => {$(
        #[derive(Serialize, Deserialize)]
        pub(super) struct $wire { $($field: f64,)* }

        impl From<$model> for $wire {
            fn from(model: $model) -> Self {
                Self { $($field: model.$field,)* }
            }
        }

        impl From<$wire> for $model {
            fn from(wire: $wire) -> Self {
                Self { $($field: wire.$field,)* }
            }
        }
    )*};
}

wire_struct! {
    ImageVelocityWire <-> ImageVelocity { vx, vy }
    WorldVelocityWire <-> WorldVelocity { pitch, speed, yaw }
    SphericalWire <-> SphericalCoordinates { distance, azimuth, elevation }
}

/// Geographic coordinates; the elevation is absent when unknown.
#[derive(Serialize, Deserialize)]
pub(super) struct GeographicWire {
    latitude: f64,
    longitude: f64,
    elevation: Option<f64>,
}

impl From<GeographicCoordinates> for GeographicWire {
    fn from(model: GeographicCoordinates) -> Self {
        Self {
            latitude: model.latitude,
            longitude: model.longitude,
            elevation: model.elevation,
        }
    }
}

impl From<GeographicWire> for GeographicCoordinates {
    fn from(wire: GeographicWire) -> Self {
        Self {
            latitude: wire.latitude,
            longitude: wire.longitude,
            elevation: wire.elevation,
        }
    }
}
