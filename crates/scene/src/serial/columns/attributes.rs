//! The attribute columns of a Sample Item: one per attribute name and value kind of the primary classifications.

use std::collections::HashMap;

use super::{NO_ID, Strings};
use crate::{Classification, Value};

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub(super) enum Kind {
    Bool,
    Number,
    Str,
    Colors,
}

/// The cells of one attribute column, a cell per row.
pub(super) enum Cells {
    Bool(Vec<u8>),
    Number(Vec<f64>),
    Str(Vec<u32>),
    Colors(Vec<[u32; 2]>),
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

/// One attribute column: the name and a cell per row.
pub(super) struct Column<'a> {
    pub(super) name: &'a str,
    pub(super) cells: Cells,
}

fn colour(value: &Value) -> Option<Colour<'_>> {
    let fields = value.as_map()?;
    let field = |key: &str| {
        fields
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value)
            .filter(|value| !matches!(value, Value::Null))
    };
    let [red, green, blue] = field("rgb")?.as_list()? else {
        return None;
    };
    let channel = |value: &Value| u8::try_from(value.as_i64()?).ok();
    let rgba = u32::from_be_bytes([channel(red)?, channel(green)?, channel(blue)?, 255]);
    let score = match field("score") {
        None => f64::NAN,
        Some(score) => score.as_f64()?,
    };
    let name = match field("name") {
        None => None,
        Some(name) => Some(name.as_str()?),
    };
    Some(Colour { rgba, score, name })
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

    /// Sets the cells of `row` from `classification`, in `rows` rows; the first attribute of a name wins.
    pub(super) fn add(
        &mut self,
        strings: &mut Strings<'a>,
        rows: usize,
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
                    Kind::Bool => Cells::Bool(vec![2; rows]),
                    Kind::Number => Cells::Number(vec![f64::NAN; rows]),
                    Kind::Str => Cells::Str(vec![NO_ID; rows]),
                    Kind::Colors => Cells::Colors(vec![[NO_ID, 0]; rows]),
                };
                self.columns.push(Column { name, cells });
                self.columns.len() - 1
            });
            match (&mut self.columns[at].cells, parsed) {
                (Cells::Bool(cells), Cell::Bool(flag)) => cells[row] = u8::from(flag),
                (Cells::Number(cells), Cell::Number(number)) => cells[row] = number,
                (Cells::Str(cells), Cell::Str(text)) => cells[row] = strings.intern(text),
                (Cells::Colors(cells), Cell::Colors(colours)) => {
                    let first = u32::try_from(self.rgba.len()).unwrap_or(NO_ID);
                    let count = u32::try_from(colours.len()).unwrap_or(NO_ID);
                    cells[row] = [first, count];
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
}
