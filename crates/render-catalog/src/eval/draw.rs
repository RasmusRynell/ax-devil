//! Drawing primitives: styles become shared paints, primitives become rows of the Drawing Instructions.

use std::collections::HashMap;

use drawing::{
    Anchor, BoxRow, CircleRow, DrawingInstructions, LabelRow, LabelStyle, LineRow, Paint, PathRow,
    PointRow, RunRow, StrId, TextRow, Weight,
};

use super::text::hundredths;
use super::value::{above_zero, at_least_zero, box_of, number, point_of, rgb_of};
use super::{Fail, Frame, Machine, Numbers};
use crate::ir::{Draw, Primitive, Value};

/// The font a text uses when its style names none.
const DEFAULT_FAMILY: &str = "Arial";

/// A paint by value, to find an equal one already in the tables.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct PaintKey {
    pen: [u8; 3],
    dashed: bool,
    brush: [u8; 4],
    family: u32,
    floats: [u32; 3],
}

impl PaintKey {
    fn of(paint: &Paint) -> Self {
        Self {
            pen: paint.pen,
            dashed: paint.dashed,
            brush: paint.brush,
            family: paint.font_family.0,
            floats: [paint.pen_width, paint.font_size, paint.radius].map(f32::to_bits),
        }
    }
}

/// A label style by value.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct StyleKey {
    background: [u8; 4],
    family: u32,
    floats: [u32; 5],
}

impl StyleKey {
    fn of(style: &LabelStyle) -> Self {
        Self {
            background: style.background,
            family: style.family.0,
            floats: [
                style.size,
                style.padding_x,
                style.padding_y,
                style.radius,
                style.gap,
            ]
            .map(f32::to_bits),
        }
    }
}

/// How many rows of each table there were, to take back what a failing row added.
pub(super) struct Mark {
    boxes: usize,
    lines: usize,
    circles: usize,
    points: usize,
    path_points: usize,
    polygons: usize,
    polylines: usize,
    texts: usize,
    labels: usize,
    runs: usize,
}

/// Appends rows to the Drawing Instructions and keeps the shared paints and label styles free of repeats.
pub(super) struct Emitter<'o> {
    out: &'o mut DrawingInstructions,
    scale: f64,
    paints: HashMap<PaintKey, u32>,
    styles: HashMap<StyleKey, u32>,
}

impl<'o> Emitter<'o> {
    pub(super) fn new(out: &'o mut DrawingInstructions, numbers: &Numbers) -> Self {
        Self {
            out,
            scale: numbers.scale,
            paints: HashMap::new(),
            styles: HashMap::new(),
        }
    }

    pub(super) fn mark(&self) -> Mark {
        let out = &*self.out;
        Mark {
            boxes: out.boxes.len(),
            lines: out.lines.len(),
            circles: out.circles.len(),
            points: out.points.len(),
            path_points: out.path_points.len(),
            polygons: out.polygons.len(),
            polylines: out.polylines.len(),
            texts: out.texts.len(),
            labels: out.labels.len(),
            runs: out.runs.len(),
        }
    }

    /// Takes back every row added since `mark`. The shared tables keep what was added; unused entries are harmless.
    pub(super) fn rollback(&mut self, mark: Mark) {
        let out = &mut *self.out;
        out.boxes.truncate(mark.boxes);
        out.lines.truncate(mark.lines);
        out.circles.truncate(mark.circles);
        out.points.truncate(mark.points);
        out.path_points.truncate(mark.path_points);
        out.polygons.truncate(mark.polygons);
        out.polylines.truncate(mark.polylines);
        out.texts.truncate(mark.texts);
        out.labels.truncate(mark.labels);
        out.runs.truncate(mark.runs);
    }

    fn paint(&mut self, paint: Paint) -> u32 {
        let out = &mut *self.out;
        *self
            .paints
            .entry(PaintKey::of(&paint))
            .or_insert_with(|| out.paint(paint))
    }

    fn label_style(&mut self, style: LabelStyle) -> u32 {
        let out = &mut *self.out;
        *self.styles.entry(StyleKey::of(&style)).or_insert_with(|| {
            out.label_styles.push(style);
            u32::try_from(out.label_styles.len() - 1).unwrap_or(u32::MAX)
        })
    }

    fn string(&mut self, text: &str) -> StrId {
        self.out.strings.intern(text)
    }

