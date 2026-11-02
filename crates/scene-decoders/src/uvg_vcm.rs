//! UVG-VCM annotations JSON v1.0: detections and tracks per source frame, with optional segmentation polygons.
//!
//! The whole file is one document, so a malformed frame fails the load: dropping it would misalign every overlay.

use std::collections::HashMap;

use foundation::{CancelToken, Interrupt};
use overlay_sync::MatchBasis;
use scene::{
    BoundingBox, ClassRule, ClassType, Classification, Coverage, Entity, EntityId, Fields,
    FilterOption, FilterPolicy, Geometry, Name, Observation, Point, Polygon, Scene, SceneTime,
    TimeSlice, Value,
};
use serde_json::value::RawValue;

use crate::{
    DecodeContext, DecodeError, DecodeSummary, DecoderSpec, FileSceneDecoder, LoadWarning,
    OnEqualKey, SceneSink, SourceFile, Storage,
};

static SPEC: DecoderSpec = DecoderSpec {
    plugin_id: "uvg-vcm",
    handler: "UVG_VCM",
    display_name: "UVG-VCM",
    extensions: &[".json"],
    artifact_version: 1,
    storage: Storage::DerivedCache,
    bases: &[MatchBasis::Frame],
    default_basis: MatchBasis::Frame,
};

/// How many classes UVG-VCM has.
const CLASS_COUNT: usize = 80;

/// UVG-VCM uses contiguous ids 1 to 80, not the sparse category ids of COCO JSON exports.
/// Source: <https://tie-ultravideo.rd.tuni.fi/UVG-VCM/index.html>
static CLASS_NAMES: [ClassType; CLASS_COUNT] = [
    ClassType::from_static("person"),
    ClassType::from_static("bicycle"),
    ClassType::from_static("car"),
    ClassType::from_static("motorcycle"),
    ClassType::from_static("airplane"),
    ClassType::from_static("bus"),
    ClassType::from_static("train"),
    ClassType::from_static("truck"),
    ClassType::from_static("boat"),
    ClassType::from_static("traffic light"),
    ClassType::from_static("fire hydrant"),
    ClassType::from_static("stop sign"),
    ClassType::from_static("parking meter"),
    ClassType::from_static("bench"),
    ClassType::from_static("bird"),
    ClassType::from_static("cat"),
    ClassType::from_static("dog"),
    ClassType::from_static("horse"),
    ClassType::from_static("sheep"),
    ClassType::from_static("cow"),
    ClassType::from_static("elephant"),
    ClassType::from_static("bear"),
    ClassType::from_static("zebra"),
    ClassType::from_static("giraffe"),
    ClassType::from_static("backpack"),
    ClassType::from_static("umbrella"),
    ClassType::from_static("handbag"),
    ClassType::from_static("tie"),
    ClassType::from_static("suitcase"),
    ClassType::from_static("frisbee"),
    ClassType::from_static("skis"),
    ClassType::from_static("snowboard"),
    ClassType::from_static("sports ball"),
    ClassType::from_static("kite"),
    ClassType::from_static("baseball bat"),
    ClassType::from_static("baseball glove"),
    ClassType::from_static("skateboard"),
    ClassType::from_static("surfboard"),
    ClassType::from_static("tennis racket"),
    ClassType::from_static("bottle"),
    ClassType::from_static("wine glass"),
    ClassType::from_static("cup"),
    ClassType::from_static("fork"),
    ClassType::from_static("knife"),
    ClassType::from_static("spoon"),
    ClassType::from_static("bowl"),
    ClassType::from_static("banana"),
    ClassType::from_static("apple"),
    ClassType::from_static("sandwich"),
    ClassType::from_static("orange"),
    ClassType::from_static("broccoli"),
    ClassType::from_static("carrot"),
    ClassType::from_static("hot dog"),
    ClassType::from_static("pizza"),
    ClassType::from_static("donut"),
    ClassType::from_static("cake"),
    ClassType::from_static("chair"),
    ClassType::from_static("couch"),
    ClassType::from_static("potted plant"),
    ClassType::from_static("bed"),
    ClassType::from_static("dining table"),
    ClassType::from_static("toilet"),
    ClassType::from_static("tv"),
    ClassType::from_static("laptop"),
    ClassType::from_static("mouse"),
    ClassType::from_static("remote"),
    ClassType::from_static("keyboard"),
    ClassType::from_static("cell phone"),
    ClassType::from_static("microwave"),
    ClassType::from_static("oven"),
    ClassType::from_static("toaster"),
    ClassType::from_static("sink"),
    ClassType::from_static("refrigerator"),
    ClassType::from_static("book"),
    ClassType::from_static("clock"),
    ClassType::from_static("vase"),
    ClassType::from_static("scissors"),
    ClassType::from_static("teddy bear"),
    ClassType::from_static("hair drier"),
    ClassType::from_static("toothbrush"),
];

/// Most frames a duplicate-id warning names.
const MAX_NAMED_FRAMES: usize = 3;

/// Reads UVG-VCM detection JSON files.
pub struct UvgVcmDecoder;

