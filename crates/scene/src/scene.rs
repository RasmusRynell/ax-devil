use std::collections::HashSet;

use crate::{
    EntityId, Fields, Image, MotionState, Name, Observation, SceneError, SceneTime, TimeSlice,
};

/// One tracked or untracked thing, with its observations sorted by time.
#[derive(Clone, Debug, PartialEq)]
pub struct Entity {
    id: EntityId,
    observations: Vec<Observation>,
    /// Pictures of the entity.
    pub images: Vec<Image>,
    /// Why the source stopped tracking the entity.
    pub end_reason: Option<Name>,
    /// Whether the entity is moving.
    pub motion_state: Option<MotionState>,
}

impl Entity {
    /// An entity with no observations.
    pub fn new(id: EntityId) -> Self {
        Self {
            id,
            observations: Vec::new(),
            images: Vec::new(),
            end_reason: None,
            motion_state: None,
        }
    }

    /// The id the source gave the entity, or one the decoder made up.
    pub fn id(&self) -> &EntityId {
        &self.id
    }

    /// Where and what the entity was, sorted by time.
    pub fn observations(&self) -> &[Observation] {
        &self.observations
    }

    /// The observation with the latest time.
    pub fn latest_observation(&self) -> Option<&Observation> {
        self.observations.last()
    }

    /// Adds an observation behind every one that is not later; observations of equal time keep the order they were
    /// added in. Adding in time order appends.
    pub fn push_observation(&mut self, observation: Observation) {
        match self.observations.last() {
            Some(last) if last.time > observation.time => {
                let at = self
                    .observations
                    .partition_point(|o| o.time <= observation.time);
                self.observations.insert(at, observation);
            }
            _ => {
                if self.observations.is_empty() {
                    // Most entities of a Scene have one observation; do not reserve the default four.
                    self.observations.reserve_exact(1);
                }
                self.observations.push(observation);
            }
        }
    }

    /// The times of the first and last observation.
    pub fn time_span(&self) -> Option<(SceneTime, SceneTime)> {
        Some((
            self.observations.first()?.time,
            self.observations.last()?.time,
        ))
    }
}

/// Which change to entity identity an [`Operation`] is.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum OperationKind {
    /// An entity is gone for good.
    Delete,
    /// An entity is now known by another id.
    Rename,
    /// Several entities are one.
    Merge,
    /// One entity is several.
    Split,
}

impl OperationKind {
    /// The kind's name for event lists and filters.
    pub fn title(self) -> &'static str {
        match self {
            Self::Delete => "Delete",
            Self::Rename => "Rename",
            Self::Merge => "Merge",
            Self::Split => "Split",
        }
    }
}

/// A change to entity identity.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Operation {
    /// The entity is gone for good.
    Delete {
        /// The removed entity.
        entity: EntityId,
    },
    /// The entity `from` is now known as `to`.
    Rename {
        /// The old id.
        from: EntityId,
        /// The new id.
        to: EntityId,
    },
    /// The `sources` are now the one entity `target`.
    Merge {
        /// The entities that were merged.
        sources: Vec<EntityId>,
        /// The entity they became.
        target: EntityId,
    },
    /// The entity `source` is now the several entities `targets`.
    Split {
        /// The entity that was split.
        source: EntityId,
        /// The entities it became.
        targets: Vec<EntityId>,
    },
}

/// A change to entity identity, and when it applies.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Event {
    /// When the change applies.
    pub time: TimeSlice,
    /// What changed.
    pub operation: Operation,
}

impl Event {
    /// An event of `operation` at `time`.
    pub fn new(time: TimeSlice, operation: Operation) -> Self {
        Self { time, operation }
    }

    /// Which kind of change the event is.
    pub fn kind(&self) -> OperationKind {
        match self.operation {
            Operation::Delete { .. } => OperationKind::Delete,
            Operation::Rename { .. } => OperationKind::Rename,
            Operation::Merge { .. } => OperationKind::Merge,
            Operation::Split { .. } => OperationKind::Split,
        }
    }

    /// A one-line description for event lists, such as `Rename 12 → 57`.
    pub fn label(&self) -> String {
        let list = |ids: &[EntityId]| {
            ids.iter()
                .map(EntityId::as_str)
                .collect::<Vec<_>>()
                .join(", ")
        };
        let title = self.kind().title();
        match &self.operation {
            Operation::Delete { entity } => format!("{title} {entity}"),
            Operation::Rename { from, to } => format!("{title} {from} → {to}"),
            Operation::Merge { sources, target } => format!("{title} {} → {target}", list(sources)),
            Operation::Split { source, targets } => format!("{title} {source} → {}", list(targets)),
        }
    }

