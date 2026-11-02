//! The consolidated formats: one line is one track, or `{version, tracks: [...]}`. Each observation becomes a
//! Scene at its own time, so the host puts every track seen at one moment on one frame.

use std::collections::BTreeMap;

use foundation::MediaTime;
use scene::{
    Classification, Entity, Geometry, Measurements, Observation, Scene, SceneTime, TimeSlice,
};
use serde_json::Value as Json;

use super::{
    frame::entity_of,
    parse::{
        Object, bounding_box, classification, entity_id, geographic, image_velocity, list,
        measurements, object, present, timestamp, world_position, world_velocity,
    },
};

/// The Scenes of a record, oldest first. `read_world_velocity` says whether the format's world velocity is read.
pub(super) fn read(record: &Object, read_world_velocity: bool) -> Result<Vec<Scene>, String> {
    let mut moments: BTreeMap<MediaTime, Vec<Entity>> = BTreeMap::new();
    for track in tracks(record)? {
        add_track(track, read_world_velocity, &mut moments)?;
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
        return Ok(vec![record]);
    }
    let mut tracks = Vec::new();
    for track in list(record, "tracks")? {
        if track.is_null() {
            continue;
        }
        let track = object(track, "a track")?;
        // A track may be wrapped as `{"object": {...}}`.
        tracks.push(match present(track.get("object")) {
            Some(inner) => object(inner, "a track")?,
            None => track,
        });
    }
    Ok(tracks)
}

fn add_track(
    track: &Object,
    read_world_velocity: bool,
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
        .map(|payload| {
            observation(
                object(payload, "an observation")?,
                &classes,
                read_world_velocity,
            )
        })
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

    for (time, observation) in observations {
        entity_of(moments.entry(time).or_default(), id.clone()).push_observation(observation);
    }
    Ok(())
}

fn observation(
    payload: &Object,
    classes: &[Classification],
    read_world_velocity: bool,
) -> Result<(MediaTime, Observation), String> {
    let time = timestamp(payload, "timestamp", "the observation timestamp")?;
    let mut observation = Observation::new(
        SceneTime::Timestamp(time),
        Geometry::Box(bounding_box(payload.get("bounding_box"))?),
    );
    observation.classifications = classes.to_vec();
    observation.measurements = measurements(Measurements {
        image_velocity: image_velocity(payload.get("velocity"))?,
        world_velocity: if read_world_velocity {
            world_velocity(payload.get("world_velocity"))?
        } else {
            None
        },
        geographic: geographic(payload.get("geoposition"))?,
        world: world_position(payload.get("world_position"))?,
    });
    Ok((time, observation))
}
