//! Building a [`History`] from the Scenes of a file in sample order.

use std::collections::{HashMap, HashSet};

use scene::{FilterPolicy, Scene};

use super::{Built, EventRow, History, Strings, kind_index};

/// An interned string table while building.
#[derive(Default)]
struct Interner {
    strings: Strings,
    index: HashMap<Box<str>, u32>,
}

impl Interner {
    /// The id of `text` and whether it is new.
    fn intern(&mut self, text: &str) -> (u32, bool) {
        if let Some(&id) = self.index.get(text) {
            return (id, false);
        }
        let id = self.strings.push(text);
        self.index.insert(text.into(), id);
        (id, true)
    }
}

/// What a sample's entry costs while building, beyond its text: map entry, offset and per-entity tables.
const STRING_BYTES: usize = 48;
const ENTITY_BYTES: usize = 32;
const CLASS_BYTES: usize = 32;
const RUN_BYTES: usize = 12;
const EVENT_BYTES: usize = 24;

/// No run yet.
const NO_RUN: u32 = u32::MAX;

/// Collects the history from the Scenes of a file in sample order. When the estimate of what it holds passes the cap
/// it drops everything and the history is [`Built::TooLarge`].
pub(crate) struct HistoryBuilder {
    cap: usize,
    bytes: usize,
    over: bool,
    samples: u32,
    ids: Interner,
    words: Interner,
    /// `(entity, class)` in order of first appearance, and the pairs already in it.
    class_log: Vec<(u32, u32)>,
    class_seen: HashSet<u64>,
    /// Per entity: the position of its latest run in `run_log`.
    last_run: Vec<u32>,
    /// `(entity, [first, last])` in order of first sample.
    run_log: Vec<(u32, [u32; 2])>,
    events_of: Vec<u8>,
    events: Vec<EventRow>,
    event_entities: Vec<u32>,
}

impl HistoryBuilder {
    /// A builder that gives up at about `cap` bytes.
    pub(crate) fn new(cap: usize) -> Self {
        Self {
            cap,
            bytes: 0,
            over: false,
            samples: 0,
            ids: Interner::default(),
            words: Interner::default(),
            class_log: Vec::new(),
            class_seen: HashSet::new(),
            last_run: Vec::new(),
            run_log: Vec::new(),
            events_of: Vec::new(),
            events: Vec::new(),
            event_entities: Vec::new(),
        }
    }

    /// Adds the next sample, which is sample `n` for the `n`th call.
    pub(crate) fn add(&mut self, scene: &Scene) {
        if self.over {
            return;
        }
        let sample = self.samples;
        self.samples += 1;
        for entity in scene.entities() {
            let e = self.entity(entity.id().as_str());
            let classes = entity
                .latest_observation()
                .into_iter()
                .flat_map(|observation| &observation.classifications);
            for classification in classes {
                let word = self.word(classification.class_type.as_str());
                if self.class_seen.insert(u64::from(e) << 32 | u64::from(word)) {
                    self.class_log.push((e, word));
                    self.bytes += CLASS_BYTES;
                }
            }
            let run = self.last_run[e as usize];
            match self.run_log.get_mut(run as usize) {
                Some((_, span)) if span[1] + 1 == sample => span[1] = sample,
                _ => {
                    self.last_run[e as usize] = self.run_log.len() as u32;
                    self.run_log.push((e, [sample, sample]));
                    self.bytes += RUN_BYTES;
                }
            }
        }
        for event in scene.events() {
            let kind = kind_index(event.kind());
            let label = self.word(&event.label());
            let first = self.event_entities.len() as u32;
            for id in event.involved_entity_ids() {
                let e = self.entity(id.as_str());
                self.events_of[e as usize] |= 1 << kind;
                self.event_entities.push(e);
            }
            let count = self.event_entities.len() as u32 - first;
            self.bytes += EVENT_BYTES + count as usize * 4;
            self.events.push(EventRow {
                sample,
                kind,
                label,
                entities: [first, count],
            });
        }
        if self.bytes > self.cap {
            *self = Self {
                over: true,
                ..Self::new(self.cap)
            };
        }
    }

    fn entity(&mut self, id: &str) -> u32 {
        let (entity, new) = self.ids.intern(id);
        if new {
            self.last_run.push(NO_RUN);
            self.events_of.push(0);
            self.bytes += id.len() + STRING_BYTES + ENTITY_BYTES;
        }
        entity
    }

    fn word(&mut self, text: &str) -> u32 {
        let (word, new) = self.words.intern(text);
        if new {
            self.bytes += text.len() + STRING_BYTES;
        }
        word
    }

    /// The history, with each entity's file mask judged by `policy`'s rules over the classes it was recorded with. No
    /// policy (the file gave none) has no options.
    pub(crate) fn finish(self, policy: Option<&FilterPolicy>) -> Built {
        if self.over {
            return Built::TooLarge;
        }
        let entities = self.ids.strings.len();
        let options = policy.map_or(&[][..], |policy| &policy.options[..]);
        let mask_words = options.len().div_ceil(32);
        // What finishing allocates is not in `bytes` yet: each entity's mask and its two ranges, and the grouped
        // lists. Judge the total before allocating any of it.
        let finishing = entities
            .checked_mul(mask_words * 4 + 2 * size_of::<[u32; 2]>())
            .and_then(|bytes| {
                bytes.checked_add(
                    self.class_log.len() * 4 + self.run_log.len() * size_of::<[u32; 2]>(),
                )
            });
        let total = finishing.and_then(|bytes| bytes.checked_add(self.bytes));
        if total.is_none_or(|total| total > self.cap) {
            return Built::TooLarge;
        }
        let (classes, class_ids) = group(entities, self.class_log);
        let (runs, run_pairs) = group(entities, self.run_log);
        let mut file_mask = vec![0_u32; entities * mask_words];
        let mut names = Vec::new();
        for entity in 0..entities {
            let [first, count] = classes[entity];
            names.clear();
            names.extend(
                class_ids[first as usize..(first + count) as usize]
                    .iter()
                    .map(|&word| self.words.strings.get(word)),
            );
            for (i, option) in options.iter().enumerate() {
                if option.rule.matches(names.iter().copied()) {
                    file_mask[entity * mask_words + i / 32] |= 1 << (i % 32);
                }
            }
        }
        Built::Ready(Box::new(History {
            ids: self.ids.strings,
            words: self.words.strings,
            options: options.len() as u32,
            samples: self.samples,
            classes,
            class_ids,
            file_mask,
            events_of: self.events_of,
            runs,
            run_pairs,
            events: self.events,
            event_entities: self.event_entities,
        }))
    }
}

/// Collects a log of `(entity, item)` into per-entity ranges (first and count) over one list, keeping the log's order
/// within each entity.
fn group<T: Copy + Default>(entities: usize, log: Vec<(u32, T)>) -> (Vec<[u32; 2]>, Vec<T>) {
    let mut ranges = vec![[0_u32; 2]; entities];
    for &(entity, _) in &log {
        ranges[entity as usize][1] += 1;
    }
    let mut next = 0;
    for range in &mut ranges {
        range[0] = next;
        next += range[1];
    }
    let mut filled = vec![0_u32; entities];
    let mut items = vec![T::default(); log.len()];
    for (entity, item) in log {
        let e = entity as usize;
        items[(ranges[e][0] + filled[e]) as usize] = item;
        filled[e] += 1;
    }
    (ranges, items)
}
