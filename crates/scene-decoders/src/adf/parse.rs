//! Reading the JSON pieces both ADF versions share: ids, times, boxes, classes and measurements.

use base64::{Engine, engine::general_purpose::STANDARD};
use foundation::MediaTime;
use scene::{
    BoundingBox, ClassType, Classification, EntityId, Fields, GeographicCoordinates, Image,
    ImageVelocity, Measurements, Score, SphericalCoordinates, Value, WorldVelocity,
};
use serde_json::{Map, Value as Json};

use crate::iso8601;

/// A JSON object.
pub(super) type Object = Map<String, Json>;

/// Colors an ADF file may name, as RGB.
static COLORS: [(&str, [i64; 3]); 16] = [
    ("red", [255, 0, 0]),
    ("green", [0, 255, 0]),
    ("blue", [0, 0, 255]),
    ("white", [255, 255, 255]),
    ("black", [0, 0, 0]),
    ("silver", [192, 192, 192]),
    ("yellow", [255, 255, 0]),
    ("orange", [255, 165, 0]),
    ("gray", [128, 128, 128]),
    ("grey", [128, 128, 128]),
    ("brown", [165, 42, 42]),
    ("pink", [255, 192, 203]),
    ("purple", [128, 0, 128]),
    ("cyan", [0, 255, 255]),
    ("magenta", [255, 0, 255]),
    ("beige", [245, 245, 220]),
];

/// The only class spellings a v1 frame accepts (in any case), and the class each names.
static V1_CLASSES: [(&str, &str); 12] = [
    ("human", "human"),
    ("person", "human"),
    ("vehicle", "vehicle"),
    ("car", "car"),
    ("bus", "bus"),
    ("truck", "truck"),
    ("bike", "bike"),
    ("bicycle", "bicycle"),
    ("vehicleother", "vehicle_other"),
    ("animal", "animal"),
    ("head", "head"),
    ("licenseplate", "license_plate"),
];

/// The spellings of a beta frame or a consolidated track that are not their class name lowercased; any other type is
/// just lowercased.
static BETA_CLASSES: [(&str, &str); 2] = [
    ("VehicleOther", "vehicle_other"),
    ("LicensePlate", "license_plate"),
];

/// `value` as an object.
pub(super) fn object<'a>(value: &'a Json, what: &str) -> Result<&'a Object, String> {
    value
        .as_object()
        .ok_or_else(|| format!("{what} must be a JSON object"))
}

/// `value` unless it is absent or null.
pub(super) fn present(value: Option<&Json>) -> Option<&Json> {
    value.filter(|value| !value.is_null())
}

/// The items under `key`; a missing or null key is an empty list.
pub(super) fn list<'a>(parent: &'a Object, key: &str) -> Result<&'a [Json], String> {
    match parent.get(key) {
        None | Some(Json::Null) => Ok(&[]),
        Some(Json::Array(items)) => Ok(items),
        Some(_) => Err(format!("{key} must be a list")),
    }
}

/// A finite number written as a JSON number, as text, or as a boolean (Python's `float()` reads `true` as 1).
pub(super) fn finite(value: &Json, what: &str) -> Result<f64, String> {
    let number = match value {
        Json::Number(number) => number.as_f64(),
        Json::Bool(flag) => Some(f64::from(u8::from(*flag))),
        Json::String(text) => text.trim().parse().ok(),
        _ => None,
    };
    number
        .filter(|number| number.is_finite())
        .ok_or_else(|| format!("{what} must be a number"))
}

/// Whether Python would call `value` true: not `false`, `null`, zero, or an empty string, list or object.
pub(super) fn truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(flag) => *flag,
        Json::Number(number) => number.as_f64() != Some(0.0),
        Json::String(text) => !text.is_empty(),
        Json::Array(items) => !items.is_empty(),
        Json::Object(fields) => !fields.is_empty(),
    }
}

fn optional_number(parent: &Object, key: &str) -> Result<Option<f64>, String> {
    present(parent.get(key))
        .map(|value| finite(value, key))
        .transpose()
}

/// The ISO 8601 time under `key`.
pub(super) fn timestamp(parent: &Object, key: &str, what: &str) -> Result<MediaTime, String> {
    parent
        .get(key)
        .and_then(Json::as_str)
        .and_then(|text| iso8601::parse_timestamp(text.trim()))
        .ok_or_else(|| format!("{what} is missing or not an ISO 8601 timestamp"))
}

/// An id written as text or a whole number.
pub(super) fn entity_id(value: Option<&Json>, what: &str) -> Result<EntityId, String> {
    let text = match value {
        Some(Json::String(text)) => text.trim().to_owned(),
        Some(Json::Number(number)) => number.to_string(),
        _ => return Err(format!("{what} is missing")),
    };
    EntityId::new(text).map_err(|_| format!("{what} is empty"))
}

