//! The Sample Item: one Scene sample as immutable typed columns that the frontend views without copying.
//!
//! This file and `frontend/src/present/columns.ts` are the single source of truth for the layout; change both
//! together and bump [`VERSION`]. The item holds what drawing a sample reads: the latest observation of every entity
//! that has one (rows, in Scene order) and the relations. History, source data and debug are never in it. It does
//! not know its source, data revision or sample index: the envelope that carries it does, so equal Scenes share the
//! cached bytes.
//!
//! # Layout
//!
//! Everything is little-endian. The header is `u32` words: magic, [`VERSION`], then the counts `entities`,
//! `relations`, `points`, `colours`, `attributes`, `mask_words`, `strings`, `states`, `vocab`, `string_bytes`, then
//! the byte offset of each [`SECTIONS`] entry in section order. Every section and
//! attribute column starts on a multiple of 8 so each is a typed-array view at any item that starts on one, and the
//! item's length is a multiple of 8. Lengths follow from the counts. Sections, with N entities:
//!
//! | Section | Cells | Missing |
//! |---|---|---|
//! | `entity_id` | `u32` string id per row | |
//! | `box` | `f32` left, top, right, bottom per row, as the Scene stores them (normalised to the frame; a polygon's bounds) | |
//! | `poly_index` | `u32` first, count per row into `poly_points`; count 0 is a box | |
//! | `poly_points` | `f32` x, y | |
//! | `motion` | `u8` per row: 0 none, 1 moving, 2 stationary, 3 unknown | 0 |
//! | `velocity` | `f32` vx, vy per row (image velocity) | NaN |
//! | `class` | `u32` string id of the primary classification's type | `u32::MAX` |
//! | `score`, `confidence` | `f64` per row, of the primary classification | NaN |
//! | `class_mask` | `mask_words` `u32` per row, see below | |
//! | `attr_index` | per attribute column `u32` name string id, kind, byte offset of its cells | |
//! | `color_rgba` | `u32` 0xRRGGBBAA per colour, alpha 255 | |
//! | `color_score` | `f64` per colour | NaN |
//! | `color_name` | `u32` string id per colour | `u32::MAX` |
//! | `rel_kind` | `u32` string id per relation | |
//! | `rel_source`, `rel_target` | `u32` row of the endpoint | `u32::MAX`: no row |
//! | `string_offsets` | `u32` start of each string in `string_bytes`, then the end of the last | |
//! | `string_bytes` | UTF-8 | |
//!
//! # Strings
//!
//! Every distinct string is stored once, in three regions whose boundaries the header gives, so a catalog literal
//! has one id whichever field it is compared with:
//!
//! 1. ids `0..states`: the motion states `moving`, `stationary`, `unknown` in [`MotionState`] order, always present.
//!    The `motion` column value `m` (1 to 3) is string `m - 1`.
//! 2. ids `states..vocab`: the class types of the primary classifications, attribute names, string attribute values
//!    and colour names in the order met walking the rows (a row's attributes, then its class type), then relation
//!    kinds in relation order. A string equal to a motion state keeps its id in region 1.
//! 3. ids `vocab..strings`: entity ids not in the regions before, in row order.
//!
//! An attribute column holds one attribute name of the primary classification, for one kind, with a cell per row:
//! kind 0 bool (`u8`, 2 missing), 1 number (`f64`, NaN missing; integers beyond 2^53 lose precision), 2 string
//! (`u32` string id, `u32::MAX` missing), 3 colour list (`u32` first, count into the colour columns; first
//! `u32::MAX` missing, an empty list has count 0). A name seen with two kinds has two columns. A colour list is a
//! list whose items are all maps with an `rgb` of three integers 0 to 255 and optionally a `name` and a `score`.
//! Other lists, maps and nulls are not shipped.
//!
//! # Class mask
//!
//! A row's mask is `mask_words = ceil(n / 32)` words, `n` being the policy's option count: bit `i % 32` of word
//! `i / 32` is set when filter option `i` selects the entity, judging all classes of its latest observation as
//! [`FilterPolicy::keeps`] does. With `E` the enabled options in the same layout and `M` the row's mask, the entity
//! stays when `n == 0 || M & E != 0 || (E != 0 && M & !E == 0)`, each test taken over all words. The mask depends on
//! the policy only, never on the user's toggles, so lanes with different toggles share an item.

