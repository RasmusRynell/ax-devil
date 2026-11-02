//! Answers to `Inspect`: every field of one object of a sample the viewer holds.
//!
//! A request is answered only from a sample the viewer's overlays count as held under the revision it names
//! (`ViewerOverlays::inspect_target`), and the answer is gated by the frame that sample ranks by, like the sample
//! itself: a follower whose presenter moved on is not sent what it may no longer see. What cannot be answered is
//! `Unavailable`, which discloses nothing and goes ungated. Work is bounded: one scene is read at a time and the
//! newest request replaces one that has not started.

use super::{Outgoing, text};
use api_types::{
    inspect::{FieldWire, InspectionWire},
    playback::ServerMessage,
};
use overlays::ViewerOverlays;
use scene::inspection::{InspectionField, Leaf, MAX_FIELDS, entity_fields};
use scene_store::{Track, TrackSnapshot};
use serde_json::Value;
use std::sync::Arc;
use tokio::task::JoinHandle;

/// The longest object id an `Inspect` may name, in bytes.
const MAX_ENTITY_BYTES: usize = 1024;

/// What an `Inspect` asked for.
#[derive(Clone)]
struct Request {
    source: u16,
    data_revision: u32,
    sample: u32,
    entity: String,
}

/// A request and the sample it may read, decided when the request arrived.
struct Job {
    request: Request,
    /// The sample's track, the snapshot its index is of and the frame it ranks by; `None` when the viewer does not
    /// hold it.
    target: Option<(Arc<Track>, Arc<TrackSnapshot>, u64)>,
}

/// A viewer's inspections: one runs at a time, the newest request waits for it.
#[derive(Default)]
pub(super) struct Inspections {
    running: Option<(Request, JoinHandle<Outgoing>)>,
    queued: Option<Job>,
}

impl Inspections {
    /// Takes a request, replacing one that has not started. Whether the viewer holds the sample is judged now.
    pub(super) fn ask(
        &mut self,
        overlays: &ViewerOverlays,
        source: u16,
        data_revision: u32,
        sample: u32,
        entity: String,
    ) {
        let target = (entity.len() <= MAX_ENTITY_BYTES)
            .then(|| overlays.inspect_target(source, data_revision, u64::from(sample)))
            .flatten();
        self.queued = Some(Job {
            request: Request {
                source,
                data_revision,
                sample,
                entity,
            },
            target,
        });
        self.start();
    }

    /// The next answer; waits while no request is running.
    pub(super) async fn next(&mut self) -> Outgoing {
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
            reply(&request, InspectionWire::Unavailable, None)
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

/// The answer to `job`, reading the sample's scene.
fn answer(Job { request, target }: Job) -> Outgoing {
    let Some((track, snapshot, frame)) = target else {
        return reply(&request, InspectionWire::Unavailable, None);
    };
    let scene = match track.scene_in(&snapshot, request.sample as usize) {
        Ok(scene) => scene,
        Err(error) => {
            tracing::debug!("Sample {} cannot be inspected: {error:?}", request.sample);
            return reply(&request, InspectionWire::Unavailable, None);
        }
    };
    let inspection = match scene.entity(&request.entity) {
        Some(entity) => {
            let (fields, truncated) = entity_fields(entity, MAX_FIELDS);
            InspectionWire::Found {
                fields: fields.into_iter().map(field).collect(),
                truncated,
            }
        }
        None => InspectionWire::Absent,
    };
    reply(&request, inspection, Some(frame))
}

/// The message, gated by `frame` when it says anything about the sample.
fn reply(request: &Request, inspection: InspectionWire, frame: Option<u64>) -> Outgoing {
    let Request {
        source,
        data_revision,
        sample,
        entity,
    } = request.clone();
    Outgoing {
        message: text(&ServerMessage::Inspected {
            source,
            data_revision,
            sample,
            entity,
            inspection,
        }),
        frames: frame.map(|frame| frame..=frame),
        data: None,
    }
}

fn field(field: InspectionField) -> FieldWire {
    let value = match field.value {
        Leaf::Null => Value::Null,
        Leaf::Bool(truth) => Value::Bool(truth),
        Leaf::Number(number) if number.fract() == 0.0 && number.abs() < 9e15 => {
            Value::from(number as i64)
        }
        Leaf::Number(number) => {
            serde_json::Number::from_f64(number).map_or(Value::Null, Value::Number)
        }
        Leaf::Text(text) => Value::String(text),
    };
    FieldWire {
        group: field.group,
        origin: field.origin,
        path: field.path,
        value,
    }
}

#[cfg(test)]
mod tests;
