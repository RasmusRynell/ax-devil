use std::{
    borrow::Cow,
    collections::{HashMap, hash_map::Entry},
    io::ErrorKind,
    path::Path,
};

use foundation::{CancelToken, Interrupt, SeqInfo, parse_seqinfo};
use memchr::memchr_iter;
use overlay_sync::MatchBasis;
use scene::{
    BoundingBox, ClassRule, ClassType, Classification, Coverage, Entity, EntityId, Fields,
    FilterOption, FilterPolicy, Geometry, Observation, Scene, SceneError, SceneTime, Score,
    TimeSlice, UNMAPPED_CLASS, Value,
};

use crate::{
    DecodeContext, DecodeError, DecodeOptions, DecodeSummary, DecoderSpec, FileSceneDecoder,
    OnEqualKey, SceneSink, SkippedRecords, SourceFile, Storage,
};

static SPEC: DecoderSpec = DecoderSpec {
    plugin_id: "mot",
    handler: "MOT_FILE",
    display_name: "MOT Annotations",
    extensions: &[".txt"],
    artifact_version: 1,
    storage: Storage::DerivedCache,
    bases: &[MatchBasis::Frame],
    default_basis: MatchBasis::Frame,
};

/// Rows between cancellation checks.
const CANCEL_EVERY: usize = 4096;
/// Widest row any supported layout has.
const MAX_COLUMNS: usize = 10;
/// Average bytes per row, used to size the row buffer up front.
const BYTES_PER_ROW: usize = 40;

/// MOT Challenge class ids 1 to 13: class, filter id, filter label.
static CLASSES: [(ClassType, &str, &str); 13] = [
    (ClassType::from_static("person"), "show_person", "Person"),
    (
        ClassType::from_static("person_on_vehicle"),
        "show_person_on_vehicle",
        "Person On Vehicle",
    ),
    (ClassType::from_static("car"), "show_car", "Car"),
    (ClassType::from_static("bicycle"), "show_bicycle", "Bicycle"),
    (
        ClassType::from_static("motorcycle"),
        "show_motorcycle",
        "Motorcycle",
    ),
    (ClassType::from_static("vehicle"), "show_vehicle", "Vehicle"),
    (
        ClassType::from_static("static_person"),
        "show_static_person",
        "Static Person",
    ),
    (
        ClassType::from_static("distractor"),
        "show_distractor",
        "Distractor",
    ),
    (
        ClassType::from_static("occluder"),
        "show_occluder",
        "Occluder",
    ),
    (
        ClassType::from_static("occluder_on_ground"),
        "show_occluder_on_ground",
        "Occluder On Ground",
    ),
    (
        ClassType::from_static("occluder_full"),
        "show_occluder_full",
        "Occluder Full",
    ),
    (
        ClassType::from_static("reflection"),
        "show_reflection",
        "Reflection",
    ),
    (ClassType::from_static("crowd"), "show_crowd", "Crowd"),
];

/// Reads MOT Challenge detection (7 or 10 columns) and ground-truth (9 columns) text files.
///
/// Frames are 1-based in the file and 0-based in Scenes. Boxes are pixels, normalized by the image size the
/// dataset's `seqinfo.ini` states, which sits in the sequence folder above `gt/` or `det/`, or beside a file
/// kept elsewhere; the video is never consulted. [`declared_options`](FileSceneDecoder::declared_options) reads it and declares it as `width` and
/// `height`, so changing the file rebuilds the scene cache. A frame without rows is empty ([`Coverage::Dense`]).
pub struct MotDecoder;

/// The size the dataset's boxes are measured in.
#[derive(Clone, Copy)]
struct Extent {
    width: f64,
    height: f64,
}

/// The size declared by [`MotDecoder::declared_options`].
fn declared_extent(options: &DecodeOptions) -> Result<Extent, DecodeError> {
    let size = |key: &str| options.get(key).and_then(serde_json::Value::as_u64);
    match (size("width"), size("height")) {
        (Some(width @ 1..), Some(height @ 1..)) => Ok(Extent {
            width: width as f64,
            height: height as f64,
        }),
        _ => Err(DecodeError(
            "MOT annotations decode only with the image size from their seqinfo.ini".to_owned(),
        )),
    }
}

/// The `seqinfo.ini` of the sequence that holds `annotations`: in the folder above a `gt` or `det` folder, else in
/// the file's own folder. No other folder is searched.
fn read_seqinfo(annotations: &Path) -> Result<SeqInfo, DecodeError> {
    let folder = annotations.parent().unwrap_or(Path::new(""));
    let in_layout_folder = folder
        .file_name()
        .is_some_and(|name| name == "gt" || name == "det");
    let sequence = if in_layout_folder {
        folder.parent().unwrap_or(folder)
    } else {
        folder
    };
    let ini = sequence.join("seqinfo.ini");
    let text = std::fs::read_to_string(&ini).map_err(|error| {
        DecodeError(if error.kind() == ErrorKind::NotFound {
            format!(
                "MOT annotations need a seqinfo.ini next to the sequence (tried {})",
                ini.display()
            )
        } else {
            format!("Cannot read {}: {error}", ini.display())
        })
    })?;
    parse_seqinfo(&text)
        .map_err(|reason| DecodeError(format!("{} is not usable: {reason}", ini.display())))
}

