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

/// Spellings that differ from the class name after lowercasing.
static ALIASES: [(&str, &str); 3] = [
    ("person", "human"),
    ("vehicleother", "vehicle_other"),
    ("licenseplate", "license_plate"),
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

fn finite(value: &Json, what: &str) -> Result<f64, String> {
    value
        .as_f64()
        .ok_or_else(|| format!("{what} must be a number"))
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

/// A picture with its time, box and base64 data.
pub(super) fn image(value: &Json) -> Result<Image, String> {
    let fields = object(value, "image")?;
    let data = fields
        .get("data")
        .and_then(Json::as_str)
        .filter(|data| !data.is_empty())
        .ok_or("the image lacks data")?;
    Ok(Image {
        timestamp: timestamp(fields, "timestamp", "the image timestamp")?,
        bounding_box: bounding_box(fields.get("bounding_box"))?,
        data: STANDARD
            .decode(data)
            .map_err(|error| format!("the image data is not base64: {error}"))?
            .into(),
    })
}

/// A class given as `{type, score, ...attributes}`.
pub(super) fn classification(payload: &Object) -> Result<Classification, String> {
    let name = payload
        .get("type")
        .and_then(Json::as_str)
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .ok_or("a class lacks its type")?;
    let lower = name.to_lowercase();
    let name = ALIASES
        .iter()
        .find(|(alias, _)| *alias == lower)
        .map_or(lower, |(_, class)| (*class).to_owned());
    let class_type = ClassType::new(name).map_err(|error| error.to_string())?;
    let score = optional_number(payload, "score")?
        .map(Score::new)
        .transpose()
        .map_err(|error| error.to_string())?;
    Ok(Classification {
        score,
        attributes: attributes(&class_type, payload)?,
        ..Classification::new(class_type)
    })
}

/// The class's own details, such as colors, by what the class is.
fn attributes(class_type: &ClassType, payload: &Object) -> Result<Fields, String> {
    let mut fields = Fields::new();
    let mut add = |name: &'static str, value: Option<Value>| {
        if let Some(value) = value {
            fields.push((name.into(), value));
        }
    };
    match class_type.as_str() {
        "human" => {
            add(
                "upper_clothing_colors",
                colors(payload, "upper_clothing_colors")?,
            );
            add(
                "lower_clothing_colors",
                colors(payload, "lower_clothing_colors")?,
            );
            add("carries_bag", flag(payload, "carries_bag")?);
            add(
                "face_visible",
                optional_number(payload, "face_visible")?.map(Value::Float),
            );
        }
        "vehicle" | "vehicle_other" | "car" | "truck" | "bus" | "bike" | "bicycle" => {
            add("vehicle_colors", colors(payload, "colors")?);
        }
        "license_plate" => {
            for key in ["country_code", "state_code", "plate_number"] {
                add(key, text(payload, key));
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

fn flag(payload: &Object, key: &str) -> Result<Option<Value>, String> {
    match payload.get(key) {
        None | Some(Json::Null) => Ok(None),
        Some(Json::Bool(flag)) => Ok(Some(Value::Bool(*flag))),
        Some(_) => Err(format!("{key} must be true or false")),
    }
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
