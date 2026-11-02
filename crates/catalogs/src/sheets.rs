//! Example Scenes for showing what a catalog looks like: a fixed picture and one Sample Item to draw on it.
//!
//! The Overview has one object for each class the built-in decoders name and one relation, so a catalog's look for
//! every class, a motion arrow, colour swatches and a relation is visible at once. The item is written by the
//! same encoder the playback socket uses, so a catalog sees what it sees in the viewer.

use bytes::Bytes;
use foundation::{
    ColorDesc, FrameId, FrameLayout, Matrix, MediaTime, PixelFormat, Plane, VideoFrame,
};
use scene::{
    BoundingBox, ClassType, Classification, Entity, EntityId, EntityRelation, FilterPolicy,
    Geometry, ImageVelocity, Measurements, MotionState, Observation, Scene, SceneError, SceneTime,
    Score, TimeSlice, Value, serial::columns::encode_sample_item,
};

/// Width of a sheet's picture.
pub const SHEET_WIDTH: u32 = 1280;
/// Height of a sheet's picture.
pub const SHEET_HEIGHT: u32 = 720;

/// Luma of mid grey in limited range; the chroma planes are 128.
const GREY_LUMA: u8 = 126;
/// Objects per row of the Overview.
const COLUMNS: usize = 5;
/// The relation's kind, which the built-in catalogs route.
const RELATION: &str = "has_part";

/// What a sheet draws on: the picture and the Sample Item of its one Scene.
pub struct Sheet {
    /// A grey frame of [`SHEET_WIDTH`] x [`SHEET_HEIGHT`].
    pub picture: VideoFrame,
    /// The Sample Item of the sheet's Scene, with no class filters.
    pub item: Vec<u8>,
}

/// The Overview sheet: see the module documentation.
pub fn overview() -> Result<Sheet, String> {
    let item = encode_sample_item(&overview_scene().map_err(describe)?, &no_filters())
        .map_err(describe)?;
    Ok(Sheet {
        picture: grey_picture()?,
        item,
    })
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

fn grey_picture() -> Result<VideoFrame, String> {
    let (width, height) = (SHEET_WIDTH as usize, SHEET_HEIGHT as usize);
    let (luma, chroma) = (width * height, width * height / 4);
    let mut data = vec![GREY_LUMA; luma];
    data.resize(luma + 2 * chroma, 128);
    let plane = |stride: usize, len: usize| Plane {
        stride: stride as u32,
        len: len as u32,
    };
    let layout = FrameLayout {
        width: SHEET_WIDTH,
        height: SHEET_HEIGHT,
        format: PixelFormat::I420,
        color: ColorDesc {
            matrix: Matrix::Bt709,
            full_range: false,
        },
        planes: [
            plane(width, luma),
            plane(width / 2, chroma),
            plane(width / 2, chroma),
        ],
    };
    let id = FrameId {
        sequence_id: 0,
        timestamp: MediaTime::ZERO,
    };
    VideoFrame::new(id, true, layout, Bytes::from(data))
        .map_err(|error| format!("Cannot build the Overview picture: {}", error.reason))
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
    fn the_overview_is_a_grey_picture_and_an_item_of_every_class_and_one_relation() {
        let sheet = overview().expect("the sheet builds");

        let layout = sheet.picture.layout();
        assert_eq!((layout.width, layout.height), (SHEET_WIDTH, SHEET_HEIGHT));
        assert!(sheet.picture.plane(0).iter().all(|&luma| luma == GREY_LUMA));
        // The envelope sends an item as whole words, which the receiver views in place.
        assert_eq!(sheet.item.len() % 8, 0);
        assert_eq!(&sheet.item[..4], b"AXSI");
        assert_eq!(word(&sheet.item, 1), scene::serial::columns::VERSION);
        // The counts of entities and relations.
        assert_eq!(word(&sheet.item, 2) as usize, classes().len());
        assert_eq!(word(&sheet.item, 3), 1);
    }
}
