//! The events of a file, placed on one video's frames and listed in frame order.
//!
//! An event sits on the first frame whose time is at or after its sample's time, or, matching by frame, on its sample's
//! frame number; one outside the video is left out. Matching tolerance and sticky do not move it. The list is the
//! reference Events page's: every placed event in frame order, searched by `kind`, `object` (any involved id) and free
//! text over the label.

use foundation::{CancelToken, Cancelled, Timeline};
use overlay_sync::{MatchBasis, SampleKeys};
use scene::OperationKind;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

use crate::{
    history::History,
    placement::Placement,
    query::{Record, Search},
};

/// How many events between two checks of the cancel token.
const CANCEL_EVERY: usize = 4096;
/// The frame of an event the video does not show.
const NOT_PLACED: u32 = u32::MAX;

/// Where each event of a file is on one video. Eight bytes per event.
///
/// Frames are `u32`, as in [`Placement`]: an event past `u32::MAX - 1` frames is left out.
pub struct EventPlacement {
    /// The placed events, by frame and then by index.
    order: Vec<u32>,
    /// The frame of every event, [`NOT_PLACED`] for one the video does not show.
    frames: Vec<u32>,
}

impl EventPlacement {
    /// Places the events of `history` on the frames of `timeline`. `keys` are the file's sample keys, by `basis`.
    /// Cancellation is checked as it goes.
    pub fn new(
        history: &History,
        keys: &SampleKeys,
        timeline: &Timeline,
        basis: MatchBasis,
        cancel: &CancelToken,
    ) -> Result<Self, Cancelled> {
        let count = history.event_count();
        let mut frames = vec![NOT_PLACED; count];
        let place = |frame: u64| {
            u32::try_from(frame)
                .ok()
                .filter(|&frame| frame != NOT_PLACED)
        };
        match (basis, &keys.times, &keys.frames) {
            (MatchBasis::Time, Some(times), _) => {
                let shown = timeline.as_slice();
                for (event, slot) in frames.iter_mut().enumerate() {
                    if event % CANCEL_EVERY == 0 {
                        cancel.check()?;
                    }
                    let sample = history.event_sample(event as u32) as usize;
                    let Some(time) = times.get(sample) else {
                        continue;
                    };
                    if time < shown[0] {
                        continue;
                    }
                    let frame = shown.partition_point(|&frame| frame < time);
                    if frame < shown.len() {
                        *slot = place(frame as u64).unwrap_or(NOT_PLACED);
                    }
                }
            }
            (MatchBasis::Frame, _, Some(by_frame)) => {
                let samples: Vec<u32> = (0..count as u32)
                    .map(|event| history.event_sample(event))
                    .collect();
                for (n, key) in by_frame.iter().enumerate() {
                    if n % CANCEL_EVERY == 0 {
                        cancel.check()?;
                    }
                    let Some(frame) = place(key.frame).filter(|_| key.frame < timeline.len())
                    else {
                        continue;
                    };
                    let from = samples.partition_point(|&sample| sample < key.sample);
                    let to = samples.partition_point(|&sample| sample <= key.sample);
                    frames[from..to].fill(frame);
                }
            }
            _ => {}
        }
        cancel.check()?;
        let mut order: Vec<u32> = (0..count as u32)
            .filter(|&event| frames[event as usize] != NOT_PLACED)
            .collect();
        order.sort_unstable_by_key(|&event| (frames[event as usize], event));
        Ok(Self { order, frames })
    }

    /// About how much memory the placement holds, in bytes.
    pub fn bytes(&self) -> usize {
        size_of_val(self.order.as_slice()) + size_of_val(self.frames.as_slice())
    }

    /// The frame event `event` is on; `None` when the video does not show it.
    fn frame(&self, event: u32) -> Option<u32> {
        self.frames
            .get(event as usize)
            .copied()
            .filter(|&frame| frame != NOT_PLACED)
    }

    /// The events on `frame` other than `except`, in index order.
    fn on_frame(&self, frame: u32, except: u32) -> impl Iterator<Item = u32> {
        let first = self
            .order
            .partition_point(|&event| self.frames[event as usize] < frame);
        let last = self
            .order
            .partition_point(|&event| self.frames[event as usize] <= frame);
        self.order[first..last]
            .iter()
            .copied()
            .filter(move |&event| event != except)
    }
}

/// What to list from the events the video shows.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct EventQuery {
    /// Which events to list.
    pub search: Search,
}

/// An object an event involves, as a chip of the list shows it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[ts(rename = "EventObjectWire")]
pub struct EventObject {
    /// The entity id.
    pub id: String,
    /// The first class the object was recorded with, which colors its chip; `None` when the video shows the object
    /// nowhere, and the chip takes the event's kind.
    pub class: Option<String>,
}

