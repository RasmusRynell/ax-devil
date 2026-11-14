//! Inspection of held samples, under the requesting socket's permission and frame authority.
//! One Scene read runs per request kind; the newest pending read replaces its predecessor.
//! Invalid tokens receive a bounded refusal without replacing valid work or changing a capability.

use super::{Outgoing, text};
use api_types::{
    inspect::{
        InspectionContinuation, InspectionRead, InspectionReadResult, InspectionWire, ItemWire,
        MAX_INSPECTION_ENTITY_BYTES, MAX_INSPECTION_QUERY_PATH_BYTES, MAX_INSPECTION_QUERY_PATHS,
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
mod identity;
mod pins;
mod wire;
use wire::page_field;
mod position;
use position::{Position, PositionRef, remember};
use std::sync::{Arc, Mutex};
mod queue;
use foundation::CancelToken;
use queue::{Job, Queue};

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
                entity.len() <= MAX_INSPECTION_ENTITY_BYTES
            }
            Self::Paths(paths) => {
                paths.len() <= MAX_INSPECTION_QUERY_PATHS
                    && paths
                        .iter()
                        .all(|path| path.len() <= MAX_INSPECTION_QUERY_PATH_BYTES)
            }
        }
    }
}

/// What a request asked for.
#[derive(Clone)]
struct Request {
    validity: CancelToken,
    generation: u32,
    source: u16,
    data_revision: u32,
    sample: u32,
    ask: Ask,
}

/// The sample a request may read, with the snapshot its index is of and the frame it ranks by.
type Target = (Arc<dyn TrackRead>, Arc<TrackSnapshot>, u64);

/// A viewer's inspections: an object's fields and a search's leaves are asked and answered apart.
#[derive(Default)]
pub(super) struct Inspections {
    permission: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
    objects: Queue,
    searches: Queue,
    cursor: Option<(String, u16, u32, u32, String, PositionRef, u32)>,
    refused: Option<Outgoing>,
}

