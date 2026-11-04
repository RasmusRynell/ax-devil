//! Querying a file's history: the objects the video shows, kept by the class toggles and a search, sorted and paged.
//!
//! The search is the reference Objects page's: a parsed [`Search`] of groups (all must match) of terms (any may). The
//! text is parsed by the client; this module only evaluates what it sends.

use std::{borrow::Cow, cmp::Ordering};

use foundation::{CancelToken, Cancelled};
use scene::OperationKind;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

use crate::{history::History, placement::Placement};

/// How many entities between two checks of the cancel token.
const CANCEL_EVERY: u32 = 4096;
/// How many rows a sort orders in one piece, between two checks of the cancel token.
const SORT_RUN: usize = 16_384;

/// How a term compares a field with its value.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
#[ts(rename = "SearchOp")]
pub enum Op {
    /// `:` or `=`: equal, ignoring case.
    Eq,
    /// `>`
    Gt,
    /// `<`
    Lt,
    /// `>=`
    Ge,
    /// `<=`
    Le,
}

/// One condition of a search.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[ts(rename = "TermWire")]
pub struct Term {
    /// The field, or `null` to search the free text. A path names a field when it equals it or its trailing dotted
    /// parts, ignoring case; one that names none of an object's fields matches nothing.
    pub path: Option<String>,
    /// How the field is compared with `value`.
    pub op: Op,
    /// What to compare with, or the text to look for when there is no path.
    pub value: String,
    /// Whether the term holds where it does not match.
    pub negated: bool,
}

/// A parsed search: every group must match, and a group matches when any of its terms does. No group matches
/// everything.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[ts(rename = "SearchWire")]
pub struct Search {
    /// The groups, ANDed.
    pub groups: Vec<Vec<Term>>,
}

/// What the rows are sorted by.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "snake_case")]
#[ts(rename = "FileSortKey")]
pub enum SortKey {
    /// The id, with digit runs compared by value, so `id9` comes before `id10`.
    Id,
    /// The classes joined by a comma, ignoring case.
    Class,
    /// The first frame the object is on.
    FirstSeen,
}

/// The order of the rows. Rows equal in the key are in ascending id order whichever way the key goes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[ts(rename = "FileSortWire")]
pub struct Sort {
    /// What to sort by.
    pub key: SortKey,
    /// Whether the key runs from greatest to least.
    pub descending: bool,
}

/// What to list from the objects the video shows.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Query {
    /// The class toggles: bit `i % 32` of word `i / 32` is set for each enabled filter option, as in the Sample Item's
    /// class mask. Words past the end are 0.
    pub enabled: Vec<u32>,
    /// Which of the kept objects to list.
    pub search: Search,
    /// How to order them.
    pub sort: Sort,
}

/// An object of the file as a list shows it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[ts(rename = "FileRowWire")]
pub struct Row {
    /// The entity id.
    pub id: String,
    /// The classes it was recorded with, in order of first appearance.
    pub classes: Vec<String>,
    /// The kinds of event it is in, in the order delete, rename, merge, split.
    pub events: Vec<OperationKind>,
    /// The first frame the video shows it on.
    #[ts(type = "number")]
    pub first_frame: u64,
    /// The last frame the video shows it on.
    #[ts(type = "number")]
    pub last_frame: u64,
    /// Where it is on screen as `[first, last]` frames, in order, merged so a strip of `bins` marks can draw it; at
    /// most as many as the request's `bins`.
    #[ts(type = "Array<[number, number]>")]
    pub presence: Vec<[u64; 2]>,
}

/// The answer to a [`Query`]: how many objects it lists and in what order. Rows are made a page at a time.
pub struct Matches {
    total: u64,
    /// Entity indices in listing order.
    order: Vec<u32>,
}

impl Matches {
    /// About how much memory the listing holds, in bytes.
    pub fn bytes(&self) -> usize {
        size_of_val(self.order.as_slice())
    }

    /// How many objects the video shows that the class toggles keep.
    pub fn total(&self) -> u64 {
        self.total
    }

    /// How many of them the search matches, which is how many rows there are.
    pub fn matched(&self) -> u64 {
        self.order.len() as u64
    }

    /// Where the object `id` is in the listing, as the index of its row; `None` when the file has no such object or
    /// the class toggles, the search or the video's frames leave it out. `history` is the one the query ran over.
    pub fn row_of(&self, history: &History, id: &str) -> Option<u32> {
        let entity = history.find(id)?;
        let row = self.order.iter().position(|&listed| listed == entity)?;
        Some(row as u32)
    }

    /// Rows `offset..offset + limit` of the listing. `history` and `placement` are those the query ran over.
    pub fn rows(
        &self,
        history: &History,
        placement: &Placement,
        offset: usize,
        limit: usize,
        bins: u16,
    ) -> Vec<Row> {
        let page = self.order.iter().skip(offset).take(limit);
        page.filter_map(|&entity| {
            let runs = history.runs(entity);
            let (first, last) = placement.bounds(runs)?;
            Some(Row {
                id: history.id(entity).to_string(),
                classes: history.classes(entity).map(str::to_string).collect(),
                events: history.event_kinds(entity).collect(),
                first_frame: u64::from(first),
                last_frame: u64::from(last),
                presence: placement.presence(&placement.spans(runs), bins),
            })
        })
        .collect()
    }
}