/// A server-issued SHA-256 digest of kind, label and ordered entity ids (length-prefixed UTF-8).
/// Frame, index and classes are excluded. All 256 bits are retained; accidental collisions are impractical.
pub type EventAnchor = [u8; 32];

fn anchor<'a>(
    kind: OperationKind,
    label: &'a str,
    ids: impl Iterator<Item = &'a str>,
) -> EventAnchor {
    use sha2::{Digest, Sha256};
    let mut digest = Sha256::new();
    digest.update([match kind {
        OperationKind::Delete => 0,
        OperationKind::Rename => 1,
        OperationKind::Merge => 2,
        OperationKind::Split => 3,
    }]);
    for text in std::iter::once(label).chain(ids) {
        digest.update((text.len() as u64).to_le_bytes());
        digest.update(text.as_bytes());
    }
    digest.finalize().into()
}

/// An event of the file as a list shows it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[ts(rename = "EventRowWire")]
pub struct EventRow {
    /// Bounded identity for finding this event after a file replacement.
    pub anchor: EventAnchor,
    /// The event's index in the file's events, which names it in a request.
    pub event: u32,
    /// The frame the video shows it on.
    #[ts(type = "number")]
    pub frame: u64,
    /// What the event does.
    pub kind: OperationKind,
    /// The event as one line of text.
    pub label: String,
    /// The objects it involves, in order and with repeats.
    pub objects: Vec<EventObject>,
}

/// An object an event involves, with where the video shows it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[ts(rename = "InvolvedWire")]
pub struct Involved {
    /// The entity id.
    pub id: String,
    /// The classes it was recorded with, in order of first appearance; none when the video shows it nowhere.
    pub classes: Vec<String>,
    /// The first and last frame the video shows it on; `None` when it shows it nowhere, such as an id only the event
    /// names.
    #[ts(type = "[number, number] | null")]
    pub seen: Option<[u64; 2]>,
    /// Where it is on screen as `[first, last]` frames, in order, merged so a strip of `bins` marks can draw it.
    #[ts(type = "Array<[number, number]>")]
    pub presence: Vec<[u64; 2]>,
}

/// One event with the objects it involves and the events of its moment.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[ts(rename = "EventDetailWire")]
pub struct EventDetail {
    /// The event.
    pub row: EventRow,
    /// The objects it involves, each once, in the order the event names them.
    pub objects: Vec<Involved>,
    /// The first other events on its frame, in index order.
    pub same_moment: Vec<EventRow>,
    /// Whether its frame has more other events than `same_moment` holds.
    pub same_moment_truncated: bool,
}

/// What event rows are made from: the file's history, its events and objects placed on the video.
#[derive(Clone, Copy)]
pub struct EventView<'a> {
    /// The file's history.
    pub history: &'a History,
    /// Where the events are.
    pub events: &'a EventPlacement,
    /// Where the objects are.
    pub placement: &'a Placement,
}

impl<'a> EventView<'a> {
    /// Finds Python's nearest equal event: kind, label and ordered ids match; frame distance breaks ties in file order.
    pub fn nearest(
        self,
        previous: &EventAnchor,
        frame: u64,
        cancel: &CancelToken,
    ) -> Result<Option<u32>, Cancelled> {
        let mut best = None;
        let mut distance = u64::MAX;
        for &event in &self.events.order {
            cancel.check()?;
            let Some(data) = self.history.event(event) else {
                continue;
            };
            let candidate = anchor(
                data.kind,
                data.label,
                self.history
                    .event_entities(event)
                    .iter()
                    .map(|&id| self.history.id(id)),
            );
            if candidate != *previous {
                continue;
            }
            let delta = u64::from(self.events.frames[event as usize]).abs_diff(frame);
            if best.is_none() || delta < distance {
                best = Some(event);
                distance = delta;
            }
        }
        Ok(best)
    }

    /// The row of event `event`; `None` when the video does not show it.
    pub fn row(self, event: u32) -> Option<EventRow> {
        let frame = self.events.frame(event)?;
        let data = self.history.event(event)?;
        let objects: Vec<EventObject> = self
            .history
            .event_entities(event)
            .iter()
            .map(|&entity| EventObject {
                id: self.history.id(entity).to_string(),
                class: self.shown_classes(entity).next().map(str::to_string),
            })
            .collect();
        Some(EventRow {
            anchor: anchor(
                data.kind,
                data.label,
                objects
                    .iter()
                    .map(|object: &EventObject| object.id.as_str()),
            ),
            event,
            frame: u64::from(frame),
            kind: data.kind,
            label: data.label.to_string(),
            objects,
        })
    }

