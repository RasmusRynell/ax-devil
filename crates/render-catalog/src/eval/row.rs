//! The Scene values a recipe reads: the entity or relation being drawn, as catalog values.

use std::sync::{Arc, LazyLock};

use scene::{BoundingBox, Geometry, MotionState, Value as SceneValue};

use super::value::{point, record, text};
use super::{Fail, invalid};
use crate::ir::{Binding, BindingShape, End, SceneField, Value};
use crate::routing::{Endpoint, EntityRow, RelationRow};

/// The row a recipe is running for.
#[derive(Clone, Copy)]
pub(super) enum RowRef<'m> {
    Entity(&'m EntityRow<'m>),
    Relation(&'m RelationRow<'m>),
}

static MOTION: LazyLock<[Arc<str>; 3]> = LazyLock::new(|| {
    [
        Arc::from("moving"),
        Arc::from("stationary"),
        Arc::from("unknown"),
    ]
});

fn geometry(bounds: BoundingBox) -> Value {
    record([
        Value::Number(bounds.left()),
        Value::Number(bounds.top()),
        Value::Number(bounds.width()),
        Value::Number(bounds.height()),
    ])
}

fn id(entity: &scene::Entity) -> Value {
    text(entity.id().as_str())
}

fn number_or_null(value: Option<f64>) -> Value {
    value.map_or(Value::Null, Value::Number)
}

impl RowRef<'_> {
    /// The part of the row `field` names. Fields of the other kind of row read as null.
    pub(super) fn read(self, field: SceneField) -> Value {
        match self {
            Self::Entity(row) => entity_field(row, field),
            Self::Relation(row) => relation_field(row, field),
        }
    }

    /// The classification attribute a binding projects, null when the row has none.
    pub(super) fn binding(self, binding: &Binding) -> Result<Value, Fail> {
        let Self::Entity(EntityRow {
            classification: Some(classification),
            ..
        }) = self
        else {
            return Ok(Value::Null);
        };
        let attribute = classification.attribute(&binding.attribute);
        Ok(match binding.shape {
            BindingShape::Bool => attribute
                .and_then(SceneValue::as_bool)
                .map_or(Value::Null, Value::Bool),
            BindingShape::Number => match attribute {
                // A boolean is not a number here.
                Some(SceneValue::Int(_) | SceneValue::Float(_)) => {
                    let number = attribute.and_then(SceneValue::as_f64).unwrap_or(f64::NAN);
                    if !number.is_finite() {
                        return Err(invalid("number must be finite."));
                    }
                    Value::Number(number)
                }
                _ => Value::Null,
            },
            BindingShape::HighestScoreColor => highest_score_color(attribute),
        })
    }
}

fn entity_field(row: &EntityRow<'_>, field: SceneField) -> Value {
    let entity = row.entity;
    let observation = row.observation;
    let motion = || {
        entity.motion_state.map_or(Value::Null, |state| {
            let index = match state {
                MotionState::Moving => 0,
                MotionState::Stationary => 1,
                MotionState::Unknown => 2,
            };
            Value::Text(Arc::clone(&MOTION[index]))
        })
    };
    let polygon = || match &observation.geometry {
        Geometry::Polygon(polygon) => Value::List(
            polygon
                .points()
                .iter()
                .map(|corner| point(corner.x(), corner.y()))
                .collect(),
        ),
        Geometry::Box(_) => Value::Null,
    };
    let velocity = || {
        observation
            .measurements
            .as_ref()
            .and_then(|measured| measured.image_velocity)
            .map_or(Value::Null, |v| {
                record([Value::Number(v.vx), Value::Number(v.vy)])
            })
    };
    let classification = || {
        row.classification.map_or(Value::Null, |found| {
            record([
                text(found.class_type.as_str()),
                number_or_null(found.score.map(scene::Score::value)),
                number_or_null(found.confidence.map(scene::Confidence::value)),
            ])
        })
    };
    match field {
        SceneField::Entity => record([id(entity), motion()]),
        SceneField::EntityId => id(entity),
        SceneField::MotionState => motion(),
        SceneField::Observation => record([
            geometry(observation.geometry.bounds()),
            polygon(),
            velocity(),
        ]),
        SceneField::Geometry => geometry(observation.geometry.bounds()),
        SceneField::PolygonPoints => polygon(),
        SceneField::Velocity => velocity(),
        SceneField::Classification => classification(),
        SceneField::ClassType => row
            .classification
            .map_or(Value::Null, |found| text(found.class_type.as_str())),
        SceneField::Score => number_or_null(
            row.classification
                .and_then(|found| found.score)
                .map(scene::Score::value),
        ),
        SceneField::Confidence => number_or_null(
            row.classification
                .and_then(|found| found.confidence)
                .map(scene::Confidence::value),
        ),
        _ => Value::Null,
    }
}

fn relation_field(row: &RelationRow<'_>, field: SceneField) -> Value {
    let end = |end: End| match end {
        End::Source => row.source,
        End::Target => row.target,
    };
    let observation = |end: Endpoint<'_>| record([geometry(end.observation.geometry.bounds())]);
    let entity = |end: Endpoint<'_>| record([id(end.entity)]);
    match field {
        SceneField::Relation => record([text(row.relation.kind())]),
        SceneField::RelationType => text(row.relation.kind()),
        SceneField::Endpoint(which) => {
            let end = end(which);
            record([entity(end), observation(end)])
        }
        SceneField::EndpointEntity(which) => entity(end(which)),
        SceneField::EndpointId(which) => id(end(which).entity),
        SceneField::EndpointObservation(which) => observation(end(which)),
        SceneField::EndpointGeometry(which) => geometry(end(which).observation.geometry.bounds()),
        _ => Value::Null,
    }
}

/// Of a list of color classifications (maps of `name`, `rgb` and `score`), the one with the highest score, the first
/// of equals. Anything else, also a list with one malformed item, is no color.
fn highest_score_color(attribute: Option<&SceneValue>) -> Value {
    let Some(items) = attribute.and_then(SceneValue::as_list) else {
        return Value::Null;
    };
    let mut best: Option<(f64, [i64; 3])> = None;
    for item in items {
        let Some(found) = color_classification(item) else {
            return Value::Null;
        };
        if best.is_none_or(|(score, _)| found.0 > score) {
            best = Some(found);
        }
    }
    best.map_or(Value::Null, |(score, [r, g, b])| {
        let channels: Arc<[Value]> = Arc::new([Value::Int(r), Value::Int(g), Value::Int(b)]);
        record([Value::List(channels), Value::Number(score)])
    })
}

fn color_classification(item: &SceneValue) -> Option<(f64, [i64; 3])> {
    let SceneValue::Map(fields) = item else {
        return None;
    };
    let get = |key: &str| {
        fields
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value)
    };
    if get("name")?.as_str()?.is_empty() {
        return None;
    }
    let score = get("score")?.as_f64().filter(|score| score.is_finite())?;
    let SceneValue::List(rgb) = get("rgb")? else {
        return None;
    };
    let channel = |index: usize| {
        rgb.get(index)?
            .as_i64()
            .filter(|channel| (0..=255).contains(channel))
    };
    (rgb.len() == 3).then_some(())?;
    Some((score, [channel(0)?, channel(1)?, channel(2)?]))
}
