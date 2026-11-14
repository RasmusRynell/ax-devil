//! Building a [`History`] from the Scenes of a file in sample order.

use std::collections::{HashMap, HashSet};

use foundation::{CancelToken, Cancelled};
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

#[cfg(test)]
pub(crate) type Finalizing = std::sync::Arc<dyn Fn(&CancelToken) + Send + Sync>;

/// No run yet.
const NO_RUN: u32 = u32::MAX;

/// Collects the history from the Scenes of a file in sample order.
pub(crate) struct HistoryBuilder {
    #[cfg(test)]
    pub(crate) finalizing: Option<Finalizing>,
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
    /// A builder for the full file history.
    pub(crate) fn new() -> Self {
        Self {
            #[cfg(test)]
            finalizing: None,
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
                }
            }
            let run = self.last_run[e as usize];
            match self.run_log.get_mut(run as usize) {
                Some((_, span)) if span[1] + 1 == sample => span[1] = sample,
                _ => {
                    self.last_run[e as usize] = self.run_log.len() as u32;
                    self.run_log.push((e, [sample, sample]));
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
            self.events.push(EventRow {
                sample,
                kind,
                label,
                entities: [first, count],
            });
        }
    }

    fn entity(&mut self, id: &str) -> u32 {
        let (entity, new) = self.ids.intern(id);
        if new {
            self.last_run.push(NO_RUN);
            self.events_of.push(0);
        }
        entity
    }

    fn word(&mut self, text: &str) -> u32 {
        self.words.intern(text).0
    }

    /// The history, with each entity's file mask judged by `policy`'s rules over the classes it was recorded with. No
    /// policy (the file gave none) has no options.
    pub(crate) fn finish(
        self,
        policy: Option<&FilterPolicy>,
        cancel: &CancelToken,
    ) -> Result<Built, Cancelled> {
        cancel.check()?;
        let entities = self.ids.strings.len();
        let options = policy.map_or(&[][..], |policy| &policy.options[..]);
        let mask_words = options.len().div_ceil(32);
        let (classes, class_ids) = group(entities, self.class_log, cancel)?;
        let (runs, run_pairs) = group(entities, self.run_log, cancel)?;
        #[cfg(test)]
        if let Some(finalizing) = self.finalizing {
            finalizing(cancel);
        }
        cancel.check()?;
        let mut file_mask = vec![0_u32; entities * mask_words];
        let mut names = Vec::new();
        for entity in 0..entities {
            cancel.check()?;
            let [first, count] = classes[entity];
            names.clear();
            for &word in &class_ids[first as usize..(first + count) as usize] {
                cancel.check()?;
                names.push(self.words.strings.get(word));
            }
            for (i, option) in options.iter().enumerate() {
                cancel.check()?;
                if option
                    .rule
                    .matches(names.iter().copied().take_while(|_| !cancel.is_cancelled()))
                {
                    file_mask[entity * mask_words + i / 32] |= 1 << (i % 32);
                }
            }
        }
        cancel.check()?;
        Ok(Built::Ready(Box::new(History {
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
        })))
    }
}

/// Collects a log of `(entity, item)` into per-entity ranges (first and count) over one list, keeping the log's order
/// within each entity.
fn group<T: Copy + Default>(
    entities: usize,
    log: Vec<(u32, T)>,
    cancel: &CancelToken,
) -> Result<(Vec<[u32; 2]>, Vec<T>), Cancelled> {
    cancel.check()?;
    let mut ranges = vec![[0_u32; 2]; entities];
    for &(entity, _) in &log {
        cancel.check()?;
        ranges[entity as usize][1] += 1;
    }
    let mut next = 0;
    for range in &mut ranges {
        cancel.check()?;
        range[0] = next;
        next += range[1];
    }
    cancel.check()?;
    let mut filled = vec![0_u32; entities];
    cancel.check()?;
    let mut items = vec![T::default(); log.len()];
    for (entity, item) in log {
        cancel.check()?;
        let e = entity as usize;
        items[(ranges[e][0] + filled[e]) as usize] = item;
        filled[e] += 1;
    }
    Ok((ranges, items))
}
