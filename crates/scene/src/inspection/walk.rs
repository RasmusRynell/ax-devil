//! Stable, resumable traversal of an entity without a flattened field list.

use super::*;
use sha2::Digest;

/// Walks the entity's leaves into `sink`, in display order.
pub(super) fn walk_entity<S: Sink>(entity: &Entity, sink: &mut S) {
    let digest = None;
    let mut walk = Walk {
        sink,
        path: Vec::new(),
        address: Vec::new(),
        next: 0,
        digest,
    };
    let observation = entity.latest_observation();
    walk.branch(|walk| {
        if let Some(observation) = observation {
            walk.classification(observation);
        }
    });
    walk.branch(|walk| walk.motion(entity.motion_state, observation));
    walk.branch(|walk| {
        if let Some(observation) = observation {
            walk.geometry(observation);
        }
    });
    walk.branch(|walk| {
        if let Some(observation) = observation {
            walk.debug(observation);
        }
    });
    walk.branch(|walk| walk.raw(entity, observation));
    walk.branch(|walk| walk.images(entity));
}

struct Walk<'a, S> {
    sink: &'a mut S,
    /// The path of the value being walked.
    path: Vec<&'a str>,
    address: Vec<usize>,
    next: usize,
    digest: Option<sha2::Sha256>,
}

