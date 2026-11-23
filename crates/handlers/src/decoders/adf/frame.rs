//! The frame formats: v1 `{timestamp, detections, track_events}` and beta `{frames: [{timestamp, observations,
//! operations}]}`. One record is one Scene.

use foundation::MediaTime;
use scene::{
    Entity, EntityId, Event, Geometry, Measurements, MotionState, Observation, Operation, Scene,
    SceneTime, TimeSlice,
};
use serde_json::Value as Json;

use super::{
    Notes,
    parse::{
        Object, beta_classification, bounding_box, entity_id, filled, geographic, image,
        image_velocity, list, measurements, object, present, timestamp, truthy, v1_classification,
        world_position, world_velocity,
    },
};

/// The entity of `entities` with this id, added when it is not there: objects with one id are sightings of one entity.
pub(super) fn entity_of(entities: &mut Vec<Entity>, id: EntityId) -> &mut Entity {
    let at = entities
        .iter()
        .position(|entity| *entity.id() == id)
        .unwrap_or_else(|| {
            entities.push(Entity::new(id));
            entities.len() - 1
        });
    &mut entities[at]
}

fn scene(time: TimeSlice, entities: Vec<Entity>, events: Vec<Event>) -> Result<Vec<Scene>, String> {
    Scene::new(time)
        .with_entities(entities)
        .and_then(|scene| scene.with_events(events))
        .map(|scene| vec![scene])
        .map_err(|error| error.to_string())
}

/// A v1 frame record, which may be wrapped as `{"frame": {...}}`.
pub(super) fn read_v1(record: &Object, _notes: &mut Notes) -> Result<Vec<Scene>, String> {
    let frame = match record.get("frame") {
        Some(frame) => object(frame, "the frame")?,
        None => record,
    };
    let time = timestamp(frame, "timestamp", "the frame timestamp")?;
    let at = TimeSlice::at(SceneTime::Timestamp(time));

    let mut entities = Vec::new();
    for detection in list(frame, "detections")? {
        let detection = object(detection, "a detection")?;
        let id = entity_id(detection.get("object_track_id"), "object_track_id")?;
        let mut observation = Observation::new(
            at.start(),
            Geometry::Box(bounding_box(detection.get("bounding_box"))?),
        );
        if let Some(class) = present(detection.get("class")) {
            observation
                .classifications
                .push(v1_classification(object(class, "a class")?)?);
        }
        // In a frame, `velocity` is the speed in the world.
        observation.measurements = measurements(Measurements {
            world_velocity: world_velocity(detection.get("velocity"))?,
            geographic: geographic(detection.get("geoposition"))?,
            world: world_position(detection.get("world_position"))?,
            ..Measurements::default()
        });
        let entity = entity_of(&mut entities, id);
        entity.motion_state = match detection.get("moving") {
            Some(Json::Bool(true)) => Some(MotionState::Moving),
            Some(Json::Bool(false)) => Some(MotionState::Stationary),
            _ => entity.motion_state,
        };
        entity.push_observation(observation);
    }

    let events = list(frame, "track_events")?
        .iter()
        .map(|event| track_event(event, at))
        .collect::<Result<_, _>>()?;
    scene(at, entities, events)
}

fn track_event(event: &Json, at: TimeSlice) -> Result<Event, String> {
    let event = object(event, "a track event")?;
    let kind = event.get("type").and_then(Json::as_str).unwrap_or_default();
    let operation = match kind.to_lowercase().as_str() {
        "trackended" => Operation::Delete {
            entity: entity_id(event.get("object_track_id"), "TrackEnded object_track_id")?,
        },
        "rename" => Operation::Rename {
            from: entity_id(event.get("from_id"), "Rename from_id")?,
            to: entity_id(event.get("to_id"), "Rename to_id")?,
        },
        _ => return Err(format!("unsupported track event type '{kind}'")),
    };
    Ok(Event::new(at, operation))
}

