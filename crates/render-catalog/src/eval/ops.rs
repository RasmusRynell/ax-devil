//! The operations of the language: tests, box and point geometry, vector arrows. Text and color operations are in
//! their own modules.

use drawing::Anchor;

use super::value::{
    box_of, field_number, finite, nonnegative, number, pixel_length, point, point_of, record,
};
use super::{Fail, Machine, color, invalid, text};
use crate::ir::{Op, Value};

/// How far across and down a box an anchor lies, as fractions.
fn anchor_factors(anchor: &Value) -> (f64, f64) {
    match anchor.as_str().and_then(Anchor::parse) {
        Some(Anchor::TopCenter | Anchor::Top) => (0.5, 0.0),
        Some(Anchor::TopRight) => (1.0, 0.0),
        Some(Anchor::CenterLeft | Anchor::Left) => (0.0, 0.5),
        Some(Anchor::Center) => (0.5, 0.5),
        Some(Anchor::CenterRight | Anchor::Right) => (1.0, 0.5),
        Some(Anchor::BottomLeft) => (0.0, 1.0),
        Some(Anchor::BottomCenter | Anchor::Bottom) => (0.5, 1.0),
        Some(Anchor::BottomRight) => (1.0, 1.0),
        Some(Anchor::TopLeft) | None => (0.0, 0.0),
    }
}

/// A box of four numbers that must all be finite.
fn finite_box(x: f64, y: f64, w: f64, h: f64) -> Result<Value, Fail> {
    Ok(record([
        Value::Number(finite(x)?),
        Value::Number(finite(y)?),
        Value::Number(finite(w)?),
        Value::Number(finite(h)?),
    ]))
}

impl Machine<'_, '_> {
    /// Applies `op` to its evaluated arguments, in the order [`Op`] lists them.
    pub(super) fn call(&mut self, op: Op, args: &[Value]) -> Result<Value, Fail> {
        let arg = |index: usize| args.get(index).unwrap_or(&Value::Null);
        let (width, height) = (self.numbers.width, self.numbers.height);
        Ok(match op {
            Op::Gt => Value::Bool(number(arg(0)) > number(arg(1))),
            Op::IsPresent => Value::Bool(!arg(0).is_null()),
            Op::IsNotEmpty => Value::Bool(match arg(0) {
                Value::Text(text) => !text.is_empty(),
                Value::List(items) => !items.is_empty(),
                _ => false,
            }),
            Op::HasArea => {
                let [_, _, w, h] = box_of(arg(0));
                Value::Bool(w > 0.0 && h > 0.0)
            }
            Op::BoxAnchor => {
                let [x, y, w, h] = box_of(arg(0));
                let (ax, ay) = anchor_factors(arg(1));
                point(finite(x + w * ax)?, finite(y + h * ay)?)
            }
            Op::BoxMinSize => {
                let [_, _, w, h] = box_of(arg(0));
                pixel_length(finite((w * width).min(h * height))?)
            }
            Op::OffsetPoint => {
                let [x, y] = point_of(arg(0));
                let (dx, dy) = (self.pixels(arg(1))?, self.pixels(arg(2))?);
                point(finite(x + dx / width)?, finite(y + dy / height)?)
            }
            Op::InsetBox => self.inset_box(arg(0), arg(1), arg(2))?,
            Op::ResizeBox => self.resize_box(arg(0), arg(1), arg(2), arg(3))?,
            Op::ScaleBox => {
                let [x, y, w, h] = box_of(arg(0));
                let (ax, ay) = anchor_factors(arg(3));
                let new_w = w * nonnegative(number(arg(1)))?;
                let new_h = h * nonnegative(number(arg(2)))?;
                finite_box(x + (w - new_w) * ax, y + (h - new_h) * ay, new_w, new_h)?
            }
            Op::PlaceBox => {
                let [x, y, w, h] = box_of(arg(0));
                let (ax, ay) = anchor_factors(arg(3));
                let new_w = nonnegative(self.pixels(arg(1))?)? / width;
                let new_h = nonnegative(self.pixels(arg(2))?)? / height;
                let margin = nonnegative(self.pixels(arg(4))?)?;
                finite_box(
                    x + (w - new_w) * ax + (1.0 - 2.0 * ax) * margin / width,
                    y + (h - new_h) * ay + (1.0 - 2.0 * ay) * margin / height,
                    new_w,
                    new_h,
                )?
            }
            Op::VectorArrow => self.vector_arrow(args)?,
            Op::TrimText => text::trim_text(args)?,
            Op::NumberText => text::number_text(args)?,
            Op::LookupText => text::lookup(arg(0), arg(1), arg(2)),
            Op::ColorRamp => color::ramp(arg(0), arg(1))?,
            Op::PickColor => color::pick(arg(0), arg(1)),
        })
    }