/// The frames `placement` shows the object `id` on, as ascending `[first, last]` spans of consecutive frames with at
/// least one frame between two: every gap is kept, however short. The class toggles do not matter. Empty when the file
/// has no such object or no frame shows it.
pub fn appearances(history: &History, placement: &Placement, id: &str) -> Vec<[u64; 2]> {
    let Some(entity) = history.find(id) else {
        return Vec::new();
    };
    let spans = placement.spans(history.runs(entity));
    spans
        .into_iter()
        .map(|[first, last]| [u64::from(first), u64::from(last)])
        .collect()
}

impl Query {
    /// Lists the objects of `history` that `placement` shows on at least one frame, that the class toggles keep and
    /// that the search matches. Cancellation is checked as it goes.
    pub fn run(
        &self,
        history: &History,
        placement: &Placement,
        cancel: &CancelToken,
    ) -> Result<Matches, Cancelled> {
        let search = self.search.compile();
        let mut total = 0;
        // The entities listed and the first frame each is on.
        let mut found: Vec<(u32, u32)> = Vec::new();
        for entity in 0..history.entities() as u32 {
            if entity % CANCEL_EVERY == 0 {
                cancel.check()?;
            }
            if !history.kept(&self.enabled, entity) {
                continue;
            }
            let Some((first, _)) = placement.bounds(history.runs(entity)) else {
                continue;
            };
            total += 1;
            if search.matches_entity(history, entity) {
                found.push((entity, first));
            }
        }
        cancel.check()?;
        Ok(Matches {
            total,
            order: self.sort.order(history, found, cancel)?,
        })
    }
}

impl Sort {
    /// The entities of `found` (entity and first frame) in listing order.
    fn order(
        self,
        history: &History,
        found: Vec<(u32, u32)>,
        cancel: &CancelToken,
    ) -> Result<Vec<u32>, Cancelled> {
        let by_id = |a: u32, b: u32| natural_cmp(history.id(a), history.id(b)).then(a.cmp(&b));
        let flip = |order: Ordering| {
            if self.descending {
                order.reverse()
            } else {
                order
            }
        };
        match self.key {
            SortKey::Id => {
                let order: Vec<u32> = found.into_iter().map(|(entity, _)| entity).collect();
                sorted(
                    order,
                    |&a, &b| flip(natural_cmp(history.id(a), history.id(b))).then(a.cmp(&b)),
                    cancel,
                )
            }
            SortKey::FirstSeen => {
                let found = sorted(
                    found,
                    |a, b| flip(a.1.cmp(&b.1)).then_with(|| by_id(a.0, b.0)),
                    cancel,
                )?;
                Ok(found.into_iter().map(|(entity, _)| entity).collect())
            }
            SortKey::Class => {
                let mut keyed: Vec<(String, u32)> = Vec::with_capacity(found.len());
                for (n, (entity, _)) in found.into_iter().enumerate() {
                    if n % CANCEL_EVERY as usize == 0 {
                        cancel.check()?;
                    }
                    let classes: Vec<&str> = history.classes(entity).collect();
                    keyed.push((classes.join(", ").to_lowercase(), entity));
                }
                let positions: Vec<u32> = (0..keyed.len() as u32).collect();
                let positions = sorted(
                    positions,
                    |&a, &b| {
                        let (a, b) = (&keyed[a as usize], &keyed[b as usize]);
                        flip(a.0.cmp(&b.0)).then_with(|| by_id(a.1, b.1))
                    },
                    cancel,
                )?;
                Ok(positions
                    .into_iter()
                    .map(|at| keyed[at as usize].1)
                    .collect())
            }
        }
    }
}

/// `items` ordered by `cmp`, a total order. Runs of [`SORT_RUN`] are sorted, then merged pairwise, with the cancel
/// token checked between runs and every [`CANCEL_EVERY`] merged items, so a superseded sort stops promptly.
fn sorted<T: Copy>(
    mut items: Vec<T>,
    cmp: impl Fn(&T, &T) -> Ordering,
    cancel: &CancelToken,
) -> Result<Vec<T>, Cancelled> {
    for run in items.chunks_mut(SORT_RUN) {
        cancel.check()?;
        run.sort_by(&cmp);
    }
    let mut width = SORT_RUN;
    while width < items.len() {
        let mut merged = Vec::with_capacity(items.len());
        for pair in items.chunks(2 * width) {
            cancel.check()?;
            let (mut left, mut right) = pair.split_at(width.min(pair.len()));
            while let (Some(&l), Some(&r)) = (left.first(), right.first()) {
                if merged.len() % CANCEL_EVERY as usize == 0 {
                    cancel.check()?;
                }
                if cmp(&r, &l) == Ordering::Less {
                    merged.push(r);
                    right = &right[1..];
                } else {
                    merged.push(l);
                    left = &left[1..];
                }
            }
            merged.extend_from_slice(left);
            merged.extend_from_slice(right);
        }
        items = merged;
        width *= 2;
    }
    Ok(items)
}

