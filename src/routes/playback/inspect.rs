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
    inspect::{
        InspectionContinuation, InspectionRead, InspectionReadResult, InspectionWire, ItemWire,
        ObjectItemsWire,
    },
    playback::ServerMessage,
};
use overlays::ViewerOverlays;
use scene::{
    Scene,
    inspection::{Budget, query_leaves},
};
use scene_store::{TrackRead, TrackSnapshot};
mod pins;
mod wire;
use wire::page_field;
mod position;
use position::{Position, PositionRef, remember};
use std::sync::{Arc, Mutex};
use tokio::task::JoinHandle;

/// The longest object id an `Inspect` may name, in bytes.
const MAX_ENTITY_BYTES: usize = 1024;

/// The most paths an `InspectFields` may name, and the longest one in bytes.
const MAX_PATHS: usize = 64;
const MAX_PATH_BYTES: usize = 1024;

/// What one search may walk and collect over all the objects of a sample: leaves, and bytes of keys and texts.
const SEARCH_BUDGET: Budget = Budget {
    leaves: 4_000_000,
    bytes: 8 * 1024 * 1024,
};

/// What was asked of the sample.
#[derive(Clone)]
enum Ask {
    /// All the fields of the object with this id.
    Object(String, String, PositionRef),
    Continue(String, InspectionRead, PositionRef),
    /// The leaves of every object at these query paths, as the client spelled them.
    Paths(Vec<String>),
}

impl Ask {
    /// Whether the request is of a size to answer.
    fn within_bounds(&self) -> bool {
        match self {
            Self::Object(entity, _, _) | Self::Continue(entity, _, _) => {
                entity.len() <= MAX_ENTITY_BYTES
            }
            Self::Paths(paths) => {
                paths.len() <= MAX_PATHS && paths.iter().all(|path| path.len() <= MAX_PATH_BYTES)
            }
        }
    }
}

/// What a request asked for.
#[derive(Clone)]
struct Request {
    generation: u32,
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
    permission: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
    objects: Queue,
    searches: Queue,
    cursor: Option<(String, u16, u32, u32, String, PositionRef)>,
}

impl Inspections {
    /// Inspection work under this lease's current object-detail permission.
    pub(super) fn new(permission: Arc<dyn Fn() -> bool + Send + Sync>) -> Self {
        Self {
            permission: Some(permission),
            objects: Queue::default(),
            searches: Queue::default(),
            cursor: None,
        }
    }

    fn admit(&mut self) -> bool {
        let allowed = self
            .permission
            .as_ref()
            .is_none_or(|permission| permission());
        if !allowed {
            self.release();
        }
        allowed
    }

    /// Takes a request for an object's fields, replacing one that has not started. Whether the viewer holds the sample
    /// is judged now.
    pub(super) fn ask(
        &mut self,
        overlays: &ViewerOverlays,
        generation: u32,
        source: u16,
        data_revision: u32,
        sample: u32,
        entity: String,
    ) {
        let target = overlays.inspect_target(source, data_revision, u64::from(sample));
        self.ask_target(generation, source, data_revision, sample, entity, target);
    }

    /// Takes a request whose sample the caller already found, the way [`ask`](Self::ask) does.
    pub(super) fn ask_target(
        &mut self,
        generation: u32,
        source: u16,
        data_revision: u32,
        sample: u32,
        entity: String,
        target: Option<Target>,
    ) {
        if !self.admit() {
            return;
        }
        if let Some(old) = self.cursor.take() {
            position::invalidate(&old.5);
        }
        let token = sessions::Secret::generate().to_string();
        let position = Arc::new(Mutex::new(Position::default()));
        self.cursor = Some((
            token.clone(),
            source,
            data_revision,
            sample,
            entity.clone(),
            position.clone(),
        ));
        let ask = Ask::Object(entity, token, position);
        let request = Request {
            generation,
            source,
            data_revision,
            sample,
            ask,
        };
        self.objects.ask(request, target);
    }

    /// Releases this socket's capability, including its permutation and page addresses.
    pub(super) fn release(&mut self) {
        if let Some(cursor) = self.cursor.take() {
            position::invalidate(&cursor.5);
        }
    }

    /// Identifies the target only for this socket's current capability. The caller rechecks its held sample.
    pub(super) fn continuation_target(&self, token: &str) -> Option<(u16, u32, u32)> {
        let (held, source, revision, sample, _, _) = self.cursor.as_ref()?;
        (token == held).then_some((*source, *revision, *sample))
    }

