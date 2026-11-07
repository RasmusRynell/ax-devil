//! What a search compares in an entity: the leaves a query's paths name, found without listing the entity's fields.

use super::{Entity, Leaf, Marks, Place, Sink, path_names, walk_entity};

/// What a search may spend over all the entities it reads.
#[derive(Clone, Copy, Debug)]
pub struct Budget {
    /// How many leaves it may walk, wanted or not.
    pub leaves: usize,
    /// How many bytes of keys and texts it may collect.
    pub bytes: usize,
}

/// The search spent its [`Budget`]: it would have had to walk or collect more than it was allowed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct OverBudget;

/// The leaves of `entity` whose key one of `paths` (lower case) names, each with its key: the lower-case group and
/// the path, dotted (`debug.vendor.id`); a colour adds `rgb.r`, `rgb.g` and `rgb.b`. Fields that share a key give a
/// leaf each, so repeated values stay. Unwanted leaves cost no allocation, whatever the length of their path.
pub fn query_leaves(
    entity: &Entity,
    paths: &[String],
    budget: &mut Budget,
) -> Result<Vec<(String, Leaf)>, OverBudget> {
    let mut search = Search {
        paths,
        budget,
        found: Vec::new(),
        over: false,
    };
    walk_entity(entity, &mut search);
    if search.over {
        Err(OverBudget)
    } else {
        Ok(search.found)
    }
}

struct Search<'a> {
    paths: &'a [String],
    budget: &'a mut Budget,
    found: Vec<(String, Leaf)>,
    over: bool,
}

const CHANNELS: [&str; 3] = ["r", "g", "b"];

impl Search<'_> {
    /// The pieces of the key of the leaf at `path` in `place`, with `rgb` and a channel after it for a colour's.
    fn pieces<'p>(
        place: Place,
        path: &'p [String],
        channel: Option<&'p str>,
    ) -> impl DoubleEndedIterator<Item = &'p str> {
        std::iter::once(place.group.name())
            .chain(path.iter().map(String::as_str))
            .chain(channel.into_iter().flat_map(|channel| ["rgb", channel]))
    }

    fn names(&self, place: Place, path: &[String], channel: Option<&str>) -> bool {
        self.paths
            .iter()
            .any(|wanted| path_names(wanted, Self::pieces(place, path, channel)))
    }

    /// Keeps a leaf if the budget has room for it.
    fn keep(&mut self, place: Place, path: &[String], channel: Option<&str>, value: Leaf) {
        let key = Self::pieces(place, path, channel)
            .collect::<Vec<_>>()
            .join(".");
        let size = key.len()
            + if let Leaf::Text(text) = &value {
                text.len()
            } else {
                16
            };
        match self.budget.bytes.checked_sub(size) {
            Some(left) => {
                self.budget.bytes = left;
                self.found.push((key, value));
            }
            None => self.over = true,
        }
    }
}

impl Sink for Search<'_> {
    fn full(&mut self) -> bool {
        self.over
    }

    fn wants(&mut self, place: Place, path: &[String], color: bool) -> bool {
        match self.budget.leaves.checked_sub(1) {
            Some(left) => self.budget.leaves = left,
            None => self.over = true,
        }
        !self.over
            && (self.names(place, path, None)
                || (color && CHANNELS.iter().any(|c| self.names(place, path, Some(c)))))
    }

    fn push(&mut self, place: Place, path: &[String], value: Leaf, marks: Marks) {
        if self.names(place, path, None) {
            self.keep(place, path, None, value);
        }
        let Some(color) = marks.color else { return };
        for (channel, level) in CHANNELS.into_iter().zip([color.r, color.g, color.b]) {
            if !self.over && self.names(place, path, Some(channel)) {
                self.keep(place, path, Some(channel), Leaf::Int(i64::from(level)));
            }
        }
    }
}