/// Compares ids so digit runs count by value and text compares by code point: the id split into text, number, text,
/// number, ..., text (a text part may be empty), compared part by part, the shorter first on a tie.
fn natural_cmp(mut a: &str, mut b: &str) -> Ordering {
    loop {
        let (text_a, rest_a) = a.split_at(a.find(|c: char| c.is_ascii_digit()).unwrap_or(a.len()));
        let (text_b, rest_b) = b.split_at(b.find(|c: char| c.is_ascii_digit()).unwrap_or(b.len()));
        match text_a.cmp(text_b) {
            Ordering::Equal => {}
            other => return other,
        }
        match (rest_a.is_empty(), rest_b.is_empty()) {
            (true, true) => return Ordering::Equal,
            (true, false) => return Ordering::Less,
            (false, true) => return Ordering::Greater,
            (false, false) => {}
        }
        let (digits_a, next_a) = rest_a.split_at(
            rest_a
                .find(|c: char| !c.is_ascii_digit())
                .unwrap_or(rest_a.len()),
        );
        let (digits_b, next_b) = rest_b.split_at(
            rest_b
                .find(|c: char| !c.is_ascii_digit())
                .unwrap_or(rest_b.len()),
        );
        let (digits_a, digits_b) = (
            digits_a.trim_start_matches('0'),
            digits_b.trim_start_matches('0'),
        );
        match digits_a
            .len()
            .cmp(&digits_b.len())
            .then_with(|| digits_a.cmp(digits_b))
        {
            Ordering::Equal => {}
            other => return other,
        }
        (a, b) = (next_a, next_b);
    }
}

/// What a search looks at in one object: fields by dotted path, each with its values, and the strings free text
/// searches.
pub(crate) struct Record<'a> {
    pub fields: Vec<(&'a str, Vec<&'a str>)>,
    pub text: Vec<&'a str>,
}

/// A [`Search`] with its text lowercased once.
pub(crate) struct Compiled {
    groups: Vec<Vec<Needle>>,
}

struct Needle {
    path: Option<String>,
    op: Op,
    value: String,
    negated: bool,
}

impl Search {
    pub(crate) fn compile(&self) -> Compiled {
        let needle = |term: &Term| Needle {
            path: term.path.as_ref().map(|path| path.to_lowercase()),
            op: term.op,
            value: term.value.to_lowercase(),
            negated: term.negated,
        };
        Compiled {
            groups: self
                .groups
                .iter()
                .map(|group| group.iter().map(needle).collect())
                .collect(),
        }
    }
}

impl Compiled {
    /// Whether the object's id, classes and event kinds match: fields `id`, `class` and `event`, and free text over
    /// the id and the classes.
    fn matches_entity(&self, history: &History, entity: u32) -> bool {
        if self.groups.is_empty() {
            return true;
        }
        let id = history.id(entity);
        let classes: Vec<&str> = history.classes(entity).collect();
        let events: Vec<&str> = history
            .event_kinds(entity)
            .map(OperationKind::title)
            .collect();
        let text = std::iter::once(id).chain(classes.iter().copied()).collect();
        self.matches(&Record {
            fields: vec![("id", vec![id]), ("class", classes), ("event", events)],
            text,
        })
    }

    /// Whether every group has a term that holds for `record`.
    pub(crate) fn matches(&self, record: &Record<'_>) -> bool {
        self.groups
            .iter()
            .all(|group| group.iter().any(|term| term.holds(record)))
    }
}

impl Needle {
    fn holds(&self, record: &Record<'_>) -> bool {
        let hit = match &self.path {
            None => record
                .text
                .iter()
                .any(|text| lower(text).contains(self.value.as_str())),
            Some(path) => record
                .fields
                .iter()
                .filter(|(key, _)| path_names(path, key))
                .flat_map(|(_, values)| values)
                .any(|value| self.compares(value)),
        };
        hit != self.negated
    }

    /// Fields hold text, and text equals ignoring case; it is never greater or less than a number, so the ordering
    /// operators match nothing.
    fn compares(&self, value: &str) -> bool {
        self.op == Op::Eq && lower(value) == self.value
    }
}

/// Whether `path` (lowercase) names `key`: it equals the key or a trailing run of its dotted parts, ignoring case.
fn path_names(path: &str, key: &str) -> bool {
    let key = lower(key);
    key == path
        || key
            .strip_suffix(path)
            .is_some_and(|front| front.ends_with('.'))
}

fn lower(text: &str) -> Cow<'_, str> {
    if text.chars().any(char::is_uppercase) {
        Cow::Owned(text.to_lowercase())
    } else {
        Cow::Borrowed(text)
    }
}