    /// Queues a continuation under freshly checked sample authority.
    pub(super) fn continue_target(&mut self, read: InspectionRead, target: Option<Target>) {
        if !self.admit() {
            return;
        }
        let valid = self.continuation_target(&read.token);
        let (source, data_revision, sample) = valid.unwrap_or_default();
        let entity = self
            .cursor
            .as_ref()
            .map_or_else(String::new, |cursor| cursor.4.clone());
        let request = Request {
            generation: read.request,
            source,
            data_revision,
            sample,
            ask: Ask::Continue(
                entity,
                read,
                self.cursor
                    .as_ref()
                    .map(|cursor| cursor.5.clone())
                    .unwrap_or_default(),
            ),
        };
        if target.is_none()
            && let Some(cursor) = self.cursor.take()
        {
            position::invalidate(&cursor.5);
        }
        self.objects
            .ask(request, target.filter(|_| valid.is_some()));
    }

    /// Takes a request for the leaves of every object at `paths`, replacing one that has not started.
    pub(super) fn ask_fields(
        &mut self,
        overlays: &ViewerOverlays,
        generation: u32,
        source: u16,
        data_revision: u32,
        sample: u32,
        paths: Vec<String>,
    ) {
        let target = overlays.inspect_target(source, data_revision, u64::from(sample));
        self.ask_fields_target(generation, source, data_revision, sample, paths, target);
    }

