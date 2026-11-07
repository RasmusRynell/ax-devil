//! The writing half: borrowed views of the model that serialize without copying it.
//!
//! JSON omits what is absent so documents stay short; postcard is positional, so it writes every field. The read
//! half in `read.rs` declares the same fields in the same order.

use serde::{
    Serialize, Serializer,
    ser::{SerializeSeq, SerializeStruct, SerializeStructVariant},
};

use super::{
    picture::BytesOut,
    value::FieldsOut,
    wire::{GeographicWire, ImageVelocityWire, SphericalWire, WorldVelocityWire},
};
use crate::{
    Classification, Entity, EntityId, EntityRelation, Event, Geometry, Image, Measurements,
    MotionState, Observation, Operation, SCENE_VERSION, Scene, SceneTime, TimeSlice,
};

/// A borrowed model value being written.
pub(super) struct W<'a, T: ?Sized>(pub &'a T);

/// A borrowed list of model values being written.
struct List<'a, T>(&'a [T]);

impl<T> Serialize for List<'_, T>
where
    for<'b> W<'b, T>: Serialize,
{
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.collect_seq(self.0.iter().map(W))
    }
}

/// Entity ids as a list of text.
struct Ids<'a>(&'a [EntityId]);

impl Serialize for Ids<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.collect_seq(self.0.iter().map(EntityId::as_str))
    }
}

/// A document of Scenes.
pub(super) struct Document<'a>(pub &'a [Scene]);

impl Serialize for Document<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut doc = serializer.serialize_struct("Document", 2)?;
        doc.serialize_field("scene_version", &SCENE_VERSION.to_string())?;
        doc.serialize_field("scenes", &List(self.0))?;
        doc.end()
    }
}

impl Serialize for W<'_, Scene> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let scene = self.0;
        let mut out = serializer.serialize_struct("Scene", 5)?;
        out.serialize_field("time", &W(&scene.time()))?;
        out.serialize_field("entities", &List(scene.entities()))?;
        out.serialize_field("events", &List(scene.events()))?;
        out.serialize_field("relations", &List(scene.relations()))?;
        out.serialize_field("debug", &FieldsOut(scene.debug()))?;
        out.end()
    }
}

impl Serialize for W<'_, TimeSlice> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut out = serializer.serialize_struct("TimeSlice", 2)?;
        out.serialize_field("start", &W(&self.0.start()))?;
        out.serialize_field("end", &W(&self.0.end()))?;
        out.end()
    }
}

impl Serialize for W<'_, SceneTime> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self.0 {
            SceneTime::Frame(frame) => {
                serializer.serialize_newtype_variant("SceneTime", 0, "frame", frame)
            }
            SceneTime::Timestamp(time) => {
                serializer.serialize_newtype_variant("SceneTime", 1, "us", &time.0)
            }
        }
    }
}

impl Serialize for W<'_, Entity> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let entity = self.0;
        let with_images = !serializer.is_human_readable() || !entity.images.is_empty();
        let mut out = serializer.serialize_struct("Entity", 4 + usize::from(with_images))?;
        out.serialize_field("id", entity.id().as_str())?;
        out.serialize_field("observations", &List(entity.observations()))?;
        out.serialize_field("end_reason", &entity.end_reason.as_deref())?;
        out.serialize_field("motion_state", &entity.motion_state.as_ref().map(W))?;
        if with_images {
            out.serialize_field("images", &List(&entity.images))?;
        }
        out.end()
    }
}

impl Serialize for W<'_, MotionState> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self.0 {
            MotionState::Moving => serializer.serialize_unit_variant("MotionState", 0, "moving"),
            MotionState::Stationary => {
                serializer.serialize_unit_variant("MotionState", 1, "stationary")
            }
            MotionState::Unknown => serializer.serialize_unit_variant("MotionState", 2, "unknown"),
        }
    }
}

impl Serialize for W<'_, Image> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let image = self.0;
        let bounds = &image.bounding_box;
        let mut out = serializer.serialize_struct("Image", 3)?;
        out.serialize_field("timestamp_us", &image.timestamp.0)?;
        out.serialize_field(
            "box",
            &[bounds.left(), bounds.top(), bounds.right(), bounds.bottom()],
        )?;
        out.serialize_field("data", &BytesOut(&image.data))?;
        out.end()
    }
}