use std::collections::HashMap;

use self::attributes::{Attributes, Cells, Kind};
use super::failed;
use crate::{
    Classification, Entity, FilterPolicy, Geometry, MotionState, Observation, Point, Scene,
    SceneError,
};

/// The item format version; the reader refuses another.
pub const VERSION: u32 = 1;

/// The first word of an item: the bytes `AXSI`.
const MAGIC: u32 = u32::from_le_bytes(*b"AXSI");

/// A cell that is missing in a `u32` column.
const NO_ID: u32 = u32::MAX;

/// The sections in the order of the header's offset table.
#[derive(Clone, Copy)]
enum Section {
    EntityId,
    Box,
    PolyIndex,
    PolyPoints,
    Motion,
    Velocity,
    Class,
    Score,
    Confidence,
    ClassMask,
    AttrIndex,
    ColorRgba,
    ColorScore,
    ColorName,
    RelKind,
    RelSource,
    RelTarget,
    StringOffsets,
    StringBytes,
}

/// How many sections the header lists.
const SECTIONS: usize = Section::StringBytes as usize + 1;

/// Magic, version and the ten counts, as words.
const COUNTS: usize = 12;

/// The motion states in `motion` column order after 0 (none); their names are the first strings of every item.
const MOTION_STATES: [(MotionState, &str); 3] = [
    (MotionState::Moving, "moving"),
    (MotionState::Stationary, "stationary"),
    (MotionState::Unknown, "unknown"),
];

/// The header: its counts and the offset of each section.
const HEADER_BYTES: usize = (COUNTS + SECTIONS) * 4;

/// An entity that has a latest observation.
struct Row<'a> {
    entity: &'a Entity,
    observation: &'a Observation,
    primary: Option<&'a Classification>,
}

/// Distinct strings in the order they were first seen, after the motion states.
struct Strings<'a> {
    index: HashMap<&'a str, u32>,
    list: Vec<&'a str>,
}

impl<'a> Strings<'a> {
    fn new() -> Self {
        let mut strings = Self {
            index: HashMap::new(),
            list: Vec::new(),
        };
        for (_, name) in MOTION_STATES {
            strings.intern(name);
        }
        strings
    }

    fn intern(&mut self, text: &'a str) -> u32 {
        if let Some(&id) = self.index.get(text) {
            return id;
        }
        // More than u32::MAX strings fail the length check at the end.
        let id = u32::try_from(self.list.len()).unwrap_or(NO_ID);
        self.index.insert(text, id);
        self.list.push(text);
        id
    }
}

/// The words of a row's class mask: bit `i % 32` of word `i / 32` set when option `i` of `policy` selects the entity
/// by any class of its latest observation.
fn class_mask(policy: &FilterPolicy, observation: &Observation) -> Vec<u32> {
    let mut words = vec![0; policy.options.len().div_ceil(32)];
    for (index, option) in policy.options.iter().enumerate() {
        let names = observation
            .classifications
            .iter()
            .map(|classification| classification.class_type.as_str());
        if option.rule.matches(names) {
            words[index / 32] |= 1 << (index % 32);
        }
    }
    words
}

/// The outline of a row's polygon; a box has none.
fn polygon<'a>(row: &Row<'a>) -> &'a [Point] {
    match &row.observation.geometry {
        Geometry::Polygon(polygon) => polygon.points(),
        Geometry::Box(_) => &[],
    }
}

/// The sections written so far, with the offset of each.
struct Item {
    bytes: Vec<u8>,
    offsets: [u32; SECTIONS],
}

