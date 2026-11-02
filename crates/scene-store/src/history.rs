//! The file history: every entity a file holds, the classes it was recorded with, the events it is in and the runs of
//! samples it appears in. It is built while the file is ingested, stored in the artifact and loaded when a query asks.
//!
//! Queries over it are in [`crate::query`]; placing its runs on a video's frames is in [`crate::placement`].

use std::{
    fs::File,
    sync::{Arc, Weak},
};

use foundation::{ErrorCode, UserError};
use scene::OperationKind;
use serde::{Deserialize, Serialize};

use crate::{format::read_history, track::Ingest};

mod build;

pub(crate) use build::HistoryBuilder;

/// The most memory a history may take, estimated while it is built. A larger one is not kept; the file has none.
pub(crate) const HISTORY_BYTES: usize = 256 << 20;

/// The kinds of event in the order of their bits in [`History`]'s per-entity event set.
const KINDS: [OperationKind; 4] = [
    OperationKind::Delete,
    OperationKind::Rename,
    OperationKind::Merge,
    OperationKind::Split,
];

fn kind_index(kind: OperationKind) -> u8 {
    match kind {
        OperationKind::Delete => 0,
        OperationKind::Rename => 1,
        OperationKind::Merge => 2,
        OperationKind::Split => 3,
    }
}

/// Why a track has no history to query.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum HistoryUnavailable {
    /// The ingest is still running; the history is complete only when it has ended.
    Reading,
    /// The history would take more memory than a history may.
    TooLarge,
    /// The ingest failed before it could build the history, or the stored one is damaged.
    Failed(UserError),
}

/// Distinct strings, each stored once; an id is the string's position.
#[derive(Default, Serialize, Deserialize)]
struct Strings {
    text: String,
    /// Where each string ends in `text`.
    ends: Vec<u32>,
}

impl Strings {
    fn len(&self) -> usize {
        self.ends.len()
    }

    fn bytes(&self) -> usize {
        self.text.len() + size_of_val(self.ends.as_slice())
    }

    fn get(&self, id: u32) -> &str {
        let at = id as usize;
        let start = if at == 0 {
            0
        } else {
            self.ends[at - 1] as usize
        };
        &self.text[start..self.ends[at] as usize]
    }

    /// Adds `text` as the next string. The history cap keeps the arena far below `u32::MAX` bytes.
    fn push(&mut self, text: &str) -> u32 {
        self.text.push_str(text);
        self.ends
            .push(u32::try_from(self.text.len()).unwrap_or(u32::MAX));
        (self.ends.len() - 1) as u32
    }

    /// Whether every string ends in order, inside `text` and on a character boundary.
    fn is_valid(&self) -> bool {
        let mut last = 0;
        self.ends.iter().all(|&end| {
            let end = end as usize;
            let ok = last <= end && self.text.is_char_boundary(end);
            last = end;
            ok
        }) && last == self.text.len()
    }
}

/// One event: where it is, what it is and which entities it involves.
#[derive(Clone, Copy, Serialize, Deserialize)]
struct EventRow {
    sample: u32,
    kind: u8,
    /// A `words` string.
    label: u32,
    /// First and count into `event_entities`.
    entities: [u32; 2],
}

/// An event of the file as the history holds it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EventRef<'a> {
    /// The sample that carries the event.
    pub sample: u32,
    /// What the event does.
    pub kind: OperationKind,
    /// The event as one line of text.
    pub label: &'a str,
    /// The ids of the entities it involves.
    pub entities: Vec<&'a str>,
}

/// What a file holds, entity by entity. Entity `e` is the `e`th id in order of first mention; an entity that only an
/// event mentions has no runs.
#[derive(Serialize, Deserialize)]
pub struct History {
    ids: Strings,
    /// Class names and event labels.
    words: Strings,
    /// How many filter options the policy had; each entity has `ceil(options / 32)` mask words.
    options: u32,
    /// How many samples the file has; runs are sample indices below it.
    samples: u32,
    /// Per entity: first and count into `class_ids`, the classes of its latest observation in each sample, in order of
    /// first appearance.
    classes: Vec<[u32; 2]>,
    class_ids: Vec<u32>,
    /// Per entity: bit `i % 32` of word `i / 32` is set when filter option `i` selects the classes it was recorded
    /// with. The same layout as the Sample Item's class mask.
    file_mask: Vec<u32>,
    /// Per entity: a bit per [`KINDS`] entry it is involved in.
    events_of: Vec<u8>,
    /// Per entity: first and count into `run_pairs`.
    runs: Vec<[u32; 2]>,
    /// First and last sample of each run of consecutive samples in which an entity appears, in sample order.
    run_pairs: Vec<[u32; 2]>,
    events: Vec<EventRow>,
    event_entities: Vec<u32>,
}

