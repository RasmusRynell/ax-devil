//! What one CVAT shape element says: its frame, whether it is drawn, where, and what class.

use std::borrow::Cow;

use scene::{ClassType, Fields, Name, Value};

/// Class names CVAT files use all the time, kept as static strings.
const COMMON_CLASSES: [&str; 14] = [
    "person",
    "car",
    "bus",
    "truck",
    "bike",
    "bicycle",
    "animal",
    "head",
    "face",
    "bag",
    "human",
    "vehicle",
    "license_plate",
    "unknown",
];

/// The text of attribute `name`.
pub(super) fn attr<'a>(attributes: &'a Fields, name: &str) -> Option<&'a str> {
    attributes
        .iter()
        .find(|(key, _)| key == name)
        .and_then(|(_, value)| value.as_str())
}

/// A whole number written in an attribute or element.
pub(super) fn int(text: &str) -> Option<i64> {
    text.trim().parse().ok()
}

fn float(text: &str) -> Option<f64> {
    text.trim()
        .parse::<f64>()
        .ok()
        .filter(|value| value.is_finite())
}

/// The class a label names: trimmed and lowercased, or `None` for no label.
fn class_of(label: &str) -> Option<ClassType> {
    let label = label.trim();
    if label.is_empty() {
        return None;
    }
    let name = match COMMON_CLASSES
        .iter()
        .find(|common| common.eq_ignore_ascii_case(label))
    {
        Some(common) => Cow::Borrowed(*common),
        None => Cow::Owned(label.to_lowercase()),
    };
    ClassType::new(name).ok()
}

/// Where a shape is, in pixels.
pub(super) enum Drawn {
    /// Left, top, right and bottom edges.
    Box([f64; 4]),
    /// At least three points.
    Polygon(Vec<[f64; 2]>),
}

/// How a shape names its frame.
pub(super) enum FrameRef {
    /// It does not; the image around it decides.
    Absent,
    /// It does, but not with a frame number.
    Invalid(String),
    /// A frame number.
    At(u64),
}

/// A shape that is drawn.
pub(super) struct Rendered {
    pub class: ClassType,
    pub drawn: Drawn,
    occluded: bool,
    keyframe: bool,
    z_order: Option<i64>,
    group: Option<i64>,
}

/// Whether and how a shape is drawn.
pub(super) enum Draw {
    /// Outside the image, unlabeled, or not a box or polygon: kept as debug data only.
    Skip,
    /// A box or polygon whose coordinates cannot be drawn: kept as debug data only.
    Rejected,
    /// Drawn.
    Rendered(Rendered),
}

/// What a shape element's attributes say, read when the element starts.
pub(super) struct ShapeHead {
    pub frame: FrameRef,
    pub draw: Draw,
}

impl ShapeHead {
    /// Reads shape `tag` with `attributes`, inside a container with `container` attributes.
    pub(super) fn read(tag: &str, attributes: &Fields, container: &Fields) -> Self {
        let frame = match attr(attributes, "frame") {
            None => FrameRef::Absent,
            Some(text) => text
                .trim()
                .parse::<u64>()
                .map_or_else(|_| FrameRef::Invalid(text.to_owned()), FrameRef::At),
        };
        let class = class_of(
            attr(attributes, "label")
                .or_else(|| attr(container, "label"))
                .unwrap_or(""),
        );
        let draw = match class {
            Some(class)
                if attr(attributes, "outside") != Some("1") && matches!(tag, "box" | "polygon") =>
            {
                match geometry(tag, attributes) {
                    Some(drawn) => Draw::Rendered(Rendered {
                        class,
                        drawn,
                        occluded: attr(attributes, "occluded") == Some("1"),
                        keyframe: attr(attributes, "keyframe") == Some("1"),
                        z_order: attr(attributes, "z_order").and_then(int),
                        group: attr(attributes, "group_id")
                            .or_else(|| attr(container, "group_id"))
                            .and_then(int),
                    }),
                    None => Draw::Rejected,
                }
            }
            _ => Draw::Skip,
        };
        Self { frame, draw }
    }
}

/// A box with finite, increasing edges, or a polygon with three valid points; malformed pairs are skipped.
fn geometry(tag: &str, attributes: &Fields) -> Option<Drawn> {
    if tag == "box" {
        let edge = |name| attr(attributes, name).and_then(float);
        let (left, top, right, bottom) = (edge("xtl")?, edge("ytl")?, edge("xbr")?, edge("ybr")?);
        return (right > left && bottom > top).then_some(Drawn::Box([left, top, right, bottom]));
    }
    let points: Vec<[f64; 2]> = attr(attributes, "points")?
        .split(';')
        .filter_map(|pair| {
            let (x, y) = pair.split_once(',')?;
            Some([float(x)?, float(y)?])
        })
        .collect();
    (points.len() >= 3).then_some(Drawn::Polygon(points))
}

/// The `attribute` children of an element value as (name, text), for named ones.
fn attribute_children(element: &Value) -> impl Iterator<Item = (&str, &str)> {
    let found = element
        .as_map()
        .and_then(|fields| fields.iter().find(|(key, _)| key == "attribute"))
        .map(|(_, value)| value);
    let items = match found {
        Some(Value::List(items)) => items.as_slice(),
        Some(one) => std::slice::from_ref(one),
        None => &[],
    };
    items.iter().filter_map(|item| {
        let fields = item.as_map()?;
        let lookup = |key: &str| {
            fields
                .iter()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value)
        };
        let name = lookup("@attributes")?
            .as_map()?
            .iter()
            .find(|(name, _)| name == "name")?
            .1
            .as_str()
            .filter(|name| !name.is_empty())?;
        Some((name, lookup("#text").and_then(Value::as_str).unwrap_or("")))
    })
}

/// The attributes of a drawn shape's classification: the container's `attribute` children, then the shape's (which
/// win a name clash and keep the first position), then the flags CVAT sets.
pub(super) fn classification_attributes(
    context: &Value,
    shape: &Value,
    rendered: &Rendered,
) -> Fields {
    let mut fields = Fields::new();
    for (name, text) in attribute_children(context).chain(attribute_children(shape)) {
        let value = Value::Str(Name::Owned(text.to_owned()));
        match fields.iter_mut().find(|(existing, _)| existing == name) {
            Some((_, slot)) => *slot = value,
            None => fields.push((Name::Owned(name.to_owned()), value)),
        }
    }
    if rendered.occluded {
        fields.push(("occluded".into(), Value::Bool(true)));
    }
    if rendered.keyframe {
        fields.push(("keyframe".into(), Value::Bool(true)));
    }
    if let Some(z_order) = rendered.z_order {
        fields.push(("z_order".into(), Value::Int(z_order)));
    }
    if let Some(group) = rendered.group {
        fields.push(("group_id".into(), Value::Int(group)));
    }
    fields
}
