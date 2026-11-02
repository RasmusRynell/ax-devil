//! The consolidated formats: one line is one track, or `{version, tracks: [...]}`. Each observation becomes a
//! Scene at its own time, so the host puts every track seen at one moment on one frame.

use std::collections::BTreeMap;

use foundation::MediaTime;
use scene::{
    Classification, Entity, Geometry, Measurements, Name, Observation, Scene, SceneTime, TimeSlice,
};
use serde_json::Value as Json;

use super::{
    frame::entity_of,
    parse::{
        Object, bounding_box, classification, entity_id, geographic, image, image_velocity, list,
        measurements, object, present, timestamp, world_position, world_velocity,
    },
};

/// The Scenes of a record, oldest first. `v1` says whether the format is v1, which also reads the world velocity and
/// lets an image's `crop_box` stand in for its `bounding_box`.
pub(super) fn read(record: &Object, v1: bool) -> Result<Vec<Scene>, String> {
    let mut moments: BTreeMap<MediaTime, Vec<Entity>> = BTreeMap::new();
    for track in tracks(record)? {
        add_track(track, v1, &mut moments)?;
    }
    moments
        .into_iter()
        .map(|(time, entities)| {
            Scene::new(TimeSlice::at(SceneTime::Timestamp(time)))
                .with_entities(entities)
                .map_err(|error| error.to_string())
        })
        .collect()
}

/// The tracks of a record: all of a document with a `version`, else the record itself.
fn tracks(record: &Object) -> Result<Vec<&Object>, String> {
    if present(record.get("version")).is_none() {
        return Ok(vec![unwrapped(record)?]);
    }
    let mut tracks = Vec::new();
    for track in list(record, "tracks")? {
        if !track.is_null() {
            tracks.push(unwrapped(object(track, "a track")?)?);
        }
    }
    Ok(tracks)
}

/// A track may be wrapped as `{"object": {...}}`.
fn unwrapped(track: &Object) -> Result<&Object, String> {
    match present(track.get("object")) {
        Some(inner) => object(inner, "a track"),
        None => Ok(track),
    }
}

/// Adds the track's observations to the Scenes of their times. Its picture and end reason are of the whole track, so
/// they go with the last observation.
fn add_track(
    track: &Object,
    v1: bool,
    moments: &mut BTreeMap<MediaTime, Vec<Entity>>,
) -> Result<(), String> {
    let id = entity_id(track.get("id"), "the track id")?;
    let classes = list(track, "classes")?
        .iter()
        .map(|class| classification(object(class, "a class")?))
        .collect::<Result<Vec<_>, _>>()?;

    let path = list(track, "path")?;
    let sightings = if path.is_empty() {
        list(track, "observations")?
    } else {
        path
    };
    let observations = sightings
        .iter()
        .map(|payload| observation(object(payload, "an observation")?, &classes, v1))
        .collect::<Result<Vec<_>, _>>()?;
    let (Some(first), Some(last)) = (
        observations.iter().map(|(time, _)| *time).min(),
        observations.iter().map(|(time, _)| *time).max(),
    ) else {
        return Err("the track has no observations or path".to_owned());
    };

    let start = timestamp(track, "start_time", "start_time")?;
    let end = timestamp(track, "end_time", "end_time")?;
    if (start, end) != (first, last) {
        return Err("start_time and end_time do not match the observations".to_owned());
    }
    if let Some(seconds) = present(track.get("duration")).and_then(Json::as_f64)
        && (seconds * 1_000_000.0).round() as i64 != last.0 - first.0
    {
        return Err("duration does not match the observations".to_owned());
    }

    let mut picture = present(track.get("image"))
        .map(|picture| image(picture, v1))
        .transpose()?;
    let mut end_reason = present(track.get("end_reason"))
        .and_then(Json::as_str)
        .filter(|reason| !reason.is_empty())
        .map(|reason| Name::from(reason.to_owned()));
    for (time, observation) in observations {
        let entity = entity_of(moments.entry(time).or_default(), id.clone());
        entity.push_observation(observation);
        if time == last {
            entity.images.extend(picture.take());
            entity.end_reason = end_reason.take();
        }
    }
    Ok(())
}

fn observation(
    payload: &Object,
    classes: &[Classification],
    v1: bool,
) -> Result<(MediaTime, Observation), String> {
    let time = timestamp(payload, "timestamp", "the observation timestamp")?;
    let mut observation = Observation::new(
        SceneTime::Timestamp(time),
        Geometry::Box(bounding_box(payload.get("bounding_box"))?),
    );
    observation.classifications = classes.to_vec();
    observation.measurements = measurements(Measurements {
        image_velocity: image_velocity(payload.get("velocity"))?,
        world_velocity: if v1 {
            world_velocity(payload.get("world_velocity"))?
        } else {
            None
        },
        geographic: geographic(payload.get("geoposition"))?,
        world: world_position(payload.get("world_position"))?,
    });
    Ok((time, observation))
}