/// One valid row, its box already normalized.
struct Row {
    frame: u64,
    track_id: i64,
    geometry: BoundingBox,
    /// A detection's score; ground truth has none.
    score: Option<Score>,
    /// Ground truth's mark; detections have none.
    mark: Option<i64>,
    class_id: i64,
    visibility: f64,
}

impl FileSceneDecoder for MotDecoder {
    fn spec(&self) -> &'static DecoderSpec {
        &SPEC
    }

    fn declared_options(
        &self,
        path: &Path,
        _options: &DecodeOptions,
    ) -> Result<DecodeOptions, DecodeError> {
        let info = read_seqinfo(path)?;
        Ok(DecodeOptions::from_iter([
            ("width".to_owned(), info.width.into()),
            ("height".to_owned(), info.height.into()),
        ]))
    }

    fn decode_file(
        &self,
        source: &dyn SourceFile,
        context: &DecodeContext<'_>,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        let extent = declared_extent(context.options).map_err(Interrupt::Failed)?;
        let input = source.bytes(cancel)?;
        let mut skipped = SkippedRecords::new("rows", "Objects on skipped rows are not shown.");
        let (mut rows, total) = read_rows(input, extent, &mut skipped, cancel)?;
        if !rows.is_sorted_by_key(|row| row.frame) {
            // Stable: file order within a frame decides the ids of untracked detections.
            rows.sort_by_key(|row| row.frame);
        }

        let mut seen = 0;
        let mut tracks: HashMap<i64, Track> = HashMap::new();
        for frame_rows in rows.chunk_by(|a, b| a.frame == b.frame) {
            if seen / CANCEL_EVERY != (seen + frame_rows.len()) / CANCEL_EVERY {
                cancel.check()?;
            }
            seen += frame_rows.len();
            let scene = frame_scene(frame_rows, &mut tracks)
                .map_err(|error| Interrupt::Failed(DecodeError(error.to_string())))?;
            if sink.sample(scene, OnEqualKey::Replace).is_break() {
                break;
            }
        }

        Ok(DecodeSummary {
            warnings: skipped.warning(total).into_iter().collect(),
            filter: filter_policy(),
            metadata: Fields::new(),
            coverage: Coverage::Dense,
        })
    }
}

/// Parses every non-blank line; returns the valid rows and the number of non-blank lines.
fn read_rows(
    input: &[u8],
    extent: Extent,
    skipped: &mut SkippedRecords,
    cancel: &CancelToken,
) -> Result<(Vec<Row>, usize), Interrupt<DecodeError>> {
    let mut rows = Vec::with_capacity(input.len() / BYTES_PER_ROW);
    let mut total = 0;
    let mut start = 0;
    let mut line_number = 0;
    let line_ends =
        memchr_iter(b'\n', input).chain((!input.ends_with(b"\n")).then_some(input.len()));
    for end in line_ends {
        let line = &input[start..end];
        start = end + 1;
        line_number += 1;
        if line_number % CANCEL_EVERY == 0 {
            cancel.check()?;
        }
        let line = match std::str::from_utf8(line) {
            Ok(text) => text.trim(),
            Err(_) => {
                total += 1;
                skipped.add(format!("line {line_number}: not valid UTF-8 text"));
                continue;
            }
        };
        if line.is_empty() {
            continue;
        }
        total += 1;
        match parse_row(line, extent) {
            Ok(row) => rows.push(row),
            Err(reason) => skipped.add(format!("line {line_number}: {reason}")),
        }
    }
    Ok((rows, total))
}

