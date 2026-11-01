//! The bounded cache of decoded Scenes a track keeps in memory.

use std::{
    collections::{BTreeMap, HashMap},
    mem::{size_of, size_of_val},
    sync::Arc,
};

use scene::{Fields, Geometry, Scene, Value};

/// Least recently used Scenes by sample index, kept under a byte budget.
pub(crate) struct SceneCache {
    budget: usize,
    used: usize,
    tick: u64,
    entries: HashMap<usize, Entry>,
    /// Entries by the tick they were last used, oldest first.
    recency: BTreeMap<u64, usize>,
}

struct Entry {
    scene: Arc<Scene>,
    bytes: usize,
    tick: u64,
}

impl SceneCache {
    pub(crate) fn new(budget: usize) -> Self {
        Self {
            budget,
            used: 0,
            tick: 0,
            entries: HashMap::new(),
            recency: BTreeMap::new(),
        }
    }

    /// The Scene of sample `index`, marked as just used.
    pub(crate) fn get(&mut self, index: usize) -> Option<Arc<Scene>> {
        self.tick += 1;
        let entry = self.entries.get_mut(&index)?;
        self.recency.remove(&entry.tick);
        entry.tick = self.tick;
        self.recency.insert(self.tick, index);
        Some(entry.scene.clone())
    }

    /// Keeps `scene`, evicting the least recently used until it fits. A Scene larger than the whole budget is not
    /// kept.
    pub(crate) fn insert(&mut self, index: usize, scene: Arc<Scene>) {
        let bytes = footprint(&scene);
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
                scene,
                bytes,
                tick: self.tick,
            },
        );
        self.used += bytes;
    }

    /// How many bytes of Scenes are held, by [`footprint`].
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
            if let Geometry::Polygon(polygon) = &observation.geometry {
                bytes += size_of_val(polygon.points());
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
