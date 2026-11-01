use std::cmp::Ordering;

use serde_json::{Map, Number, Value, json};

use crate::{DrawingInstructions, LabelRow, Paint, StrId};

/// One value of a canonical row. Rows are trees of these so that they sort by their leaves in field order.
enum Cell {
    Null,
    Flag(bool),
    Int(u8),
    Num(f64),
    Text(String),
    List(Vec<Cell>),
    Map(Vec<(&'static str, Cell)>),
}

impl Cell {
    fn json(&self) -> Value {
        match self {
            Self::Null => Value::Null,
            Self::Flag(flag) => Value::Bool(*flag),
            Self::Int(number) => Value::from(*number),
            Self::Num(number) => Number::from_f64(*number).map_or(Value::Null, Value::Number),
            Self::Text(text) => Value::String(text.clone()),
            Self::List(items) => Value::Array(items.iter().map(Self::json).collect()),
            Self::Map(members) => Value::Object(
                members
                    .iter()
                    .map(|(key, cell)| ((*key).to_string(), cell.json()))
                    .collect::<Map<_, _>>(),
            ),
        }
    }

    /// The leaves in field order as `(rank, number, text)`: null ranks 0, numbers and flags 1, text 2.
    fn leaves(&self, out: &mut Vec<(u8, f64, String)>) {
        match self {
            Self::Null => out.push((0, 0.0, String::new())),
            Self::Flag(flag) => out.push((1, f64::from(u8::from(*flag)), String::new())),
            Self::Int(number) => out.push((1, f64::from(*number), String::new())),
            Self::Num(number) => out.push((1, *number, String::new())),
            Self::Text(text) => out.push((2, 0.0, text.clone())),
            Self::List(items) => items.iter().for_each(|item| item.leaves(out)),
            Self::Map(members) => members.iter().for_each(|(_, cell)| cell.leaves(out)),
        }
    }
}

fn compare(a: &[(u8, f64, String)], b: &[(u8, f64, String)]) -> Ordering {
    a.iter()
        .zip(b)
        .map(|(a, b)| {
            a.0.cmp(&b.0)
                .then_with(|| a.1.total_cmp(&b.1))
                .then_with(|| a.2.cmp(&b.2))
        })
        .find(|order| order.is_ne())
        .unwrap_or(a.len().cmp(&b.len()))
}

/// A coordinate or length rounded to five decimals; `-0` becomes `0`.
fn n(value: f32) -> Cell {
    let rounded = (f64::from(value) * 1e5).round() / 1e5;
    Cell::Num(if rounded == 0.0 { 0.0 } else { rounded })
}

fn channels(values: &[u8]) -> Cell {
    Cell::List(values.iter().map(|v| Cell::Int(*v)).collect())
}

fn row(members: Vec<(&'static str, Cell)>) -> Cell {
    Cell::Map(members)
}

impl DrawingInstructions {
    /// The text dump the oracle comparison reads (`fixtures/oracle/README.md`), independent of table order and
    /// sharing, so two renderings that draw the same things compare equal.
    ///
    /// An object with one array per kind, always present: `boxes`, `circles`, `lines`, `points`, `polygons`,
    /// `polylines`, `texts`, `labels`. Each row is an object with the shared paint resolved into it and the strings
    /// resolved, every number rounded to five decimals. Boxes carry `x y w h pen pen_width dashed brush radius`,
    /// circles `x y r pen pen_width dashed brush`, lines `x1 y1 x2 y2 pen pen_width dashed`, points
    /// `x y pen pen_width`, polygons `points pen pen_width dashed brush`, polylines `points pen pen_width dashed`,
    /// texts `x y text anchor color font_family font_size` and labels `x y anchor size family background padding_x
    /// padding_y radius gap runs`. A label background with alpha 0 is `null`, a label without a visible run is left
    /// out, a bar run has an empty `text` and a text run a `null` `bar`.
    ///
    /// Rows of a kind are sorted by their leaves in field order, each compared as null, then numbers and flags
    /// numerically, then text bytewise.
    pub fn canonical(&self) -> Value {
        let paint = |index: u32| self.paint_at(index);
        let shape = |paint: &Paint, names: &[&'static str]| -> Vec<(&'static str, Cell)> {
            names
                .iter()
                .map(|&name| {
                    let cell = match name {
                        "pen" => channels(&paint.pen),
                        "pen_width" => n(paint.pen_width),
                        "dashed" => Cell::Flag(paint.dashed),
                        "brush" => channels(&paint.brush),
                        _ => n(paint.radius),
                    };
                    (name, cell)
                })
                .collect()
        };
        let with =
            |mut members: Vec<(&'static str, Cell)>, paint: &Paint, names: &[&'static str]| {
                members.extend(shape(paint, names));
                row(members)
            };
        let boxes = self.boxes.iter().map(|r| {
            let members = vec![("x", n(r.x)), ("y", n(r.y)), ("w", n(r.w)), ("h", n(r.h))];
            with(
                members,
                &paint(r.paint),
                &["pen", "pen_width", "dashed", "brush", "radius"],
            )
        });
        let circles = self.circles.iter().map(|r| {
            let members = vec![("x", n(r.x)), ("y", n(r.y)), ("r", n(r.radius))];
            with(
                members,
                &paint(r.paint),
                &["pen", "pen_width", "dashed", "brush"],
            )
        });
        let lines = self.lines.iter().map(|r| {
            let members = vec![
                ("x1", n(r.x1)),
                ("y1", n(r.y1)),
                ("x2", n(r.x2)),
                ("y2", n(r.y2)),
            ];
            with(members, &paint(r.paint), &["pen", "pen_width", "dashed"])
        });
        let points = self.points.iter().map(|r| {
            let members = vec![("x", n(r.x)), ("y", n(r.y))];
            with(members, &paint(r.paint), &["pen", "pen_width"])
        });
        let path = |first: u32, count: u32| {
            let start = usize::try_from(first).unwrap_or(usize::MAX);
            let end = start.saturating_add(usize::try_from(count).unwrap_or(usize::MAX));
            let slice = self.path_points.get(start..end.min(self.path_points.len()));
            Cell::List(
                slice
                    .unwrap_or_default()
                    .iter()
                    .map(|[x, y]| Cell::List(vec![n(*x), n(*y)]))
                    .collect(),
            )
        };
        let polygons = self.polygons.iter().map(|r| {
            let members = vec![("points", path(r.first, r.count))];
            with(
                members,
                &paint(r.paint),
                &["pen", "pen_width", "dashed", "brush"],
            )
        });
        let polylines = self.polylines.iter().map(|r| {
            let members = vec![("points", path(r.first, r.count))];
            with(members, &paint(r.paint), &["pen", "pen_width", "dashed"])
        });
        let texts = self.texts.iter().map(|r| {
            let paint = paint(r.paint);
            row(vec![
                ("x", n(r.x)),
                ("y", n(r.y)),
                ("text", self.text(r.text)),
                ("anchor", Cell::Text(r.anchor.name().to_string())),
                ("color", channels(&paint.pen)),
                ("font_family", self.text(paint.font_family)),
                ("font_size", n(paint.font_size)),
            ])
        });
        let labels = self.labels.iter().filter_map(|label| self.label(label));
        let kinds: [(&str, Vec<Cell>); 8] = [
            ("boxes", boxes.collect()),
            ("circles", circles.collect()),
            ("lines", lines.collect()),
            ("points", points.collect()),
            ("polygons", polygons.collect()),
            ("polylines", polylines.collect()),
            ("texts", texts.collect()),
            ("labels", labels.collect()),
        ];
        let mut out = Map::new();
        for (name, rows) in kinds {
            let mut keyed: Vec<_> = rows
                .into_iter()
                .map(|cell| {
                    let mut leaves = Vec::new();
                    cell.leaves(&mut leaves);
                    (leaves, cell)
                })
                .collect();
            keyed.sort_by(|a, b| compare(&a.0, &b.0));
            out.insert(
                name.to_string(),
                Value::Array(keyed.iter().map(|(_, cell)| cell.json()).collect()),
            );
        }
        json!(out)
    }

    /// The text of a string id; empty for none, as a label family without a font reads in the oracle.
    fn text(&self, id: StrId) -> Cell {
        Cell::Text(self.strings.get(id).unwrap_or_default().to_string())
    }

    fn paint_at(&self, index: u32) -> Paint {
        usize::try_from(index)
            .ok()
            .and_then(|i| self.paints.get(i))
            .copied()
            .unwrap_or(Paint {
                pen: [0; 3],
                dashed: false,
                pen_width: 0.0,
                brush: [0; 4],
                font_family: StrId::NONE,
                font_size: 0.0,
                radius: 0.0,
            })
    }

    /// A label as the oracle writes it, or `None` when it shows no run.
    fn label(&self, label: &LabelRow) -> Option<Cell> {
        let style = *self.label_styles.get(usize::try_from(label.style).ok()?)?;
        let start = usize::try_from(label.first_run).ok()?;
        let end = start.saturating_add(usize::try_from(label.runs).ok()?);
        let runs: Vec<Cell> = self
            .runs
            .get(start..end.min(self.runs.len()))
            .unwrap_or_default()
            .iter()
            .filter_map(|run| {
                let bar = !run.bar.is_nan();
                let text = if bar {
                    String::new()
                } else {
                    self.strings.get(run.text).unwrap_or_default().to_string()
                };
                (bar || !text.is_empty()).then(|| {
                    row(vec![
                        ("text", Cell::Text(text)),
                        ("bar", if bar { n(run.bar) } else { Cell::Null }),
                        ("rgb", channels(&run.rgb)),
                        ("weight", Cell::Text(run.weight.name().to_string())),
                    ])
                })
            })
            .collect();
        if runs.is_empty() {
            return None;
        }
        let background = if style.background[3] == 0 {
            Cell::Null
        } else {
            channels(&style.background)
        };
        Some(row(vec![
            ("x", n(label.x)),
            ("y", n(label.y)),
            ("anchor", Cell::Text(label.anchor.name().to_string())),
            ("size", n(style.size)),
            ("family", self.text(style.family)),
            ("background", background),
            ("padding_x", n(style.padding_x)),
            ("padding_y", n(style.padding_y)),
            ("radius", n(style.radius)),
            ("gap", n(style.gap)),
            ("runs", Cell::List(runs)),
        ]))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Anchor, BoxRow, TextRow};

    #[test]
    fn the_canonical_form_ignores_table_order_and_resolves_strings() {
        let build = |swap: bool| {
            let mut d = DrawingInstructions::default();
            let green = d.paint(Paint {
                pen: [0, 255, 0],
                dashed: false,
                pen_width: 0.002,
                brush: [0; 4],
                font_family: StrId::NONE,
                font_size: 0.02,
                radius: 0.0,
            });
            let mut boxes = vec![
                BoxRow {
                    x: 0.5,
                    y: 0.5,
                    w: 0.1,
                    h: 0.1,
                    paint: green,
                },
                BoxRow {
                    x: 0.1,
                    y: 0.2,
                    w: 0.3,
                    h: 0.4,
                    paint: green,
                },
            ];
            if swap {
                boxes.reverse();
            }
            d.boxes = boxes;
            let text = d.strings.intern("car");
            d.texts.push(TextRow {
                x: 0.1,
                y: 0.2,
                text,
                anchor: Anchor::TopLeft,
                paint: green,
            });
            d
        };

        let canonical = build(false).canonical();
        assert_eq!(canonical, build(true).canonical());
        assert_eq!(canonical["boxes"][0]["x"], 0.1, "sorted by x first");
        assert_eq!(canonical["boxes"][0]["pen"], json!([0, 255, 0]));
        assert_eq!(canonical["boxes"][0]["pen_width"], 0.002);
        assert_eq!(canonical["texts"][0]["text"], "car");
        assert_eq!(canonical["texts"][0]["anchor"], "top-left");
        assert_eq!(canonical["texts"][0]["font_family"], "");
        assert_eq!(canonical["labels"], json!([]));
    }
}