    /// The first point of a run of `path_points` and how many were added.
    fn path(&mut self, points: &[Value]) -> (u32, u32) {
        let first = u32::try_from(self.out.path_points.len()).unwrap_or(u32::MAX);
        self.out.path_points.extend(points.iter().map(|corner| {
            let [x, y] = point_of(corner);
            [x as f32, y as f32]
        }));
        (first, u32::try_from(points.len()).unwrap_or(u32::MAX))
    }

    fn scaled(&self, pixels: f64) -> f32 {
        (pixels / self.scale) as f32
    }
}

fn anchor(value: &Value) -> Anchor {
    // The contract has no baseline anchor yet; a text on its baseline sits where a bottom-left one does, which is
    // within a descender of it.
    match value.as_str() {
        Some("baseline") => Anchor::BottomLeft,
        Some(name) => Anchor::parse(name).unwrap_or(Anchor::TopLeft),
        None => Anchor::TopLeft,
    }
}

fn weight(value: &Value) -> Weight {
    match value.as_str() {
        Some("medium") => Weight::Medium,
        Some("semibold") => Weight::Semibold,
        Some("bold") => Weight::Bold,
        _ => Weight::Regular,
    }
}

fn channel(value: &Value) -> u8 {
    // Channels are whole numbers in 0..=255 by their type; clamping only guards the cast.
    number(value).clamp(0.0, 255.0) as u8
}

impl Machine<'_, '_> {
    /// Evaluates a primitive's fields and style and appends its row. Nothing is drawn for a shape without area, a
    /// circle without radius, a polygon without points or a label without visible runs.
    pub(super) fn draw(&mut self, frame: &mut Frame<'_>, draw: &Draw) -> Result<(), Fail> {
        let fields = self.eval(frame, &draw.fields)?;
        let style = self.eval(frame, &draw.style)?;
        let field = |index: usize| fields.field(index);
        match draw.primitive {
            Primitive::Box => {
                let paint = self.shape_paint(&style)?;
                let [x, y, w, h] = box_of(field(0));
                if w > 0.0 && h > 0.0 {
                    self.emitter.out.boxes.push(BoxRow {
                        x: x as f32,
                        y: y as f32,
                        w: w as f32,
                        h: h as f32,
                        paint,
                    });
                }
            }
            Primitive::Line => {
                let paint = self.shape_paint(&style)?;
                let ([x1, y1], [x2, y2]) = (point_of(field(0)), point_of(field(1)));
                self.emitter.out.lines.push(LineRow {
                    x1: x1 as f32,
                    y1: y1 as f32,
                    x2: x2 as f32,
                    y2: y2 as f32,
                    paint,
                });
            }
            Primitive::Point => {
                let paint = self.shape_paint(&style)?;
                let [x, y] = point_of(field(0));
                self.emitter.out.points.push(PointRow {
                    x: x as f32,
                    y: y as f32,
                    paint,
                });
            }
            Primitive::Circle => {
                let paint = self.shape_paint(&style)?;
                let [x, y] = point_of(field(0));
                let radius = at_least_zero(self.pixels(field(1))?, || {
                    "Radius must be nonnegative.".to_string()
                })?;
                if radius > 0.0 {
                    let radius = self.emitter.scaled(radius);
                    self.emitter.out.circles.push(CircleRow {
                        x: x as f32,
                        y: y as f32,
                        radius,
                        paint,
                    });
                }
            }
            Primitive::Polygon | Primitive::Polyline => {
                let paint = self.shape_paint(&style)?;
                let points = field(0).as_list().unwrap_or_default();
                if !points.is_empty() {
                    let (first, count) = self.emitter.path(points);
                    let row = PathRow {
                        first,
                        count,
                        paint,
                    };
                    let out = &mut *self.emitter.out;
                    if draw.primitive == Primitive::Polygon {
                        out.polygons.push(row);
                    } else {
                        out.polylines.push(row);
                    }
                }
            }
            Primitive::Text => self.text(&fields, &style)?,
            Primitive::Label => self.label(&fields, &style)?,
        }
        Ok(())
    }

