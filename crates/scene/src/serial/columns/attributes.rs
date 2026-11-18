//! The attribute columns of a Sample Item: one per attribute name and value kind of the primary classifications.

use std::collections::HashMap;

use super::{NO_ID, Strings, f64s, failed, u32s};
use crate::{Classification, ColorEntry, SceneError, Value};

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub(super) enum Kind {
    Bool,
    Number,
    Str,
    Colors,
}

impl Kind {
    /// The bytes of one cell in the item.
    fn cell_bytes(self) -> usize {
        match self {
            Self::Bool => 1,
            Self::Str => 4,
            Self::Number | Self::Colors => 8,
        }
    }
}

/// The set cells of one attribute column as (row, value) in row order; the item stores a cell per row, so the
/// dense column is only built when written.
pub(super) enum Cells {
    Bool(Vec<(usize, u8)>),
    Number(Vec<(usize, f64)>),
    Str(Vec<(usize, u32)>),
    Colors(Vec<(usize, [u32; 2])>),
}

/// One attribute value of the kinds an item ships.
enum Cell<'a> {
    Bool(bool),
    Number(f64),
    Str(&'a str),
    Colors(Vec<Colour<'a>>),
}

struct Colour<'a> {
    rgba: u32,
    score: f64,
    name: Option<&'a str>,
}

/// One attribute column: the name and its set cells.
pub(super) struct Column<'a> {
    pub(super) name: &'a str,
    pub(super) kind: Kind,
    cells: Cells,
}

/// `rows` cells, `missing` except where `set` gives one.
fn dense<T: Copy>(rows: usize, missing: T, set: &[(usize, T)]) -> Vec<T> {
    let mut cells = vec![missing; rows];
    for &(row, cell) in set {
        cells[row] = cell;
    }
    cells
}

impl Column<'_> {
    /// Appends the column's cell for each of `rows` rows.
    pub(super) fn write(&self, rows: usize, out: &mut Vec<u8>) {
        match &self.cells {
            Cells::Bool(set) => out.extend_from_slice(&dense(rows, 2, set)),
            Cells::Number(set) => f64s(out, dense(rows, f64::NAN, set)),
            Cells::Str(set) => u32s(out, dense(rows, NO_ID, set)),
            Cells::Colors(set) => u32s(out, dense(rows, [NO_ID, 0], set).into_iter().flatten()),
        }
    }
}

fn colour(value: &Value) -> Option<Colour<'_>> {
    let ColorEntry { rgb, score, name } = ColorEntry::read(value)?;
    Some(Colour {
        rgba: u32::from_be_bytes([rgb.r, rgb.g, rgb.b, 255]),
        score: score.unwrap_or(f64::NAN),
        name,
    })
}

/// The cell of an attribute value, or `None` for a value an item does not ship.
fn cell(value: &Value) -> Option<(Kind, Cell<'_>)> {
    match value {
        Value::Bool(flag) => Some((Kind::Bool, Cell::Bool(*flag))),
        Value::Int(_) | Value::Float(_) => Some((Kind::Number, Cell::Number(value.as_f64()?))),
        Value::Str(text) => Some((Kind::Str, Cell::Str(text))),
        Value::List(items) => Some((
            Kind::Colors,
            Cell::Colors(items.iter().map(colour).collect::<Option<_>>()?),
        )),
        Value::Null | Value::Map(_) => None,
    }
}

/// The attribute columns of the primary classifications, and the strings and colours they use.
pub(super) struct Attributes<'a> {
    pub(super) columns: Vec<Column<'a>>,
    by_kind: HashMap<(&'a str, Kind), usize>,
    pub(super) rgba: Vec<u32>,
    pub(super) score: Vec<f64>,
    pub(super) name: Vec<u32>,
}

impl<'a> Attributes<'a> {
    pub(super) fn new() -> Self {
        Self {
            columns: Vec::new(),
            by_kind: HashMap::new(),
            rgba: Vec::new(),
            score: Vec::new(),
            name: Vec::new(),
        }
    }

    /// Sets the cells of row `row` from `classification`, the first attribute of a name wins.
    pub(super) fn add(
        &mut self,
        strings: &mut Strings<'a>,
        row: usize,
        classification: &'a Classification,
    ) {
        let attributes = &classification.attributes;
        for (position, (name, value)) in attributes.iter().enumerate() {
            if attributes[..position]
                .iter()
                .any(|(other, _)| other == name)
            {
                continue;
            }
            let Some((kind, parsed)) = cell(value) else {
                continue;
            };
            let name: &'a str = name;
            let at = *self.by_kind.entry((name, kind)).or_insert_with(|| {
                strings.intern(name);
                let cells = match kind {
                    Kind::Bool => Cells::Bool(Vec::new()),
                    Kind::Number => Cells::Number(Vec::new()),
                    Kind::Str => Cells::Str(Vec::new()),
                    Kind::Colors => Cells::Colors(Vec::new()),
                };
                self.columns.push(Column { name, kind, cells });
                self.columns.len() - 1
            });
            match (&mut self.columns[at].cells, parsed) {
                (Cells::Bool(cells), Cell::Bool(flag)) => cells.push((row, u8::from(flag))),
                (Cells::Number(cells), Cell::Number(number)) => cells.push((row, number)),
                (Cells::Str(cells), Cell::Str(text)) => cells.push((row, strings.intern(text))),
                (Cells::Colors(cells), Cell::Colors(colours)) => {
                    let first = u32::try_from(self.rgba.len()).unwrap_or(NO_ID);
                    let count = u32::try_from(colours.len()).unwrap_or(NO_ID);
                    cells.push((row, [first, count]));
                    for colour in colours {
                        self.rgba.push(colour.rgba);
                        self.score.push(colour.score);
                        self.name
                            .push(colour.name.map_or(NO_ID, |name| strings.intern(name)));
                    }
                }
                _ => {}
            }
        }
    }

    /// Fails when the dense columns alone would not fit the item's 32-bit offsets for `rows` rows. Checked before
    /// any column is built: a few thousand entities with distinct attribute names make gigabytes of mostly empty cells.
    pub(super) fn check_size(&self, rows: usize) -> Result<(), SceneError> {
        self.columns
            .iter()
            .try_fold(0_usize, |total, column| {
                total.checked_add(rows.checked_mul(column.kind.cell_bytes())?)
            })
            .filter(|&bytes| u32::try_from(bytes).is_ok())
            .map(drop)
            .ok_or_else(|| failed("the sample is too large for an item"))
    }
}
