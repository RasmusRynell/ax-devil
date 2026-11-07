//! Answers to `Inspect` and `InspectFields`: every field of one object of a sample the viewer holds, and the leaves of
//! all its objects at the paths a search names.
//!
//! A request is answered only from a sample the viewer's overlays count as held under the revision it names
//! (`ViewerOverlays::inspect_target`), and the answer is gated by the frame that sample ranks by, like the sample
//! itself: a follower whose presenter moved on is not sent what it may no longer see. What cannot be answered is
//! `Unavailable` (`objects: null` for a search), which discloses nothing and goes ungated. Work is bounded: one scene
//! is read at a time per kind of request and the newest request replaces one of its kind that has not started.

use super::{Outgoing, text};
use api_types::{
    inspect::{FieldWire, InspectionWire, ItemWire, ObjectItemsWire},
    playback::ServerMessage,
};
use overlays::ViewerOverlays;
use scene::{
    Scene,
    inspection::{InspectionField, MAX_FIELDS, entity_fields, path_names},
};
use scene_store::{TrackRead, TrackSnapshot};
use std::sync::Arc;
use tokio::task::JoinHandle;

/// The longest object id an `Inspect` may name, in bytes.
const MAX_ENTITY_BYTES: usize = 1024;

/// The most paths an `InspectFields` may name, and the longest one in bytes.
const MAX_PATHS: usize = 64;
const MAX_PATH_BYTES: usize = 1024;

/// What was asked of the sample.
#[derive(Clone)]
enum Ask {
    /// All the fields of the object with this id.
    Object(String),
    /// The leaves of every object at these query paths, as the client spelled them.
    Paths(Vec<String>),
}

impl Ask {
    /// Whether the request is of a size to answer.
    fn within_bounds(&self) -> bool {
        match self {
            Self::Object(entity) => entity.len() <= MAX_ENTITY_BYTES,
            Self::Paths(paths) => {
                paths.len() <= MAX_PATHS && paths.iter().all(|path| path.len() <= MAX_PATH_BYTES)
            }
        }
    }
}

/// What a request asked for.
#[derive(Clone)]
struct Request {
    source: u16,
    data_revision: u32,
    sample: u32,
    ask: Ask,
}

/// The sample a request may read, with the snapshot its index is of and the frame it ranks by.
type Target = (Arc<dyn TrackRead>, Arc<TrackSnapshot>, u64);

/// A request and the sample it may read, decided when the request arrived.
struct Job {
    request: Request,
    /// `None` when the viewer does not hold the sample.
    target: Option<Target>,
}

/// The requests of one kind: one runs at a time, the newest waits for it.
#[derive(Default)]
struct Queue {
    running: Option<(Request, JoinHandle<Outgoing>)>,
    queued: Option<Job>,
}

impl Queue {
    fn ask(&mut self, request: Request, target: Option<Target>) {
        let target = target.filter(|_| request.ask.within_bounds());
        self.queued = Some(Job { request, target });
        self.start();
    }

    /// The next answer; waits while no request is running.
    async fn next(&mut self) -> Outgoing {
        let Some((request, running)) = self.running.as_mut() else {
            return std::future::pending().await;
        };
        // Only the handle is polled, so a dropped call loses nothing.
        let finished = running.await;
        let request = request.clone();
        self.running = None;
        self.start();
        finished.unwrap_or_else(|error| {
            tracing::warn!("An inspection failed: {error}");
            unavailable(&request)
        })
    }

    fn start(&mut self) {
        if self.running.is_none()
            && let Some(job) = self.queued.take()
        {
            let request = job.request.clone();
            self.running = Some((request, tokio::task::spawn_blocking(move || answer(job))));
        }
    }
}

/// A viewer's inspections: an object's fields and a search's leaves are asked and answered apart.
#[derive(Default)]
pub(super) struct Inspections {
    objects: Queue,
    searches: Queue,
}

impl Inspections {
    /// Takes a request for an object's fields, replacing one that has not started. Whether the viewer holds the sample
    /// is judged now.
    pub(super) fn ask(
        &mut self,
        overlays: &ViewerOverlays,
        source: u16,
        data_revision: u32,
        sample: u32,
        entity: String,
    ) {
        let target = overlays.inspect_target(source, data_revision, u64::from(sample));
        self.ask_target(source, data_revision, sample, entity, target);
    }

