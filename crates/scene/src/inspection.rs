//! An entity's fields as the inspector lists them: the leaves of its latest observation, grouped by what they say.
//!
//! Hit-testing never reads `source_data` or `debug`, but inspecting an object shows both, under Debug with their
//! origin. A Scene's own `debug` is never listed.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

mod leaf;
mod query;

pub use leaf::Leaf;
use leaf::{datetime_leaf, number_leaf};
pub use query::{Budget, OverBudget, query_leaves};

use crate::{
    Classification, ColorEntry, Entity, Geometry, MotionState, Name, Observation, Rgb, SceneTime,
    Value, WorldVelocity,
};

/// The most fields an inspection lists.
pub const MAX_FIELDS: usize = 4096;

/// Sections of the inspection, in display order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
#[ts(rename = "FieldGroupWire")]
pub enum FieldGroup {
    /// What the object is: class, score, confidence, attributes.
    Classification,
    /// Where it moves.
    Motion,
    /// Where it is.
    Geometry,
    /// What the file and the decoder carried along.
    Debug,
    /// Everything else the entity and its observation hold.
    Raw,
    /// The pictures of the entity.
    Images,
}

impl FieldGroup {
    /// The group's name in a query: `classification`, `debug`, and so on.
    pub const fn name(self) -> &'static str {
        match self {
            Self::Classification => "classification",
            Self::Motion => "motion",
            Self::Geometry => "geometry",
            Self::Debug => "debug",
            Self::Raw => "raw",
            Self::Images => "images",
        }
    }
}

/// Who produced a field of the Debug group.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
#[ts(rename = "FieldOriginWire")]
pub enum FieldOrigin {
    /// The source's own data, as it wrote it.
    Source,
    /// What the decoder computed.
    Decoder,
}

/// One leaf of an entity, addressed by its path inside its group.
#[derive(Clone, Debug, PartialEq)]
pub struct InspectionField {
    /// The section the field is listed in.
    pub group: FieldGroup,
    /// Who produced the field; only in the Debug group.
    pub origin: Option<FieldOrigin>,
    /// The field's place in the data; a list index is its decimal digits.
    pub path: Vec<String>,
    /// The leaf.
    pub value: Leaf,
    /// The colour the leaf is the score of, for an entry of a colour attribute.
    pub color: Option<Rgb>,
    /// What a view shows in place of the value: a score or confidence to two decimals.
    pub text: Option<String>,
    /// The value of a score, a confidence or a colour's score that lies within 0 to 1, for a view to draw as a bar.
    pub fraction: Option<f64>,
}

/// Whether a query's `path` (lower case) names the key made of `pieces` joined by dots: it equals the key or a
/// trailing run of its dotted parts, ignoring the key's case. The key is read from its end, only as far as the path
/// reaches, and is never built.
pub fn path_names<'a>(path: &str, pieces: impl DoubleEndedIterator<Item = &'a str>) -> bool {
    // A dot after every piece: after the first, it stands for the front of the key.
    let mut key = pieces.rev().flat_map(|piece| {
        let reversed = piece.chars().rev().flat_map(|c| c.to_lowercase().rev());
        reversed.chain(std::iter::once('.'))
    });
    path.chars().rev().all(|wanted| key.next() == Some(wanted)) && key.next() == Some('.')
}

/// What a leaf is besides its value.
#[derive(Default)]
struct Marks {
    color: Option<Rgb>,
    text: Option<String>,
    fraction: Option<f64>,
}

/// The fraction a score or confidence of `value` fills.
fn fraction(value: f64) -> Option<f64> {
    (0.0..=1.0).contains(&value).then_some(value)
}

/// The entity's fields from its latest observation, in display order: at most `limit`, and the flag says some were
/// left out. Empty values are not listed, except in Debug, where an empty text, list or map is shown as the source
/// reported it.
pub fn entity_fields(entity: &Entity, limit: usize) -> (Vec<InspectionField>, bool) {
    let mut listing = Listing {
        limit,
        fields: Vec::new(),
        cut: false,
    };
    walk_entity(entity, &mut listing);
    (listing.fields, listing.cut)
}

