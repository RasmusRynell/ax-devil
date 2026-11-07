//! What a search compares in an entity: the leaves a query's paths name, found without listing the entity's fields.

use super::{Entity, Leaf, Marks, Place, Sink, walk_entity};

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
    ) -> impl DoubleEndedIterator<Item = &'p str> + Clone {
        std::iter::once(place.group.name())
            .chain(path.iter().map(String::as_str))
            .chain(channel.into_iter().flat_map(|channel| ["rgb", channel]))
    }

    /// Whether a wanted path names the leaf's key. A key a path cannot be matched against refuses the search.
    fn names(&mut self, place: Place, path: &[String], channel: Option<&str>) -> bool {
        for wanted in self.paths {
            match path_names(wanted, Self::pieces(place, path, channel)) {
                Ok(true) => return true,
                Ok(false) => {}
                Err(KeyTooLong) => self.over = true,
            }
            if self.over {
                break;
            }
        }
        false
    }

    /// Keeps a leaf if the budget has room for it.
    fn keep(&mut self, place: Place, path: &[String], channel: Option<&str>, value: Leaf) {
        // The key is measured before it is built, so that one that does not fit is never allocated.
        let pieces = || Self::pieces(place, path, channel);
        let key_bytes = pieces().map(str::len).sum::<usize>() + pieces().count() - 1;
        let text = if let Leaf::Text(text) = &value {
            text.len()
        } else {
            16
        };
        match self.budget.bytes.checked_sub(key_bytes + text) {
            Some(left) => {
                self.budget.bytes = left;
                self.found
                    .push((pieces().collect::<Vec<_>>().join("."), value));
            }
            None => self.over = true,
        }
    }
}

impl Sink for Search<'_> {
    fn full(&mut self) -> bool {
        self.over
    }

    fn wants(&mut self, place: Place, path: &[String], color: bool, bytes: usize) -> bool {
        match self.budget.leaves.checked_sub(1) {
            Some(left) => self.budget.leaves = left,
            None => self.over = true,
        }
        if self.over {
            return false;
        }
        let named = self.names(place, path, None)
            || (color && CHANNELS.iter().any(|c| self.names(place, path, Some(c))));
        // A text that cannot be kept is not copied.
        if bytes > self.budget.bytes {
            self.over = named;
        }
        named && !self.over
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
/// Whether a query's `path` (lower case) names the key made of `pieces` joined by dots: it equals the key or a
/// trailing run of its dotted parts, ignoring the key's case. The key is read from its end, only as far as the path
/// reaches, and is not built; unless the path has a sigma, whose lower case depends on its neighbours (`AΣ` is `aς`
/// as a key, `aσ` letter by letter), when a key with a capital sigma is lowercased whole, as a string. Such a path is
/// not matched against a key longer than [`MAX_SIGMA_KEY_BYTES`], which is refused.
pub fn path_names<'a>(
    path: &str,
    pieces: impl DoubleEndedIterator<Item = &'a str> + Clone,
) -> Result<bool, KeyTooLong> {
    if path.contains(['σ', 'ς']) {
        let bytes = pieces.clone().map(str::len).sum::<usize>() + pieces.clone().count();
        if bytes > MAX_SIGMA_KEY_BYTES {
            return Err(KeyTooLong);
        }
        // Lowercasing a capital sigma is the one thing that depends on the letters around it.
        if pieces.clone().any(|piece| piece.contains('Σ')) {
            let key = pieces.collect::<Vec<_>>().join(".").to_lowercase();
            return Ok(key == path
                || key
                    .strip_suffix(path)
                    .is_some_and(|front| front.ends_with('.')));
        }
    }
    // A dot after every piece: after the first, it stands for the front of the key.
    let mut key = pieces.rev().flat_map(|piece| {
        let reversed = piece.chars().rev().flat_map(|c| c.to_lowercase().rev());
        reversed.chain(std::iter::once('.'))
    });
    let named = path.chars().rev().all(|wanted| key.next() == Some(wanted));
    Ok(named && key.next() == Some('.'))
}

/// The longest key, in bytes, that a path with a sigma is matched against.
pub const MAX_SIGMA_KEY_BYTES: usize = 64 * 1024;

/// A path with a sigma was asked of a key longer than [`MAX_SIGMA_KEY_BYTES`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct KeyTooLong;