    /// Takes a request whose sample the caller already found, the way [`ask`](Self::ask) does.
    pub(super) fn ask_target(
        &mut self,
        source: u16,
        data_revision: u32,
        sample: u32,
        entity: String,
        target: Option<Target>,
    ) {
        let ask = Ask::Object(entity);
        let request = Request {
            source,
            data_revision,
            sample,
            ask,
        };
        self.objects.ask(request, target);
    }

    /// Takes a request for the leaves of every object at `paths`, replacing one that has not started.
    pub(super) fn ask_fields(
        &mut self,
        overlays: &ViewerOverlays,
        source: u16,
        data_revision: u32,
        sample: u32,
        paths: Vec<String>,
    ) {
        let target = overlays.inspect_target(source, data_revision, u64::from(sample));
        self.ask_fields_target(source, data_revision, sample, paths, target);
    }

    /// Takes a request whose sample the caller already found, the way [`ask_fields`](Self::ask_fields) does.
    pub(super) fn ask_fields_target(
        &mut self,
        source: u16,
        data_revision: u32,
        sample: u32,
        paths: Vec<String>,
        target: Option<Target>,
    ) {
        let request = Request {
            source,
            data_revision,
            sample,
            ask: Ask::Paths(paths),
        };
        self.searches.ask(request, target);
    }

    /// The next answer; waits while no request is running.
    pub(super) async fn next(&mut self) -> Outgoing {
        tokio::select! {
            message = self.objects.next() => message,
            message = self.searches.next() => message,
        }
    }
}

/// The answer to `job`, reading the sample's scene.
fn answer(Job { request, target }: Job) -> Outgoing {
    let Some((track, snapshot, frame)) = target else {
        return unavailable(&request);
    };
    match track.scene_in(&snapshot, request.sample as usize) {
        Ok(scene) => Outgoing {
            message: text(&reply(&request, Some(&scene))),
            frames: Some(frame..=frame),
            data: None,
        },
        Err(error) => {
            tracing::debug!("Sample {} cannot be inspected: {error:?}", request.sample);
            unavailable(&request)
        }
    }
}

/// What the viewer is told of a sample it cannot ask about: nothing, and so ungated.
fn unavailable(request: &Request) -> Outgoing {
    Outgoing {
        message: text(&reply(request, None)),
        frames: None,
        data: None,
    }
}

/// The message for `request`, answered from `scene`, or `Unavailable` when there is none.
fn reply(request: &Request, scene: Option<&Scene>) -> ServerMessage {
    let Request {
        source,
        data_revision,
        sample,
        ask,
    } = request;
    let (source, data_revision, sample) = (*source, *data_revision, *sample);
    match ask {
        Ask::Object(entity) => ServerMessage::Inspected {
            source,
            data_revision,
            sample,
            entity: entity.clone(),
            inspection: scene.map_or(InspectionWire::Unavailable, |scene| {
                inspection(scene, entity)
            }),
        },
        Ask::Paths(paths) => ServerMessage::InspectedFields {
            source,
            data_revision,
            sample,
            paths: paths.clone(),
            objects: scene.map(|scene| leaves(scene, paths)),
        },
    }
}

fn inspection(scene: &Scene, entity: &str) -> InspectionWire {
    match scene.entity(entity) {
        Some(entity) => {
            let (fields, truncated) = entity_fields(entity, MAX_FIELDS);
            InspectionWire::Found {
                fields: fields.into_iter().map(field).collect(),
                truncated,
            }
        }
        None => InspectionWire::Absent,
    }
}

/// Every object's leaves whose key one of `paths` names; objects with none are left out.
fn leaves(scene: &Scene, paths: &[String]) -> Vec<ObjectItemsWire> {
    let paths: Vec<String> = paths.iter().map(|path| path.to_lowercase()).collect();
    let mut objects = Vec::new();
    for entity in scene.entities() {
        let (fields, _) = entity_fields(entity, usize::MAX);
        let mut items = Vec::new();
        for item in fields.iter().flat_map(InspectionField::query_items) {
            let key = item.key();
            if paths.iter().any(|path| path_names(path, &key)) {
                items.push(ItemWire {
                    key,
                    value: item.value,
                });
            }
        }
        if !items.is_empty() {
            objects.push(ObjectItemsWire {
                entity: entity.id().to_string(),
                items,
            });
        }
    }
    objects
}

fn field(field: InspectionField) -> FieldWire {
    FieldWire {
        group: field.group,
        origin: field.origin,
        path: field.path,
        value: field.value,
        rgb: field.color.map(|color| [color.r, color.g, color.b]),
        text: field.text,
        fraction: field.fraction,
    }
}

#[cfg(test)]
mod tests;