/// Where the leaves of a walk go, one at a time with the path they are at, borrowed.
trait Sink {
    /// Whether the walk should stop: asked with a value still to list. A sink that cuts the walk short says so.
    fn full(&mut self) -> bool;
    /// Whether the leaf at `path` is wanted, asked before it is made so that an unwanted one costs nothing; `color`
    /// says the leaf is a colour's score, whose `rgb` channels are leaves of their own.
    fn wants(&mut self, place: Place, path: &[String], color: bool) -> bool;
    /// Takes a leaf that is wanted.
    fn push(&mut self, place: Place, path: &[String], value: Leaf, marks: Marks);
}

/// The sink of an inspection: every leaf, up to `limit`.
struct Listing {
    limit: usize,
    fields: Vec<InspectionField>,
    cut: bool,
}

impl Sink for Listing {
    fn full(&mut self) -> bool {
        let full = self.fields.len() >= self.limit;
        self.cut |= full;
        full
    }

    fn wants(&mut self, _place: Place, _path: &[String], _color: bool) -> bool {
        true
    }

    fn push(&mut self, place: Place, path: &[String], value: Leaf, marks: Marks) {
        if self.full() {
            return;
        }
        self.fields.push(InspectionField {
            group: place.group,
            origin: place.origin,
            path: path.to_vec(),
            value,
            color: marks.color,
            text: marks.text,
            fraction: marks.fraction,
        });
    }
}

/// Walks the entity's leaves into `sink`, in display order.
fn walk_entity<S: Sink>(entity: &Entity, sink: &mut S) {
    let mut walk = Walk {
        sink,
        path: Vec::new(),
    };
    let observation = entity.latest_observation();
    if let Some(observation) = observation {
        walk.classification(observation);
    }
    walk.motion(entity.motion_state, observation);
    if let Some(observation) = observation {
        walk.geometry(observation);
        walk.debug(observation);
    }
    walk.raw(entity, observation);
    walk.images(entity);
}

#[derive(Clone, Copy)]
struct Place {
    group: FieldGroup,
    origin: Option<FieldOrigin>,
}

const fn place(group: FieldGroup) -> Place {
    Place {
        group,
        origin: None,
    }
}

struct Walk<'a, S> {
    sink: &'a mut S,
    /// The path of the value being walked.
    path: Vec<String>,
}

