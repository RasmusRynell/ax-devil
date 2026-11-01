//! Small constructors and readers for the values the evaluator builds and takes apart.

use std::cmp::Ordering;
use std::sync::{Arc, LazyLock};

use super::Fail;
use crate::ir::Value;

/// The unit of a length that is already in pixels.
static PIXELS: LazyLock<Arc<str>> = LazyLock::new(|| Arc::from("px"));

/// A record of these fields, in the order of its type.
pub(super) fn record<const N: usize>(fields: [Value; N]) -> Value {
    Value::Record(Arc::new(fields))
}

/// An `image_point`.
pub(super) fn point(x: f64, y: f64) -> Value {
    record([Value::Number(x), Value::Number(y)])
}

/// A `length` of `amount` pixels.
pub(super) fn pixel_length(amount: f64) -> Value {
    record([Value::Number(amount), Value::Text(Arc::clone(&PIXELS))])
}

/// A text value.
pub(super) fn text(text: &str) -> Value {
    Value::Text(Arc::from(text))
}

/// The number of a value that is a number by its type; NaN otherwise, which every check rejects.
pub(super) fn number(value: &Value) -> f64 {
    value.as_f64().unwrap_or(f64::NAN)
}

/// Field `index` of a record as a number.
pub(super) fn field_number(value: &Value, index: usize) -> f64 {
    number(value.field(index))
}

/// The `x`, `y`, `w` and `h` of an `image_box`.
pub(super) fn box_of(value: &Value) -> [f64; 4] {
    [0, 1, 2, 3].map(|index| field_number(value, index))
}

/// The `x` and `y` of an `image_point`.
pub(super) fn point_of(value: &Value) -> [f64; 2] {
    [0, 1].map(|index| field_number(value, index))
}

/// The channels of an `rgb` value.
pub(super) fn rgb_of(value: &Value) -> [u8; 3] {
    let items = value.as_list().unwrap_or_default();
    [0, 1, 2].map(|index| {
        let channel = items.get(index).map_or(0.0, number);
        // Channels are checked to be whole numbers in 0..=255 by their type; clamping only guards the cast.
        channel.clamp(0.0, 255.0) as u8
    })
}

/// The number, failing when it is not finite.
pub(super) fn finite(value: f64) -> Result<f64, Fail> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(super::invalid("Numeric result must be finite."))
    }
}

/// The number, failing with `message` unless it is zero or more; NaN fails.
pub(super) fn at_least_zero(value: f64, message: impl FnOnce() -> String) -> Result<f64, Fail> {
    match value.partial_cmp(&0.0) {
        Some(Ordering::Greater | Ordering::Equal) => Ok(value),
        _ => Err(super::invalid(message())),
    }
}

/// The number, failing with `message` unless it is above zero; NaN fails.
pub(super) fn above_zero(value: f64, message: impl FnOnce() -> String) -> Result<f64, Fail> {
    match value.partial_cmp(&0.0) {
        Some(Ordering::Greater) => Ok(value),
        _ => Err(super::invalid(message())),
    }
}

/// The number, failing when it is negative or not a number.
pub(super) fn nonnegative(value: f64) -> Result<f64, Fail> {
    at_least_zero(value, || "Value must be nonnegative.".to_string())
}
