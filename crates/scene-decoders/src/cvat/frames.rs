//! The shapes of one frame, gathered while the file streams past, and the Scene and filters built from them.

use std::collections::{BTreeSet, HashMap};

use scene::{
    BoundingBox, ClassRule, ClassType, Classification, Entity, EntityId, Fields, FilterOption,
    FilterPolicy, Geometry, Observation, Point, Polygon, Scene, SceneError, SceneTime, TimeSlice,
    Value,
};

use super::shape::Drawn;

/// A drawn shape waiting for the image size, which a CVAT file may give after the shapes.
pub(super) struct Pending {
    pub entity: EntityId,
    pub class: ClassType,
    pub drawn: Drawn,
    pub attributes: Fields,
    /// The shape as the file wrote it, with its track or image.
    pub raw: Value,
}

/// Everything one frame holds.
#[derive(Default)]
pub(super) struct FrameAcc {
    /// The `image` element's context, when the frame has one.
    pub image: Option<Value>,
    /// The width and height that image gives; zero for none.
    pub size: [i64; 2],
    /// Shapes kept as debug data only.
    pub unrendered: Vec<Value>,
    pub shapes: Vec<Pending>,
}

impl FrameAcc {
    /// The frame's image: its context and the size it gives.
    pub(super) fn set_image(&mut self, context: Value, size: [i64; 2]) {
        self.image = Some(context);
        self.size = size;
    }

    /// The frame's Scene. A frame size of its own wins over `fallback`, the size the task gives; a frame with shapes
    /// to draw needs one of them.
    pub(super) fn into_scene(
        self,
        frame: u64,
        fallback: [i64; 2],
        index: &mut HashMap<EntityId, usize>,
    ) -> Result<Scene, String> {
        let [width, height] = [0, 1].map(|axis| {
            if self.size[axis] > 0 {
                self.size[axis]
            } else {
                fallback[axis]
            }
        });
        if !self.shapes.is_empty() && (width <= 0 || height <= 0) {
            return Err(format!(
                "frame {frame} has shapes but no image size: give original_size in the task or width and height on the image"
            ));
        }
        let time = SceneTime::Frame(frame);
        let mut entities: Vec<Entity> = Vec::new();
        index.clear();
        for pending in self.shapes {
            let geometry = geometry(pending.drawn, width as f64, height as f64)
                .map_err(|error| error.to_string())?;
            let mut observation = Observation::new(time, geometry);
            observation.classifications = vec![Classification {
                attributes: pending.attributes,
                ..Classification::new(pending.class)
            }];
            let observation = observation
                .with_details(vec![("cvat".into(), pending.raw)], Fields::new())
                .map_err(|error| error.to_string())?;
            match index.get(&pending.entity) {
                Some(&at) => entities[at].push_observation(observation),
                None => {
                    index.insert(pending.entity.clone(), entities.len());
                    let mut entity = Entity::new(pending.entity);
                    entity.push_observation(observation);
                    entities.push(entity);
                }
            }
        }
        let mut cvat = Fields::new();
        if let Some(image) = self.image {
            cvat.push(("image".into(), image));
        }
        if !self.unrendered.is_empty() {
            cvat.push(("unrendered_shapes".into(), Value::List(self.unrendered)));
        }
        Scene::new(TimeSlice::at(time))
            .with_entities(entities)
            .map(|scene| scene.with_debug(vec![("cvat".into(), Value::Map(cvat))]))
            .map_err(|error| error.to_string())
    }
}

/// The pixel geometry as fractions of the frame.
fn geometry(drawn: Drawn, width: f64, height: f64) -> Result<Geometry, SceneError> {
    match drawn {
        Drawn::Box([left, top, right, bottom]) => {
            BoundingBox::new(left / width, top / height, right / width, bottom / height)
                .map(Geometry::Box)
        }
        Drawn::Polygon(points) => points
            .into_iter()
            .map(|[x, y]| Point::new(x / width, y / height))
            .collect::<Result<Vec<_>, _>>()
            .and_then(Polygon::new)
            .map(Geometry::Polygon),
    }
}

/// Python's `str.title` for a label: the first letter of each run of letters is uppercase, the rest lowercase.
fn title(text: &str) -> String {
    let mut in_word = false;
    let mut out = String::with_capacity(text.len());
    for character in text.chars() {
        if character.is_alphabetic() {
            if in_word {
                out.extend(character.to_lowercase());
            } else {
                out.extend(character.to_uppercase());
            }
        } else {
            out.push(character);
        }
        in_word = character.is_alphabetic();
    }
    out
}

/// The filter id of a label: its letters with spaces and slashes as underscores, and a number added when another
/// label (earlier in sort order) already took that id, so each checkbox stays its own class and ids are the same on
/// every run.
fn unique_id(label: &str, taken: &mut BTreeSet<String>) -> String {
    let base = label.replace([' ', '/'], "_");
    let base = base.trim_matches('_');
    let base = format!("show_{}", if base.is_empty() { "unlabeled" } else { base });
    let mut id = base.clone();
    let mut number = 2;
    while !taken.insert(id.clone()) {
        id = format!("{base}_{number}");
        number += 1;
    }
    id
}

/// One filter per label, sorted; a file with no labels gets one `Unlabeled` filter that shows everything.
pub(super) fn filter_policy(labels: &BTreeSet<ClassType>) -> FilterPolicy {
    let mut taken = BTreeSet::new();
    let mut options: Vec<FilterOption> = labels
        .iter()
        .map(|label| {
            let words = label.as_str().replace('_', " ");
            let words = words.trim();
            FilterOption {
                id: unique_id(label.as_str(), &mut taken).into(),
                label: if words.is_empty() {
                    "Unlabeled".to_owned()
                } else {
                    title(words)
                }
                .into(),
                default_enabled: true,
                rule: ClassRule {
                    types: vec![label.as_str().to_owned().into()],
                    include_empty: false,
                    exclude: false,
                },
            }
        })
        .collect();
    if options.is_empty() {
        options.push(FilterOption {
            id: "show_unlabeled".into(),
            label: "Unlabeled".into(),
            default_enabled: true,
            rule: ClassRule {
                types: Vec::new(),
                include_empty: true,
                exclude: false,
            },
        });
    }
    FilterPolicy {
        name: "cvat",
        options,
    }
}