    /// The paint of a shape: outline, fill and corner radius, each when its style has it.
    fn shape_paint(&mut self, style: &Value) -> Result<u32, Fail> {
        let (stroke, fill, radius) = (style.field(0), style.field(1), style.field(2));
        let mut paint = Paint {
            pen: [0; 3],
            dashed: false,
            pen_width: 0.0,
            brush: [0; 4],
            font_family: StrId::NONE,
            font_size: 0.0,
            radius: 0.0,
        };
        if !stroke.is_null() {
            let width = self.pixels(stroke.field(1))? / self.emitter.scale;
            let width = at_least_zero(width, || "Stroke width must be nonnegative.".to_string())?;
            paint.pen = rgb_of(stroke.field(0));
            paint.pen_width = width as f32;
            paint.dashed = stroke.field(2).as_str() == Some("dash");
        }
        if !fill.is_null() {
            let [r, g, b] = rgb_of(fill.field(0));
            paint.brush = [r, g, b, channel(fill.field(1))];
        }
        if !radius.is_null() {
            let corner = self.pixels(radius)? / self.emitter.scale;
            let corner =
                at_least_zero(corner, || "Corner radius must be nonnegative.".to_string())?;
            paint.radius = corner as f32;
        }
        Ok(self.emitter.paint(paint))
    }

    fn text(&mut self, fields: &Value, style: &Value) -> Result<(), Fail> {
        let style = style.field(0);
        let size = above_zero(self.pixels(style.field(1))?, || {
            "Font size must be positive.".to_string()
        })?;
        let family = style.field(2).as_str().unwrap_or(DEFAULT_FAMILY);
        let family = self.emitter.string(family);
        let paint = self.emitter.paint(Paint {
            pen: rgb_of(style.field(0)),
            dashed: false,
            pen_width: 0.0,
            brush: [0; 4],
            font_family: family,
            font_size: self.emitter.scaled(size),
            radius: 0.0,
        });
        let [x, y] = point_of(fields.field(0));
        let text = self
            .emitter
            .string(fields.field(1).as_str().unwrap_or_default());
        self.emitter.out.texts.push(TextRow {
            x: x as f32,
            y: y as f32,
            text,
            anchor: anchor(fields.field(2)),
            paint,
        });
        Ok(())
    }

    fn label(&mut self, fields: &Value, style: &Value) -> Result<(), Fail> {
        let label = style.field(0);
        let size = above_zero(self.pixels(label.field(0))?, || {
            "Label size must be positive.".to_string()
        })?;
        let mut spacing = [0.0; 4];
        for (slot, (index, name)) in spacing.iter_mut().zip([
            (3, "padding_x"),
            (4, "padding_y"),
            (5, "radius"),
            (6, "gap"),
        ]) {
            let pixels = self.pixels(label.field(index))?;
            *slot = at_least_zero(pixels, || format!("Label {name} must be nonnegative."))?;
        }
        let runs = fields.field(2).as_list().unwrap_or_default();
        let first_run = u32::try_from(self.emitter.out.runs.len()).unwrap_or(u32::MAX);
        for run in runs {
            let text = run.field(0).as_str().unwrap_or_default();
            let bar = run.field(1);
            let (text, bar) = if bar.is_null() {
                (text, f32::NAN)
            } else {
                ("", hundredths(number(bar)) as f32)
            };
            if text.is_empty() && bar.is_nan() {
                continue;
            }
            let text = if bar.is_nan() {
                self.emitter.string(text)
            } else {
                StrId::NONE
            };
            self.emitter.out.runs.push(RunRow {
                text,
                bar,
                rgb: rgb_of(run.field(2)),
                weight: weight(run.field(3)),
            });
        }
        let count = u32::try_from(self.emitter.out.runs.len()).unwrap_or(u32::MAX) - first_run;
        if count == 0 {
            return Ok(());
        }
        let background = label.field(2);
        let background = if background.is_null() {
            [0; 4]
        } else {
            let [r, g, b] = rgb_of(background.field(0));
            [r, g, b, channel(background.field(1))]
        };
        let family = label
            .field(1)
            .as_str()
            .map_or(StrId::NONE, |family| self.emitter.string(family));
        let [padding_x, padding_y, radius, gap] = spacing.map(|pixels| self.emitter.scaled(pixels));
        let style = self.emitter.label_style(LabelStyle {
            size: self.emitter.scaled(size),
            family,
            background,
            padding_x,
            padding_y,
            radius,
            gap,
        });
        let [x, y] = point_of(fields.field(0));
        self.emitter.out.labels.push(LabelRow {
            x: x as f32,
            y: y as f32,
            anchor: anchor(fields.field(1)),
            style,
            first_run,
            runs: count,
        });
        Ok(())
    }
}
