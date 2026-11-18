//! The bounded caches a track keeps in memory: decoded Scenes and encoded Sample Items.

use std::{
    collections::{BTreeMap, HashMap},
    mem::{size_of, size_of_val},
    sync::Arc,
};

use scene::{Fields, Scene, Value};

/// Least recently used values by sample index, kept under a byte budget.
pub(crate) struct Lru<V> {
    budget: usize,
    used: usize,
    tick: u64,
    entries: HashMap<usize, Entry<V>>,
    /// Entries by the tick they were last used, oldest first.
    recency: BTreeMap<u64, usize>,
}

/// Decoded Scenes, measured by [`footprint`].
pub(crate) type SceneCache = Lru<Arc<Scene>>;

/// Encoded Sample Items, measured by their length.
pub(crate) type ItemCache = Lru<Arc<[u8]>>;

struct Entry<V> {
    value: V,
    bytes: usize,
    tick: u64,
}

impl<V: Clone> Lru<V> {
    pub(crate) fn new(budget: usize) -> Self {
        Self {
            budget,
            used: 0,
            tick: 0,
            entries: HashMap::new(),
            recency: BTreeMap::new(),
        }
    }

    /// The value of sample `index`, marked as just used.
    pub(crate) fn get(&mut self, index: usize) -> Option<V> {
        self.tick += 1;
        let entry = self.entries.get_mut(&index)?;
        self.recency.remove(&entry.tick);
        entry.tick = self.tick;
        self.recency.insert(self.tick, index);
        Some(entry.value.clone())
    }

    /// Keeps `value` of `bytes`, evicting the least recently used until it fits. A value larger than the whole
    /// budget is not kept.
    pub(crate) fn insert(&mut self, index: usize, value: V, bytes: usize) {
        if bytes > self.budget || self.entries.contains_key(&index) {
            return;
        }
        while self.used + bytes > self.budget {
            let Some((_, oldest)) = self.recency.pop_first() else {
                break;
            };
            if let Some(evicted) = self.entries.remove(&oldest) {
                self.used -= evicted.bytes;
            }
        }
        self.tick += 1;
        self.recency.insert(self.tick, index);
        self.entries.insert(
            index,
            Entry {
                value,
                bytes,
                tick: self.tick,
            },
        );
        self.used += bytes;
    }

    /// How many bytes are held.
    #[cfg(test)]
    pub(crate) fn used(&self) -> usize {
        self.used
    }
}

/// An estimate of the memory a Scene holds: the sizes of its values plus the text and lists they own. It leans high
/// (no allocator slack is subtracted) so the cache stays under its budget.
pub(crate) fn footprint(scene: &Scene) -> usize {
    let mut bytes = size_of::<Scene>() + fields(scene.debug());
    for entity in scene.entities() {
        bytes += size_of::<scene::Entity>() + entity.id().as_str().len() + 16;
        bytes += entity
            .images
            .iter()
            .map(|image| size_of::<scene::Image>() + image.data.len())
            .sum::<usize>();
        for observation in entity.observations() {
            bytes += size_of::<scene::Observation>()
                + fields(observation.source_data())
                + fields(observation.debug());
            if let Some(points) = observation.geometry.polygon_points() {
                bytes += size_of_val(points);
            }
            if observation.measurements.is_some() {
                bytes += size_of::<scene::Measurements>();
            }
            for class in &observation.classifications {
                bytes += size_of::<scene::Classification>()
                    + class.class_type.as_str().len()
                    + fields(&class.attributes);
            }
        }
    }
    bytes += size_of_val(scene.events());
    bytes += scene.relations().len() * (size_of::<scene::EntityRelation>() + 32);
    bytes
}

fn fields(fields: &Fields) -> usize {
    fields
        .iter()
        .map(|(key, value)| size_of::<(scene::Name, Value)>() + key.len() + payload(value))
        .sum()
}

fn payload(value: &Value) -> usize {
    match value {
        Value::Str(text) => text.len(),
        Value::List(items) => items
            .iter()
            .map(|item| size_of::<Value>() + payload(item))
            .sum(),
        Value::Map(entries) => fields(entries),
        Value::Null | Value::Bool(_) | Value::Int(_) | Value::Float(_) => 0,
    }
}