impl History {
    /// About how much memory the history holds, in bytes.
    pub fn bytes(&self) -> usize {
        self.ids.bytes()
            + self.words.bytes()
            + size_of_val(self.classes.as_slice())
            + size_of_val(self.class_ids.as_slice())
            + size_of_val(self.file_mask.as_slice())
            + size_of_val(self.events_of.as_slice())
            + size_of_val(self.runs.as_slice())
            + size_of_val(self.run_pairs.as_slice())
            + size_of_val(self.events.as_slice())
            + size_of_val(self.event_entities.as_slice())
    }

    /// How many entities there are.
    pub fn entities(&self) -> usize {
        self.ids.len()
    }

    /// How many samples the file has.
    pub fn samples(&self) -> u32 {
        self.samples
    }

    /// The id of `entity`.
    pub(crate) fn id(&self, entity: u32) -> &str {
        self.ids.get(entity)
    }

    /// The classes `entity` was recorded with, in order of first appearance.
    pub(crate) fn classes(&self, entity: u32) -> impl Iterator<Item = &str> {
        let [first, count] = self.classes[entity as usize];
        self.class_ids[first as usize..(first + count) as usize]
            .iter()
            .map(|&word| self.words.get(word))
    }

    /// The kinds of event `entity` is in, in the order delete, rename, merge, split.
    pub(crate) fn event_kinds(&self, entity: u32) -> impl Iterator<Item = OperationKind> {
        let set = self.events_of[entity as usize];
        KINDS
            .into_iter()
            .enumerate()
            .filter_map(move |(bit, kind)| (set >> bit & 1 != 0).then_some(kind))
    }

    /// The runs of consecutive samples `entity` appears in: first and last sample of each, ascending.
    pub(crate) fn runs(&self, entity: u32) -> &[[u32; 2]] {
        let [first, count] = self.runs[entity as usize];
        &self.run_pairs[first as usize..(first + count) as usize]
    }

    /// Whether the class toggles keep `entity`. `enabled` has bit `i % 32` of word `i / 32` set for each enabled
    /// filter option, as the Sample Item's class mask does; with `E` those bits and `M` the entity's mask, it stays when
    /// the policy has no options, `M & E != 0`, or something is enabled and no disabled option selects it.
    pub(crate) fn kept(&self, enabled: &[u32], entity: u32) -> bool {
        if self.options == 0 {
            return true;
        }
        let words = self.mask_words();
        let at = entity as usize * words;
        let (mut selected, mut any_enabled, mut disabled_selects) = (false, false, false);
        for (word, &mask) in self.file_mask[at..at + words].iter().enumerate() {
            let on = enabled.get(word).copied().unwrap_or(0) & self.option_bits(word);
            selected |= mask & on != 0;
            any_enabled |= on != 0;
            disabled_selects |= mask & !on != 0;
        }
        selected || (any_enabled && !disabled_selects)
    }

    /// The events of the file in sample order.
    pub fn events(&self) -> impl Iterator<Item = EventRef<'_>> {
        self.events.iter().map(|row| {
            let [first, count] = row.entities;
            EventRef {
                sample: row.sample,
                kind: KINDS[usize::from(row.kind)],
                label: self.words.get(row.label),
                entities: self.event_entities[first as usize..(first + count) as usize]
                    .iter()
                    .map(|&entity| self.ids.get(entity))
                    .collect(),
            }
        })
    }

    fn mask_words(&self) -> usize {
        (self.options as usize).div_ceil(32)
    }

    /// The bits of mask word `word` that name an option.
    fn option_bits(&self, word: usize) -> u32 {
        match self.options as usize - word * 32 {
            left if left >= 32 => u32::MAX,
            left => (1 << left) - 1,
        }
    }

    /// Whether the parts agree with each other, so reading by them cannot go out of bounds.
    fn is_consistent(&self) -> bool {
        let entities = self.ids.len();
        let within = |ranges: &[[u32; 2]], len: usize| {
            ranges
                .iter()
                .all(|&[first, count]| first as usize + count as usize <= len)
        };
        let words = self.words.len();
        self.ids.is_valid()
            && self.words.is_valid()
            && self.classes.len() == entities
            && self.runs.len() == entities
            && self.events_of.len() == entities
            && self.file_mask.len() == entities * self.mask_words()
            && within(&self.classes, self.class_ids.len())
            && within(&self.runs, self.run_pairs.len())
            && self.class_ids.iter().all(|&word| (word as usize) < words)
            && self
                .run_pairs
                .iter()
                .all(|&[first, last]| first <= last && last < self.samples)
            && self.event_entities.iter().all(|&e| (e as usize) < entities)
            && self.events.iter().all(|row| {
                (row.label as usize) < words
                    && usize::from(row.kind) < KINDS.len()
                    && row.sample < self.samples
                    && row.entities[0] as usize + row.entities[1] as usize
                        <= self.event_entities.len()
            })
    }
}

