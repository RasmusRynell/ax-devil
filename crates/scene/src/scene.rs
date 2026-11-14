use std::collections::{HashMap, HashSet, hash_map::Entry};

use serde::{Deserialize, Serialize};
use ts_rs::TS;

use crate::{
    EntityId, Fields, Image, MotionState, Name, Observation, SceneError, TimeKind, TimeSlice,
};

/// One tracked or untracked thing, with its observations in insertion order once it is in a Scene.
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

    /// Where and what the entity was, in Scene insertion order; in the order added before that.
    pub fn observations(&self) -> &[Observation] {
        &self.observations
    }

    /// The last observation after applying arrival insertion at the Scene boundary.
    pub fn latest_observation(&self) -> Option<&Observation> {
        self.observations.last()
    }

    /// Adds an observation after the others. The final order is made once, by [`Scene::with_entities`], using
    /// Python's arrival insertion rule.
    pub fn push_observation(&mut self, observation: Observation) {
        if self.observations.is_empty() {
            // Most entities of a Scene have one observation; do not reserve the default four.
            self.observations.reserve_exact(1);
        }
        self.observations.push(observation);
    }

    /// The earliest to the latest time of the observations that have one on the `kind` clock; `None` when none has.
    pub fn span(&self, kind: TimeKind) -> Option<TimeSlice> {
        let mut times = self.observations.iter().filter_map(|o| o.time.on(kind));
        let first = times.next()?;
        let (start, end) = times.fold((first, first), |(start, end), time| {
            (start.min(time), end.max(time))
        });
        TimeSlice::new(start, end).ok()
    }
}

/// Which change to entity identity an operation is.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
#[ts(rename = "OperationKindWire")]
pub enum OperationKind {
    /// An entity is gone for good.
    Delete,
    /// An entity is now known by another id.
    Rename,
    /// Several entities are one.
    Merge,
    /// One entity is several.
    Split,
    /// A remark that changes nothing.
    Note,
}

impl OperationKind {
    /// The kind's name for event lists and filters.
    pub fn title(self) -> &'static str {
        match self {
            Self::Delete => "Delete",
            Self::Rename => "Rename",
            Self::Merge => "Merge",
            Self::Split => "Split",
            Self::Note => "Note",
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
    /// A remark at that moment; it involves no entity.
    Note {
        /// What the remark says.
        text: String,
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
            Operation::Note { .. } => OperationKind::Note,
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
            Operation::Note { text } => format!("{title} {text}"),
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
                Operation::Note { .. } => (None, &[], None),
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
    values: Fields,
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
            values: Fields::new(),
        }
    }

    /// Sets the entities. Each arriving observation moves before strictly greater neighbours on its timestamp
    /// clock (when supplied), otherwise its frame clock, stopping at a missing, equal or smaller value.
    /// Observation clocks are independent of the Scene key and are never synthesized.
    ///
    /// An id given twice keeps the position of its first entity and the value of its last.
    pub fn with_entities(mut self, entities: Vec<Entity>) -> Result<Self, SceneError> {
        let mut slots: HashMap<EntityId, usize> = HashMap::with_capacity(entities.len());
        let mut unique: Vec<Entity> = Vec::with_capacity(entities.len());
        for mut entity in entities {
            crate::order::order(&mut entity.observations);
            match slots.entry(entity.id.clone()) {
                Entry::Occupied(slot) => unique[*slot.get()] = entity,
                Entry::Vacant(slot) => {
                    slot.insert(unique.len());
                    unique.push(entity);
                }
            }
        }
        self.entities = unique;
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

    /// Sets the values the file reports about the moment as a whole, such as a count or a level.
    pub fn with_values(mut self, values: Fields) -> Self {
        self.values = values;
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

    /// The values the file reports about the moment as a whole.
    pub fn values(&self) -> &Fields {
        &self.values
    }
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<OperationKind>();
}

#[cfg(test)]
mod tests {
    use foundation::MediaTime;

    use super::*;
    use crate::{BoundingBox, Geometry, ObservationTime, SceneTime};

    fn id(text: &str) -> EntityId {
        EntityId::new(text).expect("non-empty")
    }

    fn observation(time: impl Into<ObservationTime>, x: f64) -> Observation {
        let bounds = BoundingBox::new_outside(x, 0.0, x + 0.1, 0.1).expect("a box");
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
    fn a_scene_orders_same_clock_observations_and_equal_times_keep_arrival_order() {
        let mut entity = Entity::new(id("a"));
        for (time, x) in [(5, 0.5), (2, 0.2), (5, 0.6), (9, 0.9), (2, 0.3)] {
            entity.push_observation(observation(SceneTime::Frame(time), x));
        }

        let scene = Scene::new(TimeSlice::at(SceneTime::Frame(0)))
            .with_entities(vec![entity])
            .expect("frames");

        let entity = &scene.entities()[0];
        let order: Vec<_> = entity
            .observations()
            .iter()
            .map(|o| o.geometry.bounds().left())
            .collect();
        assert_eq!(order, [0.2, 0.3, 0.5, 0.6, 0.9]);
        assert_eq!(
            entity.span(TimeKind::Frame),
            TimeSlice::new(SceneTime::Frame(2), SceneTime::Frame(9)).ok()
        );
        // No observation on the clock, no span: Python's sentinel start and end are this `None`.
        assert_eq!(entity.span(TimeKind::Timestamp), None);
    }

    #[test]
    fn a_scene_replaces_repeated_entities_and_accepts_other_observation_clocks() {
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
        // An id given twice keeps the first position and the last value.
        let mut replaced = entity_at("a", frame);
        replaced.motion_state = Some(MotionState::Stationary);
        let merged = scene()
            .with_entities(vec![
                entity_at("a", frame),
                entity_at("b", frame),
                replaced.clone(),
            ])
            .expect("duplicates are replaced");
        assert_eq!(merged.entities(), [replaced, entity_at("b", frame)]);
        let other = scene()
            .with_entities(vec![entity_at("a", stamp)])
            .expect("other clock");
        assert_eq!(
            other.entities()[0].latest_observation().unwrap().time,
            stamp.into()
        );
        assert_eq!(other.entities()[0].span(TimeKind::Frame), None);
        // Timestamp order wins when both clocks are supplied, independently of the Scene key.
        let mut dual = Entity::new(id("a"));
        for (frame, us, x) in [(2, 10, 0.2), (1, 20, 0.1)] {
            let time = ObservationTime::new(Some(frame), Some(MediaTime(us))).expect("a time");
            dual.push_observation(observation(time, x));
        }
        let lefts = |scene: Scene| -> Vec<f64> {
            let observations = scene.entities()[0].observations();
            observations
                .iter()
                .map(|o| o.geometry.bounds().left())
                .collect()
        };
        let by_frame = scene().with_entities(vec![dual.clone()]).expect("frames");
        let by_time = Scene::new(TimeSlice::at(stamp)).with_entities(vec![dual]);
        assert_eq!(lefts(by_frame), [0.2, 0.1]);
        assert_eq!(lefts(by_time.expect("timestamps")), [0.2, 0.1]);
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