    /// The entities the event involves: the ones it changes first, then the ones they become.
    pub fn involved_entity_ids(&self) -> impl Iterator<Item = &EntityId> {
        let (first, middle, last): (Option<&EntityId>, &[EntityId], Option<&EntityId>) =
            match &self.operation {
                Operation::Delete { entity } => (Some(entity), &[], None),
                Operation::Rename { from, to } => (Some(from), &[], Some(to)),
                Operation::Merge { sources, target } => (None, sources, Some(target)),
                Operation::Split { source, targets } => (Some(source), targets, None),
            };
        first.into_iter().chain(middle).chain(last)
    }
}

/// A directed relation between two entities of a Scene, such as `has_part`.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct EntityRelation {
    kind: Name,
    source: EntityId,
    target: EntityId,
}

impl EntityRelation {
    /// The relation `kind` from `source` to `target`; `kind` must not be empty.
    pub fn new(
        kind: impl Into<Name>,
        source: EntityId,
        target: EntityId,
    ) -> Result<Self, SceneError> {
        let kind = kind.into();
        if kind.is_empty() {
            return Err(SceneError::EmptyRelationType);
        }
        Ok(Self {
            kind,
            source,
            target,
        })
    }

    /// What the relation is.
    pub fn kind(&self) -> &str {
        &self.kind
    }

    /// The entity the relation starts at.
    pub fn source(&self) -> &EntityId {
        &self.source
    }

    /// The entity the relation points to.
    pub fn target(&self) -> &EntityId {
        &self.target
    }
}

/// Everything an overlay file says about one moment.
///
/// A Scene is built whole and then shared; nothing changes a built Scene.
#[derive(Clone, Debug, PartialEq)]
pub struct Scene {
    time: TimeSlice,
    entities: Vec<Entity>,
    events: Vec<Event>,
    relations: Vec<EntityRelation>,
    debug: Fields,
}

impl Scene {
    /// A Scene of `time` with nothing in it.
    pub fn new(time: TimeSlice) -> Self {
        Self {
            time,
            entities: Vec::new(),
            events: Vec::new(),
            relations: Vec::new(),
            debug: Fields::new(),
        }
    }

    /// Sets the entities. Their ids must differ and every observation must be on the Scene's clock.
    pub fn with_entities(mut self, entities: Vec<Entity>) -> Result<Self, SceneError> {
        let kind = self.time.kind();
        let mut ids = HashSet::with_capacity(entities.len());
        for entity in &entities {
            if !ids.insert(entity.id.as_str()) {
                return Err(SceneError::DuplicateEntity(entity.id.clone()));
            }
            if entity.observations.iter().any(|o| o.time.kind() != kind) {
                return Err(SceneError::TimeKindMismatch);
            }
        }
        self.entities = entities;
        Ok(self)
    }

    /// Sets the events. Every event must be on the Scene's clock.
    pub fn with_events(mut self, events: Vec<Event>) -> Result<Self, SceneError> {
        if events
            .iter()
            .any(|event| event.time.kind() != self.time.kind())
        {
            return Err(SceneError::TimeKindMismatch);
        }
        self.events = events;
        Ok(self)
    }

    /// Sets the relations, dropping repeats and keeping the order they first appear in.
    pub fn with_relations(mut self, relations: impl IntoIterator<Item = EntityRelation>) -> Self {
        let mut seen = HashSet::new();
        self.relations = relations
            .into_iter()
            .filter(|relation| seen.insert(relation.clone()))
            .collect();
        self
    }

    /// Sets the decoder diagnostics shown only in debug views.
    pub fn with_debug(mut self, debug: Fields) -> Self {
        self.debug = debug;
        self
    }

    /// The moment this Scene describes.
    pub fn time(&self) -> TimeSlice {
        self.time
    }

    /// The things seen at that moment.
    pub fn entities(&self) -> &[Entity] {
        &self.entities
    }

    /// The entity with this id.
    pub fn entity(&self, id: &str) -> Option<&Entity> {
        self.entities.iter().find(|entity| entity.id == id)
    }

    /// Changes to entity identity announced at that moment.
    pub fn events(&self) -> &[Event] {
        &self.events
    }

    /// Relations between the Scene's entities.
    pub fn relations(&self) -> &[EntityRelation] {
        &self.relations
    }

    /// Decoder diagnostics shown only in debug views.
    pub fn debug(&self) -> &Fields {
        &self.debug
    }
}

