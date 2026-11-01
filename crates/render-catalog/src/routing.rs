//! Which recipe draws which entity and relation of a filtered Scene.
//!
//! An entity is routed by the primary classification of its latest observation: none goes to the `unclassified`
//! recipe, a type with a recipe of its own to that recipe, any other type to the `classified` one. An entity without
//! observations is not drawn. A relation is routed by its type and drawn only when both endpoints are in the view and
//! have observations.

use std::collections::HashMap;

use scene::{Classification, Entity, EntityRelation, Observation, SceneView};

use crate::ir::Catalog;

/// An entity with the observation and classification that are drawn.
pub(crate) struct EntityRow<'a> {
    pub(crate) entity: &'a Entity,
    pub(crate) observation: &'a Observation,
    pub(crate) classification: Option<&'a Classification>,
}

/// One end of a relation.
#[derive(Clone, Copy)]
pub(crate) struct Endpoint<'a> {
    pub(crate) entity: &'a Entity,
    pub(crate) observation: &'a Observation,
}

/// A relation with its two ends.
pub(crate) struct RelationRow<'a> {
    pub(crate) relation: &'a EntityRelation,
    pub(crate) source: Endpoint<'a>,
    pub(crate) target: Endpoint<'a>,
}

/// The rows of each recipe, indexed like [`Catalog::recipes`].
pub(crate) struct Routed<'a> {
    pub(crate) entities: Vec<Vec<EntityRow<'a>>>,
    pub(crate) relations: Vec<Vec<RelationRow<'a>>>,
}

/// Routes the entities and relations of `view` to the recipes `wanted` accepts; rows of other recipes are left out.
pub(crate) fn route<'a>(
    catalog: &Catalog,
    view: &SceneView<'a>,
    wanted: impl Fn(usize) -> bool,
) -> Routed<'a> {
    let count = catalog.recipes.len();
    let mut routed = Routed {
        entities: (0..count).map(|_| Vec::new()).collect(),
        relations: (0..count).map(|_| Vec::new()).collect(),
    };
    let routing = &catalog.routing;
    let mut with_observation: HashMap<&str, Endpoint<'a>> = HashMap::new();
    let has_relations = !view.scene.relations().is_empty()
        && routing.relations.values().any(|&recipe| wanted(recipe));
    for entity in view.entities() {
        let Some(observation) = entity.latest_observation() else {
            continue;
        };
        if has_relations {
            with_observation.insert(
                entity.id().as_str(),
                Endpoint {
                    entity,
                    observation,
                },
            );
        }
        let classification = observation.primary_classification();
        let recipe = match classification {
            None => routing.unclassified,
            Some(found) => routing
                .classifications
                .get(found.class_type.as_str())
                .copied()
                .unwrap_or(routing.classified),
        };
        if wanted(recipe) {
            routed.entities[recipe].push(EntityRow {
                entity,
                observation,
                classification,
            });
        }
    }
    if has_relations {
        for relation in view.scene.relations() {
            let Some(&recipe) = routing.relations.get(relation.kind()) else {
                continue;
            };
            let ends = (
                with_observation.get(relation.source().as_str()),
                with_observation.get(relation.target().as_str()),
            );
            if let (true, (Some(&source), Some(&target))) = (wanted(recipe), ends) {
                routed.relations[recipe].push(RelationRow {
                    relation,
                    source,
                    target,
                });
            }
        }
    }
    routed
}
