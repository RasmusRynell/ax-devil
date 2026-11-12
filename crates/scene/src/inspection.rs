//! An entity's fields as the inspector lists them: the leaves of its latest observation, grouped by what they say.
//!
//! Hit-testing never reads `source_data` or `debug`, but inspecting an object shows both, under Debug with their
//! origin. A Scene's own `debug` is never listed.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

mod leaf;
mod list_shape;
mod pages;
mod traversal;
mod walk;
pub use pages::{FieldChunk, FieldPage, MAX_COPY_BYTES, PageField};
pub use traversal::{MAX_CLASSIFICATIONS, Traversal};
use walk::walk_entity;
mod query;
mod raw;
pub use raw::{RawCopy, raw_chunk};

pub use leaf::Leaf;
use leaf::{datetime_leaf, number_leaf};
pub use query::{Budget, KeyTooLong, OverBudget, path_names, query_leaves};

use crate::{
    Classification, ColorEntry, Entity, MotionState, Name, Observation, Rgb, Value, WorldVelocity,
};

/// The most fields one inspection page lists.
pub const MAX_FIELDS: usize = 4096;

/// The most bytes of paths and texts an inspection lists. Every field repeats the keys above it, so a long key over many
/// leaves would otherwise multiply; what does not fit continues on another page.
pub const MAX_INSPECTION_BYTES: usize = 8 * 1024 * 1024;

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

/// Where the leaves of a walk go, one at a time with the path they are at, borrowed.
trait Sink {
    fn wants_fingerprint(&self) -> bool {
        false
    }
    fn fingerprint(&mut self, _: &sha2::Sha256) {}
    fn enter(&mut self, _: &[usize]) -> bool {
        true
    }
    fn colors(&mut self, _: &[usize], items: &[Value]) -> Option<bool> {
        Some(
            !items.is_empty()
                && items
                    .iter()
                    .all(|item| ColorEntry::read_exact(item).is_some()),
        )
    }

    fn ranked(&self) -> Option<&[u32]> {
        None
    }
    fn resume(&self) -> &[usize] {
        &[]
    }
    fn address(&mut self, _: &[usize]) {}

    /// Whether the walk should stop: asked with a value still to list. A sink that cuts the walk short says so.
    fn full(&mut self) -> bool;
    /// Whether the leaf at `path` is wanted, asked before it is made so that an unwanted one costs nothing; `color`
    /// says the leaf is a colour's score, whose `rgb` channels are leaves of their own, and `bytes` how long its text
    /// is, so that one too long to keep is never copied.
    fn wants(&mut self, place: Place, path: &[&str], color: bool, bytes: usize) -> bool;
    /// Visits text without first copying the whole value.
    fn text(&mut self, place: Place, path: &[&str], text: &str) {
        if self.wants(place, path, false, text.len()) {
            self.push(place, path, Leaf::Text(text.to_owned()), Marks::default());
        }
    }
    /// Takes a leaf that is wanted.
    fn push(&mut self, place: Place, path: &[&str], value: Leaf, marks: Marks);
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

/// Visits the types this module sends to the frontend, and its modules' types.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<FieldGroup>();
    types.visit::<FieldOrigin>();
    leaf::visit_api_types(types);
}

#[cfg(test)]
mod tests;
