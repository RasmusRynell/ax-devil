//! The fragments of one start key, joined into the Scene that key is.

use std::collections::HashMap;

use scene::{Entity, EntityId, EntityRelation, Event, Fields, Scene, SceneError, TimeSlice};

use crate::lru::footprint;

/// The Scenes given for one start key. A key with one Scene stays that Scene; a second joins them.
///
/// Joining: entities in the order they first appear, an entity id seen again adds its observations and images and
/// lets a later end reason or motion state win; relations, events and debug fields are appended; the slice ends at
/// the later end.
pub(crate) struct Group {
    content: Content,
    bytes: usize,
}

enum Content {
    Whole(Scene),
    Joined(Box<Parts>),
}

/// A Scene taken apart so more can be added to it without rebuilding.
struct Parts {
    slice: TimeSlice,
    entities: Vec<Entity>,
    at: HashMap<EntityId, usize>,
    events: Vec<Event>,
    relations: Vec<EntityRelation>,
    debug: Fields,
}

impl Group {
    pub(crate) fn new(scene: Scene) -> Self {
        Self {
            bytes: footprint(&scene),
            content: Content::Whole(scene),
        }
    }

    /// An estimate of the memory the group holds.
    pub(crate) fn bytes(&self) -> usize {
        self.bytes
    }

    /// Adds the contents of `scene`, which has the same start key and clock.
    pub(crate) fn merge(&mut self, scene: &Scene) -> Result<(), SceneError> {
        self.bytes += footprint(scene);
        if let Content::Whole(first) = &self.content {
            let parts = Parts::of(first);
            self.content = Content::Joined(Box::new(parts));
        }
        match &mut self.content {
            Content::Joined(parts) => parts.add(scene),
            Content::Whole(_) => Ok(()),
        }
    }

    pub(crate) fn into_scene(self) -> Result<Scene, SceneError> {
        match self.content {
            Content::Whole(scene) => Ok(scene),
            Content::Joined(parts) => parts.into_scene(),
        }
    }

    /// The Scene the group is now, which later fragments do not change.
    pub(crate) fn to_scene(&self) -> Result<Scene, SceneError> {
        match &self.content {
            Content::Whole(scene) => Ok(scene.clone()),
            Content::Joined(parts) => parts.clone_parts().into_scene(),
        }
    }
}

impl Parts {
    fn into_scene(self) -> Result<Scene, SceneError> {
        let Self {
            slice,
            entities,
            events,
            relations,
            debug,
            ..
        } = self;
        let scene = Scene::new(slice)
            .with_entities(entities)?
            .with_events(events)?;
        Ok(scene.with_relations(relations).with_debug(debug))
    }

    /// A copy of what the Scene is made of; the lookup is not needed to build it.
    fn clone_parts(&self) -> Self {
        Self {
            slice: self.slice,
            entities: self.entities.clone(),
            at: HashMap::new(),
            events: self.events.clone(),
            relations: self.relations.clone(),
            debug: self.debug.clone(),
        }
    }

    fn of(scene: &Scene) -> Self {
        let mut parts = Self {
            slice: scene.time(),
            entities: Vec::new(),
            at: HashMap::new(),
            events: Vec::new(),
            relations: Vec::new(),
            debug: Fields::new(),
        };
        parts.take_contents(scene);
        parts
    }

    fn add(&mut self, scene: &Scene) -> Result<(), SceneError> {
        self.slice = TimeSlice::new(self.slice.start(), self.slice.end().max(scene.time().end()))?;
        self.take_contents(scene);
        Ok(())
    }

    fn take_contents(&mut self, scene: &Scene) {
        for entity in scene.entities() {
            let Some(&at) = self.at.get(entity.id()) else {
                self.at.insert(entity.id().clone(), self.entities.len());
                self.entities.push(entity.clone());
                continue;
            };
            let joined = &mut self.entities[at];
            for observation in entity.observations() {
                joined.push_observation(observation.clone());
            }
            joined.images.extend(entity.images.iter().cloned());
            if entity.end_reason.is_some() {
                joined.end_reason.clone_from(&entity.end_reason);
            }
            if entity.motion_state.is_some() {
                joined.motion_state = entity.motion_state;
            }
        }
        self.events.extend(scene.events().iter().cloned());
        self.relations.extend(scene.relations().iter().cloned());
        self.debug.extend(scene.debug().iter().cloned());
    }
}