impl FileSceneDecoder for UvgVcmDecoder {
    fn spec(&self) -> &'static DecoderSpec {
        &SPEC
    }

    fn decode_file(
        &self,
        source: &dyn SourceFile,
        _context: &DecodeContext<'_>,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        let input = source.bytes(cancel)?;
        let mut document: HashMap<&str, &RawValue> = serde_json::from_slice(input)
            .map_err(|error| fail(format!("UVG-VCM document: {error}")))?;
        let version = document
            .remove("version")
            .and_then(|raw| serde_json::from_str::<String>(raw.get()).ok());
        if version.as_deref() != Some("1.0") {
            return Err(fail(
                "UVG-VCM annotations must declare version '1.0'".to_string(),
            ));
        }

        let frames: Option<Vec<&RawValue>> = (1..=document.len())
            .map(|number| document.get(number.to_string().as_str()).copied())
            .collect();
        let Some(frames) = frames.filter(|frames| !frames.is_empty()) else {
            return Err(fail(
                "UVG-VCM frame keys must be consecutive integers starting at '1', including empty frames".to_string(),
            ));
        };

        let mut seen = [false; CLASS_COUNT];
        let mut duplicates = Vec::new();
        for (index, raw) in frames.iter().enumerate() {
            cancel.check()?;
            let (scene, ambiguous) = decode_frame(index as u64, raw, &mut seen).map_err(fail)?;
            if !ambiguous.is_empty() {
                duplicates.push((index + 1, ambiguous));
            }
            if sink.sample(scene, OnEqualKey::Replace).is_break() {
                break;
            }
        }

        Ok(DecodeSummary {
            warnings: duplicate_warning(&duplicates, frames.len())
                .into_iter()
                .collect(),
            filter: filter_policy(&seen),
            metadata: Fields::new(),
            coverage: Coverage::Dense,
        })
    }
}

fn fail(message: String) -> Interrupt<DecodeError> {
    Interrupt::Failed(DecodeError(message))
}

/// The field `name` of a JSON object; the last of repeated names wins, as in other JSON readers.
fn field<'a>(object: &'a Fields, name: &str) -> Option<&'a Value> {
    object
        .iter()
        .rev()
        .find(|(key, _)| key == name)
        .map(|(_, value)| value)
}

fn integer(value: Option<&Value>, context: &str, minimum: i64) -> Result<i64, String> {
    match value {
        Some(Value::Int(number)) if *number >= minimum => Ok(*number),
        _ => Err(format!("{context}: expected an integer >= {minimum}")),
    }
}

fn number(value: Option<&Value>, context: &str) -> Result<f64, String> {
    match value {
        Some(Value::Int(number)) => Ok(*number as f64),
        Some(Value::Float(number)) if number.is_finite() => Ok(*number),
        _ => Err(format!("{context}: expected a finite number")),
    }
}

