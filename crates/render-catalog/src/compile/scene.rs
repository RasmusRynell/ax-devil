//! Which part of the row or render context a reference reads.

use std::sync::Arc;

use crate::definitions::{Type, types};
use crate::ir::{ContextField, End, SceneField, SceneKind};

/// The render context number called `name`.
pub(super) fn context_field(name: &str) -> Option<ContextField> {
    Some(match name {
        "width" => ContextField::Width,
        "height" => ContextField::Height,
        "aspect_ratio" => ContextField::AspectRatio,
        "scale_factor" => ContextField::ScaleFactor,
        "px_h" => ContextField::PxH,
        "px_v" => ContextField::PxV,
        "px_min" => ContextField::PxMin,
        _ => return None,
    })
}

/// The deepest [`SceneField`] along `names`, a path through the row type already checked to exist, with that field's
/// type and the field indices of the rest of the path.
pub(super) fn scene_field(scene: SceneKind, names: &[&str]) -> (SceneField, Type, Vec<u32>) {
    let t = types();
    let root = match scene {
        SceneKind::Entity => &t.scene,
        SceneKind::Relation => &t.relation_scene,
    };
    let mut kind = Arc::clone(root);
    let mut best = None;
    let mut indices = Vec::with_capacity(names.len());
    for (depth, name) in names.iter().enumerate() {
        let Some((index, field)) = kind.field(name) else {
            break;
        };
        indices.push(index as u32);
        kind = Arc::clone(field);
        if let Some(found) = named(scene, &names[..=depth]) {
            best = Some((found, Arc::clone(&kind), depth + 1));
        }
    }
    match best {
        Some((field, kind, consumed)) => (field, kind, indices[consumed..].to_vec()),
        // Every first name of a row type is a field above, so this is never reached; read the whole first field.
        None => (SceneField::Entity, Arc::clone(&t.scene), indices),
    }
}

fn named(scene: SceneKind, path: &[&str]) -> Option<SceneField> {
    Some(match (scene, path) {
        (SceneKind::Entity, ["entity"]) => SceneField::Entity,
        (SceneKind::Entity, ["entity", "id"]) => SceneField::EntityId,
        (SceneKind::Entity, ["entity", "motion_state"]) => SceneField::MotionState,
        (SceneKind::Entity, ["observation"]) => SceneField::Observation,
        (SceneKind::Entity, ["observation", "geometry"]) => SceneField::Geometry,
        (SceneKind::Entity, ["observation", "polygon_points"]) => SceneField::PolygonPoints,
        (SceneKind::Entity, ["observation", "velocity"]) => SceneField::Velocity,
        (SceneKind::Entity, ["classification"]) => SceneField::Classification,
        (SceneKind::Entity, ["classification", "type"]) => SceneField::ClassType,
        (SceneKind::Entity, ["classification", "score"]) => SceneField::Score,
        (SceneKind::Entity, ["classification", "confidence"]) => SceneField::Confidence,
        (SceneKind::Relation, ["relation"]) => SceneField::Relation,
        (SceneKind::Relation, ["relation", "type"]) => SceneField::RelationType,
        (SceneKind::Relation, [end, rest @ ..]) => {
            let end = match *end {
                "source" => End::Source,
                "target" => End::Target,
                _ => return None,
            };
            match rest {
                [] => SceneField::Endpoint(end),
                ["entity"] => SceneField::EndpointEntity(end),
                ["entity", "id"] => SceneField::EndpointId(end),
                ["observation"] => SceneField::EndpointObservation(end),
                ["observation", "geometry"] => SceneField::EndpointGeometry(end),
                _ => return None,
            }
        }
        _ => return None,
    })
}
