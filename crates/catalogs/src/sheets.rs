//! The example Scene a catalog is exercised on: one Sample Item.
//!
//! The Overview has one object for each class the built-in decoders name and one relation, so a catalog's look for
//! every class, a motion arrow, colour swatches and a relation is visible at once. The item is written by the
//! same encoder the playback socket uses, so a catalog sees what it sees in the viewer.

use scene::{
    BoundingBox, ClassType, Classification, Entity, EntityId, EntityRelation, FilterPolicy,
    Geometry, ImageVelocity, Measurements, MotionState, Observation, Scene, SceneError, SceneTime,
    Score, TimeSlice, Value, serial::columns::encode_sample_item,
};

/// Objects per row of the Overview.
const COLUMNS: usize = 5;
/// The relation's kind, which the built-in catalogs route.
const RELATION: &str = "has_part";

/// The Sample Item of the Overview sheet, with no class filters: see the module documentation.
pub fn overview() -> Result<Vec<u8>, String> {
    encode_sample_item(&overview_scene().map_err(describe)?, &no_filters()).map_err(describe)
}

fn describe(error: SceneError) -> String {
    format!("Cannot build the Overview sheet: {error}")
}

/// A policy without options keeps every entity.
fn no_filters() -> FilterPolicy {
    FilterPolicy {
        name: "overview",
        options: Vec::new(),
    }
}

/// The classes of the built-in decoders, with the human and its head first so the relation joins neighbours.
fn classes() -> [ClassType; 15] {
    [
        ClassType::HUMAN,
        ClassType::HEAD,
        ClassType::VEHICLE,
        ClassType::VEHICLE_OTHER,
        ClassType::CAR,
        ClassType::BUS,
        ClassType::TRUCK,
        ClassType::BIKE,
        ClassType::BICYCLE,
        ClassType::ANIMAL,
        ClassType::LICENSE_PLATE,
        ClassType::FACE,
        ClassType::BAG,
        ClassType::UNKNOWN,
        ClassType::unmapped(99),
    ]
}

/// A list of one colour, the shape decoders give colour attributes.
fn colour(name: &'static str, rgb: [i64; 3]) -> Value {
    Value::List(vec![Value::Map(vec![
        ("name".into(), Value::Str(name.into())),
        (
            "rgb".into(),
            Value::List(rgb.into_iter().map(Value::Int).collect()),
        ),
        ("score".into(), Value::Float(0.9)),
    ])])
}

fn overview_scene() -> Result<Scene, SceneError> {
    let classes = classes();
    let rows = classes.len().div_ceil(COLUMNS);
    let mut entities = Vec::with_capacity(classes.len());
    for (index, class) in classes.into_iter().enumerate() {
        let (column, row) = (index % COLUMNS, index / COLUMNS);
        let left = column as f64 / COLUMNS as f64 + 0.04;
        let top = row as f64 / rows as f64 + 0.06;
        let bounds = BoundingBox::new(left, top, left + 0.12, top + 0.2)?;
        let id = EntityId::new(class.as_str().trim_start_matches('?'))?;
        let mut classification = Classification::new(class.clone());
        classification.score = Some(Score::new(0.87)?);
        let mut observation = Observation::new(SceneTime::Frame(0), Geometry::Box(bounds));
        let mut entity = Entity::new(id);
        if class == ClassType::HUMAN {
            classification
                .attributes
                .push(("upper_clothing_colors".into(), colour("red", [200, 30, 30])));
            observation.measurements = Some(Box::new(Measurements {
                image_velocity: Some(ImageVelocity {
                    vx: 0.04,
                    vy: -0.01,
                }),
                ..Measurements::default()
            }));
            entity.motion_state = Some(MotionState::Moving);
        } else if class == ClassType::CAR {
            classification
                .attributes
                .push(("vehicle_colors".into(), colour("blue", [30, 60, 200])));
            entity.motion_state = Some(MotionState::Stationary);
        }
        observation.classifications.push(classification);
        entity.push_observation(observation);
        entities.push(entity);
    }
    let part = EntityRelation::new(
        RELATION,
        EntityId::new(ClassType::HUMAN.as_str())?,
        EntityId::new(ClassType::HEAD.as_str())?,
    )?;
    Ok(Scene::new(TimeSlice::at(SceneTime::Frame(0)))
        .with_entities(entities)?
        .with_relations([part]))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn word(item: &[u8], index: usize) -> u32 {
        u32::from_le_bytes(
            item[index * 4..index * 4 + 4]
                .try_into()
                .expect("four bytes"),
        )
    }

    #[test]
    fn the_overview_is_an_item_of_every_class_and_one_relation() {
        let item = overview().expect("the sheet builds");
        // The envelope sends an item as whole words, which the receiver views in place.
        assert_eq!(item.len() % 8, 0);
        assert_eq!(&item[..4], b"AXSI");
        assert_eq!(word(&item, 1), scene::serial::columns::VERSION);
        // The counts of entities and relations.
        assert_eq!(word(&item, 2) as usize, classes().len());
        assert_eq!(word(&item, 3), 1);
    }
}