/// A beta record: one frame, `{frame: {...}}`, or `{frames: [...]}`. The Scene spans the frames' times, and each
/// observation keeps its own.
pub(super) fn read_beta(record: &Object, notes: &mut Notes) -> Result<Vec<Scene>, String> {
    let listed = list(record, "frames")?;
    let frames: Vec<&Object> = if listed.is_empty() {
        let frame =
            present(record.get("frame")).map_or(Ok(record), |frame| object(frame, "the frame"))?;
        vec![frame]
    } else {
        listed
            .iter()
            .map(|frame| object(frame, "a frame"))
            .collect::<Result<_, _>>()?
    };

    let mut times = Vec::with_capacity(frames.len());
    for frame in &frames {
        times.push(timestamp(frame, "timestamp", "the frame timestamp")?);
    }
    let (first, last) = times.iter().fold(
        (MediaTime(i64::MAX), MediaTime(i64::MIN)),
        |(first, last), &time| (first.min(time), last.max(time)),
    );
    let span = TimeSlice::new(SceneTime::Timestamp(first), SceneTime::Timestamp(last))
        .map_err(|error| error.to_string())?;

    let mut entities = Vec::new();
    let mut events = Vec::new();
    let mut world_velocities = 0;
    for (frame, time) in frames.into_iter().zip(times) {
        for observation in list(frame, "observations")? {
            let observation = object(observation, "an observation")?;
            world_velocities += usize::from(observation.get("world_velocity").is_some_and(truthy));
            beta_observation(observation, &mut entities)?;
        }
        let at = TimeSlice::at(SceneTime::Timestamp(time));
        for operation in list(frame, "operations")? {
            let operation = object(operation, "an operation")?;
            match operation.get("type").and_then(Json::as_str) {
                Some("DeleteOperation") => {
                    if let Ok(entity) =
                        entity_id(operation.get("id").filter(|id| truthy(id)), "Delete id")
                    {
                        events.push(Event::new(at, Operation::Delete { entity }));
                    }
                }
                Some("RenameOperation") => events.push(Event::new(
                    at,
                    Operation::Rename {
                        from: entity_id(operation.get("from"), "Rename from")?,
                        to: entity_id(operation.get("to"), "Rename to")?,
                    },
                )),
                _ => {}
            }
        }
    }
    let scenes = scene(span, entities, events)?;
    // Only a record that is shown counts.
    notes.world_velocity += world_velocities;
    notes.differing_times += usize::from(first != last);
    Ok(scenes)
}

/// Adds the observation, and its picture, to its entity. Only the first of `classes` is used.
fn beta_observation(payload: &Object, entities: &mut Vec<Entity>) -> Result<(), String> {
    let id = entity_id(payload.get("track_id"), "track_id")?;
    let time = timestamp(payload, "timestamp", "the observation timestamp")?;
    let mut observation = Observation::new(
        SceneTime::Timestamp(time),
        Geometry::Box(bounding_box(payload.get("bounding_box"))?),
    );
    let class = match present(payload.get("class")) {
        Some(class) => Some(class),
        None => list(payload, "classes")?
            .first()
            .and_then(|class| present(Some(class))),
    };
    if let Some(class) = class {
        observation
            .classifications
            .extend(beta_classification(object(class, "a class")?)?);
    }
    // An empty position or geoposition is none, as an empty velocity is.
    observation.measurements = measurements(Measurements {
        image_velocity: image_velocity(payload.get("velocity"))?,
        geographic: geographic(filled(payload.get("geoposition")))?,
        world: world_position(filled(payload.get("world_position")))?,
        // The beta format's world velocity has no known shape, so it is not read; `read_beta` counts it.
        ..Measurements::default()
    });
    let picture = present(payload.get("image"))
        .map(|picture| image(picture, false))
        .transpose()?;

    let entity = entity_of(entities, id);
    entity.push_observation(observation);
    entity.images.extend(picture);
    Ok(())
}