/// A box of `left`, `top`, `right` and `bottom` (also capitalized), already normalized.
pub(super) fn bounding_box(value: Option<&Json>) -> Result<BoundingBox, String> {
    let value = present(value).ok_or("the bounding box is missing")?;
    let sides = object(value, "the bounding box")?;
    let side = |lower: &str, upper: &str| {
        let found = present(sides.get(lower)).or_else(|| present(sides.get(upper)));
        finite(
            found.ok_or_else(|| format!("the bounding box lacks {lower}"))?,
            lower,
        )
    };
    BoundingBox::new(
        side("left", "Left")?,
        side("top", "Top")?,
        side("right", "Right")?,
        side("bottom", "Bottom")?,
    )
    .map_err(|error| format!("the bounding box: {error}"))
}

/// The numbers of an object, each under the first of its alternative keys that is there.
fn numbers<const N: usize>(
    value: &Json,
    what: &str,
    keys: [&[&str]; N],
) -> Result<[f64; N], String> {
    let fields = object(value, what)?;
    let mut found = [0.0; N];
    for (slot, names) in found.iter_mut().zip(keys) {
        let number = names
            .iter()
            .find_map(|name| present(fields.get(*name)))
            .ok_or_else(|| format!("{what} lacks {}", names[0]))?;
        *slot = finite(number, names[0])?;
    }
    Ok(found)
}

/// Velocity in the image, `vx` and `vy`.
pub(super) fn image_velocity(value: Option<&Json>) -> Result<Option<ImageVelocity>, String> {
    present(value)
        .map(|value| {
            numbers(value, "velocity", [&["vx"], &["vy"]]).map(|[vx, vy]| ImageVelocity { vx, vy })
        })
        .transpose()
}

/// Velocity in the world, `pitch`, `speed` and `yaw`.
pub(super) fn world_velocity(value: Option<&Json>) -> Result<Option<WorldVelocity>, String> {
    present(value)
        .map(|value| {
            numbers(value, "world velocity", [&["pitch"], &["speed"], &["yaw"]])
                .map(|[pitch, speed, yaw]| WorldVelocity { pitch, speed, yaw })
        })
        .transpose()
}

/// A place relative to the camera; the beta format spells the keys `r` and `azimut`.
pub(super) fn world_position(value: Option<&Json>) -> Result<Option<SphericalCoordinates>, String> {
    present(value)
        .map(|value| {
            numbers(
                value,
                "world position",
                [&["distance", "r"], &["azimuth", "azimut"], &["elevation"]],
            )
            .map(|[distance, azimuth, elevation]| SphericalCoordinates {
                distance,
                azimuth,
                elevation,
            })
        })
        .transpose()
}

/// A place on Earth.
pub(super) fn geographic(value: Option<&Json>) -> Result<Option<GeographicCoordinates>, String> {
    let Some(value) = present(value) else {
        return Ok(None);
    };
    let [latitude, longitude] = numbers(value, "geoposition", [&["latitude"], &["longitude"]])?;
    Ok(Some(GeographicCoordinates {
        latitude,
        longitude,
        elevation: optional_number(object(value, "geoposition")?, "elevation")?,
    }))
}

/// The measurements, or `None` when the source gave none.
pub(super) fn measurements(measurements: Measurements) -> Option<Box<Measurements>> {
    (measurements != Measurements::default()).then(|| Box::new(measurements))
}

/// A picture with its time, box and base64 data. `crop_box` says whether a `crop_box` stands in for a missing
/// `bounding_box`.
pub(super) fn image(value: &Json, crop_box: bool) -> Result<Image, String> {
    let fields = object(value, "image")?;
    let data = fields
        .get("data")
        .and_then(Json::as_str)
        .filter(|data| !data.is_empty())
        .ok_or("the image lacks data")?;
    Ok(Image {
        timestamp: timestamp(fields, "timestamp", "the image timestamp")?,
        bounding_box: bounding_box(
            fields
                .get("bounding_box")
                .or_else(|| crop_box.then(|| fields.get("crop_box")).flatten()),
        )?,
        data: STANDARD
            .decode(data)
            .map_err(|error| format!("the image data is not base64: {error}"))?
            .into(),
    })
}

/// A class of a consolidated track, named by any non-empty type as beta names them. `v1` says whether the class has the
/// details of v1.
pub(super) fn track_classification(payload: &Object, v1: bool) -> Result<Classification, String> {
    let spelling = payload
        .get("type")
        .and_then(Json::as_str)
        .filter(|spelling| !spelling.is_empty())
        .ok_or("a class lacks its type")?;
    classified(
        ClassType::new(class_name(spelling)).map_err(|error| error.to_string())?,
        payload,
        v1,
    )
}

/// The class a beta spelling names.
fn class_name(spelling: &str) -> String {
    BETA_CLASSES
        .iter()
        .find(|(known, _)| *known == spelling)
        .map_or_else(|| spelling.to_lowercase(), |(_, class)| (*class).to_owned())
}

/// A class of a v1 frame, which accepts only the spellings of `V1_CLASSES` and rejects any other.
pub(super) fn v1_classification(payload: &Object) -> Result<Classification, String> {
    let kind = present(payload.get("type")).ok_or("a class lacks its type")?;
    let class = kind
        .as_str()
        .map(str::to_lowercase)
        .and_then(|lower| V1_CLASSES.iter().find(|(spelling, _)| *spelling == lower))
        .ok_or_else(|| format!("unknown class type {kind}"))?;
    classified(
        ClassType::new(class.1).map_err(|error| error.to_string())?,
        payload,
        true,
    )
}