fn parse_row(line: &str, extent: Extent) -> Result<Row, String> {
    let mut parts = [""; MAX_COLUMNS];
    let mut count = 0;
    for part in line.split(',') {
        if let Some(slot) = parts.get_mut(count) {
            *slot = part;
        }
        count += 1;
    }
    let ground_truth = match count {
        7 | 10 => false,
        9 => true,
        _ => return Err(format!("expected 7, 9 or 10 columns, got {count}")),
    };

    let frame = whole(parts[0], "frame number")?;
    let track_id = whole(parts[1], "track id")?;
    let left = number(parts[2], "box left")?;
    let top = number(parts[3], "box top")?;
    let width = number(parts[4], "box width")?;
    let height = number(parts[5], "box height")?;
    let value = number(parts[6], if ground_truth { "mark" } else { "score" })?;
    let (class_id, visibility) = if ground_truth {
        (
            whole(parts[7], "class id")?,
            number(parts[8], "visibility")?,
        )
    } else {
        (1, 1.0)
    };

    if frame < 1 {
        return Err(format!("frame number {frame} is below 1"));
    }
    if width < 0.0 || height < 0.0 {
        return Err("negative box width or height".to_string());
    }
    let left = left / extent.width;
    let top = top / extent.height;
    let geometry = BoundingBox::new(
        left,
        top,
        left + width / extent.width,
        top + height / extent.height,
    )
    .map_err(|error| error.to_string())?;
    // A detection's score is the source's own number at any range; ground truth has neither score nor confidence.
    let (score, mark) = if ground_truth {
        (None, Some(value as i64))
    } else {
        (
            Some(Score::new(value).map_err(|error| error.to_string())?),
            None,
        )
    };
    Ok(Row {
        frame: (frame - 1) as u64,
        track_id,
        geometry,
        score,
        mark,
        class_id,
        visibility,
    })
}

fn whole(field: &str, what: &str) -> Result<i64, String> {
    field
        .trim()
        .parse()
        .map_err(|_| format!("{what} '{field}' is not a whole number"))
}

fn number(field: &str, what: &str) -> Result<f64, String> {
    match field.trim().parse::<f64>() {
        Ok(value) if value.is_finite() => Ok(value),
        _ => Err(format!("{what} '{field}' is not a finite number")),
    }
}

/// Where a track was last seen: its id for the whole file, so every frame shares it, and its entity's position in
/// that frame.
struct Track {
    id: EntityId,
    frame: u64,
    slot: usize,
}

/// The Scene of one frame; `tracks` is kept between frames.
fn frame_scene(rows: &[Row], tracks: &mut HashMap<i64, Track>) -> Result<Scene, SceneError> {
    let frame = rows[0].frame;
    let mut entities: Vec<Entity> = Vec::with_capacity(rows.len());
    for (index, row) in rows.iter().enumerate() {
        let observation = observation(row);
        if row.track_id < 0 {
            let mut entity = Entity::new(EntityId::new(format!("untracked:{frame}:{index}"))?);
            entity.push_observation(observation);
            entities.push(entity);
            continue;
        }
        let id = match tracks.entry(row.track_id) {
            Entry::Occupied(mut seen) => {
                let track = seen.get_mut();
                if track.frame == frame {
                    entities[track.slot].push_observation(observation);
                    continue;
                }
                (track.frame, track.slot) = (frame, entities.len());
                track.id.clone()
            }
            Entry::Vacant(new) => {
                let id = EntityId::from(row.track_id);
                new.insert(Track {
                    id: id.clone(),
                    frame,
                    slot: entities.len(),
                });
                id
            }
        };
        let mut entity = Entity::new(id);
        entity.push_observation(observation);
        entities.push(entity);
    }
    Scene::new(TimeSlice::at(SceneTime::Frame(frame))).with_entities(entities)
}

fn observation(row: &Row) -> Observation {
    let mut attributes = vec![
        (Cow::Borrowed("class_id"), Value::Int(row.class_id)),
        (
            Cow::Borrowed("source"),
            Value::Str(Cow::Borrowed(if row.mark.is_some() { "gt" } else { "det" })),
        ),
        (
            Cow::Borrowed("visibility_ratio"),
            Value::Float(row.visibility),
        ),
    ];
    if let Some(mark) = row.mark {
        attributes.push((Cow::Borrowed("gt_mark"), Value::Int(mark)));
    }
    let mut observation =
        Observation::new(SceneTime::Frame(row.frame), Geometry::Box(row.geometry));
    observation.classifications = vec![Classification {
        score: row.score,
        attributes,
        ..Classification::new(class_type(row.class_id))
    }];
    observation
}

fn class_type(class_id: i64) -> ClassType {
    usize::try_from(class_id - 1)
        .ok()
        .and_then(|index| CLASSES.get(index))
        .map_or_else(|| ClassType::unmapped(class_id), |class| class.0.clone())
}

/// One filter per MOT class and one for every class id the table lacks.
fn filter_policy() -> FilterPolicy {
    let rule = |name: &str| ClassRule {
        types: vec![Cow::Owned(name.to_owned())],
        include_empty: false,
        exclude: false,
    };
    let mut options: Vec<FilterOption> = CLASSES
        .iter()
        .map(|(class, id, label)| FilterOption {
            id: Cow::Borrowed(*id),
            label: Cow::Borrowed(*label),
            default_enabled: true,
            rule: rule(class.as_str()),
        })
        .collect();
    options.push(FilterOption {
        id: Cow::Borrowed("show_unmapped"),
        label: Cow::Borrowed("Unmapped"),
        default_enabled: true,
        rule: rule(UNMAPPED_CLASS),
    });
    FilterPolicy {
        name: "mot",
        options,
    }
}