/// One source frame as a Scene, and the track ids it repeats.
fn decode_frame(
    index: u64,
    raw: &RawValue,
    seen: &mut [bool; CLASS_COUNT],
) -> Result<(Scene, Vec<i64>), String> {
    let frame = index + 1;
    let raw_detections: Vec<&RawValue> = serde_json::from_str(raw.get())
        .map_err(|_| format!("Frame {frame}: expected a JSON array"))?;
    let mut detections = Vec::with_capacity(raw_detections.len());
    for (position, raw) in raw_detections.into_iter().enumerate() {
        match serde_json::from_str(raw.get()) {
            Ok(Value::Map(fields)) => detections.push(fields),
            _ => {
                return Err(format!(
                    "Frame {frame} detection {position}: expected a JSON object"
                ));
            }
        }
    }
    let track_ids = detections
        .iter()
        .enumerate()
        .map(|(position, detection)| {
            integer(
                field(detection, "track_id"),
                &format!("Frame {frame} detection {position}.track_id"),
                0,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    let mut sorted = track_ids.clone();
    sorted.sort_unstable();
    let mut repeated: Vec<i64> = sorted
        .windows(2)
        .filter(|pair| pair[0] == pair[1])
        .map(|pair| pair[0])
        .collect();
    repeated.dedup();

    let time = SceneTime::Frame(index);
    let mut entities = Vec::with_capacity(detections.len());
    for (position, (detection, track_id)) in detections.into_iter().zip(track_ids).enumerate() {
        let context = format!("Frame {frame} detection {position}");
        let class_id = integer(
            field(&detection, "class_id"),
            &format!("{context}.class_id"),
            1,
        )?;
        let Some(class) = usize::try_from(class_id)
            .ok()
            .and_then(|id| CLASS_NAMES.get(id - 1))
        else {
            return Err(format!(
                "{context}.class_id: expected a UVG-VCM category ID in 1..80"
            ));
        };
        if field(&detection, "class_name")
            .is_some_and(|value| value.as_str() != Some(class.as_str()))
        {
            return Err(format!(
                "{context}.class_name: does not match category {class_id} ({class})"
            ));
        }
        seen[class_id as usize - 1] = true;

        let id = if repeated.binary_search(&track_id).is_ok() {
            EntityId::new(format!("ambiguous:{index}:{track_id}:{position}"))
                .map_err(|error| error.to_string())?
        } else {
            EntityId::from(track_id)
        };
        let mut attributes: Fields = vec![
            ("class_id".into(), Value::Int(class_id)),
            ("track_id".into(), Value::Int(track_id)),
        ];
        for name in ["iscrowd", "mask_color"] {
            if let Some(value) = field(&detection, name) {
                attributes.push((name.into(), value.clone()));
            }
        }
        let mut classification = Classification::new(class.clone());
        classification.attributes = attributes;
        let mut observation = Observation::new(time, geometry(&detection, &context)?);
        observation.classifications = vec![classification];
        let observation = observation
            .with_details(
                vec![("uvg_vcm".into(), Value::Map(detection))],
                Fields::new(),
            )
            .map_err(|error| format!("{context}: {error}"))?;
        let mut entity = Entity::new(id);
        entity.push_observation(observation);
        entities.push(entity);
    }
    let scene = Scene::new(TimeSlice::at(time))
        .with_entities(entities)
        .map_err(|error| format!("Frame {frame}: {error}"))?
        .with_debug(vec![(
            "duplicate_track_ids".into(),
            Value::List(repeated.iter().map(|id| Value::Int(*id)).collect()),
        )]);
    Ok((scene, repeated))
}

/// The box, or the polygon that replaces it. Coordinates slightly outside 0..1 are the authors' and are kept.
fn geometry(detection: &Fields, context: &str) -> Result<Geometry, String> {
    let [left, top, right, bottom] = ["x_min", "y_min", "x_max", "y_max"]
        .map(|name| number(field(detection, name), &format!("{context}.{name}")));
    let (left, top, right, bottom) = (left?, top?, right?, bottom?);
    let bounds = BoundingBox::new(left, top, right, bottom)
        .map_err(|_| format!("{context}: bounding-box maxima must not be below minima"))?;
    let Some(polygon) = field(detection, "polygon") else {
        return Ok(Geometry::Box(bounds));
    };
    let Value::List(coordinates) = polygon else {
        return Err(format!("{context}.polygon: expected a JSON array"));
    };
    if coordinates.len() < 6 || coordinates.len() % 2 != 0 {
        return Err(format!(
            "{context}.polygon: expected at least three x/y coordinate pairs"
        ));
    }
    let values = coordinates
        .iter()
        .map(|value| number(Some(value), &format!("{context}.polygon")))
        .collect::<Result<Vec<_>, _>>()?;
    let points = values
        .as_chunks::<2>()
        .0
        .iter()
        .map(|&[x, y]| Point::new(x, y))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("{context}.polygon: {error}"))?;
    Polygon::new(points)
        .map(Geometry::Polygon)
        .map_err(|error| format!("{context}.polygon: {error}"))
}

/// The warning for frames whose track ids repeat, naming the first few.
fn duplicate_warning(duplicates: &[(usize, Vec<i64>)], total: usize) -> Option<LoadWarning> {
    (!duplicates.is_empty()).then(|| LoadWarning {
        problem: format!("Duplicate tracking IDs in {} of {total} frames", duplicates.len()),
        reasons: duplicates
            .iter()
            .take(MAX_NAMED_FRAMES)
            .map(|(frame, ids)| format!("frame {frame}: {ids:?}").chars().take(200).collect())
            .collect(),
        consequence: "Affected detections are shown with frame-local ambiguous IDs; the original IDs remain in their attributes."
            .to_string(),
        more_reasons: duplicates.len() > MAX_NAMED_FRAMES,
    })
}

/// One filter per class the file uses, in alphabetical order, or `show_unclassified` when it has none.
fn filter_policy(seen: &[bool; CLASS_COUNT]) -> FilterPolicy {
    let mut labels: Vec<&str> = CLASS_NAMES
        .iter()
        .zip(seen)
        .filter(|(_, used)| **used)
        .map(|(class, _)| class.as_str())
        .collect();
    labels.sort_unstable();
    let mut options: Vec<FilterOption> = labels
        .into_iter()
        .map(|label| FilterOption {
            id: format!("show_{}", label.replace(' ', "_")).into(),
            label: title_case(label).into(),
            default_enabled: true,
            rule: ClassRule {
                types: vec![Name::Borrowed(label)],
                include_empty: false,
                exclude: false,
            },
        })
        .collect();
    if options.is_empty() {
        options.push(FilterOption {
            id: "show_unclassified".into(),
            label: "Unclassified".into(),
            default_enabled: true,
            rule: ClassRule {
                types: Vec::new(),
                include_empty: true,
                exclude: false,
            },
        });
    }
    FilterPolicy {
        name: SPEC.plugin_id,
        options,
    }
}

fn title_case(label: &str) -> String {
    label
        .split(' ')
        .map(|word| {
            let mut characters = word.chars();
            characters.next().map_or_else(String::new, |first| {
                format!("{}{}", first.to_ascii_uppercase(), characters.as_str())
            })
        })
        .collect::<Vec<_>>()
        .join(" ")
}