/// What a history is stored as: a checksum of the rest, then postcard of [`Stored`].
fn encode(stored: &StoredRef<'_>) -> Result<Vec<u8>, String> {
    let payload = postcard::to_stdvec(stored).map_err(|e| e.to_string())?;
    let mut bytes = Vec::with_capacity(payload.len() + 4);
    bytes.extend_from_slice(&crc32fast::hash(&payload).to_le_bytes());
    bytes.extend_from_slice(&payload);
    Ok(bytes)
}

/// [`Stored`] by reference. The variants stay in the same order, which is what postcard encodes.
#[derive(Serialize)]
enum StoredRef<'a> {
    Ready(&'a History),
    TooLarge,
}

#[derive(Deserialize)]
enum Stored {
    Ready(Box<History>),
    TooLarge,
}

fn damaged(reason: &str) -> HistoryUnavailable {
    HistoryUnavailable::Failed(UserError::new(
        ErrorCode::FileUnreadable,
        format!(
            "The file history of this overlay cannot be read ({reason}); open the overlay again"
        ),
    ))
}

/// Reads the history section of an artifact.
fn load(file: &File) -> Result<History, HistoryUnavailable> {
    let bytes = read_history(file).map_err(|reason| damaged(&reason))?;
    let Some((crc, payload)) = bytes.split_first_chunk::<4>() else {
        return Err(damaged("there is none"));
    };
    if crc32fast::hash(payload) != u32::from_le_bytes(*crc) {
        return Err(damaged("its checksum does not match"));
    }
    match postcard::from_bytes(payload) {
        Ok(Stored::Ready(history)) if history.is_consistent() => Ok(*history),
        Ok(Stored::Ready(_)) => Err(damaged("it is inconsistent")),
        Ok(Stored::TooLarge) => Err(HistoryUnavailable::TooLarge),
        Err(error) => Err(damaged(&error.to_string())),
    }
}

/// A finished history, or the fact that it would not fit.
pub(crate) enum Built {
    Ready(Box<History>),
    TooLarge,
}

impl Built {
    /// The artifact's history section. A history that encodes to more than the cap is stored as too large.
    pub(crate) fn section(&self) -> Result<Vec<u8>, String> {
        let stored = match self {
            Self::Ready(history) => StoredRef::Ready(history),
            Self::TooLarge => StoredRef::TooLarge,
        };
        let bytes = encode(&stored)?;
        if bytes.len() > HISTORY_BYTES {
            return encode(&StoredRef::TooLarge);
        }
        Ok(bytes)
    }

    /// The slot of a track whose ingest ended without an artifact to read the history from.
    pub(crate) fn held(self) -> Slot {
        match self {
            Self::Ready(history) => Slot::Held(Arc::new(*history)),
            Self::TooLarge => Slot::Unavailable(HistoryUnavailable::TooLarge),
        }
    }
}

/// Why [`Slot::get`] found no history.
pub(crate) enum Miss {
    Unavailable(HistoryUnavailable),
    /// The stored history could not be read, so the artifact is bad.
    Damaged(HistoryUnavailable),
}

/// Where a track's history is.
pub(crate) enum Slot {
    Unavailable(HistoryUnavailable),
    /// In the artifact. It is read when asked and shared while any query holds it.
    Stored(Weak<History>),
    /// In memory: the ingest ended without storing an artifact.
    Held(Arc<History>),
}

impl Slot {
    /// The slot of a track created in this state of its ingest.
    pub(crate) fn initial(ingest: &Ingest) -> Self {
        match ingest {
            Ingest::Reading => Self::Unavailable(HistoryUnavailable::Reading),
            Ingest::Complete => Self::Stored(Weak::new()),
            Ingest::Incomplete(error) => {
                Self::Unavailable(HistoryUnavailable::Failed(error.clone()))
            }
        }
    }

    /// The history, read from `file` (the artifact) when nobody holds it.
    pub(crate) fn get(&mut self, file: &File) -> Result<Arc<History>, Miss> {
        match self {
            Self::Unavailable(why) => Err(Miss::Unavailable(why.clone())),
            Self::Held(history) => Ok(history.clone()),
            Self::Stored(shared) => {
                if let Some(history) = shared.upgrade() {
                    return Ok(history);
                }
                match load(file) {
                    Ok(history) => {
                        let history = Arc::new(history);
                        *shared = Arc::downgrade(&history);
                        Ok(history)
                    }
                    Err(why) => {
                        *self = Self::Unavailable(why.clone());
                        Err(match why {
                            HistoryUnavailable::Failed(_) => Miss::Damaged(why),
                            _ => Miss::Unavailable(why),
                        })
                    }
                }
            }
        }
    }
}