impl<S: Sink> Walk<'_, S> {
    /// Gives the sink a leaf at the current path, made only if the sink wants it.
    fn emit(&mut self, place: Place, value: impl FnOnce() -> Leaf, marks: Marks) {
        self.emit_sized(place, 0, value, marks);
    }

    /// Like `emit`, for a leaf with `bytes` of text.
    fn emit_sized(
        &mut self,
        place: Place,
        bytes: usize,
        value: impl FnOnce() -> Leaf,
        marks: Marks,
    ) {
        self.sink.address(&self.address);
        if let Some(digest) = &self.digest {
            self.sink.fingerprint(digest);
        }
        if self
            .sink
            .wants(place, &self.path, marks.color.is_some(), bytes)
        {
            self.sink.push(place, &self.path, value(), marks);
        }
    }

    fn leaf(&mut self, place: Place, value: Leaf) {
        let bytes = match &value {
            Leaf::Text(text) => text.len(),
            _ => 0,
        };
        self.emit_sized(place, bytes, || value, Marks::default());
    }

    fn text(&mut self, place: Place, text: &str) {
        self.sink.address(&self.address);
        if let Some(digest) = &self.digest {
            self.sink.fingerprint(digest);
        }
        self.sink.text(place, &self.path, text);
    }

    /// A number, or its name when the source's float is not finite.
    fn number(&mut self, place: Place, number: f64) {
        self.leaf(place, number_leaf(number));
    }

    /// Whether the sink is full, which is also when something is left out: called with a value still to list.
    fn stop(&mut self) -> bool {
        self.sink.full()
    }

    /// Walks `inside` with `segment` added to the path.
    fn under(&mut self, segment: &str, inside: impl FnOnce(&mut Walk<'_, S>)) {
        self.descend(Some(segment), inside);
    }

    fn branch(&mut self, inside: impl FnOnce(&mut Walk<'_, S>)) {
        self.descend(None, inside);
    }

    fn skip(&self) -> usize {
        let resume = self.sink.resume();
        if resume.starts_with(&self.address) {
            resume.get(self.address.len()).copied().unwrap_or(0)
        } else {
            0
        }
    }

    fn descend(&mut self, segment: Option<&str>, inside: impl FnOnce(&mut Walk<'_, S>)) {
        let mut address = self.address.clone();
        address.push(self.next);
        self.next += 1;
        let resume = self.sink.resume();
        let common = address.len().min(resume.len());
        if address[..common] < resume[..common] || self.sink.full() {
            return;
        }
        if !self.sink.enter(&address) {
            return;
        }
        let mut digest = self.digest.clone();
        if self.sink.wants_fingerprint()
            && let Some(segment) = segment
        {
            if let Some(digest) = &mut digest {
                digest.update((segment.len() as u64).to_le_bytes());
                for bytes in segment.as_bytes().chunks(64 << 10) {
                    if self.sink.full() {
                        return;
                    }
                    digest.update(bytes);
                }
            } else if self.path.iter().map(|part| part.len()).sum::<usize>() + segment.len()
                > 16 << 10
            {
                // Start once at a long parent, then clone its fixed-size hash state for its children.
                let mut next = sha2::Sha256::new();
                for part in self.path.iter().copied().chain(std::iter::once(segment)) {
                    next.update((part.len() as u64).to_le_bytes());
                    for bytes in part.as_bytes().chunks(64 << 10) {
                        if self.sink.full() {
                            return;
                        }
                        next.update(bytes);
                    }
                }
                digest = Some(next);
            }
        }
        let mut nested = Walk {
            sink: &mut *self.sink,
            path: self.path.iter().copied().chain(segment).collect(),
            address,
            next: 0,
            digest,
        };
        inside(&mut nested);
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
        let primary = self.sink.ranked().map_or_else(
            || observation.primary_classification(),
            |order| {
                order
                    .first()
                    .map(|&index| &observation.classifications[index as usize])
            },
        );
        if let Some(primary) = primary {
            self.under("type", |walk| walk.text(place, primary.class_type.as_str()));
            self.scores(place, primary);
            self.under("attributes", |walk| walk.map(place, &primary.attributes));
        }
        let fallback = self
            .sink
            .ranked()
            .is_none()
            .then(|| observation.ranked_classifications());
        self.under("candidates", |walk| {
            let count = walk
                .sink
                .ranked()
                .map_or_else(|| fallback.as_ref().map_or(0, Vec::len), <[u32]>::len);
            for index in walk.skip()..count {
                if walk.stop() {
                    break;
                }
                let candidate = if let Some(order) = walk.sink.ranked() {
                    &observation.classifications[order[index] as usize]
                } else {
                    fallback
                        .as_ref()
                        .expect("the legacy query walk prepared its order")[index]
                };
                walk.next = index;
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
        if let Some(points) = observation.geometry.polygon_points() {
            self.count_at(place, "points", points.len() as u64);
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
            self.branch(|walk| {
                for (index, (key, value)) in fields.iter().enumerate().skip(walk.skip()) {
                    walk.next = index;
                    if walk.stop() {
                        break;
                    }
                    walk.under(key, |walk| walk.reported(place, value));
                }
            });
        }
    }

    fn raw(&mut self, entity: &Entity, observation: Option<&Observation>) {
        let place = place(FieldGroup::Raw);
        if let Some(reason) = entity.end_reason.as_deref().filter(|r| !r.is_empty()) {
            self.under("end_reason", |walk| walk.text(place, reason));
        }
        if let Some(observation) = observation {
            if let Some(time) = observation.time.timestamp() {
                self.under("timestamp", |walk| walk.leaf(place, datetime_leaf(time)));
            }
            if let Some(frame) = observation.time.frame() {
                self.under("frame_number", |walk| walk.leaf(place, Leaf::Int(frame)));
            }
        }
    }

    fn images(&mut self, entity: &Entity) {
        let place = place(FieldGroup::Images);
        for (index, image) in entity.images.iter().enumerate().skip(self.skip()) {
            self.next = index;
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
        for (index, (key, value)) in fields.iter().enumerate().skip(self.skip()) {
            self.next = index;
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
                match self.sink.colors(&self.address, items) {
                    Some(true) => return self.colors(place, items),
                    Some(false) => {}
                    None => return,
                }
                for (index, item) in items.iter().enumerate().skip(self.skip()) {
                    self.next = index;
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
    fn colors(&mut self, place: Place, colors: &[Value]) {
        for (index, item) in colors.iter().enumerate().skip(self.skip()) {
            let Some(color) = ColorEntry::read_exact(item) else {
                continue;
            };
            self.next = index;
            if self.stop() {
                break;
            }
            let segment = color.name.filter(|name| !name.is_empty()).map_or_else(
                || std::borrow::Cow::Owned(index.to_string()),
                std::borrow::Cow::Borrowed,
            );
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
                for (index, (key, value)) in fields.iter().enumerate().skip(self.skip()) {
                    self.next = index;
                    if self.stop() {
                        break;
                    }
                    self.under(key, |walk| walk.reported(place, value));
                }
            }
            Value::List(items) => {
                for (index, item) in items.iter().enumerate().skip(self.skip()) {
                    self.next = index;
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
