//! The consolidated formats: one line is one track, or `{version, tracks: [...]}`. Each observation becomes a
//! Scene at its own time, so the host puts every track seen at one moment on one frame.

use std::collections::{BTreeMap, HashMap, hash_map::Entry};

use foundation::MediaTime;
use scene::{
    Classification, Entity, EntityId, Geometry, Image, Measurements, Name, Observation, Scene,
    SceneTime, TimeSlice,
};
use serde_json::Value as Json;

use super::{
    Notes,
    frame::entity_of,
    parse::{
        Object, bounding_box, entity_id, filled, finite, geographic, image, image_velocity, list,
        measurements, object, present, timestamp, track_classification, truthy, world_position,
        world_velocity,
    },
};

/// The only `version` a beta document may have.
const BETA_VERSION: &str = "1.0.0-beta1";

/// A track that has been checked, ready to be given to the Scenes of its times.
struct Track {
    id: EntityId,
    observations: Vec<(MediaTime, Observation)>,
    /// The time of the last observation, which gets the picture and the end reason.
    last: MediaTime,
    picture: Option<Image>,
    end_reason: Option<Name>,
    /// How many observations had a world velocity that beta does not read.
    unread_world_velocities: usize,
}

/// The Scenes of a record, oldest first. `v1` says whether the format is v1, which also reads the world velocity,
/// lets an image's `crop_box` stand in for its `bounding_box`, takes any `version` and a missing `duration`, reads
/// `path` before `observations` and does not unwrap `{"object": ...}`. Beta notes the world velocities it leaves out.
/// Of tracks with one id the last replaces the others.
pub(super) fn read(record: &Object, v1: bool, notes: &mut Notes) -> Result<Vec<Scene>, String> {
    let mut tracks: Vec<Track> = Vec::new();
    let mut places: HashMap<EntityId, usize> = HashMap::new();
    let mut unread_world_velocities = 0;
    for payload in payloads(record, v1)? {
        let track = read_track(payload, v1)?;
        unread_world_velocities += track.unread_world_velocities;
        match places.entry(track.id.clone()) {
            Entry::Occupied(place) => tracks[*place.get()] = track,
            Entry::Vacant(place) => {
                place.insert(tracks.len());
                tracks.push(track);
            }
        }
    }

    let mut moments: BTreeMap<MediaTime, Vec<Entity>> = BTreeMap::new();
    for track in tracks {
        let (mut picture, mut end_reason) = (track.picture, track.end_reason);
        for (time, observation) in track.observations {
            let entity = entity_of(moments.entry(time).or_default(), track.id.clone());
            entity.push_observation(observation);
            if time == track.last {
                entity.images.extend(picture.take());
                entity.end_reason = end_reason.take();
            }
        }
    }
    let scenes = moments
        .into_iter()
        .map(|(time, entities)| {
            Scene::new(TimeSlice::at(SceneTime::Timestamp(time)))
                .with_entities(entities)
                .map_err(|error| error.to_string())
        })
        .collect::<Result<_, _>>()?;
    // Only a record that is shown counts.
    notes.world_velocity += unread_world_velocities;
    Ok(scenes)
}

/// The tracks of a record: all of a document with a `version`, else the record itself.
fn payloads(record: &Object, v1: bool) -> Result<Vec<&Object>, String> {
    let Some(version) = present(record.get("version")) else {
        return Ok(vec![unwrapped(record, v1)]);
    };
    if !v1 && version.as_str() != Some(BETA_VERSION) {
        return Err(format!(
            "unsupported version {version}, expected \"{BETA_VERSION}\""
        ));
    }
    let mut tracks = Vec::new();
    for track in list(record, "tracks")? {
        if !track.is_null() {
            tracks.push(unwrapped(object(track, "a track")?, v1));
        }
    }
    Ok(tracks)
}

/// A beta track may be wrapped as `{"object": {...}}`; v1 never is.
fn unwrapped(track: &Object, v1: bool) -> &Object {
    if v1 {
        return track;
    }
    present(track.get("object"))
        .and_then(Json::as_object)
        .unwrap_or(track)
}

/// Checks the track and reads its observations, picture and end reason.
fn read_track(track: &Object, v1: bool) -> Result<Track, String> {
    let id = track.get("id");
    // Beta takes no id that Python counts as false, such as the number 0; v1 takes any.
    if !v1 && !id.is_some_and(truthy) {
        return Err("the track id is missing".to_owned());
    }
    let id = entity_id(id, "the track id")?;
    let classes = list(track, "classes")?
        .iter()
        .map(|class| track_classification(object(class, "a class")?, v1))
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .flatten()
        .collect::<Vec<_>>();

    // Beta has no `path`.
    let path = if v1 { list(track, "path")? } else { &[] };
    let sightings = if path.is_empty() {
        list(track, "observations")?
    } else {
        path
    };
    let unread_world_velocities = if v1 {
        0
    } else {
        sightings
            .iter()
            .filter(|sighting| sighting.get("world_velocity").is_some_and(truthy))
            .count()
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
    let span = TimeSlice::new(SceneTime::Timestamp(first), SceneTime::Timestamp(last))
        .map_err(|error| error.to_string())?;

    let start = timestamp(track, "start_time", "start_time")?;
    let end = timestamp(track, "end_time", "end_time")?;
    if (start, end) != (first, last) {
        return Err("start_time and end_time do not match the observations".to_owned());
    }
    let duration = present(track.get("duration"))
        .map(|seconds| finite(seconds, "duration"))
        .transpose()?;
    match duration {
        None if !v1 => return Err("the track lacks its duration".to_owned()),
        // Exactly the seconds between the first and last observation, as a microsecond count is.
        Some(seconds) if seconds != span.duration() => {
            return Err("duration does not match the observations".to_owned());
        }
        _ => {}
    }

    // v1 reads an empty image as none; beta refuses it.
    let picture = if v1 {
        filled(track.get("image"))
    } else {
        present(track.get("image"))
    }
    .map(|picture| image(picture, v1))
    .transpose()?;
    let end_reason = present(track.get("end_reason"))
        .and_then(Json::as_str)
        .filter(|reason| !reason.is_empty())
        .map(|reason| Name::from(reason.to_owned()));
    Ok(Track {
        id,
        observations,
        last,
        picture,
        end_reason,
        unread_world_velocities,
    })
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
    // v1 refuses an empty position; beta reads it as none.
    let place = |key: &str| {
        if v1 {
            present(payload.get(key))
        } else {
            filled(payload.get(key))
        }
    };
    observation.measurements = measurements(Measurements {
        image_velocity: image_velocity(payload.get("velocity"))?,
        world_velocity: if v1 {
            world_velocity(payload.get("world_velocity"))?
        } else {
            None
        },
        geographic: geographic(place("geoposition"))?,
        world: world_position(place("world_position"))?,
    });
    Ok((time, observation))
}