impl Item {
    /// Pads to a multiple of 8 and returns where the next part starts.
    fn align(&mut self) -> Result<u32, SceneError> {
        let at = self.bytes.len().next_multiple_of(8);
        self.bytes.resize(at, 0);
        u32::try_from(at).map_err(|_| failed("the sample is too large for an item"))
    }

    fn section(
        &mut self,
        section: Section,
        fill: impl FnOnce(&mut Vec<u8>),
    ) -> Result<(), SceneError> {
        self.offsets[section as usize] = self.align()?;
        fill(&mut self.bytes);
        Ok(())
    }
}

fn u32s(out: &mut Vec<u8>, values: impl IntoIterator<Item = u32>) {
    for value in values {
        out.extend_from_slice(&value.to_le_bytes());
    }
}

fn f32s(out: &mut Vec<u8>, values: impl IntoIterator<Item = f32>) {
    for value in values {
        out.extend_from_slice(&value.to_le_bytes());
    }
}

fn f64s(out: &mut Vec<u8>, values: impl IntoIterator<Item = f64>) {
    for value in values {
        out.extend_from_slice(&value.to_le_bytes());
    }
}

fn count(length: usize) -> Result<u32, SceneError> {
    u32::try_from(length).map_err(|_| failed("the sample is too large for an item"))
}

