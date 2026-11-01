//! What one ONVIF `Object` says: where it is, what it is, and its colors.

use std::borrow::Cow;

use quick_xml::{XmlVersion, events::BytesStart};
use scene::{
    BoundingBox, ClassType, Classification, EntityId, Fields, Geometry, Observation, Point,
    Polygon, SceneTime, Score, Value,
};

/// Element names the decoder reads, matched by local name; every other element is `Other`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Tag {
    Frame,
    MetadataStream,
    Object,
    ObjectTree,
    Rename,
    Delete,
    From,
    To,
    Appearance,
    Shape,
    BoundingBox,
    CenterOfGravity,
    Polygon,
    Point,
    Class,
    Type,
    VehicleInfo,
    HumanBody,
    Clothing,
    Tops,
    Bottoms,
    Color,
    ColorCluster,
    Other,
}

impl Tag {
    pub(super) fn from_local(name: &str) -> Self {
        match name {
            "Frame" | "SampleFrame" => Self::Frame,
            "MetadataStream" => Self::MetadataStream,
            "Object" => Self::Object,
            "ObjectTree" => Self::ObjectTree,
            "Rename" => Self::Rename,
            "Delete" => Self::Delete,
            "from" => Self::From,
            "to" => Self::To,
            "Appearance" => Self::Appearance,
            "Shape" => Self::Shape,
            "BoundingBox" => Self::BoundingBox,
            "CenterOfGravity" => Self::CenterOfGravity,
            "Polygon" => Self::Polygon,
            "Point" => Self::Point,
            "Class" => Self::Class,
            "Type" => Self::Type,
            "VehicleInfo" => Self::VehicleInfo,
            "HumanBody" => Self::HumanBody,
            "Clothing" => Self::Clothing,
            "Tops" => Self::Tops,
            "Bottoms" => Self::Bottoms,
            "Color" => Self::Color,
            "ColorCluster" => Self::ColorCluster,
            _ => Self::Other,
        }
    }
}

/// The values of the named attributes of `element`, matched by local name.
pub(super) fn attrs<'a, const N: usize>(
    element: &'a BytesStart<'_>,
    names: [&str; N],
) -> Result<[Option<Cow<'a, str>>; N], String> {
    let mut found = [const { None }; N];
    for attribute in element.attributes() {
        let attribute = attribute.map_err(|error| format!("invalid attribute: {error}"))?;
        if let Some(slot) = names
            .iter()
            .position(|name| attribute.key.local_name().as_ref() == *name)
        {
            let value = attribute
                .normalized_value(XmlVersion::Implicit1_0)
                .map_err(|error| format!("invalid attribute value: {error}"))?;
            found[slot] = Some(value);
        }
    }
    Ok(found)
}

/// A finite number written in an attribute.
fn number(what: &str, text: &str) -> Result<f64, String> {
    text.trim()
        .parse::<f64>()
        .ok()
        .filter(|value| value.is_finite())
        .ok_or_else(|| format!("{what}: invalid number {text:?}"))
}

/// Like [`number`], for an attribute that must be there.
fn required(what: &str, text: Option<&str>) -> Result<f64, String> {
    number(what, text.ok_or_else(|| format!("{what} is missing"))?)
}

/// ONVIF coordinates run -1 to 1 with y upwards; frame coordinates run 0 to 1 with y downwards.
fn to_frame([x, y]: [f64; 2]) -> [f64; 2] {
    [
        ((x + 1.0) * 0.5).clamp(0.0, 1.0),
        ((1.0 - y) * 0.5).clamp(0.0, 1.0),
    ]
}

/// Names that ONVIF spells in other ways; the rest are just lowercased.
static ALIASES: [(&str, ClassType); 5] = [
    ("person", ClassType::HUMAN),
    ("vehicleother", ClassType::VEHICLE_OTHER),
    ("licenseplate", ClassType::LICENSE_PLATE),
    ("humanface", ClassType::FACE),
    ("human_face", ClassType::FACE),
];
/// Class names that need no allocation.
static KNOWN: [ClassType; 12] = [
    ClassType::HUMAN,
    ClassType::VEHICLE,
    ClassType::VEHICLE_OTHER,
    ClassType::CAR,
    ClassType::BUS,
    ClassType::TRUCK,
    ClassType::BIKE,
    ClassType::BICYCLE,
    ClassType::ANIMAL,
    ClassType::HEAD,
    ClassType::LICENSE_PLATE,
    ClassType::FACE,
];

