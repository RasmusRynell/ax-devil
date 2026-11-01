//! Text operations: trimming and eliding ids, formatting numbers, mapping texts.

use std::sync::Arc;

use super::value::{number, text as text_value};
use super::{Fail, invalid};
use crate::ir::Value;

/// Shortens text to `max_length` characters. Text shortened at its end is cut at the first delimiter when one is
/// given (also text that fits) and marked with the suffix when that changed it. Text elided in the middle keeps both
/// ends with the suffix between, so ids that differ only at their start or their end stay apart. Text that already
/// fits is returned unchanged.
pub(super) fn trim_text(args: &[Value]) -> Result<Value, Fail> {
    let [text, max_length, delimiter, suffix, elide] = args else {
        return Err(invalid("trim_text takes five arguments."));
    };
    let Some(original) = text.as_str() else {
        return Ok(Value::Null);
    };
    let count = number(max_length);
    if count.is_nan() || count < 0.0 {
        return Err(invalid("Text length must be nonnegative."));
    }
    let count = count as usize;
    let suffix = suffix.as_str().unwrap_or_default();
    if elide.as_str() == Some("middle") {
        let length = original.chars().count();
        if length <= count {
            return Ok(text.clone());
        }
        let head = count.div_ceil(2);
        let mut result = String::with_capacity(original.len() + suffix.len());
        result.extend(original.chars().take(head));
        result.push_str(suffix);
        result.extend(original.chars().skip(length - count + head));
        return Ok(text_value(&result));
    }
    let delimiter = delimiter.as_str().filter(|delimiter| !delimiter.is_empty());
    // Bytes are at least characters, so text this short certainly fits.
    if original.len() <= count && delimiter.is_none() {
        return Ok(text.clone());
    }
    let end = original
        .char_indices()
        .nth(count)
        .map_or(original.len(), |(index, _)| index);
    let mut kept = &original[..end];
    if let Some(delimiter) = delimiter {
        kept = kept.split(delimiter).next().unwrap_or(kept);
    }
    if kept == original {
        return Ok(text.clone());
    }
    Ok(text_value(&format!("{kept}{suffix}")))
}

/// A number with fixed decimals, rounded half to even on its binary value like Python's `f"{value:.2f}"`; null for no
/// number, and for zero when `hide_zero`.
pub(super) fn number_text(args: &[Value]) -> Result<Value, Fail> {
    let [value, precision, hide_zero] = args else {
        return Err(invalid("number_text takes three arguments."));
    };
    let Some(value) = value.as_f64() else {
        return Ok(Value::Null);
    };
    let places = number(precision);
    if !(0.0..=100.0).contains(&places) {
        return Err(invalid("Precision must be in [0,100]."));
    }
    if hide_zero.as_bool() == Some(true) && value == 0.0 {
        return Ok(Value::Null);
    }
    Ok(Value::Text(Arc::from(format!(
        "{value:.places$}",
        places = places as usize
    ))))
}

/// The entry of the mapping with the text as key, else the default; null stays null.
pub(super) fn lookup(text: &Value, mapping: &Value, default: &Value) -> Value {
    let Some(key) = text.as_str() else {
        return Value::Null;
    };
    mapping
        .as_list()
        .unwrap_or_default()
        .iter()
        .find(|entry| entry.field(0).as_str() == Some(key))
        .map_or_else(|| default.clone(), |entry| entry.field(1).clone())
}

/// A bar fill clamped to 0..=1 and rounded to hundredths, finer than a bar's pixels, so similar values draw alike.
pub(super) fn hundredths(bar: f64) -> f64 {
    format!("{:.2}", bar.clamp(0.0, 1.0)).parse().unwrap_or(0.0)
}