impl Serialize for W<'_, Observation> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let observation = self.0;
        let keep_all = !serializer.is_human_readable();
        let measurements = observation.measurements.as_deref();
        let source_data = observation.source_data();
        let debug = observation.debug();
        let shown = [
            keep_all || measurements.is_some(),
            keep_all || !source_data.is_empty(),
            keep_all || !debug.is_empty(),
        ];
        let mut out = serializer
            .serialize_struct("Observation", 3 + shown.iter().filter(|&&on| on).count())?;
        out.serialize_field("time", &W(&observation.time))?;
        out.serialize_field("geometry", &W(&observation.geometry))?;
        out.serialize_field("classifications", &List(&observation.classifications))?;
        if shown[0] {
            out.serialize_field("measurements", &measurements.map(W))?;
        }
        if shown[1] {
            out.serialize_field("source_data", &FieldsOut(source_data))?;
        }
        if shown[2] {
            out.serialize_field("debug", &FieldsOut(debug))?;
        }
        out.end()
    }
}

impl Serialize for W<'_, Geometry> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self.0 {
            Geometry::Box(bounds) => serializer.serialize_newtype_variant(
                "Geometry",
                0,
                "box",
                &[bounds.left(), bounds.top(), bounds.right(), bounds.bottom()],
            ),
            Geometry::Polygon(polygon) => serializer.serialize_newtype_variant(
                "Geometry",
                1,
                "polygon",
                &Points(polygon.points()),
            ),
        }
    }
}

struct Points<'a>(&'a [crate::Point]);

impl Serialize for Points<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut out = serializer.serialize_seq(Some(self.0.len()))?;
        for point in self.0 {
            out.serialize_element(&[point.x(), point.y()])?;
        }
        out.end()
    }
}

impl Serialize for W<'_, Classification> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let class = self.0;
        let mut out = serializer.serialize_struct("Classification", 4)?;
        out.serialize_field("type", class.class_type.as_str())?;
        out.serialize_field("score", &class.score.map(|score| score.value()))?;
        out.serialize_field("confidence", &class.confidence.map(|c| c.value()))?;
        out.serialize_field("attributes", &FieldsOut(&class.attributes))?;
        out.end()
    }
}

impl Serialize for W<'_, Measurements> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let m = self.0;
        let keep_all = !serializer.is_human_readable();
        let image_velocity = m.image_velocity.map(ImageVelocityWire::from);
        let world_velocity = m.world_velocity.map(WorldVelocityWire::from);
        let geographic = m.geographic.map(GeographicWire::from);
        let world = m.world.map(SphericalWire::from);
        let present = [
            image_velocity.is_some(),
            world_velocity.is_some(),
            geographic.is_some(),
            world.is_some(),
        ];
        let count = present.iter().filter(|&&on| on).count();
        let mut out =
            serializer.serialize_struct("Measurements", if keep_all { 4 } else { count })?;
        if keep_all || present[0] {
            out.serialize_field("image_velocity", &image_velocity)?;
        }
        if keep_all || present[1] {
            out.serialize_field("world_velocity", &world_velocity)?;
        }
        if keep_all || present[2] {
            out.serialize_field("geographic", &geographic)?;
        }
        if keep_all || present[3] {
            out.serialize_field("world", &world)?;
        }
        out.end()
    }
}

impl Serialize for W<'_, Event> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut out = serializer.serialize_struct("Event", 2)?;
        out.serialize_field("time", &W(&self.0.time))?;
        out.serialize_field("operation", &W(&self.0.operation))?;
        out.end()
    }
}

impl Serialize for W<'_, Operation> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        const NAME: &str = "Operation";
        match self.0 {
            Operation::Delete { entity } => {
                let mut out = serializer.serialize_struct_variant(NAME, 0, "delete", 1)?;
                out.serialize_field("entity", entity.as_str())?;
                out.end()
            }
            Operation::Rename { from, to } => {
                let mut out = serializer.serialize_struct_variant(NAME, 1, "rename", 2)?;
                out.serialize_field("from", from.as_str())?;
                out.serialize_field("to", to.as_str())?;
                out.end()
            }
            Operation::Merge { sources, target } => {
                let mut out = serializer.serialize_struct_variant(NAME, 2, "merge", 2)?;
                out.serialize_field("sources", &Ids(sources))?;
                out.serialize_field("target", target.as_str())?;
                out.end()
            }
            Operation::Split { source, targets } => {
                let mut out = serializer.serialize_struct_variant(NAME, 3, "split", 2)?;
                out.serialize_field("source", source.as_str())?;
                out.serialize_field("targets", &Ids(targets))?;
                out.end()
            }
        }
    }
}

impl Serialize for W<'_, EntityRelation> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let relation = self.0;
        let mut out = serializer.serialize_struct("EntityRelation", 3)?;
        out.serialize_field("type", relation.kind())?;
        out.serialize_field("source", relation.source().as_str())?;
        out.serialize_field("target", relation.target().as_str())?;
        out.end()
    }
}
