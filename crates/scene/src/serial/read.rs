//! The reading half: owned shapes that deserialize from either encoding, then build the model through its
//! constructors so nothing invalid gets in.

use serde::Deserialize;

use super::{
    picture::BytesIn,
    value::FieldsIn,
    wire::{GeographicWire, ImageVelocityWire, SphericalWire, WorldVelocityWire},
};
use crate::{
    BoundingBox, ClassType, Classification, Confidence, Entity, EntityId, EntityRelation, Event,
    Geometry, Image, Measurements, MotionState, Name, Observation, Operation, Point, Polygon,
    Scene, SceneError, SceneTime, Score, TimeSlice,
};

#[derive(Deserialize)]
pub(super) struct DocumentIn {
    pub scene_version: String,
    pub scenes: Vec<SceneIn>,
}

#[derive(Deserialize)]
pub(super) struct SceneIn {
    time: SliceIn,
    entities: Vec<EntityIn>,
    #[serde(default)]
    events: Vec<EventIn>,
    #[serde(default)]
    relations: Vec<RelationIn>,
    #[serde(default)]
    debug: FieldsIn,
}

#[derive(Deserialize)]
struct SliceIn {
    start: TimeIn,
    end: TimeIn,
}

#[derive(Clone, Copy, Deserialize)]
enum TimeIn {
    #[serde(rename = "frame")]
    Frame(u64),
    #[serde(rename = "us")]
    Us(i64),
}