/// The Sample Item of `scene`: see the module documentation for the layout.
///
/// `policy` supplies the class mask. Fails when the sample is too large for 32-bit offsets.
pub fn encode_sample_item(scene: &Scene, policy: &FilterPolicy) -> Result<Vec<u8>, SceneError> {
    let rows: Vec<Row> = scene
        .entities()
        .iter()
        .filter_map(|entity| {
            let observation = entity.latest_observation()?;
            let primary = observation.primary_classification();
            Some(Row {
                entity,
                observation,
                primary,
            })
        })
        .collect();

    let mut strings = Strings::new();
    let mut attributes = Attributes::new();
    let classes: Vec<u32> = rows
        .iter()
        .enumerate()
        .map(|(row, entry)| {
            let Some(primary) = entry.primary else {
                return NO_ID;
            };
            attributes.add(&mut strings, rows.len(), row, primary);
            strings.intern(primary.class_type.as_str())
        })
        .collect();
    let relation_kinds: Vec<u32> = scene
        .relations()
        .iter()
        .map(|relation| strings.intern(relation.kind()))
        .collect();
    let vocab = count(strings.list.len())?;
    let entity_ids: Vec<u32> = rows
        .iter()
        .map(|row| strings.intern(row.entity.id().as_str()))
        .collect();
    let row_of: HashMap<&str, u32> = rows
        .iter()
        .enumerate()
        .map(|(row, entry)| (entry.entity.id().as_str(), count(row).unwrap_or(NO_ID)))
        .collect();
    let endpoint = |id: &str| row_of.get(id).copied().unwrap_or(NO_ID);

    let mut item = Item {
        bytes: vec![0; HEADER_BYTES],
        offsets: [0; SECTIONS],
    };
    let mut attribute_index = Vec::with_capacity(attributes.columns.len() * 3);
    for column in &attributes.columns {
        let at = item.align()?;
        let kind = match &column.cells {
            Cells::Bool(cells) => {
                item.bytes.extend_from_slice(cells);
                Kind::Bool
            }
            Cells::Number(cells) => {
                f64s(&mut item.bytes, cells.iter().copied());
                Kind::Number
            }
            Cells::Str(cells) => {
                u32s(&mut item.bytes, cells.iter().copied());
                Kind::Str
            }
            Cells::Colors(cells) => {
                u32s(&mut item.bytes, cells.iter().flatten().copied());
                Kind::Colors
            }
        };
        attribute_index.extend([strings.intern(column.name), kind as u32, at]);
    }

    let bounds = |row: &Row| {
        let bounds = row.observation.geometry.bounds();
        [bounds.left(), bounds.top(), bounds.right(), bounds.bottom()]
    };
    let mut first = 0_usize;
    let mut spans = Vec::with_capacity(rows.len() * 2);
    for row in &rows {
        let points = polygon(row).len();
        spans.extend([count(first)?, count(points)?]);
        first += points;
    }
    item.section(Section::EntityId, |out| {
        u32s(out, entity_ids.iter().copied())
    })?;
    item.section(Section::Box, |out| {
        f32s(out, rows.iter().flat_map(bounds).map(|edge| edge as f32));
    })?;
    item.section(Section::PolyIndex, |out| u32s(out, spans.iter().copied()))?;
    item.section(Section::PolyPoints, |out| {
        let points = rows.iter().flat_map(polygon);
        f32s(out, points.flat_map(|p| [p.x() as f32, p.y() as f32]));
    })?;
    item.section(Section::Motion, |out| {
        out.extend(rows.iter().map(|row| {
            let state = row.entity.motion_state;
            MOTION_STATES
                .iter()
                .position(|(known, _)| Some(*known) == state)
                .map_or(0, |at| at as u8 + 1)
        }));
    })?;
    item.section(Section::Velocity, |out| {
        let velocity = |row: &Row| {
            let velocity = row.observation.measurements.as_ref();
            match velocity.and_then(|measured| measured.image_velocity) {
                Some(velocity) => [velocity.vx as f32, velocity.vy as f32],
                None => [f32::NAN; 2],
            }
        };
        f32s(out, rows.iter().flat_map(velocity));
    })?;
    item.section(Section::Class, |out| u32s(out, classes.iter().copied()))?;
    let number = |pick: fn(&Classification) -> Option<f64>| {
        rows.iter()
            .map(move |row| row.primary.and_then(pick).unwrap_or(f64::NAN))
    };
    item.section(Section::Score, |out| {
        f64s(out, number(|c| c.score.map(|score| score.value())));
    })?;
    item.section(Section::Confidence, |out| {
        f64s(
            out,
            number(|c| c.confidence.map(|confidence| confidence.value())),
        );
    })?;
    item.section(Section::ClassMask, |out| {
        let masks = rows
            .iter()
            .flat_map(|row| class_mask(policy, row.observation));
        u32s(out, masks);
    })?;
    item.section(Section::AttrIndex, |out| u32s(out, attribute_index))?;
    item.section(Section::ColorRgba, |out| {
        u32s(out, attributes.rgba.iter().copied());
    })?;
    item.section(Section::ColorScore, |out| {
        f64s(out, attributes.score.iter().copied());
    })?;
    item.section(Section::ColorName, |out| {
        u32s(out, attributes.name.iter().copied());
    })?;
    item.section(Section::RelKind, |out| u32s(out, relation_kinds))?;
    let relations = scene.relations();
    item.section(Section::RelSource, |out| {
        u32s(out, relations.iter().map(|r| endpoint(r.source().as_str())));
    })?;
    item.section(Section::RelTarget, |out| {
        u32s(out, relations.iter().map(|r| endpoint(r.target().as_str())));
    })?;
    let mut end = 0_u32;
    let mut string_offsets = Vec::with_capacity(strings.list.len() + 1);
    for text in &strings.list {
        string_offsets.push(end);
        end = end.saturating_add(count(text.len())?);
    }
    string_offsets.push(end);
    item.section(Section::StringOffsets, |out| u32s(out, string_offsets))?;
    item.section(Section::StringBytes, |out| {
        strings
            .list
            .iter()
            .for_each(|text| out.extend_from_slice(text.as_bytes()));
    })?;
    item.align()?;

    let header = [
        MAGIC,
        VERSION,
        count(rows.len())?,
        count(relations.len())?,
        count(first)?,
        count(attributes.rgba.len())?,
        count(attributes.columns.len())?,
        count(policy.options.len().div_ceil(32))?,
        count(strings.list.len())?,
        count(MOTION_STATES.len())?,
        vocab,
        end,
    ]
    .into_iter()
    .chain(item.offsets);
    for (word, at) in header.zip((0..).step_by(4)) {
        item.bytes[at..at + 4].copy_from_slice(&word.to_le_bytes());
    }
    Ok(item.bytes)
}

mod attributes;
#[cfg(test)]
mod tests;