impl Inspections {
    /// Inspection work under this lease's current object-detail permission.
    pub(super) fn new(permission: Arc<dyn Fn() -> bool + Send + Sync>) -> Self {
        Self {
            permission: Some(permission),
            objects: Queue::default(),
            searches: Queue::default(),
            cursor: None,
            refused: None,
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
        let allowed = self.admit();
        self.release();
        let readable = allowed && entity.len() <= MAX_INSPECTION_ENTITY_BYTES;
        let token = sessions::Secret::generate().to_string();
        let position = Arc::new(Mutex::new(Position::default()));
        if readable {
            self.cursor = Some((
                token.clone(),
                source,
                data_revision,
                sample,
                entity.clone(),
                position.clone(),
                generation,
            ));
        }
        let validity = position::validity(&position);
        let ask = Ask::Object(entity, token, position);
        let request = Request {
            validity,
            generation,
            source,
            data_revision,
            sample,
            ask,
        };
        if readable {
            self.objects.ask(request, target);
        } else {
            self.refuse(&request);
        }
    }

    fn refuse(&mut self, request: &Request) {
        let mut outgoing = unavailable(request);
        // Only an echo of this request and a non-data refusal; permission/frame checks must not suppress it.
        outgoing.inspection = None;
        self.refused = Some(outgoing);
    }

    /// Releases this socket's capability, including its permutation and page addresses.
    fn release(&mut self) {
        if let Some(cursor) = self.cursor.take() {
            position::invalidate(&cursor.5);
        }
        self.objects.cancel_pending();
    }

    /// Resolves a continuation through this socket's capability and its viewer's currently held samples.
    pub(super) fn continue_read(
        &mut self,
        read: InspectionRead,
        resolve: impl FnOnce(u16, u32, u32) -> Option<Target>,
    ) {
        let target = self
            .continuation_target(&read.token)
            .and_then(|(s, r, n)| resolve(s, r, n));
        self.continue_target(read, target);
    }

    /// Queues a continuation under freshly checked sample authority.
    fn continue_target(&mut self, read: InspectionRead, target: Option<Target>) {
        let allowed = self.admit();
        let valid = self.continuation_target(&read.token);
        let (source, data_revision, sample) = valid.unwrap_or_default();
        let entity = self
            .cursor
            .as_ref()
            .map_or_else(String::new, |cursor| cursor.4.clone());
        let validity = self
            .cursor
            .as_ref()
            .map(|c| position::validity(&c.5))
            .unwrap_or_default();
        let request = Request {
            validity,
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
        if !allowed || valid.is_none() {
            self.refuse(&request);
            return;
        }
        if target.is_none() || request.validity.is_cancelled() {
            self.release();
            self.refuse(&request);
            return;
        }
        self.objects.ask(request, target);
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
        let allowed = self.admit();
        let request = Request {
            validity: CancelToken::new(),
            generation,
            source,
            data_revision,
            sample,
            ask: Ask::Paths(paths),
        };
        if allowed {
            self.searches.ask(request, target);
        } else {
            self.refuse(&request);
        }
    }

    /// The next answer or token refusal; waits while there is neither.
    pub(super) async fn next(&mut self) -> Outgoing {
        if let Some(refused) = self.refused.take() {
            return refused;
        }
        tokio::select! {
            message = self.objects.next() => message,
            message = self.searches.next() => message,
        }
    }
}

impl Drop for Inspections {
    fn drop(&mut self) {
        self.release();
    }
}

/// The answer to `job`, reading the sample's scene.
fn answer(Job { request, target }: Job) -> Outgoing {
    if request.validity.is_cancelled() {
        return unavailable(&request);
    }
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
    if request.validity.is_cancelled() {
        return unavailable(&request);
    }
    let scene = cached.map_or_else(|| track.scene_in(&snapshot, request.sample as usize), Ok);
    if request.validity.is_cancelled() {
        return unavailable(&request);
    }
    let response = reply(&request, scene.as_deref().ok());
    if request.validity.is_cancelled() {
        return unavailable(&request);
    }
    let message = text(&response);
    if let (Some(position), Ok(scene)) = (position, &scene) {
        let mut position = position
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if !position.validity.is_cancelled() && position.traversal.is_some() {
            position.scene = Some(scene.clone());
        }
    }
    match scene {
        Ok(_) => Outgoing {
            message,
            frames: Some(frame..=frame),
            data: None,
            inspection: Some(request.validity.clone()),
            refusal: Some(text(&reply(&request, None))),
            whole_file: false,
        },
        Err(error) => {
            tracing::debug!("Sample {} cannot be inspected: {error:?}", request.sample);
            unavailable(&request)
        }
    }
}

/// What the viewer is told of a sample it cannot ask about: nothing, and so ungated. It is also what the writer sends
/// in place of an answer the viewer may no longer have.
fn unavailable(request: &Request) -> Outgoing {
    Outgoing {
        message: text(&reply(request, None)),
        frames: None,
        data: None,
        inspection: Some(request.validity.clone()),
        refusal: Some(text(&reply(request, None))),
        whole_file: false,
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
        ..
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
            if position::validity(position).is_cancelled() {
                return InspectionWire::Unavailable;
            }
            let traversal = match position::prepare(position, entity) {
                Ok(traversal) => traversal,
                Err(reason) => return InspectionWire::Refused { reason },
            };
            let page = traversal.page(entity, 0, &[]);
            if position::validity(position).is_cancelled() {
                return InspectionWire::Unavailable;
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
        None => InspectionWire::Absent,
    }
}

#[cfg(test)]
fn continued(scene: Option<&Scene>, entity: &str, read: &InspectionRead) -> InspectionReadResult {
    let position = Arc::default();
    if let Some(scene) = scene {
        inspection_at(scene, entity, "held", &position);
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
        let Some(address) = position::address(position, &traversal, entity, ordinal as usize)
        else {
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
        let Some(address) = position::start(position, read.offset as usize) else {
            return InspectionReadResult::Refused {
                reason: "Inspection expired; select the object again".into(),
            };
        };
        let page = traversal.page(entity, read.offset as usize, &address);
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
