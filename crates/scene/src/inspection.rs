//! An entity's fields as the inspector lists them: the leaves of its latest observation, grouped by what they say.
//!
//! Hit-testing never reads `source_data` or `debug`, but inspecting an object shows both, under Debug with their
//! origin. A Scene's own `debug` is never listed.

use crate::{
    Classification, Entity, Geometry, MotionState, Name, Observation, SceneTime, Value,
    WorldVelocity,
};

/// The most fields an inspection lists.
pub const MAX_FIELDS: usize = 4096;

/// The most characters of a text, and of one segment of a path, an inspection keeps.
pub const MAX_TEXT_CHARS: usize = 4096;

/// Sections of the inspection, in display order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
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
}

/// Who produced a field of the Debug group.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FieldOrigin {
    /// The source's own data, as it wrote it.
    Source,
    /// What the decoder computed.
    Decoder,
}

/// The value of a field: a leaf of the entity's data.
#[derive(Clone, Debug, PartialEq)]
pub enum Leaf {
    /// No value.
    Null,
    /// A truth value.
    Bool(bool),
    /// A finite number.
    Number(f64),
    /// Text, at most [`MAX_TEXT_CHARS`] characters.
    Text(String),
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
}

/// The entity's fields from its latest observation, in display order: at most `limit`, and the flag says some were
/// left out or a text was cut. Empty values are not listed, except in Debug, where an empty text, list or map is
/// shown as the source reported it.
pub fn entity_fields(entity: &Entity, limit: usize) -> (Vec<InspectionField>, bool) {
    let mut walk = Walk {
        limit,
        fields: Vec::new(),
        cut: false,
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
    (walk.fields, walk.cut)
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

struct Walk {
    limit: usize,
    fields: Vec<InspectionField>,
    cut: bool,
    /// The path of the value being walked.
    path: Vec<String>,
}

impl Walk {
    /// Lists a leaf at the current path unless the limit is reached, which stops the walk.
    fn leaf(&mut self, place: Place, value: Leaf) {
        if self.fields.len() >= self.limit {
            self.cut = true;
            return;
        }
        self.fields.push(InspectionField {
            group: place.group,
            origin: place.origin,
            path: self.path.clone(),
            value,
        });
    }

    fn text(&mut self, place: Place, text: &str) {
        let value = self.shorten(text);
        self.leaf(place, Leaf::Text(value));
    }

    /// A number, or its name when the source's float is not finite.
    fn number(&mut self, place: Place, number: f64) {
        if number.is_finite() {
            self.leaf(place, Leaf::Number(number));
        } else {
            self.text(place, &number.to_string());
        }
    }

    /// Whether the limit is reached, which is also when something is left out: called with a value still to list.
    fn stop(&mut self) -> bool {
        let full = self.fields.len() >= self.limit;
        self.cut |= full;
        full
    }

    fn shorten(&mut self, text: &str) -> String {
        match text.char_indices().nth(MAX_TEXT_CHARS) {
            Some((end, _)) => {
                self.cut = true;
                text[..end].to_owned()
            }
            None => text.to_owned(),
        }
    }

    /// Walks `inside` with `segment` added to the path.
    fn under(&mut self, segment: &str, inside: impl FnOnce(&mut Self)) {
        let segment = self.shorten(segment);
        self.path.push(segment);
        inside(self);
        self.path.pop();
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
        if let Some(score) = classification.score {
            self.number_at(place, "score", score.value());
        }
        if let Some(confidence) = classification.confidence {
            self.number_at(place, "confidence", confidence.value());
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
            self.number_at(place, "points", polygon.points().len() as f64);
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
                SceneTime::Frame(frame) => self.number_at(place, "frame_number", frame as f64),
                SceneTime::Timestamp(time) => self.number_at(place, "timestamp_us", time.0 as f64),
            }
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
            Value::Int(whole) => self.number(place, *whole as f64),
            Value::Float(number) => self.number(place, *number),
            Value::Str(text) => self.text(place, text),
            Value::Null | Value::List(_) | Value::Map(_) => {}
        }
    }
}

#[cfg(test)]
mod tests;