    /// Takes a request whose sample the caller already found, the way [`ask_fields`](Self::ask_fields) does.
    pub(super) fn ask_fields_target(
        &mut self,
        generation: u32,
        source: u16,
        data_revision: u32,
        sample: u32,
        paths: Vec<String>,
        target: Option<Target>,
    ) {
        if !self.admit() {
            return;
        }
        let request = Request {
            generation,
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

impl Drop for Inspections {
    fn drop(&mut self) {
        if let Some(cursor) = &self.cursor {
            position::invalidate(&cursor.5);
        }
    }
}

/// The answer to `job`, reading the sample's scene.
fn answer(Job { request, target }: Job) -> Outgoing {
    let Some((track, snapshot, frame)) = target else {
        return unavailable(&request);
    };
    let position = match &request.ask {
        Ask::Object(_, _, position) | Ask::Continue(_, _, position) => Some(position),
        Ask::Paths(_) => None,
    };
    let cached = position.and_then(|position| {
        position
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .scene
            .clone()
    });
    // Admission still checks the current held revision before this path. Reuse the immutable input so a large
    // uncached record is not decoded again for every 64-KiB chunk.
    let scene = cached.map_or_else(|| track.scene_in(&snapshot, request.sample as usize), Ok);
    if let (Some(position), Ok(scene)) = (position, &scene) {
        let mut position = position
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if position.active {
            position.scene = Some(scene.clone());
        }
    }
    match scene {
        Ok(scene) => Outgoing {
            message: text(&reply(&request, Some(&scene))),
            frames: Some(frame..=frame),
            data: None,
            inspection: true,
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
        inspection: true,
    }
}

/// The message for `request`, answered from `scene`, or `Unavailable` when there is none.
fn reply(request: &Request, scene: Option<&Scene>) -> ServerMessage {
    let Request {
        generation,
        source,
        data_revision,
        sample,
        ask,
    } = request;
    let (source, data_revision, sample) = (*source, *data_revision, *sample);
    match ask {
        Ask::Object(entity, token, position) => ServerMessage::Inspected {
            request: *generation,
            source,
            data_revision,
            sample,
            entity: entity.clone(),
            inspection: scene.map_or(InspectionWire::Unavailable, |scene| {
                inspection_at(scene, entity, token, position)
            }),
        },
        Ask::Continue(entity, read, position) => ServerMessage::InspectContinued {
            request: read.request,
            result: continued_at(scene, entity, read, position),
        },
        Ask::Paths(paths) => ServerMessage::InspectedFields {
            request: *generation,
            source,
            data_revision,
            sample,
            paths: paths.clone(),
            objects: scene.and_then(|scene| leaves(scene, paths)),
        },
    }
}

#[cfg(test)]
fn inspection(scene: &Scene, entity: &str, token: &str) -> InspectionWire {
    inspection_at(scene, entity, token, &Arc::default())
}

fn inspection_at(
    scene: &Scene,
    entity: &str,
    token: &str,
    position: &PositionRef,
) -> InspectionWire {
    match scene.entity(entity) {
        Some(entity) => {
            if !position
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .active
            {
                return InspectionWire::Unavailable;
            }
            let traversal = match scene::inspection::Traversal::new(entity) {
                Ok(traversal) => Arc::new(traversal),
                Err(reason) => {
                    position::invalidate(position);
                    return InspectionWire::Refused { reason };
                }
            };
            if !position
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .active
            {
                return InspectionWire::Unavailable;
            }
            let page = traversal.page(entity, 0, &[]);
            {
                let mut position = position
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                if !position.active {
                    return InspectionWire::Unavailable;
                }
                position.traversal = Some(traversal);
            }
            remember(position, 0, &page);
            let next = page.next.map(|next| next as u32);
            InspectionWire::Found {
                fields: page.fields.into_iter().map(page_field).collect(),
                truncated: next.is_some(),
                continuation: Some(InspectionContinuation {
                    token: token.to_owned(),
                    next,
                }),
            }
        }
        None => {
            position::invalidate(position);
            InspectionWire::Absent
        }
    }
}

#[cfg(test)]
fn continued(scene: Option<&Scene>, entity: &str, read: &InspectionRead) -> InspectionReadResult {
    let position = Arc::default();
    if let Some(entity) = scene.and_then(|scene| scene.entity(entity)) {
        let traversal =
            Arc::new(scene::inspection::Traversal::new(entity).expect("bounded test entity"));
        remember(&position, 0, &traversal.page(entity, 0, &[]));
        position.lock().unwrap().traversal = Some(traversal);
    }
    continued_at(scene, entity, read, &position)
}

fn continued_at(
    scene: Option<&Scene>,
    entity: &str,
    read: &InspectionRead,
    position: &PositionRef,
) -> InspectionReadResult {
    let Some(entity) = scene.and_then(|scene| scene.entity(entity)) else {
        return InspectionReadResult::Refused {
            reason: "Inspection expired; select the object again".into(),
        };
    };
    let Some(traversal) = position
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .traversal
        .clone()
    else {
        return InspectionReadResult::Refused {
            reason: "Inspection expired; select the object again".into(),
        };
    };
    if let Some(wanted) = &read.pins {
        return pins::resolve(position, entity, &traversal, wanted, read.offset as usize);
    }
    if read.raw == Some(true) {
        return position::raw(position, entity, read.offset as usize);
    }
    if let Some(ordinal) = read.field {
        let address = {
            let state = position
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            state
                .fields
                .get(&(ordinal as usize))
                .or_else(|| state.pins.fields.get(&(ordinal as usize)))
                .cloned()
        };
        let Some(address) = address else {
            return InspectionReadResult::Refused {
                reason: "Inspection expired; select the object again".into(),
            };
        };
        let chunk = traversal.chunk(
            entity,
            ordinal as usize,
            read.offset as usize,
            read.path,
            &address,
        );
        match chunk {
            Ok(chunk) => InspectionReadResult::Chunk {
                text: chunk.text,
                total: chunk.total as u32,
                next: chunk.next.map(|next| next as u32),
            },
            Err(reason) => InspectionReadResult::Refused { reason },
        }
    } else {
        let next = position
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .next
            .clone();
        let page = if let Some((ordinal, address)) =
            next.filter(|(ordinal, _)| *ordinal == read.offset as usize)
        {
            traversal.page(entity, ordinal, &address)
        } else if read.offset == 0 {
            traversal.page(entity, 0, &[])
        } else {
            return InspectionReadResult::Refused {
                reason: "Inspection expired; select the object again".into(),
            };
        };
        remember(position, read.offset as usize, &page);
        InspectionReadResult::Page {
            fields: page.fields.into_iter().map(page_field).collect(),
            next: page.next.map(|next| next as u32),
        }
    }
}

/// Every object's leaves whose key one of `paths` names; objects with none are left out. `None` when the search would
/// walk or collect more than [`SEARCH_BUDGET`] over the scene, which is refused rather than answered in part.
fn leaves(scene: &Scene, paths: &[String]) -> Option<Vec<ObjectItemsWire>> {
    let paths: Vec<String> = paths.iter().map(|path| path.to_lowercase()).collect();
    let mut budget = SEARCH_BUDGET;
    let mut objects = Vec::new();
    for entity in scene.entities() {
        let found = query_leaves(entity, &paths, &mut budget).ok()?;
        if !found.is_empty() {
            objects.push(ObjectItemsWire {
                entity: entity.id().to_string(),
                items: found
                    .into_iter()
                    .map(|(key, value)| ItemWire { key, value })
                    .collect(),
            });
        }
    }
    Some(objects)
}

#[cfg(test)]
mod tests;