    /// Event `event` with its objects and up to `same_moment_limit` other events of its frame; `bins` is as for
    /// [`crate::Query`]'s rows. `None` when the video does not show it.
    pub fn detail(self, event: u32, bins: u16, same_moment_limit: usize) -> Option<EventDetail> {
        let row = self.row(event)?;
        let mut entities: Vec<u32> = Vec::new();
        for &entity in self.history.event_entities(event) {
            if !entities.contains(&entity) {
                entities.push(entity);
            }
        }
        let objects = entities
            .into_iter()
            .map(|entity| {
                let runs = self.history.runs(entity);
                let bounds = self.placement.bounds(runs);
                Involved {
                    id: self.history.id(entity).to_string(),
                    classes: self.shown_classes(entity).map(str::to_string).collect(),
                    seen: bounds.map(|(first, last)| [u64::from(first), u64::from(last)]),
                    presence: self.placement.presence(&self.placement.spans(runs), bins),
                }
            })
            .collect();
        // One more than the limit is found, to tell that there is more; rows are made for the limit only.
        let mut others: Vec<u32> = self
            .events
            .on_frame(self.events.frame(event)?, event)
            .take(same_moment_limit.saturating_add(1))
            .collect();
        let same_moment_truncated = others.len() > same_moment_limit;
        others.truncate(same_moment_limit);
        Some(EventDetail {
            row,
            objects,
            same_moment: others
                .into_iter()
                .filter_map(|other| self.row(other))
                .collect(),
            same_moment_truncated,
        })
    }

    /// The first `limit` events that involve the object `id`, in frame order, and whether there are more. Rows are
    /// made for the events returned only.
    pub fn object_events(self, id: &str, limit: usize) -> (Vec<EventRow>, bool) {
        let Some(entity) = self.history.find(id) else {
            return (Vec::new(), false);
        };
        let mut found: Vec<u32> = self
            .events
            .order
            .iter()
            .copied()
            .filter(|&event| self.history.event_entities(event).contains(&entity))
            .take(limit.saturating_add(1))
            .collect();
        let more = found.len() > limit;
        found.truncate(limit);
        (
            found
                .into_iter()
                .filter_map(|event| self.row(event))
                .collect(),
            more,
        )
    }

    /// The classes of `entity` when the video shows it, else none.
    fn shown_classes(self, entity: u32) -> impl Iterator<Item = &'a str> {
        let shown = self.placement.bounds(self.history.runs(entity)).is_some();
        self.history.classes(entity).filter(move |_| shown)
    }
}

/// The answer to an [`EventQuery`]: how many events the video shows and which of them the search lists, in frame
/// order. Rows are made a page at a time.
pub struct EventMatches {
    total: u64,
    /// Event indices in listing order.
    matched: Vec<u32>,
}

impl EventMatches {
    /// About how much memory the listing holds, in bytes.
    pub fn bytes(&self) -> usize {
        size_of_val(self.matched.as_slice())
    }

    /// How many events the video shows.
    pub fn total(&self) -> u64 {
        self.total
    }

    /// How many of them the search matches, which is how many rows there are.
    pub fn matched(&self) -> u64 {
        self.matched.len() as u64
    }

    /// Where event `event` is in the listing, as the index of its row; `None` when the search or the video leaves it
    /// out.
    pub fn row_of(&self, event: u32) -> Option<u32> {
        let row = self.matched.iter().position(|&listed| listed == event)?;
        Some(row as u32)
    }

    /// How many listed events are on frames at or before `frame`: the row before which a line for that frame goes.
    pub fn frame_row(&self, events: &EventPlacement, frame: u64) -> u32 {
        self.matched
            .partition_point(|&event| u64::from(events.frames[event as usize]) <= frame)
            as u32
    }

    /// Rows `offset..offset + limit` of the listing.
    pub fn rows(&self, view: EventView<'_>, offset: usize, limit: usize) -> Vec<EventRow> {
        self.matched
            .iter()
            .skip(offset)
            .take(limit)
            .filter_map(|&event| view.row(event))
            .collect()
    }
}

impl EventQuery {
    /// Lists the events `events` places on the video that the search matches. Cancellation is checked as it goes.
    pub fn run(
        &self,
        history: &History,
        events: &EventPlacement,
        cancel: &CancelToken,
    ) -> Result<EventMatches, Cancelled> {
        let search = self.search.compile();
        let total = events.order.len() as u64;
        if self.search.groups.is_empty() {
            return Ok(EventMatches {
                total,
                matched: events.order.clone(),
            });
        }
        let mut matched = Vec::new();
        for (n, &event) in events.order.iter().enumerate() {
            if n % CANCEL_EVERY == 0 {
                cancel.check()?;
            }
            let Some(data) = history.event(event) else {
                continue;
            };
            let kind = data.kind.title();
            if search.matches(&Record {
                fields: vec![("kind", vec![kind]), ("object", data.entities)],
                text: vec![data.label],
            }) {
                matched.push(event);
            }
        }
        cancel.check()?;
        Ok(EventMatches { total, matched })
    }
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<EventObject>();
    types.visit::<EventRow>();
    types.visit::<Involved>();
    types.visit::<EventDetail>();
}