/// The class for `text` (not empty), lowercased.
fn lowercase(text: &str) -> Option<ClassType> {
    KNOWN
        .iter()
        .find(|known| known.as_str().eq_ignore_ascii_case(text))
        .cloned()
        .or_else(|| ClassType::new(text.to_lowercase()).ok())
}

fn class_type(text: &str) -> Option<ClassType> {
    ALIASES
        .iter()
        .find(|(alias, _)| alias.eq_ignore_ascii_case(text))
        .map(|(_, class)| class.clone())
        .or_else(|| lowercase(text))
}

/// A color cluster as `{rgb: [r, g, b], score}`.
fn color(element: &BytesStart<'_>) -> Result<Value, String> {
    let [space, x, y, z, likelihood] = attrs(element, ["Colorspace", "X", "Y", "Z", "Likelihood"])?;
    if space.as_deref() != Some("RGB") {
        return Err(format!(
            "Only RGB colorspace is supported, got: {}",
            space.as_deref().unwrap_or("none")
        ));
    }
    let channel = |name: &str, text: Option<Cow<'_, str>>| {
        let text = text.as_deref().map_or("", str::trim);
        let value = if text.is_empty() {
            0
        } else {
            text.parse::<i64>()
                .map_err(|_| format!("Color {name}: invalid integer {text:?}"))?
        };
        Ok::<_, String>(value)
    };
    let (r, g, b) = (channel("X", x)?, channel("Y", y)?, channel("Z", z)?);
    if ![r, g, b].iter().all(|value| (0..=255).contains(value)) {
        return Err(format!("RGB values must be 0-255, got R={r}, G={g}, B={b}"));
    }
    let score = required("Likelihood in color cluster", likelihood.as_deref())?;
    Ok(Value::Map(vec![
        (
            "rgb".into(),
            Value::List(vec![Value::Int(r), Value::Int(g), Value::Int(b)]),
        ),
        ("score".into(), Value::Float(score)),
    ]))
}

/// Which `Type` element is being read.
#[derive(Clone, Copy)]
enum TypeOf {
    Class,
    Vehicle,
}

/// One `Object` as read, before it becomes an Observation.
pub(super) struct RawObject {
    pub(super) id: EntityId,
    bounding_box: Option<[f64; 4]>,
    polygons: u32,
    polygon: Vec<[f64; 2]>,
    center: Option<[f64; 2]>,
    class: Option<(ClassType, Option<Score>)>,
    vehicle: Option<(ClassType, Option<Score>)>,
    tops: Vec<Value>,
    bottoms: Vec<Value>,
    vehicle_colors: Vec<Value>,
    reading: Option<(TypeOf, Option<Score>)>,
    text: String,
}

impl RawObject {
    /// An object whose `ObjectId` is `id`; a missing or empty one is `unknown`.
    pub(super) fn new(id: Option<&str>) -> Result<Self, String> {
        let id = id.filter(|id| !id.is_empty()).unwrap_or("unknown");
        Ok(Self {
            id: EntityId::new(id).map_err(|error| error.to_string())?,
            bounding_box: None,
            polygons: 0,
            polygon: Vec::new(),
            center: None,
            class: None,
            vehicle: None,
            tops: Vec::new(),
            bottoms: Vec::new(),
            vehicle_colors: Vec::new(),
            reading: None,
            text: String::new(),
        })
    }