#[derive(Deserialize)]
struct EntityIn {
    id: String,
    observations: Vec<ObservationIn>,
    end_reason: Option<String>,
    motion_state: Option<MotionIn>,
    #[serde(default)]
    images: Vec<ImageIn>,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum MotionIn {
    Moving,
    Stationary,
    Unknown,
}

#[derive(Deserialize)]
struct ImageIn {
    timestamp_us: i64,
    #[serde(rename = "box")]
    bounds: [f64; 4],
    data: BytesIn,
}

#[derive(Deserialize)]
struct ObservationIn {
    time: TimeIn,
    geometry: GeometryIn,
    #[serde(default)]
    classifications: Vec<ClassificationIn>,
    measurements: Option<MeasurementsIn>,
    #[serde(default)]
    source_data: FieldsIn,
    #[serde(default)]
    debug: FieldsIn,
}

#[derive(Deserialize)]
enum GeometryIn {
    #[serde(rename = "box")]
    Box([f64; 4]),
    #[serde(rename = "polygon")]
    Polygon(Vec<[f64; 2]>),
}

#[derive(Deserialize)]
struct ClassificationIn {
    #[serde(rename = "type")]
    class_type: String,
    score: Option<f64>,
    confidence: Option<f64>,
    #[serde(default)]
    attributes: FieldsIn,
}

#[derive(Deserialize)]
struct MeasurementsIn {
    image_velocity: Option<ImageVelocityWire>,
    world_velocity: Option<WorldVelocityWire>,
    geographic: Option<GeographicWire>,
    world: Option<SphericalWire>,
}

#[derive(Deserialize)]
struct EventIn {
    time: SliceIn,
    operation: OperationIn,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum OperationIn {
    Delete {
        entity: String,
    },
    Rename {
        from: String,
        to: String,
    },
    Merge {
        sources: Vec<String>,
        target: String,
    },
    Split {
        source: String,
        targets: Vec<String>,
    },
}

#[derive(Deserialize)]
struct RelationIn {
    #[serde(rename = "type")]
    kind: String,
    source: String,
    target: String,
}

impl SceneIn {
    /// The Scene, built through the model's constructors.
    pub(super) fn build(self) -> Result<Scene, SceneError> {
        let entities = self
            .entities
            .into_iter()
            .map(EntityIn::build)
            .collect::<Result<Vec<_>, _>>()?;
        let events = self
            .events
            .into_iter()
            .map(EventIn::build)
            .collect::<Result<Vec<_>, _>>()?;
        let relations = self
            .relations
            .into_iter()
            .map(|r| {
                EntityRelation::new(r.kind, EntityId::new(r.source)?, EntityId::new(r.target)?)
            })
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Scene::new(self.time.build()?)
            .with_entities(entities)?
            .with_events(events)?
            .with_relations(relations)
            .with_debug(self.debug.0))
    }
}

impl TimeIn {
    fn build(self) -> SceneTime {
        match self {
            Self::Frame(frame) => SceneTime::Frame(frame),
            Self::Us(us) => SceneTime::Timestamp(foundation::MediaTime(us)),
        }
    }
}

impl SliceIn {
    fn build(self) -> Result<TimeSlice, SceneError> {
        TimeSlice::new(self.start.build(), self.end.build())
    }
}

impl EntityIn {
    fn build(self) -> Result<Entity, SceneError> {
        let mut entity = Entity::new(EntityId::new(self.id)?);
        for observation in self.observations {
            entity.push_observation(observation.build()?);
        }
        entity.images = self
            .images
            .into_iter()
            .map(|image| {
                let [left, top, right, bottom] = image.bounds;
                Ok(Image {
                    timestamp: foundation::MediaTime(image.timestamp_us),
                    bounding_box: BoundingBox::new(left, top, right, bottom)?,
                    data: image.data.0,
                })
            })
            .collect::<Result<_, SceneError>>()?;
        entity.end_reason = self.end_reason.map(Name::Owned);
        entity.motion_state = self.motion_state.map(|state| match state {
            MotionIn::Moving => MotionState::Moving,
            MotionIn::Stationary => MotionState::Stationary,
            MotionIn::Unknown => MotionState::Unknown,
        });
        Ok(entity)
    }
}

impl ObservationIn {
    fn build(self) -> Result<Observation, SceneError> {
        let geometry = match self.geometry {
            GeometryIn::Box([left, top, right, bottom]) => {
                Geometry::Box(BoundingBox::new(left, top, right, bottom)?)
            }
            GeometryIn::Polygon(points) => Geometry::Polygon(Polygon::new(
                points
                    .into_iter()
                    .map(|[x, y]| Point::new(x, y))
                    .collect::<Result<_, _>>()?,
            )?),
        };
        let mut observation = Observation::new(self.time.build(), geometry)
            .with_details(self.source_data.0, self.debug.0)?;
        observation.classifications = self
            .classifications
            .into_iter()
            .map(ClassificationIn::build)
            .collect::<Result<_, _>>()?;
        observation.measurements = self.measurements.map(|m| {
            Box::new(Measurements {
                image_velocity: m.image_velocity.map(Into::into),
                world_velocity: m.world_velocity.map(Into::into),
                geographic: m.geographic.map(Into::into),
                world: m.world.map(Into::into),
            })
        });
        Ok(observation)
    }
}

impl ClassificationIn {
    fn build(self) -> Result<Classification, SceneError> {
        Ok(Classification {
            class_type: ClassType::new(Name::Owned(self.class_type))?,
            score: self.score.map(Score::new).transpose()?,
            confidence: self.confidence.map(Confidence::new).transpose()?,
            attributes: self.attributes.0,
        })
    }
}

impl EventIn {
    fn build(self) -> Result<Event, SceneError> {
        let id = |text: String| EntityId::new(text);
        let ids = |texts: Vec<String>| {
            texts
                .into_iter()
                .map(EntityId::new)
                .collect::<Result<Vec<_>, _>>()
        };
        let operation = match self.operation {
            OperationIn::Delete { entity } => Operation::Delete {
                entity: id(entity)?,
            },
            OperationIn::Rename { from, to } => Operation::Rename {
                from: id(from)?,
                to: id(to)?,
            },
            OperationIn::Merge { sources, target } => Operation::Merge {
                sources: ids(sources)?,
                target: id(target)?,
            },
            OperationIn::Split { source, targets } => Operation::Split {
                source: id(source)?,
                targets: ids(targets)?,
            },
        };
        Ok(Event::new(self.time.build()?, operation))
    }
}