/// A class of a beta frame, or `None` when its type is false, null, zero or empty. Beta tells its classes apart by
/// their exact spelling, so `Human` has clothing colors and `human` does not.
pub(super) fn beta_classification(payload: &Object) -> Result<Option<Classification>, String> {
    let kind = payload.get("type").ok_or("a class lacks its type")?;
    let score = score(payload)?;
    if !truthy(kind) {
        return Ok(None);
    }
    let spelling = kind.as_str().ok_or("a class type must be text")?;
    let mut fields = Fields::new();
    match spelling {
        "Car" | "Bus" | "Truck" | "Vehicle" => {
            push(&mut fields, "vehicle_colors", colors(payload, "colors")?);
        }
        "Human" => {
            for key in ["upper_clothing_colors", "lower_clothing_colors"] {
                push(&mut fields, key, colors(payload, key)?);
            }
        }
        "LicensePlate" => {
            for key in ["country_code", "plate_number"] {
                push(&mut fields, key, text(payload, key));
            }
        }
        _ => {}
    }
    Ok(Some(Classification {
        score,
        attributes: fields,
        ..Classification::new(
            ClassType::new(class_name(spelling)).map_err(|error| error.to_string())?,
        )
    }))
}

fn score(payload: &Object) -> Result<Option<Score>, String> {
    optional_number(payload, "score")?
        .map(Score::new)
        .transpose()
        .map_err(|error| error.to_string())
}

fn classified(class_type: ClassType, payload: &Object, v1: bool) -> Result<Classification, String> {
    Ok(Classification {
        score: score(payload)?,
        attributes: attributes(&class_type, payload, v1)?,
        ..Classification::new(class_type)
    })
}

fn push(fields: &mut Fields, name: &'static str, value: Option<Value>) {
    if let Some(value) = value {
        fields.push((name.into(), value));
    }
}

/// The class's own details, such as colors, by what the class is. Beta has fewer than v1: no bag or face of a human, no
/// colors of a bike or bicycle and no state of a license plate.
fn attributes(class_type: &ClassType, payload: &Object, v1: bool) -> Result<Fields, String> {
    let mut fields = Fields::new();
    match class_type.as_str() {
        "human" => {
            for key in ["upper_clothing_colors", "lower_clothing_colors"] {
                push(&mut fields, key, colors(payload, key)?);
            }
            if v1 {
                push(&mut fields, "carries_bag", flag(payload, "carries_bag"));
                push(
                    &mut fields,
                    "face_visible",
                    optional_number(payload, "face_visible")?.map(Value::Float),
                );
            }
        }
        "vehicle" | "vehicle_other" | "car" | "truck" | "bus" => {
            push(&mut fields, "vehicle_colors", colors(payload, "colors")?);
        }
        "bike" | "bicycle" if v1 => {
            push(&mut fields, "vehicle_colors", colors(payload, "colors")?);
        }
        "license_plate" => {
            let keys: &[&'static str] = if v1 {
                &["country_code", "state_code", "plate_number"]
            } else {
                &["country_code", "plate_number"]
            };
            for &key in keys {
                push(&mut fields, key, text(payload, key));
            }
        }
        _ => {}
    }
    Ok(fields)
}

fn text(payload: &Object, key: &str) -> Option<Value> {
    match payload.get(key) {
        Some(Json::String(text)) if !text.is_empty() => Some(Value::Str(text.clone().into())),
        Some(Json::Number(number)) => Some(Value::Str(number.to_string().into())),
        _ => None,
    }
}

/// A flag of any JSON value, true as [`truthy`] says; `None` when the key is absent.
fn flag(payload: &Object, key: &str) -> Option<Value> {
    payload.get(key).map(|value| Value::Bool(truthy(value)))
}

/// A list of `{name, score}` colors as `{name, rgb, score}`; `None` when there are none.
fn colors(payload: &Object, key: &str) -> Result<Option<Value>, String> {
    let items = list(payload, key)?;
    if items.is_empty() {
        return Ok(None);
    }
    let colors = items
        .iter()
        .map(|item| {
            let item = object(item, "a color")?;
            let (Some(name), Some(score)) = (
                item.get("name").and_then(Json::as_str),
                item.get("score").and_then(Json::as_f64),
            ) else {
                return Err("a color needs a name and a score".to_owned());
            };
            let (_, rgb) = COLORS
                .iter()
                .find(|(known, _)| known.eq_ignore_ascii_case(name))
                .ok_or_else(|| format!("the color '{name}' is not known"))?;
            Ok(Value::Map(vec![
                ("name".into(), Value::Str(name.to_owned().into())),
                ("rgb".into(), Value::List(rgb.map(Value::Int).to_vec())),
                ("score".into(), Value::Float(score)),
            ]))
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(Some(Value::List(colors)))
}