#[cfg(test)]
mod tests {
    use foundation::MediaTime;

    use super::*;
    use crate::{BoundingBox, Geometry};

    fn id(text: &str) -> EntityId {
        EntityId::new(text).expect("non-empty")
    }

    fn observation(time: SceneTime, x: f64) -> Observation {
        let bounds = BoundingBox::new(x, 0.0, x + 0.1, 0.1).expect("a box");
        Observation::new(time, Geometry::Box(bounds))
    }

    #[test]
    fn events_describe_themselves() {
        let at = TimeSlice::at(SceneTime::Frame(0));
        let ids = |texts: &[&str]| texts.iter().map(|t| id(t)).collect::<Vec<_>>();
        let events = [
            Event::new(at, Operation::Delete { entity: id("12") }),
            Event::new(
                at,
                Operation::Rename {
                    from: id("12"),
                    to: id("57"),
                },
            ),
            Event::new(
                at,
                Operation::Merge {
                    sources: ids(&["1", "2"]),
                    target: id("3"),
                },
            ),
            Event::new(
                at,
                Operation::Split {
                    source: id("3"),
                    targets: ids(&["4", "5"]),
                },
            ),
        ];

        let labels: Vec<_> = events.iter().map(Event::label).collect();
        assert_eq!(
            labels,
            [
                "Delete 12",
                "Rename 12 → 57",
                "Merge 1, 2 → 3",
                "Split 3 → 4, 5"
            ]
        );
        let kinds: Vec<_> = events.iter().map(|e| e.kind().title()).collect();
        assert_eq!(kinds, ["Delete", "Rename", "Merge", "Split"]);
        let involved: Vec<Vec<&str>> = events
            .iter()
            .map(|e| e.involved_entity_ids().map(EntityId::as_str).collect())
            .collect();
        assert_eq!(
            involved,
            [
                vec!["12"],
                vec!["12", "57"],
                vec!["1", "2", "3"],
                vec!["3", "4", "5"]
            ]
        );
    }

    #[test]
    fn observations_stay_in_time_order_and_equal_times_in_arrival_order() {
        let mut entity = Entity::new(id("a"));
        for (time, x) in [(5, 0.5), (2, 0.2), (5, 0.6), (9, 0.9), (2, 0.3)] {
            entity.push_observation(observation(SceneTime::Frame(time), x));
        }

        let order: Vec<_> = entity
            .observations()
            .iter()
            .map(|o| (o.time, o.geometry.bounds().left()))
            .collect();
        assert_eq!(
            order,
            [
                (SceneTime::Frame(2), 0.2),
                (SceneTime::Frame(2), 0.3),
                (SceneTime::Frame(5), 0.5),
                (SceneTime::Frame(5), 0.6),
                (SceneTime::Frame(9), 0.9),
            ]
        );
        assert_eq!(
            entity.time_span(),
            Some((SceneTime::Frame(2), SceneTime::Frame(9)))
        );
    }

    #[test]
    fn a_scene_holds_unique_entities_and_one_kind_of_time() {
        let scene = || Scene::new(TimeSlice::at(SceneTime::Frame(0)));
        let entity_at = |name: &str, time| {
            let mut entity = Entity::new(id(name));
            entity.push_observation(observation(time, 0.1));
            entity
        };
        let frame = SceneTime::Frame(0);
        let stamp = SceneTime::Timestamp(MediaTime(0));

        assert!(
            scene()
                .with_entities(vec![entity_at("a", frame), entity_at("b", frame)])
                .is_ok()
        );
        assert_eq!(
            scene().with_entities(vec![entity_at("a", frame), entity_at("a", frame)]),
            Err(SceneError::DuplicateEntity(id("a")))
        );
        assert_eq!(
            scene().with_entities(vec![entity_at("a", stamp)]),
            Err(SceneError::TimeKindMismatch)
        );
        let late = Event::new(TimeSlice::at(stamp), Operation::Delete { entity: id("a") });
        assert_eq!(
            scene().with_events(vec![late]),
            Err(SceneError::TimeKindMismatch)
        );
    }

    #[test]
    fn relations_need_a_type_and_repeats_are_dropped() {
        assert_eq!(
            EntityRelation::new("", id("a"), id("b")),
            Err(SceneError::EmptyRelationType)
        );
        let relation = EntityRelation::new("has_part", id("a"), id("b")).expect("a relation");

        let scene = Scene::new(TimeSlice::at(SceneTime::Frame(0)))
            .with_relations([relation.clone(), relation.clone()]);

        assert_eq!(scene.relations(), [relation]);
    }
}