impl<S: Sink> Walk<'_, S> {
    /// Gives the sink a leaf at the current path, made only if the sink wants it.
    fn emit(&mut self, place: Place, value: impl FnOnce() -> Leaf, marks: Marks) {
        if self.sink.wants(place, &self.path, marks.color.is_some()) {
            self.sink.push(place, &self.path, value(), marks);
        }
    }

    fn leaf(&mut self, place: Place, value: Leaf) {
        self.emit(place, || value, Marks::default());
    }

    fn text(&mut self, place: Place, text: &str) {
        self.emit(place, || Leaf::Text(text.to_owned()), Marks::default());
    }

    /// A number, or its name when the source's float is not finite.
    fn number(&mut self, place: Place, number: f64) {
        self.emit(place, || number_leaf(number), Marks::default());
    }

    /// Whether the sink is full, which is also when something is left out: called with a value still to list.
    fn stop(&mut self) -> bool {
        self.sink.full()
    }

    /// Walks `inside` with `segment` added to the path.
    fn under(&mut self, segment: &str, inside: impl FnOnce(&mut Self)) {
        self.path.push(segment.to_owned());
        inside(self);
        self.path.pop();
    }

    /// A count at `segment`: a whole number, or a float when it exceeds `i64`.
    fn count_at(&mut self, place: Place, segment: &str, count: u64) {
        let leaf = i64::try_from(count).map_or(Leaf::Float(count as f64), Leaf::Int);
        self.under(segment, |walk| walk.leaf(place, leaf));
    }

    fn number_at(&mut self, place: Place, segment: &str, number: f64) {
        self.under(segment, |walk| walk.number(place, number));
    }

    fn classification(&mut self, observation: &Observation) {
        let place = place(FieldGroup::Classification);
        let primary = observation.primary_classification();
        if let Some(primary) = primary {
            self.under("type", |walk| walk.text(place, primary.class_type.as_str()));
            self.scores(place, primary);
            self.under("attributes", |walk| walk.map(place, &primary.attributes));
        }
        self.under("candidates", |walk| {
            for candidate in observation.ranked_classifications() {
                // The primary's attributes are listed under `attributes` already.
                let is_primary = primary.is_some_and(|primary| std::ptr::eq(primary, candidate));
                walk.under(candidate.class_type.as_str(), |walk| {
                    walk.scores(place, candidate);
                    if !is_primary {
                        walk.under("attributes", |walk| walk.map(place, &candidate.attributes));
                    }
                });
            }
        });
    }

    fn scores(&mut self, place: Place, classification: &Classification) {
        let measures = [
            ("score", classification.score.map(|score| score.value())),
            (
                "confidence",
                classification
                    .confidence
                    .map(|confidence| confidence.value()),
            ),
        ];
        for (name, measure) in measures {
            let Some(measure) = measure else { continue };
            let marks = Marks {
                text: Some(format!("{measure:.2}")),
                fraction: fraction(measure),
                ..Marks::default()
            };
            self.under(name, |walk| {
                walk.emit(place, || number_leaf(measure), marks)
            });
        }
    }

    fn motion(&mut self, state: Option<MotionState>, observation: Option<&Observation>) {
        let place = place(FieldGroup::Motion);
        if let Some(state) = state {
            let name = match state {
                MotionState::Moving => "moving",
                MotionState::Stationary => "stationary",
                MotionState::Unknown => "unknown",
            };
            self.under("state", |walk| walk.text(place, name));
        }
        let measured = observation.and_then(|observation| observation.measurements.as_deref());
        if let Some(velocity) = measured.and_then(|measured| measured.image_velocity) {
            self.under("velocity_in_image_space", |walk| {
                walk.number_at(place, "vx", velocity.vx);
                walk.number_at(place, "vy", velocity.vy);
            });
        }
        if let Some(WorldVelocity { pitch, speed, yaw }) =
            measured.and_then(|measured| measured.world_velocity)
        {
            self.under("velocity_in_world_space", |walk| {
                walk.number_at(place, "pitch", pitch);
                walk.number_at(place, "speed", speed);
                walk.number_at(place, "yaw", yaw);
            });
        }
    }

    fn geometry(&mut self, observation: &Observation) {
        let place = place(FieldGroup::Geometry);
        let bounds = observation.geometry.bounds();
        self.number_at(place, "x", bounds.left());
        self.number_at(place, "y", bounds.top());
        self.number_at(place, "width", bounds.width());
        self.number_at(place, "height", bounds.height());
        if let Geometry::Polygon(polygon) = &observation.geometry {
            self.count_at(place, "points", polygon.points().len() as u64);
        }
        let measured = observation.measurements.as_deref();
        if let Some(geographic) = measured.and_then(|measured| measured.geographic) {
            self.under("geographical_coordinates", |walk| {
                walk.number_at(place, "latitude", geographic.latitude);
                walk.number_at(place, "longitude", geographic.longitude);
                if let Some(elevation) = geographic.elevation {
                    walk.number_at(place, "elevation", elevation);
                }
            });
        }
        if let Some(world) = measured.and_then(|measured| measured.world) {
            self.under("world_coordinates", |walk| {
                walk.number_at(place, "distance", world.distance);
                walk.number_at(place, "azimuth", world.azimuth);
                walk.number_at(place, "elevation", world.elevation);
            });
        }
    }

    fn debug(&mut self, observation: &Observation) {
        for (origin, fields) in [
            (FieldOrigin::Source, observation.source_data()),
            (FieldOrigin::Decoder, observation.debug()),
        ] {
            let place = Place {
                group: FieldGroup::Debug,
                origin: Some(origin),
            };
            for (key, value) in fields {
                self.under(key, |walk| walk.reported(place, value));
            }
        }
    }

    fn raw(&mut self, entity: &Entity, observation: Option<&Observation>) {
        let place = place(FieldGroup::Raw);
        if let Some(reason) = entity.end_reason.as_deref().filter(|r| !r.is_empty()) {
            self.under("end_reason", |walk| walk.text(place, reason));
        }
        if let Some(observation) = observation {
            match observation.time {
                SceneTime::Frame(frame) => self.count_at(place, "frame_number", frame),
                SceneTime::Timestamp(time) => {
                    self.under("timestamp", |walk| walk.leaf(place, datetime_leaf(time)));
                }
            }
        }
    }

    fn images(&mut self, entity: &Entity) {
        let place = place(FieldGroup::Images);
        for (index, image) in entity.images.iter().enumerate() {
            if self.stop() {
                break;
            }
            self.under(&index.to_string(), |walk| {
                walk.under("timestamp", |walk| {
                    walk.leaf(place, datetime_leaf(image.timestamp));
                });
                walk.under("bounding_box", |walk| {
                    let bounds = image.bounding_box;
                    walk.number_at(place, "x", bounds.left());
                    walk.number_at(place, "y", bounds.top());
                    walk.number_at(place, "width", bounds.width());
                    walk.number_at(place, "height", bounds.height());
                });
                let size = format!("{} bytes", image.data.len());
                walk.under("data", |walk| walk.text(place, &size));
            });
        }
    }

    /// Lists the populated leaves of `fields` below the current path.
    fn map(&mut self, place: Place, fields: &[(Name, Value)]) {
        for (key, value) in fields {
            if self.stop() {
                break;
            }
            self.under(key, |walk| walk.populated(place, value));
        }
    }

    fn populated(&mut self, place: Place, value: &Value) {
        match value {
            Value::Null => {}
            Value::Str(text) if text.is_empty() => {}
            Value::Map(fields) => self.map(place, fields),
            Value::List(items) => {
                let colors: Option<Vec<_>> = items.iter().map(ColorEntry::read_exact).collect();
                if let Some(colors) = colors.filter(|colors| !colors.is_empty()) {
                    return self.colors(place, &colors);
                }
                for (index, item) in items.iter().enumerate() {
                    if self.stop() {
                        break;
                    }
                    self.under(&index.to_string(), |walk| walk.populated(place, item));
                }
            }
            leaf => self.scalar(place, leaf),
        }
    }

    /// A colour attribute: one leaf per colour, its score, named by the colour's name or its index.
    fn colors(&mut self, place: Place, colors: &[ColorEntry]) {
        for (index, color) in colors.iter().enumerate() {
            if self.stop() {
                break;
            }
            let segment = color
                .name
                .filter(|name| !name.is_empty())
                .map_or_else(|| index.to_string(), str::to_owned);
            let score = color.score.map_or(Leaf::Null, number_leaf);
            let marks = Marks {
                color: Some(color.rgb),
                fraction: color.score.and_then(fraction),
                ..Marks::default()
            };
            self.under(&segment, |walk| walk.emit(place, || score, marks));
        }
    }

    /// Like `populated`, but a source's empty values stay: `null`, `""`, `[]` and `{}`.
    fn reported(&mut self, place: Place, value: &Value) {
        match value {
            Value::Null => self.leaf(place, Leaf::Null),
            Value::Map(fields) if fields.is_empty() => self.text(place, "{}"),
            Value::List(items) if items.is_empty() => self.text(place, "[]"),
            Value::Map(fields) => {
                for (key, value) in fields {
                    if self.stop() {
                        break;
                    }
                    self.under(key, |walk| walk.reported(place, value));
                }
            }
            Value::List(items) => {
                for (index, item) in items.iter().enumerate() {
                    if self.stop() {
                        break;
                    }
                    self.under(&index.to_string(), |walk| walk.reported(place, item));
                }
            }
            leaf => self.scalar(place, leaf),
        }
    }

    fn scalar(&mut self, place: Place, value: &Value) {
        match value {
            Value::Bool(truth) => self.leaf(place, Leaf::Bool(*truth)),
            Value::Int(whole) => self.leaf(place, Leaf::Int(*whole)),
            Value::Float(number) => self.number(place, *number),
            Value::Str(text) => self.text(place, text),
            Value::Null | Value::List(_) | Value::Map(_) => {}
        }
    }
}

#[cfg(test)]
mod tests;