    fn inset_box(&self, geometry: &Value, margin: &Value, fraction: &Value) -> Result<Value, Fail> {
        if margin.is_null() == fraction.is_null() {
            return Err(invalid(
                "inset_box requires exactly one of margin or fraction.",
            ));
        }
        let [x, y, w, h] = box_of(geometry);
        let (dx, dy) = if fraction.is_null() {
            let amount = nonnegative(self.pixels(margin)?)?;
            (
                (w / 2.0).min(amount / self.numbers.width),
                (h / 2.0).min(amount / self.numbers.height),
            )
        } else {
            let fraction = number(fraction);
            if !(0.0..=0.5).contains(&fraction) {
                return Err(invalid("Inset fraction must be in [0,0.5]."));
            }
            (w * fraction, h * fraction)
        };
        finite_box(
            x + dx,
            y + dy,
            (w - 2.0 * dx).max(0.0),
            (h - 2.0 * dy).max(0.0),
        )
    }

    fn resize_box(
        &self,
        geometry: &Value,
        new_width: &Value,
        new_height: &Value,
        anchor: &Value,
    ) -> Result<Value, Fail> {
        let [x, y, w, h] = box_of(geometry);
        let (ax, ay) = anchor_factors(anchor);
        let size = |length: &Value, original: f64, extent: f64| -> Result<f64, Fail> {
            if length.is_null() {
                Ok(original)
            } else {
                Ok(nonnegative(self.pixels(length)?)? / extent)
            }
        };
        let new_w = size(new_width, w, self.numbers.width)?;
        let new_h = size(new_height, h, self.numbers.height)?;
        finite_box(x + (w - new_w) * ax, y + (h - new_h) * ay, new_w, new_h)
    }

    /// The arrow of a velocity: shaft from the origin and the two base points of a head, or null without a vector.
    fn vector_arrow(&self, args: &[Value]) -> Result<Value, Fail> {
        let arg = |index: usize| args.get(index).unwrap_or(&Value::Null);
        let [ox, oy] = point_of(arg(0));
        let vector = arg(1);
        let gain = nonnegative(number(arg(2)))?;
        let maximum = nonnegative(self.pixels(arg(3))?)?;
        let head = nonnegative(self.pixels(arg(4))?)?;
        let half = nonnegative(self.pixels(arg(5))?)?;
        let threshold = nonnegative(number(arg(6)))?;
        if vector.is_null() {
            return Ok(Value::Null);
        }
        let (width, height) = (self.numbers.width, self.numbers.height);
        let (vx, vy) = (field_number(vector, 0), field_number(vector, 1));
        let (dx, dy) = (vx * width * gain, vy * height * gain);
        let magnitude = dx.hypot(dy);
        if vx.hypot(vy) <= threshold || gain == 0.0 || maximum == 0.0 {
            return Ok(Value::Null);
        }
        if !(dx.is_finite() && dy.is_finite() && magnitude.is_finite()) {
            return Err(invalid("Numeric result must be finite."));
        }
        if magnitude == 0.0 {
            return Ok(Value::Null);
        }
        let (ux, uy) = (dx / magnitude, dy / magnitude);
        let shaft = magnitude.min(maximum);
        let (ex, ey) = (ox + ux * shaft / width, oy + uy * shaft / height);
        let headed = head > 0.0 && half > 0.0;
        let fraction = (shaft / if head > 0.0 { head } else { 1.0 }).min(1.0);
        let (length, spread) = (head * fraction, half * fraction);
        let base = |sign: f64| {
            (
                ex + (-ux * length - sign * uy * spread) / width,
                ey + (-uy * length + sign * ux * spread) / height,
            )
        };
        let (first, second) = (base(1.0), base(-1.0));
        let drawn = [ex, ey, first.0, first.1, second.0, second.1];
        if !drawn[..2].iter().all(|v| v.is_finite())
            || (headed && !drawn[2..].iter().all(|v| v.is_finite()))
        {
            return Err(invalid("Numeric result must be finite."));
        }
        let end = point(ex, ey);
        let head_points = if headed {
            Value::List(
                vec![
                    end.clone(),
                    point(first.0, first.1),
                    point(second.0, second.1),
                ]
                .into(),
            )
        } else {
            Value::List(Vec::new().into())
        };
        Ok(record([point(ox, oy), end, head_points]))
    }
}