    /// Reads an element at `path`, the element names below the `Object`.
    pub(super) fn open(&mut self, path: &[Tag], element: &BytesStart<'_>) -> Result<(), String> {
        use Tag::{
            Appearance, Bottoms, BoundingBox, CenterOfGravity, Class, Clothing, Color,
            ColorCluster, HumanBody, Point, Polygon, Shape, Tops, Type, VehicleInfo,
        };
        match path {
            [Appearance, Shape, BoundingBox] if self.bounding_box.is_none() => {
                let [left, top, right, bottom] =
                    attrs(element, ["left", "top", "right", "bottom"])?;
                let side = |name: &str, text: Option<Cow<'_, str>>| {
                    required(&format!("BoundingBox {name}"), text.as_deref())
                };
                let (left, top) = (side("left", left)?, side("top", top)?);
                self.bounding_box =
                    Some([left, top, side("right", right)?, side("bottom", bottom)?]);
            }
            [Appearance, Shape, Polygon] => self.polygons += 1,
            [Appearance, Shape, Polygon, Point] if self.polygons == 1 => {
                if let [Some(x), Some(y)] = attrs(element, ["x", "y"])? {
                    self.polygon
                        .push([number("Point x", &x)?, number("Point y", &y)?]);
                }
            }
            [Appearance, Shape, CenterOfGravity] if self.center.is_none() => {
                if let [Some(x), Some(y)] = attrs(element, ["x", "y"])? {
                    self.center = Some([
                        number("CenterOfGravity x", &x)?,
                        number("CenterOfGravity y", &y)?,
                    ]);
                }
            }
            [Appearance, Class, Type] | [Appearance, VehicleInfo, Type] => {
                let kind = if path[1] == Class {
                    TypeOf::Class
                } else {
                    TypeOf::Vehicle
                };
                let [likelihood] = attrs(element, ["Likelihood"])?;
                let score = likelihood
                    .map(|text| {
                        Score::new(number("Likelihood", &text)?).map_err(|error| error.to_string())
                    })
                    .transpose()?;
                self.reading = Some((kind, score));
                self.text.clear();
            }
            [Appearance, Color, ColorCluster, Color] => self.vehicle_colors.push(color(element)?),
            [
                Appearance,
                HumanBody,
                Clothing,
                Tops,
                Color,
                ColorCluster,
                Color,
            ] => {
                self.tops.push(color(element)?);
            }
            [
                Appearance,
                HumanBody,
                Clothing,
                Bottoms,
                Color,
                ColorCluster,
                Color,
            ] => {
                self.bottoms.push(color(element)?);
            }
            _ => {}
        }
        Ok(())
    }

    /// Text inside the open element; only `Type` text is kept.
    pub(super) fn text(&mut self, text: &str) {
        if self.reading.is_some() {
            self.text.push_str(text);
        }
    }

    /// An element at `path` ends.
    pub(super) fn close(&mut self, path: &[Tag]) {
        if !matches!(path.last(), Some(Tag::Type)) {
            return;
        }
        let Some((kind, score)) = self.reading.take() else {
            return;
        };
        let text = self.text.trim();
        if text.is_empty() {
            return;
        }
        match kind {
            TypeOf::Class if self.class.is_none() => {
                self.class = class_type(text).map(|class| (class, score));
            }
            TypeOf::Vehicle if self.vehicle.is_none() => {
                self.vehicle = lowercase(text).map(|class| (class, score));
            }
            _ => {}
        }
    }

    /// The Observation this object makes at `time`, or `None` when it has no usable shape.
    pub(super) fn into_observation(self, time: SceneTime) -> Option<Observation> {
        let geometry = if let Some([left, top, right, bottom]) = self.bounding_box {
            let [x1, y1] = to_frame([left, top]);
            let [x2, y2] = to_frame([right, bottom]);
            Geometry::Box(BoundingBox::new(x1.min(x2), y1.min(y2), x1.max(x2), y1.max(y2)).ok()?)
        } else if self.polygon.len() >= 3 {
            let points = self
                .polygon
                .into_iter()
                .map(|point| {
                    let [x, y] = to_frame(point);
                    Point::new(x, y)
                })
                .collect::<Result<Vec<_>, _>>()
                .ok()?;
            Geometry::Polygon(Polygon::new(points).ok()?)
        } else {
            // A lone center of gravity gets a small box around it.
            const HALF_SIZE: f64 = 0.01;
            let [x, y] = to_frame(self.center?);
            Geometry::Box(
                BoundingBox::new(x - HALF_SIZE, y - HALF_SIZE, x + HALF_SIZE, y + HALF_SIZE)
                    .ok()?,
            )
        };

        let classifications = self
            .class
            .map(|(mut class_type, mut score)| {
                if let Some((subtype, subtype_score)) = self.vehicle {
                    (class_type, score) = (subtype, subtype_score);
                }
                let mut attributes = Fields::new();
                for (name, colors) in [
                    ("upper_clothing_colors", self.tops),
                    ("lower_clothing_colors", self.bottoms),
                    ("vehicle_colors", self.vehicle_colors),
                ] {
                    if !colors.is_empty() {
                        attributes.push((name.into(), Value::List(colors)));
                    }
                }
                Classification {
                    score,
                    attributes,
                    ..Classification::new(class_type)
                }
            })
            .into_iter()
            .collect();
        let mut observation = Observation::new(time, geometry);
        observation.classifications = classifications;
        Some(observation)
    }
}
